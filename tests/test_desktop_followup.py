"""Keep a follow-up capture associated with the input that requested it."""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from desktop import client, native_session, observations, png_regions, session_runtime
from lib import desktop_cli


class DesktopFollowupTests(unittest.TestCase):
    def test_refused_recheck_reports_its_duration_without_a_capture(self):
        context = {"generation": "session", "control_revision": "revision"}
        backend = SimpleNamespace(status=lambda: {**context, "control_revision": "changed"}, request=None)
        with patch.object(backend, "request") as request:
            report = client.observe_after_input({"input_delivery": "completed"}, context,
                                                "/unused/after.png", settle_ms=0, backend=backend)
        request.assert_not_called()
        self.assertEqual(report["input_delivery"], "completed")
        self.assertGreaterEqual(report["timing"]["control_recheck_ms"], 0)
        self.assertNotIn("capture_round_trip_ms", report["timing"])

    def test_input_round_trip_and_submission_have_independent_clocks(self):
        response = {"input_dispatch_monotonic_ns": 20_000_000, "input_completed_monotonic_ns": 25_000_000}
        with (
            patch.object(client, "_send_action", return_value=response),
            patch.object(client.time, "monotonic_ns", side_effect=[10_000_000, 30_000_000]),
        ):
            result = client.send_action({"action": "input"})
        self.assertEqual(result["timing"], {"worker_mode": "resident", "input_round_trip_ms": 20,
                                            "input_submission_ms": 5})

    def test_explicit_input_supersession_on_both_linux_backends(self):
        context = {"generation": "session", "control_revision": "revision"}
        completed = {"input_id": "requested-input", "input_delivery": "completed",
                     "timing": {"input_round_trip_ms": 12}}
        for backend in (session_runtime, SimpleNamespace(status=None, request=None)):
            for latest in ("requested-input", "other-input", None):
                with self.subTest(native=backend is not session_runtime, latest=latest):
                    capture = {**context, "initial_frame_skipped": True, "observation_id": "new-capture"}
                    if latest is not None:
                        capture["last_input"] = {"input_id": latest}
                    with (
                        patch.object(backend, "status", return_value=context),
                        patch.object(backend, "request", return_value=capture) as request,
                    ):
                        report = client.observe_after_input(completed, context, "/unused/after.png",
                                                            settle_ms=0, backend=backend)
                    request.assert_called_once()
                    self.assertEqual(request.call_args.args[0]["action"], "screenshot")
                    self.assertEqual(report["input_id"], "requested-input")
                    self.assertEqual(report["input_delivery"], "completed")
                    self.assertEqual(report["timing"]["input_round_trip_ms"], 12)
                    self.assertGreaterEqual(report["timing"]["settle_wait_ms"], 0)
                    self.assertGreaterEqual(report["timing"]["capture_round_trip_ms"], 0)
                    if latest == "other-input":
                        self.assertNotIn("observation", report)
                        self.assertEqual(report["dispatch_stage"], "post-input-observation")
                        self.assertEqual(report["observation_failure"]["reason"], "input-superseded")
                    else:
                        self.assertEqual(report["observation"]["observation_id"], "new-capture")

    def test_capture_response_failure_preserves_completed_input(self):
        context = {"generation": "session", "control_revision": "revision"}
        completed = {"input_id": "requested-input", "input_delivery": "completed"}
        capture = {**context, "initial_frame_skipped": True, "observation_id": "next-capture"}
        backend = SimpleNamespace(status=None, request=None)
        for flags, message in (({"error": "Capture write failed", "reason": "capture-failed"}, "Capture write failed"),
                               ({"ok": False, "error": None}, "Follow-up capture failed"),
                               ({"ok": True, "error": None}, None)):
            with (
                self.subTest(flags=flags),
                patch.object(backend, "status", return_value=context),
                patch.object(backend, "request", return_value={**capture, **flags}),
            ):
                report = client.observe_after_input(completed, context, "/unused/after.png",
                                                    settle_ms=0, backend=backend)
                self.assertEqual(report["input_id"], "requested-input")
                self.assertEqual(report["input_delivery"], "completed")
                if message is None:
                    self.assertEqual(report["observation"]["observation_id"], "next-capture")
                else:
                    self.assertNotIn("observation", report)
                    self.assertEqual(report["dispatch_stage"], "post-input-observation")
                    self.assertEqual(report["observation_failure"]["error"], message)
                    self.assertEqual(report["observation_failure"].get("reason"), flags.get("reason"))


class DesktopHandoffCLITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parser = argparse.ArgumentParser()
        desktop_cli.add_desktop_subparser(cls.parser.add_subparsers())

    def setUp(self):
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(Path, "home", return_value=self.home))
        self.current = {"state": "running", "generation": "g", "control_revision": "r",
                        "agent_control": "enabled"}

    def run_cli(self, *argv):
        with redirect_stdout(out := StringIO()):
            status = desktop_cli.run_desktop_command(self.parser.parse_args(["desktop", *argv]))
        return status, json.loads(out.getvalue())

    def test_retained_capture_maps_with_null_error_and_refuses_failed_receipts(self):
        image = self.home / "frame.png"
        capture = {"generation": "g", "control_revision": "r", "observation_id": "a" * 32,
                   "geometry": [20, 30], "image_origin": [5, 6], "image_geometry": [10, 10],
                   "output": str(image), "ok": True, "error": None}

        def save_capture(_payload):
            image.write_bytes(png_regions.encode(10, 10, bytes(300)))
            return capture

        current = {**self.current, **capture, "capture_freshness": {"fresh": True}}
        with patch.object(native_session, "status", return_value=current) as status, \
                patch.object(native_session, "request", side_effect=save_capture) as request:
            code, result = self.run_cli("--native", "screenshot", "--output", str(image), "--compact")
            self.assertEqual(code, 0)
            path = next(item["path"] for item in result["artifacts"] if item["kind"] == "capture_receipt")
            argv = ("--native", "map-point", "--capture", path, "--x", "1", "--y", "2", "--image-coordinates")
            code, result = self.run_cli(*argv)
            self.assertEqual(code, 0)
            self.assertEqual(result["input_coordinates"], {"x": 6, "y": 8})
            status.return_value = {**current, "capture_freshness": None}
            code, result = self.run_cli(*argv)
            self.assertEqual((code, result["issue"]), (1, "stale_observation"))
            status.return_value = current
            failed = {**capture, "ok": False}
            for receipt in (failed, {"input_delivery": "completed", "observation": failed}):
                Path(path).write_text(json.dumps(receipt), encoding="utf-8")
                code, result = self.run_cli(*argv)
                self.assertEqual((code, result["issue"]), (2, "invalid_input"))
                self.assertNotIn("input_argv", result)
            request.assert_called_once()

    def test_followup_and_sequence_stop_on_unconfirmed_delivery(self):
        plan = self.home / "steps.json"
        plan.write_text(json.dumps([{"action": "input", "kind": "key", "key": "a", "geometry": [20, 30]},
                                    {"action": "screenshot", "output": str(self.home / "after.png")}]), encoding="utf-8")
        commands = [
            ("input", "key", "--key", "a", "--generation", "g", "--geometry", "20", "30",
             "--observe", "--output", str(self.home / "after.png"), "--settle-ms", "0"),
            ("sequence", str(plan), "--generation", "g"),
        ]
        for argv in commands:
            for delivered, effect in (("unknown", "unknown"), ("partial", "partial"), ("not-sent", "none")):
                with self.subTest(command=argv[0], delivered=delivered), \
                        patch.object(native_session, "status", return_value=self.current), \
                        patch.object(native_session, "request", return_value={
                            "input_id": "one", "input_delivery": delivered, "error": None}) as request:
                    code, result = self.run_cli("--native", *argv)
                    self.assertEqual(code, 1)
                    self.assertEqual((result["stage"], result["effect"]), ("input_confirmation", effect))
                    receipt = result.get("failed_action", result)
                    self.assertEqual((receipt["input_id"], receipt["input_delivery"]), ("one", delivered))
                    self.assertNotIn("observation", result)
                    request.assert_called_once()
                    self.assertEqual(request.call_args.args[0]["action"], "input")

    def test_save_output_failure_retains_the_full_result_with_nonzero_exit(self):
        plan = self.home / "steps.json"
        plan.write_text('[{"action":"input","kind":"key","key":"a","geometry":[20,30]}]', encoding="utf-8")
        with patch.object(native_session, "status", return_value=self.current), \
                patch.object(native_session, "request", return_value={"input_delivery": "completed", "input_id": "one"}), \
                patch.object(observations, "save_observation", side_effect=OSError("disk full")):
            code, result = self.run_cli("--native", "sequence", str(plan), "--generation", "g", "--save-output")
        self.assertEqual((code, result["issue"]), (1, "artifact_save_failed"))
        self.assertEqual((result["stage"], result["effect"]), ("save_output", "applied"))
        self.assertEqual(result["output_save_error"], "disk full")
        self.assertEqual(result["completed"], 1)
        self.assertEqual(result["results"][0]["input_id"], "one")

        with patch.object(native_session, "status", return_value=self.current), \
                patch.object(native_session, "request", return_value={"ok": True, "error": None, "windows": []}), \
                patch.object(observations, "save_observation", side_effect=OSError("disk full")):
            code, result = self.run_cli("--native", "windows", "--save-output")
        self.assertEqual((code, result["issue"]), (1, "artifact_save_failed"))
        self.assertEqual(result["windows"], [])

    def test_invalid_later_actions_are_rejected_before_the_first_input(self):
        plan = self.home / "steps.json"
        first = {"action": "input", "kind": "key", "key": "a", "geometry": [20, 30]}
        for backend, route, additional in (
            (session_runtime, (), {"action": "window", "operation": "resize", "window": "0x2a",
                                  "identity": "observed", "width": 3, "height": 0}),
            (native_session, ("--native",), {"action": "screenshot", "window": "0x2a"}),
        ):
            malformed = [
                {**first, "geometry": [20, False]},
                {"action": "input", "kind": "click", "geometry": [20, 30], "x": True, "y": 2},
                {"action": "screenshot", "active_window": "false"},
                additional,
            ]
            for later in malformed:
                with self.subTest(backend=backend.__name__, later=later), patch.object(backend, "status") as status, \
                        patch.object(backend, "request") as request:
                    plan.write_text(json.dumps([first, later]), encoding="utf-8")
                    code, result = self.run_cli(*route, "sequence", str(plan), "--generation", "g")
                    self.assertEqual((code, result["issue"]), (2, "invalid_input"))
                    self.assertEqual((result["stage"], result["effect"]), ("validation", "none"))
                    status.assert_not_called()
                    request.assert_not_called()
