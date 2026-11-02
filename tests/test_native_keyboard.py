"""Physical keyboard injection contracts without a live portal or desktop."""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
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


if __name__ == "__main__":
    unittest.main()
