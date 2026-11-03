"""Private desktop observation output and CLI compatibility checks."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import tempfile
import os
import struct
import subprocess
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from desktop.observations import compact_capture, save_observation
from desktop import client, control_state, native_session, portal_capture
from desktop import session_runtime as runtime
from desktop.display_power import display_power
from desktop.evidence import Recorder, capture_digest, finalize, verify
from lib.desktop_cli import add_desktop_subparser, run_desktop_command


class DesktopOutputTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name) / "observation"

        def create_directory(**kwargs):
            self.directory.mkdir(mode=0o700)
            return str(self.directory)

        self.addCleanup(patch.stopall)
        patch("desktop.observations.tempfile.mkdtemp", side_effect=create_directory).start()

    def observation(self) -> dict:
        return {"generation": "generation-1", "pid": 123, "root": "root-ref",
                "truncated": True, "elements": [
                    {"ref": f"ref-{index}", "name": "n" * 256, "role": "button",
                     "text": "private document content" * 10, "states": ["showing"]}
                    for index in range(128)]}

    def test_full_payload_is_private_and_preview_is_bounded(self) -> None:
        result = self.observation()
        summary = save_observation(result)
        artifact = Path(summary["artifact"])
        self.assertEqual(json.loads(artifact.read_text()), result)
        self.assertEqual(summary["bytes"], artifact.stat().st_size)
        self.assertEqual(artifact.stat().st_mode & 0o777, 0o600)
        self.assertEqual(artifact.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(summary["counts"], {"elements": 128})
        self.assertEqual(len(summary["preview"]["elements"]), 3)
        self.assertTrue(summary["truncated"])
        self.assertEqual(summary["root"], "root-ref")
        self.assertEqual(summary["generation"], "generation-1")
        self.assertLess(len(json.dumps(summary)), 2500)
        self.assertNotIn("private document", json.dumps(summary))
        self.assertEqual(len(result["elements"][0]["name"]), 256)

    def test_incomplete_artifact_is_removed_on_serialization_failure(self) -> None:
        with self.assertRaises(TypeError):
            save_observation({"elements": [object()]})
        self.assertFalse(self.directory.exists())

    def arguments(self, *argv):
        parser = argparse.ArgumentParser()
        add_desktop_subparser(parser.add_subparsers())
        return parser.parse_args(["desktop", *argv])

    def run_cli(self, result, *argv, mode="enabled"):
        with (patch("lib.desktop_cli.runtime.status", return_value={"state": "running", "generation": "generation-1", "control_revision": "r",
                                                                   "agent_control": mode, "paused": mode != "enabled"}),
              patch("lib.desktop_cli.runtime.request", return_value=result) as request,
              patch("sys.stdout", new_callable=io.StringIO) as output):
            code = run_desktop_command(self.arguments(*argv))
        return code, json.loads(output.getvalue()), request

    def test_default_inspection_keeps_legacy_payload(self) -> None:
        result = self.observation()
        for mode in ("enabled", "human-override", "disabled"):
            with self.subTest(mode=mode):
                code, output, request = self.run_cli(result, "inspect", "--pid", "123", mode=mode)
                self.assertEqual(code, 0)
                self.assertEqual(output, result)
                request.assert_called_once()
                self.assertEqual(request.call_args.args[0]["action"], "inspect")
                self.assertFalse(self.directory.exists())

    def test_save_output_preserves_query_and_failure_status(self) -> None:
        result = {**self.observation(), "error": "Timed out waiting for one complete match"}
        code, output, request = self.run_cli(result, "inspect", "--pid", "123", "--root", "root-ref",
                                             "--generation", "generation-1", "--save-output")
        self.assertEqual(code, 1)
        self.assertEqual(output["error"], result["error"])
        self.assertEqual(json.loads(Path(output["artifact"]).read_text()), result)
        self.assertEqual(request.call_args.args[0]["root"], "root-ref")
        self.assertEqual(request.call_args.args[0]["generation"], "generation-1")

    def test_windows_and_both_wait_commands_accept_saved_output(self) -> None:
        result = {"generation": "generation-1", "windows": [{"id": "42", "title": "App"}],
                  "coverage": "partial", "truncated": True}
        code, output, _ = self.run_cli(result, "windows", "--save-output")
        self.assertEqual(code, 0)
        self.assertEqual(output["counts"], {"windows": 1})
        self.assertEqual(output["coverage"], "partial")
        self.assertTrue(output["truncated"])
        self.assertTrue(self.arguments("wait", "--title", "App", "--save-output").save_output)
        self.assertTrue(self.arguments("wait-element", "--pid", "123", "--role", "button",
                                       "--generation", "generation-1", "--save-output").save_output)

    def test_compact_screenshot_keeps_timing_and_error_status(self) -> None:
        result = {"generation": "g", "geometry": [10, 20], "output": "/tmp/new.png",
                  "captured_at": "now", "changed": False, "frame_received_at": "earlier",
                  "capture_freshness": {"fresh": True, "age_seconds": 0, "remaining_seconds": 60, "max_age_seconds": 60},
                  "state": "running", "portal_stage": "ready", "error": "capture failed"}
        code, output, _ = self.run_cli(result, "screenshot", "--output", "/tmp/new.png", "--compact")
        self.assertEqual(code, 1)
        self.assertEqual(output["changed"], False)
        self.assertEqual(output["frame_received_at"], "earlier")
        self.assertEqual(output["capture_freshness"], result["capture_freshness"])
        self.assertNotIn("portal_stage", output)

    def test_selected_backend_screenshot_help(self) -> None:
        for native in (False, True):
            with patch("sys.stdout", new_callable=io.StringIO) as output, self.assertRaises(SystemExit):
                self.arguments(*(["--native"] if native else []), "screenshot", "--help")
            body = output.getvalue()
            self.assertEqual("--active-window" in body, not native)
            if native:
                self.assertIn("Window capture is unavailable", " ".join(body.split()))

    def test_selected_capture_age_is_validated_and_requires_backend_confirmation(self):
        confirmed = {"observation_id": "a" * 32, "capture_freshness": {"max_age_seconds": 180}}
        for result, expected in ((confirmed, 0), ({"output": "/tmp/new.png"}, 1)):
            code, output, request = self.run_cli(result, "screenshot", "--output", "/tmp/new.png", "--max-age-seconds", "180")
            self.assertEqual(code, expected)
            self.assertEqual(request.call_args.args[0]["max_age_seconds"], 180)
            if expected:
                self.assertIn("did not confirm", output["error"])
        code, output, request = self.run_cli({}, "screenshot", "--max-age-seconds", "601")
        self.assertEqual(code, 1)
        request.assert_not_called()

    def test_old_helper_cannot_silently_ignore_observation_input(self):
        code, output, request = self.run_cli({}, "input", "--generation", "g", "--geometry", "10", "20",
            "--observation", "a" * 32, "click", "--x", "1", "--y", "2")
        self.assertEqual(code, 1)
        self.assertIn("does not support", output["error"])
        request.assert_not_called()

    def test_post_input_capture_failure_preserves_completion_and_never_replays(self) -> None:
        backend = Mock()
        backend.status.return_value = {"generation": "g", "control_revision": "r"}
        backend.request.side_effect = RuntimeError("no subsequent frame")
        completed = {"input_completed_at": "now", "input_completed_monotonic_ns": 42}
        with patch.object(client.time, "sleep") as settle:
            result = client.observe_after_input(completed, {"generation": "g", "control_revision": "r"},
                                                "/tmp/new.png", settle_ms=120, backend=backend)
        settle.assert_called_once_with(.12)
        self.assertEqual(result["input_completed_monotonic_ns"], 42)
        self.assertIn("Do not replay", result["error"])
        backend.request.assert_called_once_with({"action": "screenshot", "generation": "g",
                                                 "control_revision": "r",
                                                 "output": "/tmp/new.png", "next_frame": True})

    def test_post_input_control_change_stops_observation(self) -> None:
        backend = Mock()
        backend.status.return_value = {"generation": "g", "control_revision": "new"}
        with patch.object(client.time, "sleep"):
            result = client.observe_after_input({}, {"generation": "g", "control_revision": "r"},
                                                "/tmp/new.png", settle_ms=0, backend=backend)
        self.assertIn("control changed", result["error"])
        backend.request.assert_not_called()

    def test_observation_does_not_claim_ordering_on_an_older_backend(self):
        backend = Mock()
        backend.status.return_value = {"generation": "g", "control_revision": "r"}
        backend.request.return_value = {"output": "/tmp/capture.png"}
        with patch.object(client.time, "sleep"):
            result = client.observe_after_input({"input_completed_at": "now"},
                {"generation": "g", "control_revision": "r"}, "/tmp/capture.png", settle_ms=0, backend=backend)
        self.assertIn("did not confirm", result["error"])
        self.assertEqual(result["input_completed_at"], "now")


class NativeObservationTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(Path, "home", return_value=self.home))
        self.session = native_session.NativeSession(Mock(fd=42))
        self.session.state = "running"
        self.session.node = 17
        self.session.geometry = self.session.logical_size = [10, 20]
        self.session.captured = time.monotonic()
        self.context = {"generation": self.session.generation, "geometry": [10, 20],
                        "control_revision": control_state.status()["control_revision"]}

    def test_input_timestamp_follows_release_and_capture_tracks_pixel_changes(self):
        released = []
        self.session.portal.notify.side_effect = lambda *args: released.append(time.monotonic_ns())
        result = self.session.handle({**self.context, "action": "input", "kind": "click", "x": 1, "y": 2})
        self.assertGreaterEqual(result["input_completed_monotonic_ns"], released[-1])
        with patch("desktop.evidence.revision", return_value={"commit": None, "dirty": None}):
            self.session.evidence = Recorder(str(self.home), self.session.generation)
        digests = iter(["a" * 64, "a" * 64, "b" * 64])
        def capture(argv, **kwargs):
            os.write(kwargs["pass_fds"][-1], b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + struct.pack(">II", 10, 20))
            return subprocess.CompletedProcess(argv, 0, json.dumps({"geometry": [10, 20],
                "pixel_sha256": next(digests), "frame_received_at": "frame-time",
                "frame_received_monotonic_ns": time.monotonic_ns(), "frame_pts_ns": 9,
                "initial_frame_skipped": True}))
        with patch.object(native_session.subprocess, "run", side_effect=capture) as run:
            for index, changed in enumerate((None, False, True)):
                observed = self.session.handle({**self.context, "action": "screenshot",
                    "output": str(self.home / f"capture-{index}.png"), "next_frame": True})
                self.assertIs(observed["changed"], changed)
                self.assertTrue(observed["capture_freshness"]["fresh"])
                self.assertGreater(observed["capture_freshness"]["remaining_seconds"], 59)
                self.assertEqual(observed["artifact_sha256"], hashlib.sha256(Path(observed["output"]).read_bytes()).hexdigest())
                self.assertEqual(observed["last_input"]["input_completed_at"], result["input_completed_at"])
                self.assertGreater(observed["frame_received_monotonic_ns"], result["input_completed_monotonic_ns"])
            self.assertEqual(run.call_args.args[0][-1], "1")
        finished = self.session.handle({**self.context, "action": "evidence", "operation": "finish"})
        report = verify(finished["artifact"])
        self.assertTrue(report["healthy"])
        self.assertEqual(report["counts"]["verified"], 3)
        data = json.loads(Path(finished["artifact"]).read_text())
        captures = [row["result"] for row in data["events"] if row["action"] == "screenshot"]
        self.assertTrue(all(row["capture_freshness"]["fresh"] for row in captures))

    def test_status_reports_capture_expiry_without_refreshing_or_delivering_input(self):
        self.session.captured = 100
        with patch.object(native_session.time, "monotonic", return_value=159) as clock, \
                patch.object(native_session.subprocess, "run") as capture:
            for now, remaining, fresh in ((159, 1, True), (160, 0, True), (160.001, 0, False), (175, 0, False)):
                clock.return_value = now
                result = self.session.handle({"action": "status"})["capture_freshness"]
                self.assertEqual(result, {"age_seconds": now - 100, "max_age_seconds": 60,
                                          "remaining_seconds": remaining, "fresh": fresh})
                self.assertEqual(self.session.captured, 100)
            with self.assertRaisesRegex(ValueError, "capture age 75.0s, limit 60s.*Recapture and re-plan"):
                self.session.handle({**self.context, "action": "input", "kind": "click", "x": 1, "y": 2})
            capture.assert_not_called()
        self.session.portal.notify.assert_not_called()

    def test_input_boundary_agrees_with_reported_capture_window(self):
        self.session.captured = 100
        with patch.object(native_session.time, "monotonic", return_value=160):
            self.assertTrue(self.session.handle({"action": "status"})["capture_freshness"]["fresh"])
            self.session.handle({**self.context, "action": "input", "kind": "move", "x": 1, "y": 2})
        self.session.portal.notify.assert_called_once()

    def test_failed_recapture_does_not_extend_the_existing_window(self):
        self.session.captured = 100
        output = self.home / "failed.png"
        with patch.object(native_session.time, "monotonic", return_value=175), \
                patch.object(native_session.subprocess, "run", side_effect=subprocess.TimeoutExpired("capture", 12)), \
                self.assertRaises(subprocess.TimeoutExpired):
            self.session.handle({**self.context, "action": "screenshot", "output": str(output)})
        with patch.object(native_session.time, "monotonic", return_value=176):
            freshness = self.session.handle({"action": "status"})["capture_freshness"]
        self.assertFalse(freshness["fresh"])
        self.assertEqual(freshness["age_seconds"], 76)
        self.assertFalse(output.exists())

    def test_no_capture_or_changed_control_cannot_advertise_or_deliver_fresh_input(self):
        # Even early in system uptime, zero means no valid capture, not a recent one.
        with patch.object(native_session.time, "monotonic", return_value=30):
            self.session.captured = 0
            result = self.session.handle({"action": "status"})["capture_freshness"]
            self.assertEqual(result, {"age_seconds": None, "max_age_seconds": 60,
                                      "remaining_seconds": 0, "fresh": False})
            with self.assertRaisesRegex(ValueError, "no current capture"):
                self.session.handle({**self.context, "action": "input", "kind": "move", "x": 1, "y": 2})
            self.session.captured = 25
            control_state.change("human-override", human_approved=True)
            result = self.session.handle({"action": "status"})
            self.assertEqual(result["agent_control"], "human-override")
            self.assertFalse(result["capture_freshness"]["fresh"])
            self.assertIsNone(result["capture_freshness"]["age_seconds"])
        self.session.portal.notify.assert_not_called()

    def test_inactive_or_expired_helper_does_not_advertise_fresh_capture(self):
        with patch.object(native_session.time, "monotonic", return_value=110):
            self.session.captured = 100
            for state in ("initializing", "failed", "stopped"):
                self.session.state = state
                self.assertFalse(self.session.snapshot_status()["capture_freshness"]["fresh"])
            self.session.state = "running"
            self.session.until = 109
            self.assertFalse(self.session.snapshot_status()["capture_freshness"]["fresh"])

    def test_offline_receipt_discards_historical_freshness(self):
        receipt = self.home / "status.json"
        receipt.write_text(json.dumps({"state": "running", "capture_freshness": {"fresh": True}}))
        receipt.chmod(0o600)
        with patch.object(native_session, "runtime_directory", return_value=self.home), \
                patch.object(native_session, "request", side_effect=FileNotFoundError):
            result = native_session.status()
        self.assertEqual(result["state"], "stopped")
        self.assertFalse(result["capture_freshness"]["fresh"])
        self.assertIsNone(result["capture_freshness"]["age_seconds"])

    def test_capture_helper_skips_initial_frame_and_hashes_visible_pixels(self):
        buffer = Mock(pts=123)
        buffer.get_size.return_value = 8
        buffer.map.return_value = (True, SimpleNamespace(data=b"abcdPAD!"))
        sample = Mock()
        sample.get_buffer.return_value = buffer
        sink = Mock()
        sink.emit.side_effect = [Mock(), sample]
        pipeline = Mock()
        pipeline.get_by_name.return_value = sink
        gst = SimpleNamespace(init=Mock(), parse_launch=Mock(return_value=pipeline), SECOND=1000000000,
                              CLOCK_TIME_NONE=-1, State=SimpleNamespace(PLAYING=1, NULL=0), MapFlags=SimpleNamespace(READ=1))
        video = SimpleNamespace(VideoInfo=SimpleNamespace(new_from_caps=lambda caps:
                                SimpleNamespace(width=1, height=1, stride=[8], offset=[0])))
        with patch.dict("sys.modules", {"gi": Mock(), "gi.repository": SimpleNamespace(Gst=gst, GstVideo=video)}):
            with (self.home / "frame.png").open("w+b") as stream:
                result = portal_capture.capture(42, 17, stream.fileno(), 1)
        self.assertEqual(sink.emit.call_count, 2)
        self.assertEqual(result["geometry"], [1, 1])
        self.assertEqual(result["frame_pts_ns"], 123)
        import hashlib
        self.assertEqual(result["pixel_sha256"], hashlib.sha256(b"abcd").hexdigest())
        pipeline.set_state.assert_called_with(0)
        sink.emit.side_effect = [sample, None]
        with patch.dict("sys.modules", {"gi": Mock(), "gi.repository": SimpleNamespace(Gst=gst, GstVideo=video)}), \
                (self.home / "no-frame.png").open("w+b") as stream, \
                self.assertRaisesRegex(RuntimeError, "No subsequent frame"):
            portal_capture.capture(42, 17, stream.fileno(), 1)
        pipeline.set_state.assert_called_with(0)

    def test_display_power_is_read_only_and_unknown_on_timeout(self):
        with patch.dict(os.environ, {"WAYLAND_DISPLAY": "wayland-0"}), \
                patch("desktop.display_power.run_captured") as run:
            run.return_value = subprocess.CompletedProcess([], 0,
                "dpms mode for screen DP-1: on\ndpms mode for screen HDMI-1: off\n", "")
            result = display_power()
            self.assertEqual(result["state"], "mixed")
            self.assertEqual(run.call_args.args[0], ["/usr/bin/kscreen-doctor", "--dpms", "show"])
            run.side_effect = subprocess.TimeoutExpired("kscreen-doctor", 3)
            self.assertEqual(display_power()["state"], "unknown")

    def test_native_doctor_keeps_power_evidence_when_library_probe_fails(self):
        with patch.object(native_session, "check_session"), \
                patch.object(native_session, "status", return_value={"state": "running"}), \
                patch("desktop.display_power.display_power", return_value={"state": "off"}), \
                patch.object(native_session.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "")):
            result = native_session.doctor()
        self.assertFalse(result["healthy"])
        self.assertEqual(result["checks"]["display_power"]["state"], "off")

    def test_evidence_links_inputs_captures_git_tasks_and_final_process_status(self):
        with patch("desktop.evidence.revision", return_value={"commit": "a" * 40, "dirty": True}):
            started = self.session.handle({**self.context, "action": "evidence", "operation": "start",
                                           "project": str(self.home)})
            artifact = Path(started["artifact"])
            self.session.handle({**self.context, "action": "input", "kind": "text", "text": "secret-input"})
            self.session.record_evidence({"action": "screenshot"}, {"generation": self.session.generation,
                "output": "/private/capture.png", "geometry": [10, 20], "captured_at": "now", "changed": True})
            process = Mock(pid=123)
            process.poll.return_value = 0
            self.session.launches["native-launch"] = process
            finished = self.session.handle({**self.context, "action": "evidence", "operation": "finish"})
            data = json.loads(artifact.read_text())
            task = "b" * 32
            with patch("desktop.development.list_tasks", return_value={"tasks": [
                {"task": task, "created_at": data["started_at"]}], "truncated": False}), \
                    patch("desktop.development.task_status", return_value={"task": task, "project": str(self.home),
                        "state": "completed", "returncode": 0, "log": "/private/output.log"}):
                summary = finalize(finished["artifact"], tasks=[], renderer="OpenGL / fixture GPU")
        data = json.loads(artifact.read_text())
        self.assertTrue(summary["finalized"])
        self.assertEqual(data["revision_start"], {"commit": "a" * 40, "dirty": True})
        self.assertEqual(data["native_launches"][0]["returncode"], 0)
        self.assertEqual(data["tasks"][0]["state"], "completed")
        self.assertEqual(data["renderer"]["source"], "operator-observed")
        self.assertEqual(data["events"][0]["action"], "input")
        self.assertIn("input_completed_at", data["events"][0]["result"])
        self.assertEqual(data["events"][1]["result"]["output"], "/private/capture.png")
        self.assertNotIn("secret-input", artifact.read_text())
        self.assertEqual(artifact.stat().st_mode & 0o777, 0o600)
        self.assertEqual(artifact.parent.stat().st_mode & 0o777, 0o700)

    def test_evidence_failure_does_not_turn_completed_input_into_retry(self):
        recorder = Mock()
        recorder.event.side_effect = OSError("disk full")
        self.session.evidence = recorder
        result = self.session.handle({**self.context, "action": "input", "kind": "click", "x": 1, "y": 2})
        self.assertIn("input_completed_at", result)
        self.assertIn("evidence_error", result)
        self.assertNotIn("error", result)
        self.assertEqual(self.session.portal.notify.call_count, 3)

    def test_evidence_is_bounded_and_close_checkpoints_without_stopping_launches(self):
        with patch("desktop.evidence.revision", return_value={"commit": None, "dirty": None}), \
                patch("desktop.evidence.EVENT_LIMIT", 1):
            self.session.evidence = Recorder(str(self.home), self.session.generation)
            artifact = self.session.evidence.path
            self.session.record_evidence({"action": "input", "kind": "text", "text": "private"}, None)
            self.session.record_evidence({"action": "input", "kind": "text", "text": "more private"}, None)
            process = Mock(pid=123)
            process.poll.return_value = None
            self.session.launches["running"] = process
            self.session.close()
        data = json.loads(artifact.read_text())
        self.assertTrue(data["events_truncated"])
        self.assertEqual(len(data["events"]), 1)
        self.assertIsNone(data["native_launches"][0]["returncode"])
        self.assertFalse(data["finalized"])
        self.assertEqual(data["recording_end_reason"], "helper-stopped")
        self.assertEqual(self.session.snapshot_status()["evidence"], str(artifact))
        process.terminate.assert_not_called()

    def test_unsafe_evidence_directory_is_rejected(self):
        target = self.home / "elsewhere"
        target.mkdir()
        (self.home / ".local/state/basaltwater/desktop-evidence").symlink_to(target)
        with patch("desktop.evidence.revision"), self.assertRaisesRegex(RuntimeError, "Unsafe"):
            Recorder(str(self.home), "generation")

    def recorded_capture(self):
        with patch("desktop.evidence.revision", return_value={"commit": None, "dirty": None}):
            recorder = Recorder(str(self.home), "generation")
        capture = self.home / "capture.png"
        capture.write_bytes(b"fixture original PNG bytes")
        capture.chmod(0o600)
        recorder.event({"action": "screenshot"}, {"output": str(capture),
                       "artifact_sha256": hashlib.sha256(capture.read_bytes()).hexdigest()})
        recorder.finish({})
        return recorder, capture

    def test_evidence_verification_is_read_only_and_detects_changed_and_missing_files(self):
        recorder, capture = self.recorded_capture()
        record_bytes = recorder.path.read_bytes()
        self.assertTrue(verify(str(recorder.path))["healthy"])
        self.assertFalse(verify(str(recorder.path))["finalized"])
        capture.write_bytes(b"different")
        report = verify(str(recorder.path))
        self.assertFalse(report["healthy"])
        self.assertEqual(report["counts"]["mismatched"], 1)
        self.assertEqual(capture.read_bytes(), b"different")
        capture.unlink()
        self.assertEqual(verify(str(recorder.path))["counts"]["missing"], 1)
        self.assertEqual(recorder.path.read_bytes(), record_bytes)

    def test_evidence_verification_rejects_unsafe_files_and_bounds_diagnostics(self):
        recorder, capture = self.recorded_capture()
        original = capture.read_bytes()
        capture.unlink()
        target = self.home / "original.png"
        target.write_bytes(original)
        target.chmod(0o600)
        capture.symlink_to(target)
        self.assertEqual(verify(str(recorder.path))["counts"]["unverified"], 1)
        capture.unlink()
        os.mkfifo(capture, 0o600)
        self.assertEqual(verify(str(recorder.path))["counts"]["unverified"], 1)
        capture.unlink()
        capture.write_bytes(original)
        capture.chmod(0o644)
        self.assertEqual(verify(str(recorder.path))["counts"]["unverified"], 1)
        capture.chmod(0o600)
        with patch("desktop.evidence.CAPTURE_BYTES_LIMIT", 1):
            self.assertEqual(verify(str(recorder.path))["counts"]["unverified"], 1)
        # Legacy pixel hashes describe RGBA pixels, not PNG file bytes.
        recorder.data["events"] = [{"action": "screenshot", "result": {"output": str(capture),
                                            "pixel_sha256": "a" * 64}} for _ in range(12)]
        recorder.save()
        report = verify(str(recorder.path))
        self.assertFalse(report["healthy"])
        self.assertEqual(report["counts"]["unverified"], 12)
        self.assertEqual(len(report["problems"]), 10)
        self.assertTrue(report["problems_truncated"])

    def test_evidence_verification_does_not_certify_empty_or_truncated_records(self):
        recorder, capture = self.recorded_capture()
        recorder.data["events_truncated"] = True
        recorder.save()
        report = verify(str(recorder.path))
        self.assertFalse(report["healthy"])
        self.assertEqual(report["counts"]["verified"], 1)
        recorder.data["events"] = []
        recorder.data["events_truncated"] = False
        recorder.save()
        self.assertFalse(verify(str(recorder.path))["healthy"])

    def test_capture_digest_detects_a_concurrent_file_change(self):
        _, capture = self.recorded_capture()
        with capture.open("rb") as stream, patch("desktop.evidence.os.pread", return_value=b""):
            with self.assertRaisesRegex(ValueError, "changed"):
                capture_digest(stream.fileno())

    def test_evidence_verify_cli_works_without_live_backend_and_preserves_failures(self):
        recorder, capture = self.recorded_capture()
        parser = argparse.ArgumentParser()
        add_desktop_subparser(parser.add_subparsers())
        with patch.object(native_session, "status") as status, patch.object(native_session, "request") as request:
            for expected_code in (0, 1):
                args = parser.parse_args(["desktop", "--native", "evidence", "verify", str(recorder.path), "--json"])
                with patch("sys.stdout", new_callable=io.StringIO) as output:
                    self.assertEqual(run_desktop_command(args), expected_code)
                self.assertEqual(json.loads(output.getvalue())["healthy"], expected_code == 0)
                capture.unlink(missing_ok=True)
            status.assert_not_called()
            request.assert_not_called()


class BoundCaptureTests(unittest.TestCase):
    """Exercise the same observation lifecycle through both Linux helpers."""

    def setUp(self):
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(Path, "home", return_value=self.home))
        self.enterContext(patch.dict(os.environ, {"DISPLAY": ":10"}))
        self.now = 1000.0
        self.enterContext(patch.object(time, "monotonic", side_effect=lambda: self.now))
        self.enterContext(patch.object(runtime, "geometry", return_value=[10, 20]))
        self.enterContext(patch.object(runtime, "window_manager_ready", return_value=True))
        self.tool = self.enterContext(patch.object(runtime, "run_tool", side_effect=self.x_tool))
        self.capture = self.enterContext(patch.object(native_session.subprocess, "run", side_effect=self.portal_capture))
        process = Mock(pid=42)
        process.poll.return_value = None
        self.debian = runtime.DesktopSession({"desktop": "xfce", "username": "agent"}, process)
        self.native = native_session.NativeSession(Mock(fd=42))
        self.native.state, self.native.node, self.native.logical_size = "running", 17, [10, 20]
        self.counter = 0

    @staticmethod
    def png_header():
        return b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + struct.pack(">II", 10, 20)

    def x_tool(self, argv, **kwargs):
        if argv[0] == "scrot":
            Path(argv[-1]).write_bytes(self.png_header())
        return "X=1\nY=2"

    def portal_capture(self, argv, **kwargs):
        os.write(kwargs["pass_fds"][-1], self.png_header())
        return subprocess.CompletedProcess(argv, 0, json.dumps({"geometry": [10, 20]}))

    def observe(self, session, **options):
        self.counter += 1
        return session.handle({"action": "screenshot", "generation": session.generation,
            "output": str(self.home / f"capture-{self.counter}.png"), **options})

    def move(self, session, observation, **options):
        return session.handle({"action": "input", "kind": "move", "x": 1, "y": 2,
            "generation": session.generation, "geometry": [10, 20],
            "control_revision": session.control_revision, "observation_id": observation["observation_id"], **options})

    def test_long_decision_uses_selected_window_and_token_is_single_use(self):
        for session in (self.debian, self.native):
            with self.subTest(session=type(session).__name__):
                self.now = 1000
                observation = self.observe(session, max_age_seconds=180)
                self.assertEqual(len(observation["observation_id"]), 32)
                self.now = 1120
                status = session.handle({"action": "status"})
                self.assertEqual(status["capture_freshness"]["remaining_seconds"], 60)
                self.assertEqual(status["observation_id"], observation["observation_id"])
                result = self.move(session, observation)
                self.assertEqual(result["observation_id"], observation["observation_id"])
                self.assertIn("input_completed_monotonic_ns", result)
                with self.assertRaisesRegex(ValueError, "consumed|invalidated"):
                    self.move(session, observation)

    def test_default_expiry_and_new_capture_cannot_refresh_old_token(self):
        for session in (self.debian, self.native):
            self.now = 1000
            old = self.observe(session)
            self.now = 1061
            with self.assertRaisesRegex(ValueError, "expired"):
                self.move(session, old)
            current = self.observe(session)
            with self.assertRaisesRegex(ValueError, "superseded"):
                self.move(session, old)
            self.move(session, current)

    def test_failed_capture_preserves_token_and_its_original_deadline(self):
        for session in (self.debian, self.native):
            self.now = 1000
            old = self.observe(session)
            self.now = 1050
            tool = self.tool if session is self.debian else self.capture
            original = tool.side_effect
            tool.side_effect = RuntimeError("capture failed")
            try:
                with self.assertRaisesRegex(RuntimeError, "capture failed"):
                    self.observe(session)
            finally:
                tool.side_effect = original
            status = session.handle({"action": "status"})
            self.assertEqual(status["observation_id"], old["observation_id"])
            self.assertEqual(status["capture_freshness"]["remaining_seconds"], 10)
            self.move(session, old)

    def test_bounds_geometry_and_control_changes(self):
        for session in (self.debian, self.native):
            for value in (0, 601, True, 1.5):
                with self.subTest(value=value), self.assertRaisesRegex(ValueError, "max age"):
                    self.observe(session, max_age_seconds=value)
            for value in (1, 600):
                observed = self.observe(session, max_age_seconds=value)
                self.assertEqual(observed["capture_freshness"]["max_age_seconds"], value)
            with self.assertRaisesRegex(ValueError, "geometry"):
                self.move(session, observed, geometry=[20, 10])
            observed = self.observe(session)
            control_state.change("disabled", human_approved=True)
            control_state.change("enabled", human_approved=True)
            self.assertIsNone(session.handle({"action": "status"})["observation_id"])
            with self.assertRaisesRegex(ValueError, "invalidated"):
                self.move(session, observed)

    def test_input_failure_consumes_token_without_replay(self):
        for session in (self.debian, self.native):
            observed = self.observe(session)
            target = self.tool if session is self.debian else session.portal.notify
            target.side_effect = RuntimeError("uncertain injection")
            with self.assertRaisesRegex(RuntimeError, "uncertain injection"):
                self.move(session, observed)
            with self.assertRaisesRegex(ValueError, "consumed|invalidated"):
                self.move(session, observed)
            if session is self.debian:
                target.side_effect = self.x_tool

    def test_debian_capture_rejects_control_change_during_acquisition(self):
        previous = self.observe(self.debian)
        def capture_and_change(argv, **kwargs):
            result = self.x_tool(argv, **kwargs)
            control_state.change("disabled", human_approved=True)
            return result
        self.tool.side_effect = capture_and_change
        with self.assertRaisesRegex(RuntimeError, "control changed during capture"):
            self.observe(self.debian)
        self.assertFalse((self.home / f"capture-{self.counter}.png").exists())
        self.assertIsNone(self.debian.observation)
        self.assertIsNotNone(previous["observation_id"])

    def test_debian_observe_after_input_and_compact_coordinate_metadata(self):
        observation = self.observe(self.debian)
        result = self.move(self.debian, observation)
        with patch.object(client.time, "sleep"), patch.object(runtime, "status", return_value=self.debian.snapshot_status()), \
                patch.object(runtime, "request", side_effect=self.debian.handle):
            result = client.observe_after_input(result, {"generation": self.debian.generation,
                "control_revision": self.debian.control_revision}, str(self.home / "after.png"), settle_ms=0, backend=runtime)
        self.assertNotIn("error", result)
        self.assertNotEqual(result["observation"]["observation_id"], observation["observation_id"])
        self.assertEqual(result["observation"]["coordinate_space"], "desktop")
        window = {"origin": [3, 4], "geometry": [5, 6]}
        self.assertEqual(compact_capture({"window": window, "control_revision": "r"})["window"], window)

    def test_debian_window_token_checks_scope_placement_and_focus(self):
        window = {"id": "0x123", "origin": [2, 3], "geometry": [10, 20], "identity": "window"}
        with patch.object(runtime, "geometry", return_value=[20, 30]), \
                patch.object(runtime, "capture_window", return_value=window), \
                patch.object(runtime, "window_details", return_value=window) as details, \
                patch.object(runtime, "window_ids", return_value=["0x123"]) as active:
            observed = self.observe(self.debian)
            self.assertEqual(compact_capture(observed)["image_origin"], [2, 3])
            with self.assertRaisesRegex(ValueError, "outside the observed image"):
                self.move(self.debian, observed, geometry=[20, 30])
            self.tool.reset_mock()
            details.return_value = {**window, "origin": [3, 3]}
            with self.assertRaisesRegex(ValueError, "window changed"):
                self.move(self.debian, observed, geometry=[20, 30], kind="key", key="Return")
            self.tool.assert_not_called()
            details.return_value = window
            observed = self.observe(self.debian)
            active.return_value = ["0x124"]
            with self.assertRaisesRegex(ValueError, "remain active"):
                self.move(self.debian, observed, geometry=[20, 30], kind="key", key="Return")

    def test_debian_slow_probe_cannot_inject_after_observation_expiry(self):
        observed = self.observe(self.debian, max_age_seconds=1)
        self.tool.reset_mock()
        def slow_geometry(**kwargs):
            self.now += .6
            return [10, 20]
        with patch.object(runtime, "geometry", side_effect=slow_geometry), \
                self.assertRaisesRegex(ValueError, "expired"):
            self.move(self.debian, observed)
        self.tool.assert_not_called()

    def test_debian_target_probe_cannot_inject_after_desktop_stops(self):
        window = {"id": "0x123", "origin": [0, 0], "geometry": [10, 20], "identity": "window"}
        def active_after_stop(*args):
            self.debian.process.poll.return_value = 0
            return [window["id"]]
        with patch.object(runtime, "capture_window", return_value=window), \
                patch.object(runtime, "window_details", return_value=window), \
                patch.object(runtime, "window_ids", side_effect=active_after_stop):
            observed = self.observe(self.debian)
            self.tool.reset_mock()
            with self.assertRaisesRegex(RuntimeError, "Desktop is stopping"):
                self.move(self.debian, observed, kind="key", key="Return")
        self.tool.assert_not_called()

    def test_debian_completion_survives_geometry_loss_after_input(self):
        for bound in (False, True):
            with self.subTest(bound=bound):
                observed = self.observe(self.debian)
                sent = False
                def completed_input(argv, **kwargs):
                    nonlocal sent
                    sent = True
                    return self.x_tool(argv, **kwargs)
                def current_geometry(**kwargs):
                    if sent:
                        raise RuntimeError("Desktop geometry became unavailable")
                    return [10, 20]
                with patch.object(runtime, "geometry", side_effect=current_geometry), \
                        patch.object(runtime, "run_tool", side_effect=completed_input) as injection:
                    result = self.move(self.debian, observed,
                        observation_id=observed["observation_id"] if bound else None)
                injection.assert_called_once()
                self.assertEqual(result["geometry"], [10, 20])
                self.assertIn("input_completed_at", result)
                self.assertIn("input_completed_monotonic_ns", result)

    def test_native_crop_token_rejects_target_outside_its_image(self):
        def crop(argv, **kwargs):
            os.write(kwargs["pass_fds"][-1], b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + struct.pack(">II", 3, 4))
            return subprocess.CompletedProcess(argv, 0, json.dumps({"geometry": [10, 20],
                "image_origin": [2, 3], "image_geometry": [3, 4]}))
        self.capture.side_effect = crop
        observed = self.observe(self.native, crop=[2, 3, 3, 4])
        with self.assertRaisesRegex(ValueError, "outside the observed image"):
            self.move(self.native, observed)
        for start, end in (((1, 3), (3, 4)), ((2, 3), (5, 4))):
            with self.subTest(start=start, end=end), self.assertRaisesRegex(ValueError, "outside the observed image"):
                self.move(self.native, observed, kind="drag", x=start[0], y=start[1], to_x=end[0], to_y=end[1])
        self.native.portal.notify.assert_not_called()
        self.move(self.native, observed, x=2, y=3)

    def test_native_token_expiry_during_hold_releases_and_invalidates(self):
        observed = self.observe(self.native, max_age_seconds=1)
        def notify(*args):
            if args == ("NotifyPointerButton", "iu", 272, 1):
                self.now += 2
        self.native.portal.notify.side_effect = notify
        with self.assertRaisesRegex(ValueError, "expired"):
            self.move(self.native, observed, kind="click", hold_ms=120)
        self.assertEqual(self.native.portal.notify.call_args.args, ("NotifyPointerButton", "iu", 272, 0))
        self.assertIsNone(self.native.observation)

    def test_sequence_rejects_bad_capture_age_before_any_action(self):
        sequence = self.home / "sequence.json"
        sequence.write_text(json.dumps([{"action": "input", "kind": "key", "key": "Return", "geometry": [10, 20]},
                                        {"action": "screenshot", "max_age_seconds": 601}]))
        with patch.object(runtime, "status") as status, patch.object(runtime, "request") as request, \
                self.assertRaisesRegex(ValueError, "max age"):
            client.run_sequence(str(sequence), self.debian.generation)
        status.assert_not_called()
        request.assert_not_called()

    def test_sequence_stops_when_old_helper_ignores_selected_capture_age(self):
        sequence = self.home / "sequence.json"
        sequence.write_text(json.dumps([{"action": "screenshot", "max_age_seconds": 180},
            {"action": "input", "kind": "key", "key": "Return", "geometry": [10, 20]}]))
        backend = Mock(MAX_MESSAGE=65536)
        backend.status.return_value = {"generation": "g", "control_revision": "r", "agent_control": "enabled"}
        backend.request.return_value = {"output": "/tmp/capture.png"}
        result = client.run_sequence(str(sequence), "g", backend=backend)
        self.assertIn("did not confirm observation lifetime", result["error"])
        self.assertEqual(result["completed"], 0)
        backend.request.assert_called_once()

    def test_debian_cli_routes_discrete_input_and_post_observation_to_real_handler(self):
        parser = argparse.ArgumentParser()
        add_desktop_subparser(parser.add_subparsers())
        for bound in (False, True):
            observed = self.observe(self.debian)
            arguments = ["desktop", "input", "--generation", self.debian.generation, "--geometry", "10", "20",
                         "move", "--x", "1", "--y", "2", "--observe", "--settle-ms", "0",
                         "--output", str(self.home / f"cli-after-{bound}.png")]
            if bound:
                arguments += ["--observation", observed["observation_id"]]
            with patch.object(runtime, "status", side_effect=self.debian.snapshot_status), \
                    patch.object(runtime, "request", side_effect=self.debian.handle) as request, \
                    patch.object(client.time, "sleep"), patch("sys.stdout", new_callable=io.StringIO) as output:
                self.assertEqual(run_desktop_command(parser.parse_args(arguments)), 0, output.getvalue())
            result = json.loads(output.getvalue())
            self.assertIn("input_completed_at", result)
            self.assertNotIn("hold_ms", request.call_args_list[0].args[0])
            self.assertIsNotNone(result["observation"]["observation_id"])

    def test_bound_debian_pacing_is_refused_without_sending_any_character(self):
        observed = self.observe(self.debian)
        with patch.object(runtime, "request") as request:
            result = client.send_action({"action": "input", "kind": "text", "text": "abc", "delay_ms": 10,
                "observation_id": observed["observation_id"], "generation": self.debian.generation,
                "geometry": [10, 20], "control_revision": self.debian.control_revision})
        self.assertIn("requires server-paced-text", result["error"])
        request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
