"""Tests for isolated revision captures and private standalone comparisons."""

from __future__ import annotations

import argparse
import hashlib
from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zlib

from lib import agent_cli, agent_visuals, agent_workspace
from lib.remote_utils import CommandTimeoutError
from lib.visual_evidence import verify_receipt
from lib import visual_compare_pfm


def png_bytes(width: int = 2, height: int = 2, color: tuple = (20, 80, 120, 255)) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    raw = (b"\0" + bytes(color) * width) * height
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


class TestAgentVisuals(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.before = self.root / "before.png"
        self.after = self.root / "after.png"
        self.before.write_bytes(png_bytes())
        self.after.write_bytes(png_bytes(color=(100, 80, 120, 255)))
        self.settings = self.root / "settings.json"
        self.settings.write_text(json.dumps({"scene": "test", "camera": [0, 2, 4], "seed": 42}))
        self.parser = argparse.ArgumentParser()
        agent_cli.add_agent_subparser(self.parser.add_subparsers(dest="command"))

    def compare_args(self, *extra):
        return self.parser.parse_args([
            "agent", "visuals", "compare", str(self.before), str(self.after),
            "--output", str(self.root / "comparison"), *extra,
        ])

    def capture_args(self, *extra):
        return self.parser.parse_args([
            "agent", "visuals", "capture", "--before", "dev", "--after", "staging",
            "--settings", str(self.settings), "--output", str(self.root / "capture"),
            *extra, "--", "./capture-scene", "--output", "{output}", "--settings", "{settings}",
        ])

    def mock_capture_system(self):
        self.enterContext(patch.object(agent_workspace, "_repository_root", return_value=str(self.root / "primary")))
        validate = self.enterContext(patch.object(agent_workspace, "_validate_base", side_effect=["a" * 40, "b" * 40]))
        create = self.enterContext(patch.object(agent_workspace, "create_agent_worktree", side_effect=[
            {"path": str(self.root / "worktree-before")}, {"path": str(self.root / "worktree-after")},
        ]))

        def capture(argv, **kwargs):
            Path(argv[argv.index("--output") + 1]).write_bytes(png_bytes())
            return subprocess.CompletedProcess(argv, 0)

        execute = self.enterContext(patch.object(agent_visuals, "run", side_effect=capture))
        return validate, create, execute

    def test_comparison_embeds_images_and_settings_privately(self) -> None:
        result = agent_visuals.compare_captures(self.compare_args("--before-settings", str(self.settings)))
        viewer = Path(result["viewer"])
        html = viewer.read_text()
        self.assertIn("data:image/png;base64,", html)
        self.assertEqual(result["before"]["width"], 2)
        self.assertEqual(result["before"]["camera"], [0, 2, 4])
        self.assertNotEqual(result["before"]["sha256"], result["after"]["sha256"])
        self.assertEqual(viewer.stat().st_mode & 0o777, 0o600)
        self.assertEqual(viewer.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.before.read_bytes(), png_bytes())

    def test_metadata_cannot_inject_html_or_scripts(self) -> None:
        self.settings.write_text(json.dumps({"scene": '</script><script>alert("bad")</script>'}))
        result = agent_visuals.compare_captures(self.compare_args("--before-settings", str(self.settings)))
        html = Path(result["viewer"]).read_text()
        self.assertNotIn('</script><script>alert', html)
        self.assertIn(r'\u003c/script>', html)

    def test_existing_output_is_never_replaced(self) -> None:
        output = self.root / "comparison"
        output.mkdir()
        marker = output / "index.html"
        marker.write_text("retain")
        with self.assertRaises(FileExistsError):
            agent_visuals.compare_captures(self.compare_args())
        self.assertEqual(marker.read_text(), "retain")

    def test_nonfinite_settings_fail_before_artifact_creation(self) -> None:
        for value in ("NaN", "Infinity", "-Infinity", "1e999"):
            with self.subTest(value=value):
                self.settings.write_text('{"nested": {"value": ' + value + '}}')
                with self.assertRaises(ValueError):
                    agent_visuals.compare_captures(self.compare_args("--before-settings", str(self.settings)))
                self.assertFalse((self.root / "comparison").exists())

    def test_invalid_images_and_links_are_rejected(self) -> None:
        for content in (b"not a PNG", png_bytes()[:20]):
            self.before.write_bytes(content)
            with self.assertRaises(ValueError):
                agent_visuals._image(str(self.before), {})
        self.before.unlink()
        self.before.symlink_to(self.after)
        with self.assertRaises(OSError):
            agent_visuals._image(str(self.before), {})
        self.before.unlink()
        os.mkfifo(self.before)
        with self.assertRaises(ValueError):
            agent_visuals._image(str(self.before), {})

    def test_oversized_image_and_dimensions_are_bounded(self) -> None:
        content = bytearray(png_bytes())
        content[16:24] = struct.pack(">II", 16385, 2)
        self.before.write_bytes(content)
        with self.assertRaises(ValueError):
            agent_visuals._image(str(self.before), {})
        self.before.write_bytes(png_bytes() + b" " * 100)
        with patch.object(agent_visuals, "MAX_IMAGE_BYTES", 100), self.assertRaises(ValueError):
            agent_visuals._image(str(self.before), {})

    def test_capture_pins_refs_and_replays_settings_in_distinct_worktrees(self) -> None:
        validate, create, execute = self.mock_capture_system()
        result = agent_visuals.capture_revisions(self.capture_args("--timeout", "20"))
        self.assertTrue(result["ok"])
        self.assertEqual(validate.call_count, 2)
        self.assertEqual([call.kwargs["base"] for call in create.call_args_list], ["a" * 40, "b" * 40])
        self.assertNotEqual(create.call_args_list[0].args[1], create.call_args_list[1].args[1])
        calls = execute.call_args_list
        self.assertEqual([call.kwargs["cwd"] for call in calls], [str(self.root / "worktree-before"), str(self.root / "worktree-after")])
        self.assertTrue(all(call.kwargs["timeout"] == 20 for call in calls))
        for side, call in zip(("before", "after"), calls):
            self.assertEqual(call.kwargs["env"]["BASALTWATER_VISUAL_VERIFY"], "0")
            self.assertEqual(call.args[0][2], str(self.root / "capture" / f"{side}.png"))
            self.assertEqual(call.args[0][4], str(self.root / "capture" / "settings.json"))
            self.assertEqual(result["captures"][side]["settings"], json.loads(self.settings.read_text()))
            self.assertEqual(result["captures"][side]["status"], "captured")
        self.assertEqual(json.loads((self.root / "capture/capture.json").read_text())["ok"], True)

    def test_command_failure_retains_evidence_and_stops_second_capture(self) -> None:
        _, create, execute = self.mock_capture_system()
        execute.side_effect = None
        execute.return_value = subprocess.CompletedProcess([], 7)
        result = agent_visuals.capture_revisions(self.capture_args())
        self.assertFalse(result["ok"])
        self.assertEqual(result["captures"]["before"]["exit_status"], 7)
        self.assertEqual(result["captures"]["before"]["status"], "failed")
        self.assertEqual(create.call_count, 1)
        self.assertTrue((self.root / "capture/capture.json").is_file())
        self.assertFalse((self.root / "capture/index.html").exists())

    def test_strict_capture_requires_receipt_before_second_capture(self) -> None:
        _, create, execute = self.mock_capture_system()
        result = agent_visuals.capture_revisions(self.capture_args("--verify-evidence"))
        self.assertEqual(execute.call_args.kwargs["env"]["BASALTWATER_VISUAL_VERIFY"], "1")
        self.assertFalse(result["ok"])
        self.assertEqual(create.call_count, 1)
        self.assertFalse((self.root / "capture/index.html").exists())

    def test_runtime_receipt_rejects_stale_builds_and_unapplied_settings(self) -> None:
        artifact = self.root / "game.node"
        artifact.write_bytes(b"compiled checkpoint")
        path = self.root / "receipt.json"
        receipt = {"version": 1, "revision": "a" * 40, "effective_settings": {"quality": 1},
                   "artifacts": [{"path": "game.node", "kind": "native", "revision": "a" * 40,
                                  "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()}]}
        path.write_text(json.dumps(receipt))
        result = verify_receipt(str(path), str(self.root), "a" * 40, {"quality": 1}, ["native"])
        self.assertEqual(result["status"], "verified")
        for settings, revision, kinds, message in (({"quality": True}, "a" * 40, [], "did not take effect"),
                                                  ({}, "b" * 40, [], "revision"),
                                                  ({}, "a" * 40, ["asset"], "Missing required")):
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                verify_receipt(str(path), str(self.root), revision, settings, kinds)
        artifact.write_bytes(b"concurrent font build")
        with self.assertRaisesRegex(ValueError, "differs from its build receipt"):
            verify_receipt(str(path), str(self.root), "a" * 40, {}, [])
        receipt["artifacts"][0]["path"] = "../game.node"
        path.write_text(json.dumps(receipt))
        with self.assertRaisesRegex(ValueError, "repository-relative"):
            verify_receipt(str(path), str(self.root), "a" * 40, {}, [])

    def test_timeout_is_recorded_without_a_successful_viewer(self) -> None:
        _, _, execute = self.mock_capture_system()
        execute.side_effect = CommandTimeoutError("capture", 20)
        result = agent_visuals.capture_revisions(self.capture_args())
        self.assertFalse(result["ok"])
        self.assertIn("timed out", result["error"])
        self.assertEqual(result["captures"]["before"]["status"], "failed")

    def test_changed_settings_stop_the_pair(self) -> None:
        _, create, execute = self.mock_capture_system()

        def change_settings(argv, **kwargs):
            path = Path(argv[argv.index("--settings") + 1])
            path.chmod(0o600)
            path.write_text('{"camera": "different"}')
            return subprocess.CompletedProcess(argv, 0)

        execute.side_effect = change_settings
        result = agent_visuals.capture_revisions(self.capture_args())
        self.assertFalse(result["ok"])
        self.assertIn("changed the shared settings", result["error"])
        self.assertEqual(result["captures"]["before"]["status"], "failed")
        self.assertEqual(create.call_count, 1)

    def test_unicode_settings_remain_valid_when_the_private_copy_expands(self) -> None:
        self.mock_capture_system()
        settings = {"scene": "景" * 19000}
        self.settings.write_text(json.dumps(settings, ensure_ascii=False), encoding="utf-8")
        result = agent_visuals.capture_revisions(self.capture_args())
        self.assertTrue(result["ok"])
        self.assertEqual(result["captures"]["after"]["settings"], settings)

    def test_boolean_replacing_numeric_settings_stops_before_second_capture(self) -> None:
        self.settings.write_text('{"seed":1}')
        _, create, execute = self.mock_capture_system()

        def change_settings(argv, **kwargs):
            path = Path(argv[argv.index("--settings") + 1])
            path.chmod(0o600)
            path.write_text('{"seed":true}')
            Path(argv[argv.index("--output") + 1]).write_bytes(png_bytes())
            return subprocess.CompletedProcess(argv, 0)

        execute.side_effect = change_settings
        result = agent_visuals.capture_revisions(self.capture_args())
        self.assertFalse(result["ok"])
        self.assertIn("changed the shared settings", result["error"])
        self.assertEqual(create.call_count, 1)

    def test_invalid_ref_fails_before_artifact_or_worktree_creation(self) -> None:
        validate, create, _ = self.mock_capture_system()
        validate.side_effect = ValueError("Unsafe revision")
        with self.assertRaises(ValueError):
            agent_visuals.capture_revisions(self.capture_args())
        create.assert_not_called()
        self.assertFalse((self.root / "capture").exists())

    def test_command_requires_output_placeholder_before_mutations(self) -> None:
        args = self.capture_args()
        args.capture_command = ["--", "./capture-scene"]
        with patch.object(agent_workspace, "create_agent_worktree") as create, self.assertRaises(ValueError):
            agent_visuals.capture_revisions(args)
        create.assert_not_called()

    def test_cli_errors_are_json_and_failure_status(self) -> None:
        output = StringIO()
        with redirect_stdout(output):
            args = self.compare_args("--json")
            args.before = str(self.root / "missing.png")
            self.assertEqual(agent_cli.run_agent_command(args), 1)
        self.assertFalse(json.loads(output.getvalue())["ok"])


class TestPFMComparisons(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.before = self.root / "before.pfm"
        self.after = self.root / "after.pfm"
        self.parser = argparse.ArgumentParser()
        agent_cli.add_agent_subparser(self.parser.add_subparsers(dest="command"))

    def write_pfm(self, path, samples, *, width=2, height=1, channels=1, scale=-1):
        magic = "PF" if channels == 3 else "Pf"
        header = f"{magic}\n# synthetic fixture\n{width} {height}\n{scale}\n".encode()
        path.write_bytes(header + struct.pack(("<" if scale < 0 else ">") + "f" * len(samples), *samples))

    def args(self, *extra):
        return self.parser.parse_args(["agent", "visuals", "compare", str(self.before), str(self.after),
                                       "--output", str(self.root / "comparison"), *extra])

    def test_endianness_scale_channels_and_row_orientation(self) -> None:
        for channels in (1, 3):
            for scale in (-2, 2):
                with self.subTest(channels=channels, scale=scale):
                    samples = list(range(1, 4 * channels + 1))
                    self.write_pfm(self.before, samples, height=2, channels=channels, scale=scale)
                    metadata, values = visual_compare_pfm.read_pfm(str(self.before), {})
                    expected = samples[2 * channels:] + samples[:2 * channels]
                    self.assertEqual(list(values), [value * 2 for value in expected])
                    self.assertEqual(metadata["channels"], channels)
                    self.assertTrue(metadata["finite_samples"])

    def test_linear_metrics_and_exposure_are_independent(self) -> None:
        self.write_pfm(self.before, [0, 2])
        self.write_pfm(self.after, [1, 4])
        result = agent_visuals.compare_captures(self.args("--exposure", "-2", "--relative-floor", "0.5"))
        metrics = result["metrics"]
        self.assertEqual(metrics["mean_absolute_error"], 1.5)
        self.assertEqual(metrics["max_absolute_error"], 2)
        self.assertEqual(metrics["mean_relative_error"], 1.5)
        self.assertEqual(metrics["max_relative_error"], 2)
        self.assertAlmostEqual(metrics["root_mean_square_error"], 2.5 ** 0.5)
        self.assertEqual(metrics["changed_samples"], 2)
        self.assertEqual(result["acceptance"], "project-owned")
        self.assertEqual(result["view"]["exposure_stops"], -2)
        persisted = json.loads((self.root / "comparison/comparison.json").read_text())
        self.assertEqual(persisted, result)
        self.assertEqual(Path(result["viewer"]).stat().st_mode & 0o777, 0o600)
        self.assertEqual(Path(result["viewer"]).parent.stat().st_mode & 0o777, 0o700)
        (self.root / "comparison").rename(self.root / "first")
        second = agent_visuals.compare_captures(self.args("--exposure", "4", "--relative-floor", "0.5"))
        self.assertEqual(second["metrics"], metrics)

    def test_identical_negative_and_hdr_values_remain_linear(self) -> None:
        for path in (self.before, self.after):
            self.write_pfm(path, [-4, 1000])
        result = agent_visuals.compare_captures(self.args())
        self.assertEqual(result["metrics"]["max_absolute_error"], 0)
        self.assertEqual(result["metrics"]["changed_samples"], 0)
        self.assertEqual(result["before"]["minimum"], -4)
        self.assertEqual(result["before"]["maximum"], 1000)

    def test_nonfinite_samples_and_scale_overflow_fail_before_output(self) -> None:
        self.write_pfm(self.after, [0, 1])
        for value, scale in ((float("nan"), -1), (float("inf"), -1), (-float("inf"), -1), (3e38, -1e300)):
            with self.subTest(value=value, scale=scale):
                self.write_pfm(self.before, [0, value], scale=scale)
                with self.assertRaisesRegex(ValueError, "non-finite"):
                    agent_visuals.compare_captures(self.args())
                self.assertFalse((self.root / "comparison").exists())

    def test_invalid_headers_payloads_and_bounds(self) -> None:
        for content in (b"P6\n1 1\n-1\n1234", b"Pf\n0 2\n-1\n", b"Pf\n16385 1\n-1\n",
                        b"Pf\n1 1\n0\n1234", b"Pf\n1 1\nnan\n1234", b"Pf\n1 1\ninf\n1234",
                        b"Pf\n1 1\n-1\n123", b"Pf\n1 1\n-1\n12345", b"Pf\n2\n-1\n",
                        b"Pf\n" + b"#" * 4096 + b"\n", b"Pf\n1 1\n-1"):
            with self.subTest(content=content[:40]):
                self.before.write_bytes(content)
                with self.assertRaises(ValueError):
                    visual_compare_pfm.read_pfm(str(self.before), {})
        self.write_pfm(self.before, [0, 1])
        with patch.object(visual_compare_pfm, "MAX_PFM_BYTES", 8), self.assertRaisesRegex(ValueError, "exceeds"):
            visual_compare_pfm.read_pfm(str(self.before), {})
        with patch.object(visual_compare_pfm, "MAX_PFM_PIXELS", 1), self.assertRaisesRegex(ValueError, "dimensions"):
            visual_compare_pfm.read_pfm(str(self.before), {})

    def test_links_and_nonregular_files_are_refused(self) -> None:
        self.write_pfm(self.after, [0, 1])
        self.before.symlink_to(self.after)
        with self.assertRaises(OSError):
            visual_compare_pfm.read_pfm(str(self.before), {})
        self.before.unlink()
        os.mkfifo(self.before)
        with self.assertRaisesRegex(ValueError, "regular"):
            visual_compare_pfm.read_pfm(str(self.before), {})

    def test_incompatible_dimensions_or_channels_have_no_metrics(self) -> None:
        self.write_pfm(self.before, [0, 1])
        for samples, width, channels in (([0], 1, 1), ([0] * 6, 2, 3)):
            with self.subTest(channels=channels):
                self.write_pfm(self.after, samples, width=width, channels=channels)
                result = agent_visuals.compare_captures(self.args())
                self.assertIsNone(result["metrics"])
                self.assertIn("incompatible", result["comparison_status"])
                (self.root / "comparison").rename(self.root / f"incompatible-{channels}")

    def test_invalid_view_options_mixed_formats_and_metric_overflow(self) -> None:
        self.write_pfm(self.before, [0, 1])
        self.write_pfm(self.after, [1, 2])
        for options in (("--exposure", "nan"), ("--exposure", "33"), ("--relative-floor", "0"),
                        ("--relative-floor", "inf"), ("--relative-floor", "5e-324")):
            with self.subTest(options=options), self.assertRaises(ValueError):
                agent_visuals.compare_captures(self.args(*options))
        args = self.args()
        args.after = str(self.root / "after.png")
        with self.assertRaisesRegex(ValueError, "PFM with PFM"):
            agent_visuals.compare_captures(args)
        self.assertFalse((self.root / "comparison").exists())

    def test_pfm_metadata_cannot_inject_scripts(self) -> None:
        self.write_pfm(self.before, [0, 1])
        self.write_pfm(self.after, [0, 1])
        settings = self.root / "settings.json"
        settings.write_text(json.dumps({"scene": "</script><script>bad()</script>"}))
        result = agent_visuals.compare_captures(self.args("--before-settings", str(settings)))
        html = Path(result["viewer"]).read_text()
        self.assertNotIn("</script><script>bad()", html)
        self.assertIn(r"\u003c/script>", html)

    @unittest.skipUnless(shutil.which("node"), "Node.js unavailable")
    def test_viewer_exposure_difference_and_overlay_use_linear_values(self) -> None:
        self.write_pfm(self.before, [0.25, 2])
        self.write_pfm(self.after, [0.5, 4])
        result = agent_visuals.compare_captures(self.args())
        html = Path(result["viewer"]).read_text()
        payload = html.split('id="data">', 1)[1].split('</script>', 1)[0]
        script = html.split('<script>\n', 1)[1].split('</script>', 1)[0]
        harness = r'''
const assert = require('node:assert/strict');
const elements = Object.create(null), frames = Object.create(null);
global.document = {getElementById(id) {
  if (!elements[id]) elements[id] = {value:'100',style:{},options:[],addEventListener(){},
    getContext(){return {createImageData(w,h){return {data:new Uint8ClampedArray(w*h*4)}},
      putImageData(image){frames[id]=image.data}}}};
  return elements[id];
}};
document.getElementById('data').textContent = process.argv[1];
document.getElementById('mode').value = 'after';
'''
        assertions = r'''
assert.equal(frames.before[0], 137);
assert.equal(frames.after[0], 188);
get('mode').value='difference'; paint();
assert.equal(frames.after[0],137);
get('exposure').value='-1'; paint();
assert.equal(frames.after[0],99);
get('mode').value='overlay'; get('exposure').value='0'; paint();
assert.equal(frames.after[0],165);
assert.equal(display(-1,0),0); assert.ok(Math.abs(display(2,0)-255)<1e-10);
'''
        completed = subprocess.run([shutil.which("node"), "-e", harness + script + assertions, payload],
                                   capture_output=True, text=True, timeout=10, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
