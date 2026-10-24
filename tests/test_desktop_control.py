"""Persistent human permission and revocation tests with private temporary state."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from desktop import control_state, native_grants, native_session, session_runtime


class ControlStateTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(Path, "home", return_value=self.home))
        self.clock = self.enterContext(patch.object(control_state, "time", Mock(time=Mock(return_value=1000))))

    def change(self, mode, **options):
        return control_state.change(mode, human_approved=True, **options)

    def test_enabled_is_persistent_and_does_not_require_a_lease(self):
        current = control_state.status()
        self.assertEqual(current["agent_control"], "enabled")
        self.assertEqual(importlib.reload(control_state).status()["control_revision"], current["control_revision"])
        control_state.require_enabled(current)
        path = self.home / ".local/state/basaltwater/desktop-control/state.json"
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)

    def test_timed_override_uses_original_deadline_across_restarts(self):
        original = self.change("human-override")
        self.assertEqual(original["override_until"], 1900)
        self.clock.time.return_value = 1500
        self.assertEqual(control_state.status()["override_remaining"], 400)
        self.assertEqual(control_state.status()["override_until"], original["override_until"])
        with self.assertRaisesRegex(RuntimeError, "human-override"):
            control_state.require_enabled(original)
        self.clock.time.return_value = 1900
        after = control_state.status()
        self.assertEqual(after["agent_control"], "enabled")
        self.assertNotEqual(after["control_revision"], original["control_revision"])

    def test_indefinite_and_disabled_do_not_expire_or_allow_unapproved_changes(self):
        for mode in ("human-override", "disabled"):
            with self.subTest(mode=mode):
                current = self.change(mode, seconds=None)
                self.clock.time.return_value = 9999999
                self.assertEqual(control_state.status()["agent_control"], mode)
                self.assertIsNone(current["override_until"])
                for destination in control_state.MODES:
                    with self.assertRaisesRegex(PermissionError, "explicit human approval"):
                        control_state.change(destination)
                with self.assertRaises(RuntimeError):
                    control_state.require_enabled(current)

    def test_takeover_and_handback_invalidates_old_actions(self):
        before = control_state.status()
        self.change("human-override")
        self.change("enabled")
        with self.assertRaisesRegex(ValueError, "changed"):
            control_state.require_enabled(before)
        control_state.require_enabled(control_state.status())

    def test_legacy_pause_is_migrated_once_as_indefinite_human_override(self):
        native_grants.enable()
        path = native_grants.directory() / "grant.json"
        data = json.loads(path.read_text())
        path.write_text(json.dumps({**data, "paused": True}))
        current = control_state.status()
        self.assertEqual(current["agent_control"], "human-override")
        self.assertIsNone(current["override_until"])
        self.change("enabled")
        self.assertEqual(control_state.status()["agent_control"], "enabled")

    def test_invalid_duration_state_and_unsafe_storage_are_rejected(self):
        for seconds in (True, 0, 59, 86401, 1.5, "900"):
            with self.subTest(seconds=seconds), self.assertRaises(ValueError):
                self.change("human-override", seconds=seconds)
        with self.assertRaises(ValueError):
            self.change("paused")
        control_state.status()
        path = self.home / ".local/state/basaltwater/desktop-control/state.json"
        original = path.read_text()
        for key, value in (("agent_control", "unexpected"), ("control_revision", []),
                           ("override_until", 42), ("version", True)):
            path.write_text(json.dumps({**json.loads(original), key: value}))
            with self.subTest(key=key), self.assertRaises(ValueError):
                control_state.status()
        path.write_text(original)
        path.chmod(0o644)
        with self.assertRaises(RuntimeError):
            control_state.status()
        path.chmod(0o600)
        path.unlink()
        path.symlink_to(self.home / "outside")
        with self.assertRaises(RuntimeError):
            control_state.status()

    def test_native_stop_and_recreation_preserve_each_permission_state(self):
        for mode in control_state.MODES:
            with self.subTest(mode=mode):
                self.change(mode, seconds=None)
                session = native_session.NativeSession(Mock())
                session.state = "running"
                self.assertIsNone(session.snapshot_status()["expires_in"])
                session.close()
                self.assertEqual(session.snapshot_status()["agent_control"], mode)
                self.assertEqual(native_session.NativeSession(Mock()).snapshot_status()["agent_control"], mode)

    def test_runtime_actions_need_enabled_state_and_current_revision(self):
        process = Mock(pid=1)
        process.poll.return_value = None
        session = session_runtime.DesktopSession({"desktop": "xfce", "username": "agent"}, process)
        payload = {"action": "exec", "generation": session.generation, "argv": ["editor"],
                   **control_state.status()}
        with patch.object(session_runtime.subprocess, "Popen", return_value=Mock(pid=42)) as spawn:
            self.assertEqual(session.handle(payload)["pid"], 42)
            self.change("disabled")
            with self.assertRaisesRegex(RuntimeError, "disabled"):
                session.handle(payload)
            self.change("enabled")
            with self.assertRaisesRegex(ValueError, "changed"):
                session.handle(payload)
            self.assertEqual(spawn.call_count, 1)

    def test_setup_logout_blocks_input_without_changing_permission(self):
        current = self.change("disabled")
        process = Mock(pid=1)
        process.poll.return_value = None
        session = session_runtime.DesktopSession({"desktop": "xfce", "username": "agent"}, process)
        with patch.object(session, "logout", return_value={"logout_requested": True}) as logout:
            session.handle({"action": "maintenance-logout", "generation": session.generation})
        logout.assert_called_once()
        self.assertTrue(session.maintenance)
        self.assertEqual(control_state.status(), current)


if __name__ == "__main__":
    unittest.main()
