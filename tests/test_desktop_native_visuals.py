"""Native canvas gestures and crop mapping without accessing a real session."""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import hashlib
from io import StringIO
import json
import os
from pathlib import Path
import struct
import shlex
import subprocess
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch
import zlib

from desktop import client, control_state, native_session as native, portal_capture
from desktop.coordinates import map_capture_points, map_observation_points
from desktop.observations import compact_capture
from desktop.receipts import DesktopRequestError
from desktop.evidence import Recorder, verify
from lib import desktop_cli


class CaptureCoordinateTests(unittest.TestCase):
    def test_cli_frame_recipe_roundtrips_into_one_token_bound_action(self):
        parser = argparse.ArgumentParser()
        desktop_cli.add_desktop_subparser(parser.add_subparsers())
        capture = {"observation_id": "a" * 32, "generation": "b" * 32, "control_revision": "c" * 32,
                   "geometry": [2400, 1600], "image_origin": [0, 0], "image_geometry": [2400, 1600]}
        current = {**capture, "state": "running", "capture_freshness": {"fresh": True},
                   "input_capabilities": ["observation-input"]}
        with tempfile.TemporaryDirectory() as folder, patch.object(native, "status", return_value=current), \
                patch.object(native, "request", return_value={"input_delivery": "completed"}) as request:
            path = Path(folder) / "frame.json"
            path.write_text(json.dumps(capture))
            output = StringIO()
            with redirect_stdout(output):
                code = desktop_cli.run_desktop_command(parser.parse_args(["desktop", "--native", "map-point", "--capture", str(path),
                    "--view-geometry", "1200", "800", "--view-origin", "20", "30", "--x", "620", "--y", "430"]))
            self.assertEqual(code, 0)
            request.assert_not_called()
            result = json.loads(output.getvalue())
            action = ["desktop", "--native", "input", *shlex.split(result["input_context_args"]),
                      "click", *shlex.split(result["input_args"]), "--button", "1"]
            with redirect_stdout(StringIO()):
                self.assertEqual(desktop_cli.run_desktop_command(parser.parse_args(action)), 0)
            self.assertEqual(request.call_count, 1)
            self.assertEqual(request.call_args.args[0]["observation_id"], capture["observation_id"])
            request.reset_mock()
            used = Path(folder) / "used.png"
            used.write_bytes(b"existing")
            dangling = Path(folder) / "dangling.png"
            dangling.symlink_to(Path(folder) / "absent.png")
            for path in (used, dangling, used / "child.png", Path(folder) / "absent" / "child.png"):
                for arguments in (action, [part for part in action if part != "--native"]):
                    output = StringIO()
                    with self.subTest(path=path, arguments=arguments), redirect_stdout(output), \
                            patch.object(client.runtime, "status", return_value=current), \
                            patch.object(client.runtime, "request") as debian_request:
                        self.assertEqual(desktop_cli.run_desktop_command(parser.parse_args(
                            arguments + ["--observe", "--output", str(path)])), 1)
                        self.assertEqual(json.loads(output.getvalue())["input_delivery"], "not-sent")
                        debian_request.assert_not_called()
            request.assert_not_called()
            for button in ("left", "0", "8"):
                with redirect_stdout(StringIO()), patch("sys.stderr", StringIO()), self.assertRaises(SystemExit):
                    parser.parse_args(action + ["--button", button])

    def test_frame_mapping_keeps_context_margins_and_both_endpoints(self):
        capture = {"observation_id": "a" * 32, "generation": "b" * 32, "control_revision": "c" * 32,
                   "geometry": [2400, 1600], "image_origin": [100, 200], "image_geometry": [800, 600]}
        current = {**capture, "state": "running", "capture_freshness": {"fresh": True, "remaining_seconds": 40}}
        result = map_observation_points(capture, current, [400, 200], 210, 120,
                                       view_origin=[10, 20], to_x=409, to_y=219)
        self.assertEqual(result["input_coordinates"], {"x": 500, "y": 500, "to_x": 898, "to_y": 797})
        self.assertEqual(result["scale"], [2, 3])
        self.assertIn("--observation " + "a" * 32, result["input_context_args"])
        for changes in ({"generation": "new"}, {"geometry": [1200, 800]}, {"observation_id": None},
                        {"image_origin": [101, 200]}, {"control_revision": "new"},
                        {"capture_freshness": {"fresh": False}}, {"state": "stopped"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                map_observation_points(capture, {**current, **changes}, [400, 200], 1, 1)
        with self.assertRaisesRegex(ValueError, "within"):
            map_observation_points(capture, current, [400, 200], 9, 20, view_origin=[10, 20])

    def test_presented_full_capture_and_drag_map_to_monitor_pixels(self) -> None:
        result = map_capture_points([2400, 1600], [1920, 1280], 800, 600, to_x=1200, to_y=800)
        self.assertEqual(result["input_coordinates"], {"x": 1000, "y": 750, "to_x": 1500, "to_y": 1000})
        self.assertEqual(result["input_args"], "--x 1000 --y 750 --to-x 1500 --to-y 1000")
        self.assertEqual(result["scale"], [1.25, 1.25])
        self.assertFalse(result["input_sent"])

    def test_crop_origin_scaling_rounding_and_edges(self) -> None:
        for point, expected in (((0, 0), (100, 200)), ((200, 150), (500, 500)), ((399, 299), (898, 798))):
            with self.subTest(point=point):
                result = map_capture_points([2400, 1600], [400, 300], *point, crop=[100, 200, 800, 600])
                self.assertEqual(result["input_coordinates"], dict(zip(("x", "y"), expected)))
                self.assertEqual(result["image_origin"], [100, 200])
                self.assertEqual(result["image_geometry"], [800, 600])
        # Independent axes and floor division also cover rounded viewer sizes and upscaling.
        self.assertEqual(map_capture_points([3, 2], [8, 5], 7, 4)["input_coordinates"], {"x": 2, "y": 1})
        self.assertEqual(map_capture_points([2400, 1600], [2400, 1600], 2399, 1599)["input_coordinates"],
                         {"x": 2399, "y": 1599})

    def test_invalid_geometry_crop_points_and_partial_drag_are_rejected(self) -> None:
        base = {"geometry": [2400, 1600], "view_geometry": [1920, 1280], "x": 800, "y": 600}
        for changes in ({"geometry": None}, {"geometry": [1]}, {"geometry": [True, 100]},
                        {"geometry": [16385, 1]}, {"geometry": [8192, 8192]},
                        {"view_geometry": [0, 100]}, {"view_geometry": [100.5, 100]},
                        {"view_geometry": "1920x1280"}, {"crop": [100, 200, 2400, 1600]},
                        {"crop": [True, 0, 100, 100]}, {"x": -1}, {"x": 1920},
                        {"x": True}, {"y": 1280}, {"y": 1.5}, {"to_x": 100},
                        {"to_y": 100}, {"to_x": 100, "to_y": 1280}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                map_capture_points(**{**base, **changes})

    def test_cli_mapping_never_contacts_desktop_or_creates_artifacts(self) -> None:
        parser = argparse.ArgumentParser()
        desktop_cli.add_desktop_subparser(parser.add_subparsers())
        arguments = ["map-point", "--geometry", "2400", "1600", "--view-geometry", "400", "300",
                     "--crop", "100", "200", "800", "600", "--x", "200", "--y", "150", "--json"]
        with patch.object(native, "status") as status, patch.object(native, "request") as request, \
                patch.object(client.runtime, "status") as debian_status, \
                patch.object(client.runtime, "request") as debian_request, \
                patch.object(client, "artifact_path") as artifact:
            for prefix, suffix, code in ((["desktop", "--native"], [], 0),
                                         (["desktop", "--native"], ["--to-x", "400", "--to-y", "10"], 1),
                                         (["desktop"], [], 1)):
                output = StringIO()
                with self.subTest(prefix=prefix, suffix=suffix), redirect_stdout(output):
                    self.assertEqual(desktop_cli.run_desktop_command(parser.parse_args(prefix + arguments + suffix)), code)
                    result = json.loads(output.getvalue())
                    if code == 0:
                        self.assertEqual(result["input_args"], "--x 500 --y 500")
                    else:
                        self.assertIn("error", result)
            for mock in (status, request, debian_status, debian_request, artifact):
                mock.assert_not_called()


class NativeCanvasTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(Path, "home", return_value=self.home))
        self.portal = Mock(fd=42)
        self.session = native.NativeSession(self.portal)
        self.session.state = "running"
        self.session.node = 17
        self.session.logical_size = [1280, 720]
        self.session.geometry = [2560, 1440]
        self.session.captured = time.monotonic()
        self.payload = {"action": "input", "kind": "drag", "generation": self.session.generation,
                        "geometry": self.session.geometry, "control_revision": self.session.control_revision,
                        "x": 100, "y": 200, "to_x": 500, "to_y": 400, "duration_ms": 40}

    def test_refusal_transport_and_post_input_observation_have_distinct_receipts(self):
        with patch("desktop.evidence.revision", return_value={}):
            recorder = Recorder(str(self.home), self.session.generation)
        self.session.evidence = recorder
        self.session.captured -= 61
        with self.assertRaises(DesktopRequestError) as refused:
            self.session.handle(self.payload)
        self.assertEqual(refused.exception.receipt["input_delivery"], "not-sent")
        self.assertEqual(refused.exception.receipt["reason"], "capture-expired")
        self.assertFalse(refused.exception.receipt["capture_freshness"]["fresh"])
        self.assertIn("input_received_monotonic_ns", refused.exception.receipt)
        self.assertNotIn("input_dispatch_monotonic_ns", refused.exception.receipt)
        self.portal.notify.assert_not_called()
        self.assertEqual(recorder.data["events"][-1]["outcome"], "refused")
        self.session.captured = time.monotonic()
        self.portal.notify.side_effect = RuntimeError("ambiguous portal transport")
        with self.assertRaises(DesktopRequestError) as unknown:
            self.session.handle(self.payload)
        self.assertEqual(unknown.exception.receipt["input_delivery"], "unknown")
        self.assertEqual(unknown.exception.receipt["dispatch_stage"], "input-dispatch")
        self.assertGreaterEqual(unknown.exception.receipt["input_dispatch_monotonic_ns"], unknown.exception.receipt["input_received_monotonic_ns"])
        backend = Mock()
        backend.request.side_effect = ConnectionResetError("lost response")
        with self.assertRaises(DesktopRequestError) as transport:
            client.send_action(self.payload, backend=backend)
        self.assertEqual(transport.exception.receipt["reason"], "transport-failed")
        backend.request.side_effect = DesktopRequestError({"error": "Old worker error without dispatch evidence"})
        with self.assertRaises(DesktopRequestError) as legacy:
            client.send_action(self.payload, backend=backend)
        self.assertEqual(legacy.exception.receipt["input_delivery"], "unknown")
        backend.status.return_value = {"generation": self.session.generation, "control_revision": self.session.control_revision}
        completed = {"input_completed_at": "now", "input_id": "a" * 32}
        with patch.object(client.time, "sleep"):
            result = client.observe_after_input(completed, self.payload, str(self.home / "next.png"), settle_ms=0, backend=backend)
        self.assertEqual(result["input_delivery"], "completed")
        self.assertEqual(result["input_id"], completed["input_id"])
        self.assertEqual(result["reason"], "observation-failed")
        recorder.event({"action": "screenshot"}, {"error": "failed capture"})
        result = verify(str(recorder.path))
        self.assertEqual(result["counts"]["image_less"], 1)
        self.assertEqual(result["uncertain_events"], 2)
        self.assertFalse(result["healthy"])

    def test_duplicate_recorder_exposes_existing_handle_and_capacity(self):
        with patch("desktop.evidence.revision", return_value={}):
            started = self.session.handle({"action": "evidence", "operation": "start", "generation": self.session.generation,
                                           "project": str(self.home)})
        with self.assertRaises(DesktopRequestError) as duplicate:
            self.session.handle({"action": "evidence", "operation": "start", "generation": self.session.generation})
        self.assertEqual(duplicate.exception.receipt["artifact"], started["artifact"])
        self.assertEqual(duplicate.exception.receipt["remaining_events"], 500)

    def test_drag_interpolates_scaled_coordinates_and_releases_before_completion(self) -> None:
        with patch.object(self.session, "hold", side_effect=lambda ms, p: self.session.guard(p)) as hold:
            result = self.session.handle(self.payload)
        self.assertEqual(self.portal.notify.call_args_list, [
            call("NotifyPointerMotionAbsolute", "udd", 17, 50.0, 100.0),
            call("NotifyPointerButton", "iu", 272, 1),
            call("NotifyPointerMotionAbsolute", "udd", 17, 150.0, 150.0),
            call("NotifyPointerMotionAbsolute", "udd", 17, 250.0, 200.0),
            call("NotifyPointerButton", "iu", 272, 0),
        ])
        self.assertEqual(sum(c.args[0] for c in hold.call_args_list), 40)
        self.assertIn("input_completed_at", result)
        self.assertFalse(self.session.input_active)

    def test_invalid_drag_and_stale_observations_deliver_nothing(self) -> None:
        for changes in ({"to_x": 2560}, {"to_y": -1}, {"to_x": True}, {"duration_ms": 0},
                        {"duration_ms": 5001}, {"duration_ms": True}, {"button": 4}, {"hold_ms": 0},
                        {"geometry": [1280, 720]}, {"generation": "old"}, {"control_revision": "old"}):
            with self.subTest(changes=changes), self.assertRaises((RuntimeError, ValueError)):
                self.session.handle({**self.payload, **changes})
        self.session.captured -= 61
        with self.assertRaisesRegex(ValueError, "recent screenshot"):
            self.session.handle(self.payload)
        self.portal.notify.assert_not_called()

    def test_mid_drag_control_expiry_and_disconnect_always_release(self) -> None:
        for reason in ("human-override", "disabled", "stop", "expiry", "disconnect", "deadline"):
            with self.subTest(reason=reason):
                control_state.change("enabled", human_approved=True)
                session = native.NativeSession(self.portal)
                session.state = "running"
                session.node, session.geometry, session.logical_size = 17, [2560, 1440], [1280, 720]
                session.captured = time.monotonic()
                payload = {**self.payload, "generation": session.generation, "control_revision": session.control_revision}
                def notify(*args):
                    if args != ("NotifyPointerButton", "iu", 272, 1):
                        return
                    if reason in ("human-override", "disabled"):
                        control_state.change(reason, human_approved=True)
                    elif reason == "stop":
                        session.close()
                    elif reason == "expiry":
                        session.until = time.monotonic() - 1
                    elif reason == "disconnect":
                        payload["_cancelled"] = lambda: True
                    else:
                        payload["deadline"] = time.monotonic() - 1
                self.portal.reset_mock()
                self.portal.notify.side_effect = notify
                with self.assertRaises((RuntimeError, ValueError)):
                    session.handle(payload)
                self.assertEqual(self.portal.notify.call_args.args, ("NotifyPointerButton", "iu", 272, 0))
                self.assertFalse(session.input_active)
                self.assertTrue(session.input_released.is_set())
                self.assertEqual(self.portal.notify.call_count, 3)

    def test_partial_press_failure_releases_and_failed_release_stops_helper(self) -> None:
        self.portal.notify.side_effect = [None, RuntimeError("uncertain press"), None]
        with self.assertRaisesRegex(RuntimeError, "uncertain press"):
            self.session.handle(self.payload)
        self.assertEqual(self.portal.notify.call_args.args[-1], 0)
        def fail_release(*args):
            if args == ("NotifyPointerButton", "iu", 272, 0):
                raise RuntimeError("release failed")
        self.portal.notify.side_effect = fail_release
        with redirect_stdout(StringIO()), self.assertRaisesRegex(RuntimeError, "control stopped"):
            self.session.handle(self.payload)
        self.assertTrue(self.session.stopping.is_set())

    def test_cli_and_sequence_reject_debian_drag_before_submission(self) -> None:
        parser = argparse.ArgumentParser()
        desktop_cli.add_desktop_subparser(parser.add_subparsers())
        args = parser.parse_args(["desktop", "input", "drag", "--generation", "g", "--geometry", "2560", "1440",
                                  "--x", "1", "--y", "2", "--to-x", "3", "--to-y", "4"])
        with patch.object(client.runtime, "status", return_value={"state": "running", "generation": "g", "control_revision": "r"}), \
                patch.object(client.runtime, "request") as request, redirect_stdout(StringIO()):
            self.assertEqual(desktop_cli.run_desktop_command(args), 1)
            sequence = self.home / "sequence.json"
            sequence.write_text(json.dumps([{"action": "windows"}, {k:v for k,v in self.payload.items() if k not in ("generation", "control_revision")}]))
            with self.assertRaisesRegex(ValueError, "native desktop"):
                client.run_sequence(str(sequence), "g")
            request.assert_not_called()

    def test_crop_mapping_preserves_monitor_geometry_and_compact_evidence(self) -> None:
        crops = [[100, 200, 40, 30], [101, 200, 40, 30], [101, 200, 40, 30]]
        def capture(argv, **kwargs):
            crop = [int(n) for n in argv[-4:]]
            os.write(kwargs["pass_fds"][-1], b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + struct.pack(">II", *crop[2:]))
            return subprocess.CompletedProcess(argv, 0, json.dumps({"geometry": [2560, 1440], "image_geometry": crop[2:],
                "image_origin": crop[:2], "pixel_sha256": "a" * 64}))
        with patch.object(native.subprocess, "run", side_effect=capture):
            for index, crop in enumerate(crops):
                result = self.session.handle({"action": "screenshot", "generation": self.session.generation,
                    "output": str(self.home / f"{index}.png"), "crop": crop})
                self.assertEqual(result["geometry"], [2560, 1440])
                self.assertEqual(compact_capture(result)["image_origin"], crop[:2])
                self.assertIs(result["changed"], (None, True, False)[index])
        self.assertEqual(self.session.geometry, self.payload["geometry"])
        with patch.object(self.session, "hold"):
            self.session.handle(self.payload)
        self.portal.notify.assert_any_call("NotifyPointerMotionAbsolute", "udd", 17, 250.0, 200.0)

    def test_sequence_rejects_unsupported_crop_and_retains_old_helper_failure(self) -> None:
        sequence = self.home / "sequence.json"
        steps = [{"action": "input", "kind": "key", "geometry": [2560,1440], "key": "Return"},
                 {"action": "screenshot", "output": str(self.home / "crop.png"), "crop": [10, 20, 30, 40]}]
        sequence.write_text(json.dumps(steps))
        with patch.object(client.runtime, "request") as request, self.assertRaisesRegex(ValueError, "native screenshot"):
            client.run_sequence(str(sequence), "g")
        request.assert_not_called()
        backend = Mock(MAX_MESSAGE=65536)
        backend.status.return_value = {"generation": "g", "control_revision": "r", "agent_control": "enabled"}
        backend.request.side_effect = [{"input_completed_at": "now"}, {"geometry": [2560,1440], "image_geometry": [2560,1440]}]
        result = client.run_sequence(str(sequence), "g", backend=backend)
        self.assertEqual(result["completed"], 1)
        self.assertIn("did not confirm requested crop", result["error"])
        self.assertEqual(backend.request.call_count, 2)
        self.assertEqual(result["results"][0]["input_completed_at"], "now")

    def test_malformed_crop_result_removes_artifact_and_preserves_observation(self) -> None:
        path = self.home / "bad.png"
        def bad(argv, **kwargs):
            os.write(kwargs["pass_fds"][-1], b"not a png")
            return subprocess.CompletedProcess(argv, 0, json.dumps({"geometry": [2560,1440], "image_geometry": [40,30], "image_origin": [0,0]}))
        with patch.object(native.subprocess, "run", side_effect=bad), self.assertRaisesRegex(RuntimeError, "crop mapping"):
            self.session.handle({"action": "screenshot", "generation": self.session.generation,
                "output": str(path), "crop": [100, 200, 40, 30]})
        self.assertFalse(path.exists())
        self.assertIsNone(self.session.capture_region)

    def test_crop_helper_decodes_padded_rows_with_explicit_origin(self) -> None:
        buffer = Mock(pts=123)
        buffer.get_size.return_value = 28
        buffer.map.return_value = True, SimpleNamespace(data=b"HEADabcdWXYZPAD!efghIJKLPAD!")
        sample, pipeline, sink = Mock(), Mock(), Mock()
        sample.get_buffer.return_value = buffer
        sink.emit.return_value = sample
        pipeline.get_by_name.return_value = sink
        gst = SimpleNamespace(init=Mock(), parse_launch=Mock(return_value=pipeline), SECOND=10**9, CLOCK_TIME_NONE=-1,
                              State=SimpleNamespace(PLAYING=1, NULL=0), MapFlags=SimpleNamespace(READ=1))
        video = SimpleNamespace(VideoInfo=SimpleNamespace(new_from_caps=lambda caps:
            SimpleNamespace(width=2, height=2, stride=[12], offset=[4])))
        path = self.home / "crop.png"
        with patch.dict("sys.modules", {"gi": Mock(), "gi.repository": SimpleNamespace(Gst=gst, GstVideo=video)}), path.open("w+b") as stream:
            result = portal_capture.capture(42, 17, stream.fileno(), crop=[1, 0, 1, 2])
        self.assertEqual(result["geometry"], [2,2])
        self.assertEqual(result["image_geometry"], [1,2])
        self.assertEqual(result["image_origin"], [1,0])
        self.assertEqual(result["pixel_sha256"], hashlib.sha256(b"WXYZIJKL").hexdigest())
        content = path.read_bytes()
        self.assertEqual(zlib.decompress(content[41:-16]), b"\0WXYZ\0IJKL")
        for crop in ([True, 0, 1, 1], [0, 0, 0, 1], [-1, 0, 1, 1], [1, 1, 2, 2]):
            with self.subTest(crop=crop), self.assertRaises(ValueError):
                portal_capture.validate_crop(crop, [2, 2])


if __name__ == "__main__":
    unittest.main()
