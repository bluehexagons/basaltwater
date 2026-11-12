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
    def test_source_pixels_add_receipt_origin_once_and_keep_freshness_guards(self):
        capture = {"observation_id": "a" * 32, "generation": "b" * 32, "control_revision": "c" * 32,
                   "geometry": [2400, 1600], "image_origin": [100, 200], "image_geometry": [800, 600]}
        current = {**capture, "state": "running", "capture_freshness": {"fresh": True}}
        receipt = {"input_delivery": "completed", "observation": capture}
        result = map_observation_points(receipt, current, None, 400, 300,
                                        image_coordinates=True, to_x=799, to_y=599)
        self.assertEqual(result["input_coordinates"], {"x": 500, "y": 500, "to_x": 899, "to_y": 799})
        self.assertEqual(result["scale"], [1, 1])
        self.assertEqual(result["point_space"], "source-image")
        self.assertIsNone(result["view_geometry"])
        self.assertIsNone(result["view_origin"])
        self.assertEqual(result["observation_id"], capture["observation_id"])
        for changes in ({"capture_freshness": {"fresh": False}}, {"state": "stopped"},
                        {"control_revision": "old"}, {"image_origin": [0, 0]},
                        {"observation_id": "d" * 32}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                map_observation_points(receipt, {**current, **changes}, None, 1, 1, image_coordinates=True)
        with self.assertRaisesRegex(ValueError, "margins"):
            map_observation_points(receipt, current, None, 1, 1, image_coordinates=True, view_origin=[0, 0])
        with self.assertRaisesRegex(ValueError, "never replay"):
            map_observation_points({**capture, "input_delivery": "completed"}, current, None, 1, 1,
                                   image_coordinates=True)

    def test_source_coordinate_basis_is_explicit_and_bounded(self):
        base = dict(geometry=[2400, 1600], view_geometry=None, x=1, y=1,
                    crop=[100, 200, 800, 600], image_coordinates=True)
        for changes in ({"image_coordinates": 1}, {"view_geometry": [800, 600]},
                        {"x": True}, {"x": 800}, {"y": -1}, {"to_x": 2},
                        {"to_x": 799, "to_y": 600}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                map_capture_points(**{**base, **changes})
        parser = argparse.ArgumentParser()
        desktop_cli.add_desktop_subparser(parser.add_subparsers())
        arguments = ["desktop", "--native", "map-point", "--geometry", "2400", "1600",
                     "--crop", "100", "200", "800", "600", "--x", "400", "--y", "300"]
        for basis in ([], ["--image-coordinates", "--view-geometry", "800", "600"]):
            with patch("sys.stderr", StringIO()), self.assertRaises(SystemExit):
                parser.parse_args(arguments + basis)
        with patch.object(native, "status") as status, patch.object(native, "request") as request:
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(desktop_cli.run_desktop_command(parser.parse_args(
                    arguments + ["--image-coordinates"])), 0)
            self.assertEqual(json.loads(output.getvalue())["input_coordinates"], {"x": 500, "y": 500})
            status.assert_not_called()
            request.assert_not_called()

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
            # The action token is consumed; only the nested frame may authorize mapping.
            path.write_text(json.dumps({"observation_id": "d" * 32, "input_delivery": "completed",
                                        "observation": capture}))
            output = StringIO()
            with redirect_stdout(output):
                code = desktop_cli.run_desktop_command(parser.parse_args(["desktop", "--native", "map-point", "--capture", str(path),
                    "--view-geometry", "1200", "800", "--view-origin", "20", "30", "--x", "620", "--y", "430"]))
            self.assertEqual(code, 0)
            request.assert_not_called()
            result = json.loads(output.getvalue())
            self.assertEqual(result["capture_source"], "observation")
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

    def test_wrong_presented_dimensions_can_select_a_valid_different_control(self):
        capture = {"observation_id": "a" * 32, "generation": "b" * 32, "control_revision": "c" * 32,
                   "geometry": [2400, 1600], "image_origin": [0, 0], "image_geometry": [2400, 1600]}
        current = {**capture, "state": "running", "capture_freshness": {"fresh": True}}
        correct = map_observation_points(capture, current, [1200, 800], 600, 400)
        wrong = map_observation_points(capture, current, [2400, 1600], 600, 400)
        self.assertEqual(correct["input_coordinates"], {"x": 1200, "y": 800})
        self.assertEqual(wrong["input_coordinates"], {"x": 600, "y": 400})
        # Both are admissible arithmetic. The caller must establish viewer size.
        self.assertFalse(correct["input_sent"])
        self.assertFalse(wrong["input_sent"])
        crop = {**capture, "observation_id": "d" * 32,
                "image_origin": [1000, 600], "image_geometry": [400, 300]}
        cropped = map_observation_points(crop, {**current, **crop}, [400, 300], 200, 150)
        self.assertEqual(cropped["input_coordinates"], {"x": 1200, "y": 750})
        with self.assertRaisesRegex(ValueError, "within"):
            map_observation_points(crop, {**current, **crop}, [400, 300], 200, 150,
                                   to_x=400, to_y=150)

    def test_post_input_mapping_never_falls_back_to_action_context(self):
        capture = {"observation_id": "a" * 32, "generation": "b" * 32, "control_revision": "c" * 32,
                   "geometry": [2400, 1600], "image_origin": [100, 200], "image_geometry": [800, 600]}
        current = {**capture, "state": "running", "capture_freshness": {"fresh": True}}
        receipt = {**capture, "input_delivery": "completed", "observation": capture}
        mapped = map_observation_points(receipt, current, [400, 300], 200, 150, to_x=399, to_y=299)
        self.assertEqual(mapped["input_coordinates"], {"x": 500, "y": 500, "to_x": 898, "to_y": 798})
        # Even a top-level token matching status cannot rescue a failed/stale nested frame.
        for nested in (None, [], "invalid", {}, {**capture, "error": "failed"},
                       {**capture, "observation_id": "d" * 32}, {**capture, "control_revision": "old"},
                       {**capture, "image_geometry": [400, 300]}):
            with self.subTest(nested=nested), self.assertRaises(ValueError):
                map_observation_points({**receipt, "observation": nested}, current, [400, 300], 1, 1)
        for failed in ({**capture, "input_delivery": "completed"},
                       {**capture, "input_id": "d" * 32},
                       {**receipt, "error": "failed observation"}):
            with self.subTest(failed=failed), self.assertRaisesRegex(ValueError, "never replay"):
                map_observation_points(failed, current, [400, 300], 1, 1)
        with self.assertRaisesRegex(ValueError, "expired"):
            map_observation_points(receipt, {**current, "capture_freshness": {"fresh": False}}, [400, 300], 1, 1)

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


class PointPreviewTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.source = self.folder / "frame.png"
        self.source.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + struct.pack(">II", 100, 80) + b"\0" * 9)

    @staticmethod
    def render(arguments):
        Path(arguments[-1]).write_bytes(b"rendered PNG")
        return ""

    def test_preview_preserves_literal_bundle_and_labels_annotation(self):
        from desktop.point_preview import point_preview
        original = self.source.read_bytes()
        bundle = self.folder / "preview"
        with patch("desktop.evidence_media.media_command", side_effect=self.render), \
                patch("desktop.point_preview.media_command", side_effect=self.render) as render:
            result = point_preview(str(self.source), [10, 20, 40, 30], [10, 20], "<target>", str(bundle))
        data = json.loads(Path(result["artifact"]).read_text())
        literal = json.loads((bundle / "manifest.json").read_text())
        self.assertEqual(literal["kind"], "literal-crop")
        self.assertEqual(data["point_in_preview"], [0, 0])
        self.assertEqual(data["source"], literal["source"])
        self.assertEqual(data["source"]["sha256"], hashlib.sha256(original).hexdigest())
        self.assertEqual(data["literal_crop"]["sha256"], hashlib.sha256((bundle / "manifest.json").read_bytes()).hexdigest())
        self.assertEqual(self.source.read_bytes(), original)
        self.assertEqual((bundle / "source.png").read_bytes(), original)
        self.assertIn("drawbox=x=0:y=0:w=11:h=2", render.call_args.args[0][-4])
        self.assertIn("&lt;target&gt;", Path(result["report"]).read_text())
        for key in ("input_sent", "input_authority", "freshness_renewed"):
            self.assertFalse(data[key])
            self.assertFalse(result[key])
        for path in bundle.iterdir():
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(bundle.stat().st_mode & 0o777, 0o700)

    def test_invalid_preview_does_not_render_or_create_bundle(self):
        from desktop.point_preview import point_preview
        for rectangle, point in (([0, 0, 1025, 1], [0, 0]), ([0, 0, 10, 10], [True, 1]),
                                 ([0, 0, 10, 10], [10, 1]), ([1, 1, 10, 10], [0, 1])):
            output = self.folder / "absent"
            with self.subTest(rectangle=rectangle, point=point), \
                    patch("desktop.point_preview.crop") as crop, self.assertRaises(ValueError):
                point_preview(str(self.source), rectangle, point, "target", str(output))
            crop.assert_not_called()
            self.assertFalse(output.exists())

    def test_failed_annotation_keeps_literal_prefix_without_success_manifest(self):
        from desktop.point_preview import point_preview
        output = self.folder / "failed"
        with patch("desktop.evidence_media.media_command", side_effect=self.render), \
                patch("desktop.point_preview.media_command", side_effect=RuntimeError("renderer failed")), \
                self.assertRaisesRegex(RuntimeError, "renderer failed"):
            point_preview(str(self.source), [10, 20, 40, 30], [20, 30], "target", str(output))
        self.assertTrue((output / "manifest.json").exists())
        self.assertFalse((output / "point-preview.json").exists())
        with self.assertRaises((FileExistsError, ValueError)):
            point_preview(str(self.source), [10, 20, 40, 30], [20, 30], "target", str(output))

    def test_cli_preview_routes_offline_without_helper_calls(self):
        parser = argparse.ArgumentParser()
        desktop_cli.add_desktop_subparser(parser.add_subparsers())
        arguments = ["desktop", "--native", "evidence", "point-preview", str(self.source),
                     "--rectangle", "10", "20", "40", "30", "--point", "20", "30", "--caption", "target"]
        with patch("desktop.point_preview.point_preview", return_value={"input_sent": False}) as preview, \
                patch.object(native, "status") as status, patch.object(native, "request") as request, \
                redirect_stdout(StringIO()):
            self.assertEqual(desktop_cli.run_desktop_command(parser.parse_args(arguments)), 0)
            preview.assert_called_once()
            status.assert_not_called()
            request.assert_not_called()


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
        self.assertEqual(result["uncertain_by_action"], {"input": 1, "screenshot": 1})
        self.assertEqual(result["uncertain_event_details"][0], {
            "event_index": 1, "action": "input", "reason": unknown.exception.receipt["reason"],
            "dispatch_stage": "input-dispatch", "input_delivery": "unknown"})
        self.assertEqual(result["uncertain_event_details"][1]["action"], "screenshot")
        self.assertFalse(result["uncertain_event_details_truncated"])
        self.assertFalse(result["healthy"])

    def test_uncertain_diagnostics_are_bounded_allowlisted_and_read_only(self):
        with patch("desktop.evidence.revision", return_value={}):
            recorder = Recorder(str(self.home), self.session.generation)
        for index in range(12):
            recorder.event({"action": "control"}, {"error": "private detail", "reason": "x" * 200})
        before = recorder.path.read_bytes()
        report = verify(str(recorder.path))
        self.assertEqual(report["uncertain_events"], 12)
        self.assertEqual(report["uncertain_by_action"], {"control": 12})
        self.assertEqual(len(report["uncertain_event_details"]), 10)
        self.assertTrue(report["uncertain_event_details_truncated"])
        self.assertEqual(report["uncertain_event_details"][-1], {
            "event_index": 9, "action": "control", "reason": "x" * 128,
            "dispatch_stage": None, "input_delivery": None})
        self.assertNotIn("private detail", json.dumps(report))
        self.assertEqual(recorder.path.read_bytes(), before)
        self.assertFalse(report["healthy"])

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
        backend.status.return_value = {"state": "running", "generation": "g", "control_revision": "r", "agent_control": "enabled"}
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
