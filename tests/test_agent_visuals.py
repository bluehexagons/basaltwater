"""Tests for isolated revision captures and private standalone comparisons."""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zlib

from lib import agent_cli, agent_visuals, agent_workspace
from lib.remote_utils import CommandTimeoutError


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


if __name__ == "__main__":
    unittest.main()
