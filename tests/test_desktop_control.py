"""Persistent human permission and revocation tests with private temporary state."""

from __future__ import annotations

import asyncio
import argparse
from contextlib import redirect_stdout
import importlib
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
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

    def test_offline_revoke_migrates_legacy_pause_before_removing_grant(self):
        native_grants.enable()
        path = native_grants.directory() / "grant.json"
        path.write_text(json.dumps({**json.loads(path.read_text()), "paused": True}))
        folder = self.home / "runtime"
        folder.mkdir()
        with patch.object(native_session, "runtime_directory", return_value=folder), \
                patch.object(native_session, "request", side_effect=FileNotFoundError):
            native_session.revoke()
        self.assertIsNone(native_grants.load())
        self.assertEqual(control_state.status()["agent_control"], "human-override")
        self.assertIsNone(control_state.status()["override_until"])

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

    def test_display_wake_works_without_portal_and_preserves_permission(self):
        before = control_state.status()
        with patch.object(native_session, "check_session"), \
                patch.object(native_session, "request", side_effect=AssertionError("No portal needed")), \
                patch.object(native_session.subprocess, "run", return_value=Mock(returncode=0)) as command:
            result = native_session.wake()
        self.assertTrue(result["wake_requested"])
        self.assertEqual(control_state.status(), before)
        command.assert_called_once_with(["/usr/bin/kscreen-doctor", "--dpms", "on"],
                                        capture_output=True, text=True, timeout=10, check=False)

    def test_display_wake_honors_permission_session_failures_and_backend_errors(self):
        with patch.object(native_session, "check_session") as session, \
                patch.object(native_session.subprocess, "run", return_value=Mock(returncode=0)) as command:
            for mode in ("human-override", "disabled"):
                self.change(mode)
                with self.subTest(mode=mode), self.assertRaisesRegex(RuntimeError, mode):
                    native_session.wake()
                command.assert_not_called()
            self.change("enabled")
            session.side_effect = RuntimeError("Wrong graphical session")
            with self.assertRaisesRegex(RuntimeError, "graphical session"):
                native_session.wake()
            command.assert_not_called()
            session.side_effect = None
            command.return_value = Mock(returncode=1)
            with self.assertRaisesRegex(RuntimeError, "display wake failed"):
                native_session.wake()
            command.side_effect = FileNotFoundError
            with self.assertRaisesRegex(RuntimeError, "libkscreen"):
                native_session.wake()
            command.side_effect = lambda *args, **kwargs: (self.change("disabled"), Mock(returncode=0))[1]
            with self.assertRaisesRegex(RuntimeError, "disabled"):
                native_session.wake()

    def test_display_wake_cli_requires_native_and_does_not_start_helper(self):
        from lib import desktop_cli
        parser = argparse.ArgumentParser()
        desktop_cli.add_desktop_subparser(parser.add_subparsers())
        with patch.object(native_session, "wake", return_value={"wake_requested": True}) as wake, \
                patch.object(native_session, "start") as start, redirect_stdout(StringIO()):
            self.assertEqual(desktop_cli.run_desktop_command(parser.parse_args(["desktop", "--native", "wake", "--json"])), 0)
            wake.assert_called_once_with()
            self.assertEqual(desktop_cli.run_desktop_command(parser.parse_args(["desktop", "wake"])), 1)
            self.assertEqual(wake.call_count, 1)
            start.assert_not_called()

    def test_capture_failure_suggests_display_wake_and_removes_partial_artifact(self):
        session = native_session.NativeSession(Mock(fd=42))
        session.node = 17
        output = self.home / "failed-capture.png"
        with patch.object(native_session.subprocess, "run", return_value=Mock(
                returncode=1, stdout=json.dumps({"error": "No frame arrived"}))), \
                self.assertRaisesRegex(RuntimeError, "desktop --native wake"):
            session.capture({"output": str(output)})
        self.assertFalse(output.exists())

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

    def test_confined_gateway_can_inspect_stopped_backend_without_writing_home(self):
        from common.service_tools import guacamole_service
        with patch.object(session_runtime, "configuration", return_value={"desktop": "xfce", "username": "agent"}), \
                patch.object(session_runtime, "request", side_effect=FileNotFoundError), \
                patch.object(control_state, "status", side_effect=AssertionError("Gateway has no writable home")):
            result = asyncio.run(guacamole_service.desktop_call("status"))
        self.assertEqual(result["state"], "stopped")
        self.assertNotIn("agent_control", result)

    def test_start_does_not_need_caller_home_before_supervisor_is_running(self):
        with patch.object(session_runtime, "configuration", return_value={"desktop": "xfce", "username": "agent"}), \
                patch.object(session_runtime, "session_lock"), \
                patch.object(session_runtime, "request", side_effect=[FileNotFoundError, {"state": "running"}]), \
                patch.object(session_runtime.subprocess, "run", return_value=Mock(returncode=0)) as launch, \
                patch.object(control_state, "status", side_effect=AssertionError("Gateway has no writable home")):
            self.assertEqual(session_runtime.start()["state"], "running")
        launch.assert_called_once()

    def test_pointer_move_cannot_finish_with_click_after_human_intervention(self):
        process = Mock(pid=1)
        process.poll.return_value = None
        session = session_runtime.DesktopSession({"desktop": "xfce", "username": "agent"}, process)
        current = control_state.status()

        def move(argv):
            if "mousemove" in argv:
                self.change("disabled")
                return "X=20\nY=30"
            return ""

        with patch.object(session_runtime, "geometry", return_value=[800, 600]), patch.object(
                session_runtime, "run_tool", side_effect=move) as tool:
            with self.assertRaisesRegex(RuntimeError, "disabled"):
                session.handle({"action": "input", "generation": session.generation,
                                "geometry": [800, 600], "kind": "click", "x": 20, "y": 30, **current})
        self.assertFalse(any("click" in call.args[0] for call in tool.call_args_list))

    def test_unpaced_native_text_yields_to_human_disable_and_releases_key(self):
        portal = Mock()
        session = native_session.NativeSession(portal)
        session.state = "running"
        session.geometry = [800, 600]
        session.captured = time.monotonic()
        completed = threading.Event()

        def interrupt(_seconds):
            def human():
                session.handle({"action": "control", "mode": "disabled", "human_approved": True})
                completed.set()
            worker = threading.Thread(target=human)
            worker.start()
            self.assertTrue(completed.wait(2), "Human request could not acquire the session lock")
            worker.join(2)

        payload = {"action": "input", "generation": session.generation, "geometry": session.geometry,
                   "kind": "text", "text": "abc", **control_state.status()}
        with patch.object(native_session.time, "sleep", side_effect=interrupt):
            with self.assertRaisesRegex(RuntimeError, "disabled"):
                session.handle(payload)
        keys = [call.args[-2:] for call in portal.notify.call_args_list]
        self.assertEqual(keys, [(ord("a"), 1), (ord("a"), 0)])
        self.assertTrue(completed.is_set())

    def test_native_pointer_and_chord_events_recheck_permission_between_notifications(self):
        for kind, button in (("click", 1), ("click", 4), ("key", None)):
            with self.subTest(kind=kind, button=button):
                self.change("enabled")
                portal = Mock()
                session = native_session.NativeSession(portal)
                session.state = "running"
                session.node = 17
                session.logical_size = [800, 600]
                session.geometry = [800, 600]
                session.captured = time.monotonic()

                def interrupt(event, *args):
                    if event == "NotifyPointerMotionAbsolute" or (event == "NotifyKeyboardKeysym" and args[-1] == 1):
                        self.change("disabled")

                portal.notify.side_effect = interrupt
                payload = {"action": "input", "generation": session.generation, "geometry": session.geometry,
                           "kind": kind, "x": 20, "y": 30, "key": "ctrl+a", **control_state.status()}
                if button is not None:
                    payload["button"] = button
                with patch.object(native_session, "keysyms", return_value=[65507, 97]):
                    with self.assertRaisesRegex(RuntimeError, "disabled"):
                        session.handle(payload)
                events = [call.args for call in portal.notify.call_args_list]
                if kind == "click":
                    self.assertEqual([args[0] for args in events], ["NotifyPointerMotionAbsolute"])
                else:
                    self.assertEqual([args[-2:] for args in events], [(65507, 1), (65507, 0)])

    def test_first_permission_write_syncs_ancestor_entries_before_restart(self):
        synced = []
        real_sync = os.fsync

        def sync(descriptor):
            synced.append(Path(os.readlink(f"/proc/self/fd/{descriptor}")))
            real_sync(descriptor)

        with patch.object(control_state.os, "fsync", side_effect=sync):
            self.change("disabled")
        for ancestor in (self.home, self.home / ".local", self.home / ".local/state",
                         self.home / ".local/state/basaltwater"):
            self.assertIn(ancestor, synced)
        self.assertEqual(control_state.status()["agent_control"], "disabled")

    def test_external_state_changes_invalidate_native_capture_and_element_references(self):
        session = native_session.NativeSession(Mock())
        session.elements["old"] = {}
        session.captured = time.monotonic()
        self.change("human-override")
        self.change("enabled")
        session.sync_control()
        self.assertEqual(session.elements, {})
        self.assertEqual(session.captured, 0)

    def test_cachyos_menu_control_installs_without_starting_or_changing_permission(self):
        from common import cachyos_steps
        current = self.change("disabled")
        config = SimpleNamespace(dry_run=False)
        with patch.object(cachyos_steps, "_home", return_value=self.home), patch.object(cachyos_steps, "run") as run:
            cachyos_steps.configure_cachyos_desktop_control(config)
            path = self.home / ".local/share/applications/basaltwater-desktop-control.desktop"
            first = path.read_text()
            self.assertIn("Exec=/usr/bin/python3 -m desktop.native_handoff", first)
            self.assertNotIn("--human-approved", first)
            cachyos_steps.configure_cachyos_desktop_control(config)
            self.assertEqual(path.read_text(), first)
            run.assert_not_called()
        self.assertEqual(control_state.status(), current)
        self.assertFalse((self.home / ".config/autostart").exists())
        with patch.object(cachyos_steps, "_home") as home:
            cachyos_steps.configure_cachyos_desktop_control(SimpleNamespace(dry_run=True))
            home.assert_not_called()


if __name__ == "__main__":
    unittest.main()
