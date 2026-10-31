"""Private desktop observation output and CLI compatibility checks."""

from __future__ import annotations

import argparse
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

from desktop.observations import save_observation
from desktop import client, control_state, native_session, portal_capture
from desktop.display_power import display_power
from desktop.evidence import Recorder, finalize
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
                  "state": "running", "portal_stage": "ready", "error": "capture failed"}
        code, output, _ = self.run_cli(result, "screenshot", "--output", "/tmp/new.png", "--compact")
        self.assertEqual(code, 1)
        self.assertEqual(output["changed"], False)
        self.assertEqual(output["frame_received_at"], "earlier")
        self.assertNotIn("portal_stage", output)

    def test_selected_backend_screenshot_help(self) -> None:
        for native in (False, True):
            with patch("sys.stdout", new_callable=io.StringIO) as output, self.assertRaises(SystemExit):
                self.arguments(*(["--native"] if native else []), "screenshot", "--help")
            body = output.getvalue()
            self.assertEqual("--active-window" in body, not native)
            if native:
                self.assertIn("Window capture is unavailable", " ".join(body.split()))

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
                self.assertEqual(observed["last_input"]["input_completed_at"], result["input_completed_at"])
                self.assertGreater(observed["frame_received_monotonic_ns"], result["input_completed_monotonic_ns"])
            self.assertEqual(run.call_args.args[0][-1], "1")

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


if __name__ == "__main__":
    unittest.main()
