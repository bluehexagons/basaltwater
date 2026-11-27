"""Offline portal gesture contracts; no host devices, bus or consent changes."""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from copy import deepcopy
from io import StringIO
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import time
import unittest
from unittest.mock import Mock, call, patch

from desktop import client, native_session as native, pointer_gestures as gestures
from desktop import control_state
from desktop import session_runtime as runtime
from desktop.evidence import Recorder
from desktop.observations import CaptureObservation
from desktop.portal import Portal, REMOTE
from desktop.receipts import DesktopRequestError
from lib import desktop_cli


class PointerGestureTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(Path, "home", return_value=self.home))
        self.portal = Mock(fd=42)
        self.session = native.NativeSession(self.portal)
        self.session.state = "running"
        self.session.node = 17
        self.session.logical_size = [320, 240]
        self.session.geometry = [640, 480]
        self.session.captured = time.monotonic()
        self.session.touchscreen_granted = True
        self.session.observation = CaptureObservation(time.monotonic(), 60, [640, 480])
        self.frames = [
            {"at_ms": 0, "contacts": [{"id": 0, "x": 100, "y": 200}, {"id": 1, "x": 300, "y": 200}]},
            {"at_ms": 1, "contacts": [{"id": 0, "x": 80, "y": 200}, {"id": 1, "x": 320, "y": 200}]},
            {"at_ms": 2, "contacts": []},
        ]
        self.payload = {"action": "input", "kind": "touch", "frames": self.frames,
            "generation": self.session.generation, "geometry": [640, 480],
            "control_revision": self.session.control_revision, "observation_id": self.session.observation.id}

    def test_two_contacts_move_in_selected_stream_then_release_and_consume_capture(self):
        result = self.session.handle(self.payload)
        self.assertEqual(result["input_delivery"], "completed")
        self.assertEqual(self.portal.notify.call_args_list, [
            call("NotifyTouchDown", "uudd", 17, 0, 50., 100., timeout_ms=250),
            call("NotifyTouchDown", "uudd", 17, 1, 150., 100., timeout_ms=250),
            call("NotifyTouchMotion", "uudd", 17, 0, 40., 100., timeout_ms=250),
            call("NotifyTouchMotion", "uudd", 17, 1, 160., 100., timeout_ms=250),
            call("NotifyTouchUp", "u", 0, timeout_ms=250), call("NotifyTouchUp", "u", 1, timeout_ms=250)])
        self.assertEqual(result["touch_frames_completed"], 3)
        self.assertTrue(result["touch_contacts_released"])
        self.assertEqual(len(result["touch_frame_completed_monotonic_ns"]), 3)
        self.assertIsNone(self.session.observation)
        with self.assertRaises(DesktopRequestError):
            self.session.handle(self.payload)

    def test_complete_plan_and_capture_region_are_validated_before_any_touch(self):
        cases = []
        for change in ({"id": True}, {"id": 10}, {"x": 640}, {"y": -1}, {"x": 1.5}, {"extra": 1}):
            frames = deepcopy(self.frames)
            frames[1]["contacts"][0].update(change)
            cases.append(frames)
        for changed in ([{"at_ms": 1, "contacts": []}],
                        [{"at_ms": 0, "contacts": []}, {"at_ms": 5001, "contacts": []}],
                        self.frames[:-1], self.frames + [{"at_ms": 2, "contacts": []}],
                        [{"at_ms": 0, "contacts": []}, {"at_ms": 1, "contacts": []}]):
            cases.append(changed)
        duplicate = deepcopy(self.frames)
        duplicate[1]["contacts"][1]["id"] = 0
        cases.append(duplicate)
        for frames in cases:
            with self.subTest(frames=frames), self.assertRaises(ValueError):
                gestures.validate({**self.payload, "frames": frames})
        self.session.observation.region = [90, 190, 230, 30]
        with self.assertRaisesRegex(DesktopRequestError, "outside the observed image") as failure:
            self.session.handle(self.payload)
        self.assertEqual(failure.exception.receipt["input_delivery"], "not-sent")
        self.portal.notify.assert_not_called()

    def test_ten_contacts_and_id_reuse_have_separate_down_up_lifetimes(self):
        contacts = [{"id": slot, "x": slot * 10, "y": 20} for slot in range(10)]
        frames = [{"at_ms": 0, "contacts": contacts}, {"at_ms": 1, "contacts": []},
                  {"at_ms": 2, "contacts": contacts[:1]}, {"at_ms": 3, "contacts": []}]
        result = self.session.handle({**self.payload, "frames": frames})
        self.assertEqual(sum(c.args[0] == "NotifyTouchDown" for c in self.portal.notify.call_args_list), 11)
        self.assertEqual(sum(c.args[0] == "NotifyTouchUp" for c in self.portal.notify.call_args_list), 11)
        self.assertTrue(result["touch_contacts_released"])

    def test_missing_consent_or_observation_never_dispatches(self):
        self.session.touchscreen_granted = False
        with self.assertRaisesRegex(DesktopRequestError, "consent"):
            self.session.handle(self.payload)
        self.session.touchscreen_granted = True
        with self.assertRaisesRegex(DesktopRequestError, "single-use"):
            self.session.handle({**self.payload, "observation_id": None})
        self.portal.notify.assert_not_called()

    def test_cancelled_or_interrupted_gesture_lifts_all_contacts(self):
        def interrupted(method, *args, **kwargs):
            if method == "NotifyTouchMotion":
                raise RuntimeError("connection lost")
        self.portal.notify.side_effect = interrupted
        with self.assertRaises(DesktopRequestError) as failure:
            self.session.handle(self.payload)
        receipt = failure.exception.receipt
        self.assertEqual(receipt["input_delivery"], "unknown")
        self.assertTrue(receipt["touch_contacts_released"])
        self.assertEqual(self.session.injected_touches, set())
        for slot in (0, 1):
            self.portal.notify.assert_any_call("NotifyTouchUp", "u", slot, timeout_ms=250)

    def test_uncertain_down_is_also_released_and_release_failure_stops_session(self):
        def failing(method, *args, **kwargs):
            if method in ("NotifyTouchDown", "NotifyTouchUp"):
                raise RuntimeError("portal unavailable")
        self.portal.notify.side_effect = failing
        with self.assertRaisesRegex(DesktopRequestError, "release failed") as failure:
            self.session.handle(self.payload)
        self.assertFalse(failure.exception.receipt["touch_contacts_released"])
        self.assertEqual(self.session.state, "stopped")
        self.portal.notify.assert_any_call("NotifyTouchUp", "u", 0, timeout_ms=250)

    def test_stop_during_hold_releases_every_contact_even_if_one_release_fails(self):
        def cancel(milliseconds, payload):
            if self.session.injected_touches:
                raise RuntimeError("human override")
        def release(method, *args, **kwargs):
            if method == "NotifyTouchUp" and args[1] == 0:
                raise RuntimeError("first release failed")
        self.portal.notify.side_effect = release
        with patch.object(self.session, "hold", side_effect=cancel), self.assertRaises(DesktopRequestError):
            self.session.handle(self.payload)
        self.portal.notify.assert_any_call("NotifyTouchUp", "u", 1, timeout_ms=250)
        self.assertEqual(self.session.state, "stopped")

    def test_each_revocation_path_releases_contacts_without_finishing_plan(self):
        for reason in ("human-override", "disabled", "stop", "expiry", "disconnect", "deadline"):
            with self.subTest(reason=reason):
                control_state.change("enabled", human_approved=True)
                portal = Mock()
                session = native.NativeSession(portal)
                session.state, session.touchscreen_granted = "running", True
                session.node, session.geometry, session.logical_size = 17, [640, 480], [320, 240]
                session.captured = time.monotonic()
                session.observation = CaptureObservation(time.monotonic(), 60, [640, 480])
                payload = {**self.payload, "generation": session.generation, "control_revision": session.control_revision,
                           "observation_id": session.observation.id}

                def revoke(method, *args, **kwargs):
                    if method != "NotifyTouchDown":
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

                portal.notify.side_effect = revoke
                with self.assertRaises(DesktopRequestError) as failure:
                    session.handle(payload)
                portal.notify.assert_any_call("NotifyTouchUp", "u", 0, timeout_ms=250)
                self.assertEqual(failure.exception.receipt["input_delivery"], "unknown")
                self.assertTrue(failure.exception.receipt["touch_contacts_released"])
                self.assertEqual(failure.exception.receipt["touch_frames_completed"], 0)

    def test_plan_size_and_frame_limits_apply_before_dispatch(self):
        contacts = [{"id": slot, "x": 16383, "y": 16383} for slot in range(10)]
        frames = [{"at_ms": index, "contacts": contacts} for index in range(127)] + [{"at_ms": 127, "contacts": []}]
        gestures.validate({**self.payload, "geometry": [16384, 16384], "frames": frames})
        with self.assertRaisesRegex(ValueError, "2–128"):
            gestures.validate({**self.payload, "frames": frames + [{"at_ms": 128, "contacts": []}]})
        self.portal.notify.assert_not_called()

    def test_recorder_pins_plan_and_retains_partial_release_receipts_without_raw_frames(self):
        with patch("desktop.evidence.revision", return_value={}):
            recorder = Recorder(str(self.home), self.session.generation)
        result = self.session.handle(self.payload)
        recorder.event(self.payload, result)
        row = recorder.data["events"][-1]
        self.assertEqual(row["touch_plan"], gestures.plan_summary(self.payload))
        self.assertEqual(row["result"]["touch_frames_completed"], 3)
        self.assertTrue(row["result"]["touch_contacts_released"])
        self.assertEqual(len(row["result"]["touch_frame_completed_monotonic_ns"]), 3)
        self.assertNotIn("frames", row)
        self.assertNotIn('"contacts":', json.dumps(row))
        failed = {"error": "interrupted", "input_delivery": "unknown", "touch_frames_completed": 1,
                  "touch_contacts_released": False, "touch_frame_completed_monotonic_ns": [123]}
        recorder.event(self.payload, failed)
        self.assertFalse(recorder.data["events"][-1]["result"]["touch_contacts_released"])
        recorder.event({**self.payload, "frames": [{"private": "do not store"}]}, failed)
        self.assertEqual(recorder.data["events"][-1]["touch_plan"], {"validation": "invalid"})
        self.assertNotIn("do not store", recorder.path.read_text())

    def test_failed_release_is_journaled_before_helper_stop_finalizes_recording(self):
        with patch("desktop.evidence.revision", return_value={}):
            recorder = Recorder(str(self.home), self.session.generation)
        self.session.evidence = recorder

        def fail_up(method, *args, **kwargs):
            if method == "NotifyTouchUp":
                raise RuntimeError("release unavailable")

        self.portal.notify.side_effect = fail_up
        with self.assertRaises(DesktopRequestError):
            self.session.handle(self.payload)
        data = json.loads(recorder.path.read_text())
        event = data["events"][-1]
        self.assertEqual(event["outcome"], "uncertain")
        self.assertFalse(event["result"]["touch_contacts_released"])
        self.assertEqual(event["result"]["input_delivery"], "unknown")
        self.assertGreaterEqual(data["finished_at"], event["at"])
        self.assertIsNone(self.session.evidence)

    def test_stop_between_input_completion_and_journaling_retains_last_event(self):
        with patch("desktop.evidence.revision", return_value={}):
            recorder = Recorder(str(self.home), self.session.generation)
        self.session.evidence = recorder
        original = self.session.record_evidence

        def stop_before_record(payload, result):
            self.assertFalse(self.session.input_active)
            self.session.close()
            self.assertIs(self.session.evidence, recorder)
            original(payload, result)

        with patch.object(self.session, "record_evidence", side_effect=stop_before_record):
            self.session.handle(self.payload)
        self.assertEqual(recorder.data["events"][-1]["result"]["input_delivery"], "completed")
        self.assertTrue(recorder.data["finished_at"])
        self.assertEqual(self.session.pending_input_receipts, 0)
        self.assertIsNone(self.session.evidence)

    def test_relative_motion_and_scroll_have_explicit_units_and_bounds(self):
        for kind, values in (("relative", {"dx": -100, "dy": 40}), ("scroll", {"dx": 2, "dy": -3})):
            self.session.observation = CaptureObservation(time.monotonic(), 60, [640, 480])
            payload = {**self.payload, "kind": kind, **values, "observation_id": self.session.observation.id}
            payload.pop("frames")
            result = self.session.handle(payload)
            self.assertEqual(result["input_delivery"], "completed")
        self.portal.notify.assert_any_call("NotifyPointerMotion", "dd", -100., 40.)
        self.portal.notify.assert_any_call("NotifyPointerAxisDiscrete", "ui", 0, -3)
        self.portal.notify.assert_any_call("NotifyPointerAxisDiscrete", "ui", 1, 2)
        for kind, changes in (("relative", {"dx": 4097}), ("scroll", {"dy": 101}),
                              ("relative", {"dx": True}), ("scroll", {"dy": 0})):
            with self.subTest(kind=kind, changes=changes), self.assertRaises(ValueError):
                gestures.validate({"action": "input", "kind": kind, "observation_id": "a" * 32, **changes})

    def test_extra_mouse_buttons_are_released_and_do_not_become_wheel_events(self):
        for button, code in ((8, 275), (9, 276)):
            self.session.observation = CaptureObservation(time.monotonic(), 60, [640, 480])
            self.session.handle({**self.payload, "kind": "click", "frames": None, "x": 20, "y": 30,
                "button": button, "hold_ms": 0, "observation_id": self.session.observation.id})
            self.portal.notify.assert_any_call("NotifyPointerButton", "iu", code, 1)
            self.portal.notify.assert_any_call("NotifyPointerButton", "iu", code, 0)

    def test_debian_and_old_helpers_refuse_extended_input_before_dispatch(self):
        with patch.object(client.runtime, "request") as request, self.assertRaisesRegex(ValueError, "XRDP"):
            client.send_action(self.payload)
        request.assert_not_called()
        backend = Mock(MAX_MESSAGE=65536)
        backend.status.return_value = {"input_capabilities": []}
        with self.assertRaisesRegex(ValueError, "lacks touchscreen"):
            client.send_action(self.payload, backend=backend)
        backend.request.assert_not_called()
        backend.status.return_value = {"state": "running", "generation": "g", "control_revision": "r",
                                       "input_capabilities": ["observation-input"]}
        sequence = self.home / "sequence.json"
        sequence.write_text(json.dumps([{"action": "windows"},
            {key: value for key, value in self.payload.items() if key not in ("generation", "control_revision")}]))
        with self.assertRaisesRegex(ValueError, "lacks touchscreen"):
            client.run_sequence(str(sequence), "g", backend=backend)
        backend.request.assert_not_called()

    def test_cli_reads_complete_plan_and_native_touch_start_is_explicit(self):
        parser = argparse.ArgumentParser()
        desktop_cli.add_desktop_subparser(parser.add_subparsers())
        path = self.home / "gesture.json"
        path.write_text(json.dumps(self.frames))
        current = {"state": "running", "generation": self.session.generation,
            "control_revision": self.session.control_revision,
            "input_capabilities": ["observation-input", "touchscreen"]}
        args = parser.parse_args(["desktop", "--native", "input", "--generation", self.session.generation,
            "--geometry", "640", "480", "--observation", self.payload["observation_id"], "touch", "--frames", str(path)])
        with patch.object(native, "status", return_value=current), \
                patch.object(native, "request", return_value={"input_delivery": "completed"}) as request, redirect_stdout(StringIO()):
            self.assertEqual(desktop_cli.run_desktop_command(args), 0)
        self.assertEqual(request.call_args.args[0]["frames"], self.frames)
        with patch.object(native, "start", return_value={}) as start, redirect_stdout(StringIO()):
            self.assertEqual(desktop_cli.run_desktop_command(parser.parse_args(["desktop", "--native", "start", "--touch"])), 0)
        self.assertTrue(start.call_args.kwargs["touch"])
        with patch.object(client.runtime, "start") as start, redirect_stdout(StringIO()):
            self.assertEqual(desktop_cli.run_desktop_command(parser.parse_args(["desktop", "start", "--touch"])), 2)
        start.assert_not_called()

    def test_touch_request_does_not_silently_reuse_non_touch_session(self):
        with patch.object(native, "status", return_value={"state": "running", "input_capabilities": []}), \
                patch.object(native.subprocess, "Popen") as launch, self.assertRaisesRegex(ValueError, "stop/start --touch"):
            native.start(touch=True)
        launch.assert_not_called()


class InputCaptureAttributionTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(Path, "home", return_value=self.home))
        self.enterContext(patch.dict(os.environ, DISPLAY=":99"))
        self.enterContext(patch.object(runtime, "geometry", return_value=[10, 20]))
        self.enterContext(patch.object(runtime, "window_manager_ready", return_value=True))
        self.enterContext(patch("desktop.evidence.revision", return_value={}))
        self.enterContext(patch.object(runtime, "run_tool", side_effect=self.x_tool))
        self.enterContext(patch.object(native.subprocess, "run", side_effect=self.portal_capture))
        self.interrupted = False
        self.counter = 0

    @staticmethod
    def png_header():
        return b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + struct.pack(">II", 10, 20)

    def x_tool(self, argv, **kwargs):
        if argv[0] == "scrot":
            Path(argv[-1]).write_bytes(self.png_header())
        elif self.interrupted:
            raise RuntimeError("private backend diagnostic")
        return "X=1\nY=2"

    def portal_capture(self, argv, **kwargs):
        os.write(kwargs["pass_fds"][-1], self.png_header())
        return subprocess.CompletedProcess(argv, 0, json.dumps({"geometry": [10, 20]}))

    def observe(self, session):
        self.counter += 1
        return session.handle({"action": "screenshot", "generation": session.generation,
            "output": str(self.home / f"capture-{self.counter}.png")})

    def move(self, session, observed, **changes):
        return session.handle({"action": "input", "kind": "move", "x": 1, "y": 2,
            "generation": session.generation, "geometry": [10, 20],
            "control_revision": session.control_revision, "observation_id": observed["observation_id"], **changes})

    def test_failed_attempt_supersedes_success_in_capture_and_journal_then_recovers(self):
        for backend in ("native", "debian"):
            for stage, delivery in (("before-input", "not-sent"), ("input-dispatch", "unknown")):
                with self.subTest(backend=backend, stage=stage):
                    self.interrupted = False
                    portal = Mock(fd=42)
                    if backend == "native":
                        session = native.NativeSession(portal)
                        session.state, session.node, session.logical_size = "running", 17, [10, 20]
                    else:
                        session = runtime.DesktopSession({"desktop": "xfce", "username": "agent"}, Mock(poll=lambda: None))
                    recorder = Recorder(str(self.home), session.generation)
                    session.evidence = recorder
                    completed = self.move(session, self.observe(session))
                    fresh = self.observe(session)
                    changes = {"geometry": [11, 20]} if stage == "before-input" else {}
                    if stage == "input-dispatch":
                        self.interrupted = True
                        portal.notify.side_effect = RuntimeError("private backend diagnostic")
                    with self.assertRaises((ValueError, RuntimeError)) as failure:
                        self.move(session, fresh, **changes)
                    failed = failure.exception.receipt
                    observed = self.observe(session)
                    latest = observed["last_input"]
                    self.assertNotEqual(latest["input_id"], completed["input_id"])
                    self.assertEqual(latest["input_id"], failed["input_id"])
                    self.assertEqual(latest["dispatch_stage"], stage)
                    self.assertEqual(latest["input_delivery"], delivery)
                    self.assertNotIn("input_completed_at", latest)
                    self.assertNotIn("input_completed_monotonic_ns", latest)
                    self.assertNotIn("private backend diagnostic", json.dumps(latest))
                    self.assertEqual(recorder.data["events"][-1]["related_input_id"], failed["input_id"])
                    self.interrupted = False
                    portal.notify.side_effect = None
                    recovered = self.move(session, observed)
                    final = self.observe(session)
                    self.assertEqual(final["last_input"]["input_id"], recovered["input_id"])
                    self.assertEqual(final["last_input"]["input_delivery"], "completed")
                    self.assertEqual(final["last_input"]["input_completed_at"], recovered["input_completed_at"])
                    self.assertEqual(recorder.data["events"][-1]["related_input_id"], recovered["input_id"])

    def test_native_older_input_finishing_after_a_newer_refusal_keeps_newer_identity(self):
        for interrupted in (False, True):
            with self.subTest(interrupted=interrupted):
                session = native.NativeSession(Mock(fd=42))
                session.state, session.node, session.logical_size = "running", 17, [10, 20]
                observed = self.observe(session)
                refused = []

                def admit_during_hold(milliseconds, payload):
                    with self.assertRaises(DesktopRequestError) as failure:
                        self.move(session, observed)
                    refused.append(failure.exception.receipt)
                    if interrupted:
                        raise RuntimeError("client disconnected")

                with patch.object(session, "hold", side_effect=admit_during_hold):
                    if interrupted:
                        with self.assertRaises(DesktopRequestError):
                            self.move(session, observed, kind="click")
                    else:
                        self.move(session, observed, kind="click")
                self.assertEqual(self.observe(session)["last_input"]["input_id"], refused[0]["input_id"])
                self.assertEqual(session.last_input["input_delivery"], "not-sent")


class TouchPortalTests(unittest.TestCase):
    def setUp(self):
        self.portal = Portal.__new__(Portal)
        self.portal.bus = Mock()
        self.portal.Gio = Mock()
        self.portal.GLib = Mock()
        self.portal.GLib.Variant.side_effect = lambda signature, value: (signature, value)
        self.portal.progress = Mock()
        self.portal.disconnected = Mock()
        self.portal.restore_token = None
        self.portal.bus.call_with_unix_fd_list_sync.return_value = (Mock(unpack=lambda: (0,)), Mock(get=lambda _: 42))

    def test_touchscreen_support_and_actual_consent_are_both_required(self):
        for available, granted in ((3, 3), (7, 3), (7, 7)):
            with self.subTest(available=available, granted=granted), \
                    patch.object(self.portal, "call", return_value=Mock(unpack=lambda: (available,))), \
                    patch.object(self.portal, "response", side_effect=[{"session_handle": "session"}, {}, {},
                        {"devices": granted, "streams": [(17, {"logical_size": [320, 240]})]}]) as response:
                if available == granted == 7:
                    self.assertEqual(self.portal.start(touch=True), (17, [320, 240]))
                    self.assertEqual(response.call_args_list[1].args[-1]["types"], ("u", 7))
                else:
                    with self.assertRaisesRegex(RuntimeError, "touchscreen"):
                        self.portal.start(touch=True)
                    if available == 3:
                        response.assert_not_called()

    def test_touch_notifications_use_bounded_bus_calls_and_exact_signatures(self):
        self.portal.session = "session"
        self.portal.notify("NotifyTouchDown", "uudd", 17, 0, 10., 20., timeout_ms=250)
        self.assertEqual(self.portal.bus.call_sync.call_args.args[4],
                         ("(oa{sv}uudd)", ("session", {}, 17, 0, 10., 20.)))
        self.assertEqual(self.portal.bus.call_sync.call_args.args[-2], 250)


if __name__ == "__main__":
    unittest.main()
