"""Physical keyboard injection contracts without a live portal or desktop."""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, call, patch

from desktop import client, control_state, native_session as native
from lib import desktop_cli


class NativeKeyboardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(Path, "home", return_value=self.home))
        self.portal = Mock(fd=42)
        self.reset_session()

    def reset_session(self) -> None:
        control_state.change("enabled", human_approved=True)
        self.session = native.NativeSession(self.portal)
        self.session.state = "running"
        self.session.geometry = [2560, 1440]
        self.session.captured = time.monotonic()
        self.payload = {"action": "input", "kind": "keycode", "codes": [29, 30],
                        "generation": self.session.generation, "geometry": self.session.geometry,
                        "control_revision": self.session.control_revision, "hold_ms": 40}

    def test_physical_chord_uses_evdev_codes_and_releases_in_reverse(self) -> None:
        observed = []
        def hold(ms, payload):
            self.assertEqual(ms, 40)
            observed.append(self.session.handle({"action": "status"}))
        with patch.object(self.session, "hold", side_effect=hold):
            result = self.session.handle(self.payload)
        self.assertEqual(self.portal.notify.call_args_list, [
            call("NotifyKeyboardKeycode", "iu", 29, 1), call("NotifyKeyboardKeycode", "iu", 30, 1),
            call("NotifyKeyboardKeycode", "iu", 30, 0), call("NotifyKeyboardKeycode", "iu", 29, 0)])
        self.assertIn("input_completed_at", result)
        state = observed[0]["input_state"]
        self.assertEqual(state, {"scope": "helper-injected", "physical_modifiers": "unavailable",
                                 "keysym_presses": 0, "keycode_presses": 2, "modifiers": ["ctrl"]})
        self.assertTrue(observed[0]["control_active"])
        self.assertEqual(self.session.snapshot_status()["input_state"]["keycode_presses"], 0)
        self.assertFalse(self.session.input_active)

    def test_invalid_chords_and_stale_observations_send_nothing(self) -> None:
        changes = [{"codes": codes} for codes in (None, [], "29 30", [True], [0], [-1], [256],
                    [30.0], [29, 29], [29, {}], list(range(1, 10)))]
        changes += [{"key": "a"}, {"text": "a"}, {"kind": "key"}, {"hold_ms": 5001},
                    {"hold_ms": True}, {"geometry": [1280, 720]}, {"generation": "old"},
                    {"control_revision": "old"}]
        for change in changes:
            with self.subTest(change=change), self.assertRaises((ValueError, RuntimeError)):
                self.session.handle({**self.payload, **change})
        self.session.captured -= 61
        with self.assertRaisesRegex(ValueError, "recent screenshot"):
            self.session.handle(self.payload)
        self.portal.notify.assert_not_called()

    def test_interruption_releases_all_physical_keys(self) -> None:
        for reason in ("human-override", "disabled", "stop", "expiry", "disconnect", "deadline"):
            with self.subTest(reason=reason):
                self.reset_session()
                self.portal.reset_mock()
                def notify(*args):
                    if args != ("NotifyKeyboardKeycode", "iu", 30, 1):
                        return
                    if reason in ("human-override", "disabled"):
                        control_state.change(reason, human_approved=True)
                    elif reason == "stop":
                        self.session.close()
                    elif reason == "expiry":
                        self.session.until = time.monotonic() - 1
                    elif reason == "disconnect":
                        self.payload["_cancelled"] = lambda: True
                    else:
                        self.payload["deadline"] = time.monotonic() - 1
                self.portal.notify.side_effect = notify
                with self.assertRaises((ValueError, RuntimeError)):
                    self.session.handle(self.payload)
                self.assertEqual(self.portal.notify.call_args_list[-2:], [
                    call("NotifyKeyboardKeycode", "iu", 30, 0), call("NotifyKeyboardKeycode", "iu", 29, 0)])
                self.assertEqual(self.session.snapshot_status()["input_state"]["keycode_presses"], 0)
                self.assertFalse(self.session.input_active)
                self.assertTrue(self.session.input_released.is_set())

    def test_uncertain_press_and_failed_release_still_release_remaining_keys(self) -> None:
        self.portal.notify.side_effect = [None, RuntimeError("uncertain press"), None, None]
        with self.assertRaisesRegex(RuntimeError, "uncertain press"):
            self.session.handle(self.payload)
        self.assertEqual(self.portal.notify.call_args_list[-2:], [
            call("NotifyKeyboardKeycode", "iu", 30, 0), call("NotifyKeyboardKeycode", "iu", 29, 0)])
        self.assertEqual(self.session.snapshot_status()["input_state"]["keycode_presses"], 0)
        self.portal.reset_mock()
        self.portal.notify.side_effect = [None, None, RuntimeError("uncertain release"), None]
        with self.assertRaisesRegex(RuntimeError, "release failed"):
            self.session.handle(self.payload)
        self.assertEqual(self.portal.notify.call_args.args, ("NotifyKeyboardKeycode", "iu", 29, 0))
        self.assertEqual(self.session.state, "stopped")
        self.assertEqual(self.session.snapshot_status()["input_state"]["keycode_presses"], 1)

    def test_symbol_input_tracks_modifiers_without_exposing_symbols(self) -> None:
        observed = []
        with patch.object(native, "keysyms", return_value=[0xffe3, 0x61]) as keysyms, \
                patch.object(self.session, "hold", side_effect=lambda *args: observed.append(self.session.snapshot_status())):
            self.session.handle({**self.payload, "kind": "key", "codes": None, "key": "ctrl+a"})
        keysyms.assert_called_once_with("ctrl+a")
        self.assertEqual(observed[0]["input_state"]["modifiers"], ["ctrl"])
        self.assertEqual(observed[0]["input_state"]["keysym_presses"], 2)
        self.assertEqual(self.session.snapshot_status()["input_state"]["keysym_presses"], 0)
        self.assertNotIn("codes", observed[0]["input_state"])
        self.assertNotIn("keys", observed[0]["input_state"])

    def test_client_and_sequence_reject_physical_input_on_debian_before_request(self) -> None:
        sequence = self.home / "sequence.json"
        # A bad later step must be detected before the earlier valid action.
        sequence.write_text(json.dumps([{"action": "windows"}, {"action": "input", "kind": "keycode", "codes": [30]}]))
        with patch.object(client.runtime, "request") as request, patch.object(client.runtime, "status") as status:
            with self.assertRaisesRegex(ValueError, "native desktop"):
                client.send_action(self.payload)
            with self.assertRaisesRegex(ValueError, "native desktop"):
                client.run_sequence(str(sequence), "g")
        request.assert_not_called()
        status.assert_not_called()
        backend = Mock(MAX_MESSAGE=65536)
        sequence.write_text(json.dumps([{"action": "windows"}, {"action": "input", "kind": "keycode", "codes": [29, False]}]))
        with self.assertRaises(ValueError):
            client.run_sequence(str(sequence), "g", backend=backend)
        backend.status.assert_not_called()
        backend.request.assert_not_called()

    def test_cli_passes_physical_codes_and_hold_to_native_helper(self) -> None:
        parser = argparse.ArgumentParser()
        desktop_cli.add_desktop_subparser(parser.add_subparsers())
        args = parser.parse_args(["desktop", "--native", "input", "keycode", "--codes", "26", "27",
                                  "--generation", "g", "--geometry", "2560", "1440", "--hold-ms", "120"])
        with patch.object(native, "status", return_value={"state": "running", "generation": "g", "control_revision": "r"}), \
                patch.object(native, "request", return_value={"generation": "g"}) as request, redirect_stdout(StringIO()):
            self.assertEqual(desktop_cli.run_desktop_command(args), 0)
        self.assertEqual(request.call_args.args[0]["codes"], [26, 27])
        self.assertEqual(request.call_args.args[0]["hold_ms"], 120)


class InputReliabilityTests(unittest.TestCase):
    """Pure state/transport contracts with all platform operations mocked."""

    def setUp(self):
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.permission = {"control_revision": "r", "agent_control": "enabled"}
        self.enterContext(patch.object(control_state, "status", side_effect=lambda: dict(self.permission)))
        def enabled(payload):
            if (self.permission["agent_control"] != "enabled" or
                    payload.get("control_revision") != self.permission["control_revision"]):
                raise RuntimeError("control changed")
        self.enterContext(patch.object(control_state, "require_enabled", side_effect=enabled))
        self.session = native.NativeSession(Mock(fd=42))
        self.session.state = "running"
        self.session.node = 17
        self.session.geometry = [10, 20]
        self.session.captured = time.monotonic()
        self.payload = {"action": "input", "kind": "text", "text": "abc", "delay_ms": 10,
                        "generation": self.session.generation, "control_revision": "r", "geometry": [10, 20]}

    def test_deadline_validation_and_expired_actions_send_nothing(self):
        backend = Mock()
        for deadline in (True, "later", float("inf"), float("nan"), 10**1000, time.monotonic() - 1):
            with self.subTest(deadline=deadline), self.assertRaises((ValueError, RuntimeError)):
                client.send_action({"action": "windows"}, deadline=deadline, backend=backend)
        backend.request.assert_not_called()
        with patch.object(client.time, "monotonic", return_value=100):
            client.send_action({"action": "windows", "deadline": 102}, deadline=105, backend=backend)
        self.assertEqual(backend.request.call_args.args[0]["deadline"], 102)

    def test_negotiated_pacing_and_legacy_fallback_preserve_deadline(self):
        for capabilities, count in ((["server-paced-text"], 1), ([], 3)):
            backend = Mock()
            backend.status.return_value = {"input_capabilities": capabilities}
            backend.request.return_value = {"submitted_characters": 3}
            deadline = time.monotonic() + 2
            with self.subTest(capabilities=capabilities), patch.object(client.time, "sleep"):
                client.send_action(self.payload, deadline=deadline, backend=backend)
            self.assertEqual(backend.request.call_count, count)
            for request in backend.request.call_args_list:
                self.assertEqual(request.args[0]["deadline"], deadline)
                self.assertEqual(request.args[0].get("delay_ms"), 10 if count == 1 else None)
            backend.status.assert_called_once_with(deadline=deadline)

    def test_transport_failure_preserves_uncertainty_without_replay(self):
        backend = Mock()
        failure = TimeoutError("transport ended")
        failure.add_note("A prefix may have arrived")
        backend.request.side_effect = failure
        backend.status.return_value = {"input_capabilities": ["server-paced-text"]}
        result = client.send_action(self.payload, backend=backend)
        self.assertIsNone(result["submitted_characters"])
        self.assertTrue(result["completion_unknown"])
        self.assertIn("A prefix may have arrived", result["error"])
        self.assertIn("do not replay", result["error"])
        backend.request.assert_called_once()
        backend.reset_mock()
        with self.assertRaisesRegex(RuntimeError, "A prefix may have arrived"):
            client.send_action({**self.payload, "delay_ms": None}, backend=backend)
        backend.request.assert_called_once()

    def test_native_transport_keeps_absolute_deadline_across_socket_waits(self):
        import stat
        import struct
        from types import SimpleNamespace
        now = [100.0]
        with patch.object(native, "runtime_directory", return_value=Path("synthetic")), \
                patch.object(Path, "lstat", return_value=SimpleNamespace(st_mode=stat.S_IFSOCK | 0o600, st_uid=123)), \
                patch.object(native.os, "getuid", return_value=123, create=True), \
                patch.object(native.socket, "AF_UNIX", 1, create=True), \
                patch.object(native.socket, "SO_PEERCRED", 17, create=True), \
                patch.object(native.socket, "socket") as factory, \
                patch.object(native.time, "monotonic", side_effect=lambda: now[0]):
            connection = factory.return_value.__enter__.return_value
            connection.getsockopt.return_value = struct.pack("3i", 1, 123, 1)
            def advance(*args):
                now[0] += 1
            connection.connect.side_effect = advance
            connection.sendall.side_effect = advance
            chunks = iter([b'{"ok":', b'true}\n'])
            def receive(*args):
                advance()
                return next(chunks)
            connection.recv.side_effect = receive
            self.assertEqual(native.request({"action": "windows", "deadline": 105}), {"ok": True})
            self.assertEqual(json.loads(connection.sendall.call_args.args[0])["deadline"], 105)
            self.assertEqual([c.args[0] for c in connection.settimeout.call_args_list], [5, 4, 3, 2])
            factory.reset_mock()
            with self.assertRaisesRegex(RuntimeError, "expired"):
                native.request({"action": "input", "deadline": 103})
            factory.assert_not_called()

    def test_server_pacing_success_reports_released_characters(self):
        with patch.object(self.session, "hold", side_effect=lambda ms, p: self.session.guard(p)) as hold:
            result = self.session.handle(self.payload)
        self.assertEqual(result["submitted_characters"], 3)
        self.assertEqual([c.args[0] for c in hold.call_args_list], [0, 10, 0, 10, 0])
        self.assertEqual(self.session.injected_keysyms, set())
        self.assertIn("input_completed_at", result)

    def test_post_input_raw_capture_mismatch_never_compacts_or_replays(self):
        backend = Mock()
        backend.status.return_value = {"generation": "g", "control_revision": "r"}
        for changes in ({"generation": "new"}, {"control_revision": "new"}):
            backend.request.return_value = {"generation": "g", "control_revision": "r",
                                            "initial_frame_skipped": True, **changes}
            with patch("desktop.observations.compact_capture") as compact:
                result = client.observe_after_input({"input_completed_at": "done"},
                    {"generation": "g", "control_revision": "r"}, "unused.png", settle_ms=0, backend=backend)
            compact.assert_not_called()
            self.assertEqual(result["input_completed_at"], "done")
            self.assertIn("Do not replay", result["error"])
        self.assertEqual(backend.request.call_count, 2)

    def test_alias_duplicates_and_invalid_later_symbols_send_no_down(self):
        library = Mock()
        for symbols in ([0xffe3, 0xffe3], [0xffe3, 0]):
            library.xkb_keysym_from_name.side_effect = symbols
            with patch("ctypes.CDLL", return_value=library), self.assertRaises(ValueError):
                self.session.handle({**self.payload, "kind": "key", "text": None,
                                     "delay_ms": None, "key": "ctrl+Control_L"})
        self.session.portal.notify.assert_not_called()

    def test_server_pacing_checks_revocation_and_deadline_after_release(self):
        for reason in ("revision", "deadline", "stop", "disconnect"):
            payload = dict(self.payload)
            self.session.state = "running"
            self.session.stopping.clear()
            self.permission["control_revision"] = "r"
            self.session.control_revision = "r"
            self.session.captured = time.monotonic()
            self.session.portal.reset_mock()
            def hold(ms, current):
                if ms == 10:
                    if reason == "revision":
                        self.permission["control_revision"] = "new"
                    elif reason == "deadline":
                        current["deadline"] = time.monotonic() - 1
                    elif reason == "stop":
                        self.session.close()
                    else:
                        current["_cancelled"] = lambda: True
                self.session.guard(current)
            with self.subTest(reason=reason), patch.object(self.session, "hold", side_effect=hold), \
                    self.assertRaises(RuntimeError):
                self.session.handle(payload)
            self.assertEqual(self.session.portal.notify.call_args_list, [
                call("NotifyKeyboardKeysym", "iu", ord("a"), 1),
                call("NotifyKeyboardKeysym", "iu", ord("a"), 0)])

    def test_capture_releases_lock_and_discards_late_control_changes(self):
        from types import SimpleNamespace
        for interruption in ("revision", "stop", "deadline", "generation"):
            self.session.state = "running"
            self.session.stopping.clear()
            self.permission["control_revision"] = "r"
            self.session.control_revision = "r"
            entered, resume, finished = threading.Event(), threading.Event(), threading.Event()
            errors = []
            output = str(self.home / "synthetic-capture.png")
            capture_payload = {"action": "screenshot", "generation": self.session.generation,
                               "control_revision": "r", "output": output}
            def slow(*args, **kwargs):
                entered.set()
                if not resume.wait(3):
                    raise RuntimeError("test did not release capture")
                return SimpleNamespace(returncode=0, stdout=json.dumps({"geometry": [10, 20]}))
            def capture():
                try:
                    self.session.handle(capture_payload)
                except Exception as exc:
                    errors.append(exc)
                finally:
                    finished.set()
            import struct
            with patch.object(native.os, "open", return_value=99), \
                    patch.object(native.os, "close"), patch.object(native.os, "unlink") as unlink, \
                    patch.object(native.os, "pread", create=True, return_value=
                        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + struct.pack(">II", 10, 20)), \
                    patch.object(native.os, "O_NOFOLLOW", 0, create=True), \
                    patch("desktop.evidence.capture_digest", return_value="a" * 64), \
                    patch.object(native.subprocess, "run", side_effect=slow):
                worker = threading.Thread(target=capture)
                worker.start()
                try:
                    self.assertTrue(entered.wait(3), errors)
                    acquired = self.session.lock.acquire(timeout=1)
                    self.assertTrue(acquired, "capture blocked state lock")
                    if acquired:
                        self.session.lock.release()
                    self.session.handle({"action": "status"})
                    with self.assertRaisesRegex(RuntimeError, "already in progress"):
                        self.session.handle({"action": "screenshot", "generation": self.session.generation})
                    if interruption == "revision":
                        self.permission["control_revision"] = "new"
                        self.session.handle({"action": "status"})
                    elif interruption == "stop":
                        self.session.handle({"action": "stop"})
                    elif interruption == "deadline":
                        capture_payload["deadline"] = time.monotonic() - 1
                    else:
                        self.session.generation = "new-generation"
                    captured = self.session.captured
                finally:
                    resume.set()
                    worker.join(3)
                self.assertTrue(finished.is_set())
                self.assertEqual(len(errors), 1)
                self.assertIsInstance(errors[0], RuntimeError)
                self.assertEqual(self.session.captured, captured)
                unlink.assert_called_once_with(output)


if __name__ == "__main__":
    unittest.main()
