"""Keep a follow-up capture associated with the input that requested it."""

from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from desktop import client, session_runtime


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
