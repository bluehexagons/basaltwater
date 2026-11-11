"""Agent recipes exercise real parsers and workflows with isolated system calls."""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
import json
from pathlib import Path
import re
import shlex
import tempfile
import unittest
from unittest.mock import Mock, patch

from desktop import client, control_state, evidence, native_session, observations
from desktop.receipts import DesktopRequestError
from lib import desktop_cli

ROOT = Path(__file__).resolve().parents[1]


class ObservationRecipeTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(Path, "home", return_value=self.home))
        self.backend = Mock(MAX_MESSAGE=65536)
        self.current = {"state": "running", "generation": "g", "control_revision": "r",
                        "agent_control": "enabled", "input_capabilities": ["observation-input"]}
        self.backend.status.return_value = self.current
        self.input = {"action": "input", "kind": "click", "geometry": [800, 600], "x": 20, "y": 30,
                      "observation_id": "a" * 32}
        self.steps = [self.input, {"action": "screenshot", "after_ms": 150},
                      {"action": "screenshot", "after_ms": 400}]
        self.receipt = {"input_delivery": "completed", "input_id": "input-1",
                        "input_completed_monotonic_ns": 123, "generation": "g"}
        self.now = 10.0
        self.enterContext(patch.object(client.time, "monotonic", side_effect=lambda: self.now))
        self.enterContext(patch.object(client.time, "monotonic_ns", side_effect=lambda: int(self.now * 1e9)))
        self.sleep = self.enterContext(patch.object(client.time, "sleep", side_effect=self.advance))
        self.backend.request.side_effect = self.request

    def advance(self, seconds):
        self.now += seconds

    def request(self, payload):
        self.advance(.02)
        if payload["action"] == "input":
            return dict(self.receipt)
        return {"output": payload["output"], "generation": "g", "control_revision": "r",
                "observation_id": "b" * 32, "geometry": [800, 600], "initial_frame_skipped": True,
                "capture_freshness": {"fresh": True}, "unneeded_status": "omit"}

    def run_recipe(self):
        path = self.home / "steps.json"
        path.write_text(json.dumps(self.steps))
        return client.run_sequence(str(path), "g", backend=self.backend)

    def test_one_input_two_delayed_captures_keep_receipts_and_actual_timing(self):
        self.steps[1]["next_frame"] = self.steps[2]["next_frame"] = True
        result = self.run_recipe()
        self.assertEqual(result["completed"], 3)
        self.assertEqual(result["results"][0], self.receipt)
        self.assertNotIn("unneeded_status", result["results"][1])
        self.assertEqual([row["requested_delay_ms"] for row in result["timings"]], [0, 150, 400])
        for timing, expected in zip(result["timings"], (0, 150, 400)):
            self.assertAlmostEqual(timing["actual_delay_ms"], expected)
            self.assertGreaterEqual(timing["finished_monotonic_ns"], timing["request_started_monotonic_ns"])
        self.assertEqual([call.args[0]["action"] for call in self.backend.request.call_args_list],
                         ["input", "screenshot", "screenshot"])
        paths = [row["output"] for row in result["results"][1:]]
        self.assertEqual(len(set(paths)), 2)
        self.assertTrue(all(Path(path).parent == self.home / "Pictures" / "basaltwater" for path in paths))
        self.assertTrue(all("after_ms" not in call.args[0] for call in self.backend.request.call_args_list))

    def test_partial_capture_failures_never_replay_completed_input(self):
        for failed_index in (1, 2):
            with self.subTest(failed_index=failed_index):
                self.backend.request.reset_mock()
                def request(payload):
                    if self.backend.request.call_count - 1 == failed_index:
                        raise RuntimeError("capture unavailable")
                    return self.request(payload)
                self.backend.request.side_effect = request
                result = self.run_recipe()
                self.assertEqual(result["completed"], failed_index)
                self.assertEqual(result["results"][0], self.receipt)
                self.assertEqual(result["failed_action_index"], failed_index)
                self.assertEqual(len(result["timings"]), failed_index + 1)
                self.assertEqual(self.backend.request.call_count, failed_index + 1)
                self.assertIn("capture unavailable", result["failed_action"]["error"])

    def test_control_change_during_delay_stops_before_capture(self):
        def intervene(seconds):
            self.advance(seconds)
            self.backend.status.return_value = {**self.current, "control_revision": "changed", "agent_control": "human-override"}
        self.sleep.side_effect = intervene
        result = self.run_recipe()
        self.assertEqual(result["completed"], 1)
        self.assertEqual(result["results"][0], self.receipt)
        self.backend.request.assert_called_once()
        self.assertIn("control changed", result["error"])

    def test_expired_input_refusal_retains_stage_without_captures(self):
        refusal = {"error": "Observation expired", "input_delivery": "not-sent", "dispatch_stage": "before-input"}
        self.backend.request.side_effect = DesktopRequestError(refusal)
        result = self.run_recipe()
        self.assertEqual(result["completed"], 0)
        self.assertEqual(result["failed_action"], refusal)
        self.backend.request.assert_called_once()

    def test_invalid_capture_plan_is_refused_before_input(self):
        used = self.home / "used.png"
        used.write_bytes(b"existing")
        duplicate = str(self.home / "duplicate.png")
        variants = ([{"action": "screenshot", "after_ms": value}] for value in (-1, True, 5001, 1.5))
        plans = [*variants, [{"action": "screenshot", "next_frame": "true"}],
                 [{"action": "screenshot", "delay_ms": 150}],
                 *[[{"action": "screenshot", "output": value}] for value in (True, 12, ["frame.png"])],
                 [{"action": "screenshot", "after_ms": 3000}, {"action": "screenshot", "after_ms": 3000}],
                 [{"action": "screenshot", "output": str(used)}],
                 [{"action": "screenshot", "output": str(self.home / "absent" / "a.png")}],
                 [{"action": "screenshot", "output": duplicate}, {"action": "screenshot", "output": duplicate}]]
        for plan in plans:
            with self.subTest(plan=plan), self.assertRaises(ValueError):
                self.steps = [self.input, *plan]
                self.run_recipe()
        self.steps = [{**self.input, "after_ms": 1}]
        with self.assertRaisesRegex(ValueError, "only.*screenshots"):
            self.run_recipe()
        self.backend.status.assert_not_called()
        self.backend.request.assert_not_called()

    def test_same_recipe_runs_on_debian_without_native_frame_flags(self):
        with patch.object(client.runtime, "status", return_value=self.current), \
                patch.object(client.runtime, "request", side_effect=self.request) as request:
            path = self.home / "steps.json"
            path.write_text(json.dumps(self.steps))
            self.assertEqual(client.run_sequence(str(path), "g")["completed"], 3)
            self.assertEqual(request.call_count, 3)
            self.steps[1]["next_frame"] = True
            path.write_text(json.dumps(self.steps))
            with self.assertRaisesRegex(ValueError, "omit next_frame on Debian"):
                client.run_sequence(str(path), "g")
            self.assertEqual(request.call_count, 3)

    def test_old_helper_and_deadline_stop_without_further_input(self):
        self.steps[1]["next_frame"] = True
        self.backend.request.side_effect = [self.receipt, {"output": "/unused.png"}]
        result = self.run_recipe()
        self.assertEqual(result["completed"], 1)
        self.assertIn("did not confirm", result["error"])
        self.backend.request.reset_mock()
        self.steps[1].pop("next_frame")
        def overrun(payload):
            self.advance(21)
            return dict(self.receipt)
        self.backend.request.side_effect = overrun
        result = self.run_recipe()
        self.assertEqual(result["completed"], 1)
        self.backend.request.assert_called_once()


class RecorderHeadroomTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(Path, "home", return_value=self.home))
        self.enterContext(patch.object(evidence, "revision", return_value={}))

    def test_headroom_boundaries_and_action_reply_do_not_hide_truncation(self):
        recorder = evidence.Recorder(str(self.home), "g")
        for count, state, rotate in ((449, "available", False), (450, "low", True), (499, "low", True), (500, "full", True)):
            recorder.data["events"] = [{"action": "input"}] * count
            capacity = recorder.capacity()
            self.assertEqual((capacity["remaining_events"], capacity["headroom"], capacity["rotation_recommended"]),
                             (500 - count, state, rotate))
        session = native_session.NativeSession(Mock())
        session.evidence = recorder
        result = {"input_delivery": "completed", "input_id": "one"}
        session.record_evidence({"action": "input"}, result)
        self.assertEqual(result["input_delivery"], "completed")
        self.assertEqual(result["evidence_capacity"]["headroom"], "truncated")
        self.assertEqual(result["evidence_capacity"]["artifact"], str(recorder.path))
        self.assertIn("same recording", result["evidence_capacity"]["warning"])
        self.assertEqual(observations.compact_capture(result)["evidence_capacity"], result["evidence_capacity"])

    def test_new_segments_and_same_recording_closeouts_have_different_links(self):
        first, second = [evidence.Recorder(str(self.home), "g") for _ in range(2)]
        first.finish({})
        second.finish({})
        with patch("desktop.qa_artifacts.new_bundle", side_effect=lambda output: self.new_folder(output)):
            a = evidence.closeout(str(first.path), extras=[], derivatives=[], output="a")
            b = evidence.closeout(str(first.path), extras=[], derivatives=[], output="b", previous=a["artifact"])
            self.assertEqual(b["previous_receipt"]["artifact"], a["artifact"])
            with self.assertRaisesRegex(ValueError, "SAME recording.*checkpoint --artifact"):
                evidence.closeout(str(second.path), extras=[], derivatives=[], previous=a["artifact"])

    def new_folder(self, name):
        folder = self.home / name
        folder.mkdir()
        return folder


class PublishedRecipeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parser = argparse.ArgumentParser()
        desktop_cli.add_desktop_subparser(cls.parser.add_subparsers())

    def test_published_linux_desktop_recipes_parse_without_execution(self):
        files = [ROOT / "docs/workstations" / name for name in
                 ("desktop-observations.md", "desktop-evaluation.md", "desktop-control.md", "cachyos-desktop.md",
                  "short-observation-sequences.md")]
        files += [ROOT / "common/agent_skills" / name for name in
                  ("basaltwater-cachyos-desktop/references/native-control.md", "basaltwater-desktop/references/windows.md")]
        count = 0
        replacements = {"WIDTH": "2400", "HEIGHT": "1600", "X": "100", "Y": "100"}
        for name in files:
            source = name.read_text()
            for language, block in re.findall(r"```([^\n]*)\n(.*?)```", source, re.S):
                if language not in ("bash", "fish", "text"):
                    continue
                for line in block.replace("\\\n", " ").splitlines():
                    words = shlex.split(line, comments=True)
                    if words[:2] != ["basaltw", "desktop"]:
                        continue
                    # Shell redirects are not command arguments. Paths and IDs
                    # stay fictional, while documented numeric placeholders bind.
                    if ">" in words:
                        words = words[:words.index(">")]
                    words = [replacements.get(word, word) for word in words[1:]]
                    with self.subTest(file=name, command=line):
                        self.parser.parse_args(words)
                    count += 1
        self.assertGreater(count, 0)

    def test_published_pair_json_executes_and_saves_partial_evidence(self):
        source = (ROOT / "docs/workstations/short-observation-sequences.md").read_text()
        steps = json.loads(re.search(r"```json\n(.*?)```", source, re.S).group(1))
        current = {"state": "running", "generation": "g", "control_revision": "r", "agent_control": "enabled",
                   "input_capabilities": ["observation-input"]}
        completed = {"input_id": "one", "input_delivery": "completed"}
        with tempfile.TemporaryDirectory() as folder, patch.object(Path, "home", return_value=Path(folder)), \
                patch.object(native_session, "status", return_value=current), patch.object(client.time, "sleep"), \
                patch.object(native_session, "request", side_effect=[completed, {"initial_frame_skipped": True},
                                                                    RuntimeError("second capture failed")]) as request:
            path = Path(folder) / "steps.json"
            path.write_text(json.dumps(steps))
            with redirect_stdout(out := StringIO()):
                code = desktop_cli.run_desktop_command(self.parser.parse_args(
                    ["desktop", "--native", "sequence", str(path), "--generation", "g", "--save-output"]))
            self.assertEqual(code, 1)
            short = json.loads(out.getvalue())
            saved = json.loads(Path(short["artifact"]).read_text())
            self.assertEqual(saved["results"][0], completed)
            self.assertEqual(short["completed"], 2)
            self.assertEqual(short["failed_action_index"], 2)
            self.assertEqual(len(saved["timings"]), 3)
            self.assertEqual(request.call_count, 3)

    def test_failed_result_save_keeps_completed_input_and_capture_receipts(self):
        full = {"generation": "g", "completed": 2, "results": [
            {"input_delivery": "completed", "input_id": "one"}, {"output": "/private/frame.png"}],
            "error": "second capture failed", "failed_action_index": 2, "failed_action": {"error": "capture failed"}}
        with patch.object(native_session, "status", return_value={"state": "running", "generation": "g", "control_revision": "r"}), \
                patch.object(client, "run_sequence", return_value=full), \
                patch.object(observations, "save_observation", side_effect=OSError("disk full")), redirect_stdout(out := StringIO()):
            args = self.parser.parse_args(["desktop", "--native", "sequence", "/private/steps.json", "--generation", "g", "--save-output"])
            self.assertEqual(desktop_cli.run_desktop_command(args), 1)
        result = json.loads(out.getvalue())
        self.assertEqual(result["results"], full["results"])
        self.assertEqual(result["error"], "second capture failed")
        self.assertEqual(result["output_save_error"], "disk full")

    def test_handoff_native_route_is_separate_from_permission_and_xrdp(self):
        for mode in ("enabled", "human-override", "disabled"):
            with tempfile.TemporaryDirectory() as folder, patch.object(Path, "home", return_value=Path(folder)), \
                    patch.object(native_session, "check_session"), patch.object(native_session, "start") as start, \
                    patch.object(desktop_cli.runtime, "status", side_effect=AssertionError("XRDP must not be used")), \
                    patch.object(desktop_cli.subprocess, "Popen", return_value=Mock(pid=123)) as spawn:
                before = control_state.change(mode, human_approved=True)
                with redirect_stdout(out := StringIO()):
                    code = desktop_cli.run_desktop_command(self.parser.parse_args(["desktop", "--native", "handoff"]))
                result = json.loads(out.getvalue())
                self.assertEqual(code, 0)
                self.assertFalse(result["permission_changed"])
                self.assertFalse(result["ownership_transferred"])
                self.assertEqual(result["window_visibility"], "unverified")
                self.assertEqual(control_state.status(), before)
                self.assertTrue(spawn.call_args.args[0][1].endswith("native_handoff.py"))
                start.assert_not_called()

    def test_actionable_route_and_mapping_errors_do_not_send_requests(self):
        with patch.object(desktop_cli.runtime, "status", side_effect=RuntimeError("Shared desktop is not configured")), \
                patch.object(native_session, "request") as request, redirect_stdout(out := StringIO()):
            self.assertEqual(desktop_cli.run_desktop_command(self.parser.parse_args(["desktop", "handoff"])), 1)
            self.assertIn("desktop --native handoff", json.loads(out.getvalue())["hint"])
            request.assert_not_called()
        for extra in (["--geometry", "2400", "1600"], ["--crop", "0", "0", "10", "10"]):
            with patch.object(native_session, "status") as status, redirect_stdout(out := StringIO()):
                args = self.parser.parse_args(["desktop", "--native", "map-point", "--capture", "/absent/frame.json",
                    "--view-geometry", "1200", "800", "--x", "1", "--y", "1", *extra])
                self.assertEqual(desktop_cli.run_desktop_command(args), 1)
                self.assertIn("omit --geometry and --crop", json.loads(out.getvalue())["error"])
                status.assert_not_called()
        with redirect_stderr(message := StringIO()), self.assertRaises(SystemExit) as error:
            self.parser.parse_args(["desktop", "handoff", "--native"])
        self.assertEqual(error.exception.code, 2)
        self.assertIn("Place --native before handoff", message.getvalue())
        with redirect_stderr(message := StringIO()), self.assertRaises(SystemExit) as error:
            self.parser.parse_args(["desktop", "sequence", "/private/steps.json", "--generation", "g", "--json"])
        self.assertEqual(error.exception.code, 2)
        self.assertIn("omit --json, or use --save-output", message.getvalue())

    def test_png_and_directory_are_not_mistaken_for_json_mapping_receipts(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(native_session, "status") as status:
            image = Path(folder) / "frame.png"
            image.write_bytes(b"\x89PNG\r\n\x1a\n")
            for path in (str(image), folder):
                with self.subTest(path=path), redirect_stdout(out := StringIO()):
                    args = self.parser.parse_args(["desktop", "--native", "map-point", "--capture", path,
                        "--view-geometry", "1200", "800", "--x", "1", "--y", "1"])
                    self.assertEqual(desktop_cli.run_desktop_command(args), 1)
                    self.assertIn("saved JSON receipt", json.loads(out.getvalue())["error"])
            status.assert_not_called()


if __name__ == "__main__":
    unittest.main()
