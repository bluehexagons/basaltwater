"""Behavioral tests for shared desktop control without host mutations."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from desktop import session_runtime as runtime
from desktop import client
from desktop import setup_logout
from desktop.session_steps import assert_desktop_idle, configure_session_service, install_session_runtime, prepare_shared_desktop
from lib.config import SetupConfig
from lib.arg_parser import create_setup_argument_parser
from lib.desktop_cli import add_desktop_subparser, run_desktop_command
from lib.setup_report import SetupReport


class DesktopControlTests(unittest.TestCase):
    def setUp(self):
        ready = patch.object(runtime, "window_manager_ready", return_value=True)
        ready.start()
        self.addCleanup(ready.stop)
        self.geometry = patch.object(runtime, "geometry", return_value=[1280, 720])
        self.geometry.start()
        self.addCleanup(self.geometry.stop)
        self.environment = patch.dict("os.environ", {"DISPLAY": ":10"})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.process = Mock(pid=54321)
        self.process.poll.return_value = None
        self.session = runtime.DesktopSession({"desktop": "xfce", "username": "agent"}, self.process)

    def acquire(self):
        return self.session.handle({"action": "acquire", "generation": self.session.generation})

    def test_human_takeover_revokes_lease_but_keeps_observation(self):
        lease = self.acquire()
        self.session.handle({"action": "pause"})
        with self.assertRaisesRegex(RuntimeError, "paused"):
            self.session.handle({"action": "exec", **lease, "argv": ["editor"]})
        self.assertEqual(self.session.handle({"action": "status"})["state"], "running")
        self.session.handle({"action": "resume"})
        with self.assertRaisesRegex(ValueError, "lease"):
            self.session.handle({"action": "exec", **lease, "argv": ["editor"]})

    def test_second_agent_cannot_interleave_control(self):
        lease = self.acquire()
        with self.assertRaisesRegex(RuntimeError, "leased"):
            self.acquire()
        self.session.handle({"action": "release", **lease})
        self.assertNotEqual(self.acquire()["lease"], lease["lease"])

    @patch.object(runtime, "run_tool")
    def test_stale_session_and_geometry_never_send_input(self, tool):
        lease = self.acquire()
        payload = {"action": "input", **lease, "geometry": [800, 600], "kind": "click", "x": 20, "y": 30}
        with self.assertRaisesRegex(ValueError, "geometry changed"):
            self.session.handle(payload)
        payload["generation"] = "old-session"
        with self.assertRaisesRegex(ValueError, "session changed"):
            self.session.handle(payload)
        tool.assert_not_called()

    @patch.object(runtime.subprocess, "Popen")
    def test_application_launch_uses_argv_and_desktop_process_group(self, popen):
        popen.return_value.pid = 42
        result = self.session.handle({"action": "exec", **self.acquire(), "argv": ["editor", "$(not-a-shell)"]})
        self.assertEqual(result["pid"], 42)
        self.assertEqual(popen.call_args.args[0], ["editor", "$(not-a-shell)"])
        self.assertEqual(popen.call_args.kwargs["process_group"], self.process.pid)

    @patch.object(runtime.subprocess, "Popen")
    def test_application_launch_preserves_caller_directory(self, popen):
        with tempfile.TemporaryDirectory() as directory:
            self.session.handle({"action": "exec", **self.acquire(),
                                 "argv": ["editor", "relative.txt"], "cwd": directory})
            self.assertEqual(popen.call_args.kwargs["cwd"], directory)

    @patch.object(runtime.subprocess, "Popen")
    def test_launch_exit_status_remains_observable_during_pause(self, popen):
        popen.return_value.pid = 42
        popen.return_value.poll.return_value = 7
        result = self.session.handle({"action": "exec", **self.acquire(), "argv": ["editor"]})
        self.session.handle({"action": "pause"})
        observed = self.session.handle({"action": "launch-status", "generation": self.session.generation,
                                        "launch": result["launch"]})
        self.assertEqual(observed["returncode"], 7)
        self.assertEqual(observed["state"], "exited")

    @patch.object(runtime, "window_ids", side_effect=[["0x123"], []])
    @patch.object(runtime, "window_details", side_effect=RuntimeError("inspection failed"))
    def test_failed_window_inspection_marks_inventory_incomplete(self, details, ids):
        observed = self.session.handle({"action": "windows", "generation": self.session.generation})
        self.assertTrue(observed["truncated"])
        self.assertEqual(observed["windows"], [])

    @patch.object(runtime.subprocess, "Popen")
    def test_invalid_application_directory_does_not_launch(self, popen):
        lease = self.acquire()
        for cwd in ("relative", 12):
            with self.subTest(cwd=cwd), self.assertRaisesRegex(ValueError, "absolute directory"):
                self.session.handle({"action": "exec", **lease, "argv": ["editor"], "cwd": cwd})
        popen.assert_not_called()

    def test_expired_lease_is_rejected(self):
        lease = self.acquire()
        self.session.lease_until = 0
        with self.assertRaisesRegex(ValueError, "lease"):
            self.session.handle({"action": "exec", **lease, "argv": ["editor"]})

    @patch.object(runtime, "window_manager_ready", return_value=False)
    def test_starting_desktop_is_not_reported_ready(self, ready):
        self.assertEqual(self.session.snapshot_status()["state"], "starting")

    @patch.object(runtime, "geometry", side_effect=RuntimeError("display initializing"))
    def test_temporary_geometry_failure_keeps_status_available(self, geometry):
        result = self.session.snapshot_status()
        self.assertEqual(result["state"], "starting")
        self.assertIsNone(result["geometry"])
        self.assertEqual(result["detail"], "display initializing")
        self.assertTrue(self.session.handle({"action": "pause"})["paused"])

    @patch.object(runtime, "geometry")
    def test_stopping_status_does_not_query_dead_display(self, geometry):
        self.process.poll.return_value = 0
        self.assertEqual(self.session.snapshot_status()["state"], "stopping")
        geometry.assert_not_called()

    @patch.object(runtime, "run_tool")
    def test_invalid_click_does_not_even_move_pointer(self, tool):
        with self.assertRaisesRegex(ValueError, "Button"):
            self.session.handle({"action": "input", **self.acquire(), "geometry": [1280, 720],
                                 "kind": "click", "x": 10, "y": 10, "button": 9})
        tool.assert_not_called()

    @patch.object(runtime, "run_tool")
    def test_malformed_input_kind_is_rejected_without_losing_session(self, tool):
        lease = self.acquire()
        for kind in ([], {}, None):
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, "input kind"):
                self.session.handle({"action": "input", **lease,
                                     "geometry": [1280, 720], "kind": kind})
        self.assertEqual(self.session.snapshot_status()["state"], "running")
        tool.assert_not_called()

    @patch.object(runtime, "run_tool")
    def test_screenshot_reports_captured_pixels_and_private_permissions(self, tool):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "screen.png"
            tool.side_effect = lambda _: path.write_bytes(
                b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", 1280, 720))
            result = self.session.handle({"action": "screenshot", "generation": self.session.generation,
                                          "output": str(path)})
            self.assertEqual(result["geometry"], [1280, 720])
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    @patch.object(runtime, "window_ids", return_value=["0x123"])
    @patch.object(runtime, "window_details")
    @patch.object(runtime, "run_tool")
    def test_window_capture_preserves_desktop_coordinates_and_does_not_focus(self, tool, details, ids):
        details.return_value = {"id": "0x123", "title": "Editor", "origin": [10, 20],
                                "geometry": [640, 480], "visible": True}
        self.session.paused = True
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "app.png"
            tool.side_effect = lambda argv: path.write_bytes(
                b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", 640, 480))
            result = self.session.handle({"action": "screenshot", "generation": self.session.generation,
                                          "window": "291", "output": str(path)})
            self.assertEqual(result["geometry"], [1280, 720])
            self.assertEqual(result["image_geometry"], [640, 480])
            self.assertEqual(result["window"]["origin"], [10, 20])
            tool.assert_called_once_with(["scrot", "--overwrite", "--window", "0x123", str(path)])

    @patch.object(runtime, "window_ids", return_value=["0x123"])
    @patch.object(runtime, "window_details", return_value={"visible": False})
    @patch.object(runtime, "run_tool")
    def test_hidden_or_stale_window_never_captures_desktop_as_fallback(self, tool, details, ids):
        for window, message in (("0x123", "hidden"), ("0x456", "no longer"), ("--exec", "Window ID")):
            with self.subTest(window=window), self.assertRaisesRegex(ValueError, message):
                self.session.handle({"action": "screenshot", "generation": self.session.generation,
                                     "window": window, "output": "/tmp/unused.png"})
        tool.assert_not_called()

    @patch.object(runtime, "window_ids", side_effect=[["0x123"], ["0x123"]])
    @patch.object(runtime, "window_details", return_value={"id": "0x123", "visible": True})
    def test_active_window_resolves_to_a_specific_managed_window(self, details, ids):
        self.assertEqual(runtime.capture_window({"active_window": True})["id"], "0x123")
        self.assertEqual(ids.call_args_list[0].args, ("_NET_ACTIVE_WINDOW",))

    @patch.object(runtime, "run_tool", return_value='''
xwininfo: Window id: 0x123 "Editor"
  Absolute upper-left X:  -10
  Absolute upper-left Y:  20
  Width: 640
  Height: 480
  Map State: IsViewable
''')
    def test_window_inspection_parses_client_coordinates(self, tool):
        result = runtime.window_details("0x123")
        self.assertEqual(result["origin"], [-10, 20])
        self.assertEqual(result["geometry"], [640, 480])
        self.assertEqual(result["title"], "Editor")
        self.assertTrue(result["visible"])

    @patch.object(runtime, "capture_window")
    @patch.object(runtime, "window_details")
    @patch.object(runtime, "run_tool")
    def test_window_move_during_capture_discards_image(self, tool, details, selected):
        selected.return_value = {"id": "0x123", "origin": [10, 20], "geometry": [640, 480], "visible": True}
        details.return_value = {**selected.return_value, "origin": [30, 40]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "moving.png"
            tool.side_effect = lambda argv: path.write_bytes(
                b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", 640, 480))
            with self.assertRaisesRegex(RuntimeError, "Window changed"):
                self.session.handle({"action": "screenshot", "generation": self.session.generation,
                                     "window": "0x123", "output": str(path)})
            self.assertFalse(path.exists())

    @patch.object(runtime, "geometry", side_effect=[[1280, 720], [800, 600]])
    @patch.object(runtime, "run_tool")
    def test_desktop_resize_during_final_status_discards_image(self, tool, geometry):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "resizing.png"
            tool.side_effect = lambda argv: path.write_bytes(
                b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", 1280, 720))
            with self.assertRaisesRegex(RuntimeError, "Desktop resized"):
                self.session.handle({"action": "screenshot", "generation": self.session.generation,
                                     "output": str(path)})
            self.assertFalse(path.exists())

    @patch.object(runtime, "run_tool")
    def test_screenshot_rejects_scrot_filename_expansion(self, tool):
        with self.assertRaisesRegex(ValueError, "format characters"):
            self.session.handle({"action": "screenshot", "generation": self.session.generation,
                                 "output": "/tmp/$w.png"})
        tool.assert_not_called()

    @patch.object(runtime, "run_tool")
    def test_screenshot_refuses_overwrite_and_cleans_failed_capture(self, tool):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "screen.png"
            path.write_text("existing")
            payload = {"action": "screenshot", "generation": self.session.generation, "output": str(path)}
            with self.assertRaises(FileExistsError):
                self.session.handle(payload)
            self.assertEqual(path.read_text(), "existing")
            path.unlink()
            tool.side_effect = RuntimeError("capture failed")
            with self.assertRaisesRegex(RuntimeError, "capture failed"):
                self.session.handle(payload)
            self.assertFalse(path.exists())

    @patch.object(runtime.subprocess, "Popen")
    def test_logout_requests_desktop_logout_without_killing_other_services(self, popen):
        result = self.session.handle({"action": "logout", **self.acquire()})
        self.assertTrue(result["logout_requested"])
        popen.assert_called_once_with(["xfce4-session-logout", "--logout"], process_group=self.process.pid)
        self.process.terminate.assert_not_called()


class DesktopProductivityTests(unittest.TestCase):
    @patch.object(client, "check_dependencies", return_value={"available": True})
    @patch.object(runtime, "status", return_value={"state": "stopped"})
    @patch.object(runtime, "runtime_directory", return_value=Path("/private/runtime"))
    @patch.object(client.shutil, "which", return_value=None)
    @patch.object(client.importlib.util, "find_spec", return_value=None)
    @patch.object(client.subprocess, "run", return_value=Mock(stdout="active\n"))
    @patch.object(runtime, "start")
    def test_doctor_reports_missing_tools_without_starting_or_repairing(self, start, run, spec, which, directory, status, bindings):
        result = client.doctor()
        self.assertFalse(result["healthy"])
        self.assertIn("python3-tk", result["suggestions"][0])
        self.assertEqual([call.args[0] for call in run.call_args_list],
                         [["systemctl", "is-active", "xrdp"], ["systemctl", "is-active", "xrdp-sesman"]])
        start.assert_not_called()

    @patch.object(runtime, "window_ids", return_value=["0x123"])
    @patch.object(runtime, "window_details", return_value={"identity": "fresh", "system_window": False})
    @patch.object(runtime, "run_tool")
    def test_window_changes_reject_stale_identity_and_use_normal_close(self, tool, details, ids):
        payload = {"window": "0x123", "identity": "stale", "operation": "close"}
        with self.assertRaisesRegex(ValueError, "identity changed"):
            runtime.change_window(payload)
        tool.assert_not_called()
        payload["identity"] = "fresh"
        self.assertEqual(runtime.change_window(payload)["requested"], "close")
        tool.assert_called_once_with(["wmctrl", "-ic", "0x123"])
        tool.reset_mock()
        details.return_value["system_window"] = True
        with self.assertRaisesRegex(ValueError, "panel"):
            runtime.change_window(payload)
        tool.assert_not_called()

    @patch.object(runtime, "window_ids", return_value=["0x123"])
    @patch.object(runtime, "window_details", return_value={"identity": "fresh", "system_window": False})
    @patch.object(runtime, "geometry", return_value=[1280, 720])
    @patch.object(runtime, "run_tool")
    def test_invalid_placement_never_moves_window(self, tool, geometry, details, ids):
        for x in (-1, True, 1280):
            with self.subTest(x=x), self.assertRaisesRegex(ValueError, "outside"):
                runtime.change_window({"window": "0x123", "identity": "fresh", "operation": "move", "x": x, "y": 0})
        tool.assert_not_called()

    @patch.object(client.time, "sleep")
    @patch.object(runtime, "request", side_effect=[
        {"generation": "g", "windows": [], "truncated": True},
        {"generation": "g", "windows": [], "truncated": False}])
    def test_absence_wait_requires_complete_inventory_without_control(self, request, sleep):
        self.assertEqual(client.wait_for_window("g", window="0x123", condition="absent")["windows"], [])
        self.assertEqual([call.args[0]["action"] for call in request.call_args_list], ["windows", "windows"])
        sleep.assert_called_once()

    @patch.object(runtime, "request", return_value={"generation": "new", "windows": []})
    def test_wait_aborts_when_session_changes(self, request):
        with self.assertRaisesRegex(RuntimeError, "session changed"):
            client.wait_for_window("old", title="Editor")

    @patch.object(runtime, "request", return_value={"returncode": 7})
    def test_failed_launch_is_reported_without_relaunching(self, request):
        with self.assertRaisesRegex(RuntimeError, "code 7"):
            client.wait_for_window("g", title="Editor", launch="launch")
        request.assert_called_once_with({"action": "launch-status", "generation": "g", "launch": "launch"})

    @patch.object(client.time, "monotonic", side_effect=[0, 2])
    @patch.object(runtime, "request", return_value={"generation": "g", "windows": [{"id": "0x123", "title": "unsaved"}]})
    def test_canceled_close_times_out_instead_of_claiming_success(self, request, clock):
        with self.assertRaisesRegex(RuntimeError, "Timed out"):
            client.wait_for_window("g", window="0x123", condition="absent", timeout=1)

    @patch.object(runtime, "request", side_effect=[{"lease": "l"}, {"ok": True}, RuntimeError("paused"), {}])
    def test_sequence_releases_control_and_reports_partial_completion(self, request):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "steps.json"
            path.write_text(json.dumps([{"action": "windows"}, {"action": "input"}]))
            result = client.run_sequence(str(path), "g")
        self.assertEqual(result["completed"], 1)
        self.assertEqual(result["error"], "paused")
        self.assertEqual(request.call_args.args[0], {"action": "release", "generation": "g", "lease": "l"})

    @patch.object(runtime, "request")
    def test_sequence_rejects_embedded_control_before_acquiring(self, request):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "steps.json"
            path.write_text('[{"action":"input","lease":"other"}]')
            with self.assertRaisesRegex(ValueError, "embedded"):
                client.run_sequence(str(path), "g")
        request.assert_not_called()

    def test_capture_artifact_paths_are_private_unique_and_reject_symlinks(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(client.Path, "home", return_value=Path(directory)):
            first, second = Path(client.artifact_path()), Path(client.artifact_path())
            self.assertNotEqual(first, second)
            self.assertEqual(first.parent.stat().st_mode & 0o777, 0o700)
            first.parent.rmdir()
            first.parent.symlink_to(Path(directory), target_is_directory=True)
            with self.assertRaisesRegex(RuntimeError, "owned by you"):
                client.artifact_path()

    @patch.object(runtime, "status", return_value={"state": "running", "generation": "g"})
    @patch.object(runtime, "request")
    def test_invalid_launch_wait_does_not_start_an_application(self, request, status):
        parser = argparse.ArgumentParser()
        add_desktop_subparser(parser.add_subparsers())
        for title in ("", "x" * 513):
            args = parser.parse_args(["desktop", "exec", "--wait-window", title, "--", "editor"])
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(run_desktop_command(args), 1)
        request.assert_not_called()


class DesktopStartTests(unittest.TestCase):
    @patch.object(runtime, "configuration")
    @patch.object(runtime, "session_lock")
    @patch.object(runtime, "status", side_effect=[{"state": "stopped"}, {"state": "running"}])
    @patch.object(runtime.subprocess, "run", return_value=Mock(returncode=1))
    def test_failed_start_request_joins_a_racing_human_login(self, run, status, lock, config):
        self.assertEqual(runtime.start()["state"], "running")
        self.assertEqual(run.call_count, 1)

    @patch.object(runtime, "configuration")
    @patch.object(runtime, "session_lock")
    @patch.object(runtime, "status", side_effect=[{"state": "stopped"}, {"state": "running"}])
    @patch.object(runtime.subprocess, "run", side_effect=subprocess.TimeoutExpired("sesrun", 30))
    def test_timed_out_request_can_still_have_started_the_session(self, run, status, lock, config):
        self.assertEqual(runtime.start()["state"], "running")
        self.assertEqual(run.call_count, 1)

    @patch.object(runtime, "configuration")
    @patch.object(runtime, "session_lock")
    @patch.object(runtime, "status", return_value={"state": "stopped"})
    @patch.object(runtime.subprocess, "run", return_value=Mock(returncode=1))
    @patch.object(runtime.time, "monotonic", side_effect=[0, 31])
    def test_failed_start_is_bounded_and_does_not_repeat_login(self, clock, run, status, lock, config):
        with self.assertRaisesRegex(RuntimeError, "startup request failed"):
            runtime.start()
        self.assertEqual(run.call_count, 1)

    @patch.object(runtime, "configuration")
    @patch.object(runtime, "session_lock")
    @patch.object(runtime, "status", side_effect=[{"state": "starting"}, {"state": "running"}])
    @patch.object(runtime.subprocess, "run")
    def test_join_human_start_without_a_second_start_request(self, run, status, lock, config):
        self.assertEqual(runtime.start()["state"], "running")
        run.assert_not_called()

    @patch.object(runtime, "configuration")
    @patch.object(runtime, "session_lock")
    @patch.object(runtime, "status", return_value={"state": "running", "generation": "same"})
    @patch.object(runtime.subprocess, "run")
    def test_repeated_start_reuses_running_session(self, run, status, lock, config):
        self.assertEqual(runtime.start()["generation"], "same")
        run.assert_not_called()

    @patch.object(runtime, "configuration")
    @patch.object(runtime, "session_lock")
    @patch.object(runtime, "status", side_effect=[{"state": "stopped"}, {"state": "running"}])
    @patch.object(runtime.subprocess, "run", return_value=Mock(returncode=0))
    def test_start_authenticates_as_current_uid_without_password(self, run, status, lock, config):
        self.assertEqual(runtime.start()["state"], "running")
        self.assertEqual(run.call_args.args[0], ["xrdp-sesrun", "-t", "Xorg", "-g", "1280x720", "-b", "32"])


class DesktopMigrationTests(unittest.TestCase):
    def setUp(self):
        logout = patch("desktop.session_steps.logout_managed_desktop", return_value=False)
        self.logout = logout.start()
        self.addCleanup(logout.stop)
        capability = patch("desktop.session_steps.can_manage_system_services", return_value=True)
        self.capability = capability.start()
        self.addCleanup(capability.stop)

    @patch("desktop.session_steps.is_dry_run", return_value=False)
    @patch("desktop.session_steps.time.sleep")
    @patch("desktop.session_steps._assert_no_graphical_sessions", side_effect=[RuntimeError("teardown"), None])
    def test_setup_waits_for_logind_after_managed_logout(self, idle, sleep, dry):
        self.logout.return_value = True
        assert_desktop_idle(SetupConfig(host="vm", username="agent", system_type="agent_vm"))
        self.logout.assert_called_once()
        self.assertEqual(idle.call_count, 2)

    @patch("desktop.session_steps.is_dry_run", return_value=True)
    @patch("desktop.session_steps.run")
    def test_dry_run_never_logs_out(self, run, dry):
        assert_desktop_idle(SetupConfig(host="vm", username="agent", system_type="agent_vm"))
        self.logout.assert_not_called()
        run.assert_not_called()

    @patch("common.agent_steps.install_managed_agent_skills")
    @patch("common.agent_steps.install_agent_cli_launcher")
    @patch("desktop.session_steps.pwd.getpwnam", return_value=Mock(pw_uid=1000))
    @patch("desktop.session_steps.assert_desktop_idle")
    @patch("desktop.session_steps.is_dry_run", return_value=False)
    @patch("desktop.session_steps.run")
    def test_install_is_repeatable_and_backs_up_prior_owner_declaration(self, run, dry, idle, account, cli, skills):
        with tempfile.TemporaryDirectory() as directory:
            declaration = Path(directory) / "desktop.json"
            wrapper = Path(directory) / "startwm.sh"
            config = SetupConfig(host="vm", username="agent", system_type="agent_vm", agent_tools=["codex"])
            with patch("desktop.session_steps.CONFIG_PATH", declaration), patch("desktop.session_steps.STARTWM", str(wrapper)), \
                    patch("desktop.session_steps.configure_session_service"), \
                    patch("desktop.session_steps.install_package", return_value=True), \
                    patch("desktop.session_steps.HANDOFF_LAUNCHER", str(Path(directory) / "control")), \
                    patch("desktop.session_steps.HANDOFF_ENTRY", str(Path(directory) / "control.desktop")):
                install_session_runtime(config)
                first = declaration.read_text()
                self.assertIn("export XDG_CURRENT_DESKTOP=XFCE\n", wrapper.read_text())
                self.assertIn("export XDG_MENU_PREFIX=xfce-\n", wrapper.read_text())
                config.desktop = "i3"
                install_session_runtime(config)
            self.assertEqual(json.loads(declaration.read_text())["desktop"], "i3")
            self.assertEqual(declaration.with_suffix(".json.bak").read_text(), first)
            self.assertEqual(wrapper.stat().st_mode & 0o777, 0o755)
            self.assertIn("dbus-run-session", wrapper.read_text())
            self.assertIn("export XDG_CURRENT_DESKTOP=i3\n", wrapper.read_text())
            self.assertNotIn("XDG_MENU_PREFIX=xfce-", wrapper.read_text())
            self.assertIn(["gpasswd", "-M", "agent", "basaltwater-desktop"], [call.args[0] for call in run.call_args_list])
            self.assertEqual(skills.call_args.args, ("agent", ["codex"], ("basaltwater-desktop",)))

    @patch("desktop.session_steps.assert_desktop_idle")
    @patch("desktop.session_steps.is_dry_run", return_value=False)
    @patch("desktop.session_steps.run")
    def test_already_masked_console_is_safe_to_reconcile(self, run, dry, idle):
        with tempfile.TemporaryDirectory() as directory:
            alias = Path(directory) / "display-manager.service"
            alias.symlink_to("/dev/null")
            with patch("desktop.session_steps.DISPLAY_MANAGER_ALIAS", alias):
                prepare_shared_desktop(SetupConfig(host="vm", username="agent", system_type="agent_vm"))
            self.assertEqual(alias.readlink(), Path("/dev/null"))
            self.assertFalse(alias.with_suffix(".service.basaltwater-backup").exists())
            self.assertEqual(run.call_args.args[0][:3], ["systemctl", "mask", "--now"])

    @patch("desktop.session_steps.run")
    def test_hardened_user_fails_before_service_changes(self, run):
        config = SetupConfig(host="vm", username="agent", system_type="agent_workstation", harden_user=True)
        with self.assertRaisesRegex(ValueError, "harden-user"):
            assert_desktop_idle(config)
        run.assert_not_called()

    @patch("desktop.session_steps.is_dry_run", return_value=False)
    @patch("desktop.session_steps.run")
    def test_active_wayland_desktop_blocks_migration_without_stopping_it(self, run, dry):
        run.side_effect = [Mock(stdout="1 1000 agent seat0\n"), Mock(returncode=0, stdout="Type=wayland\nClass=user\n")]
        config = SetupConfig(host="vm", username="agent", system_type="agent_workstation")
        with self.assertRaisesRegex(RuntimeError, "log out"):
            assert_desktop_idle(config)
        self.assertEqual(run.call_count, 2)

    @patch("desktop.session_steps.is_dry_run", return_value=False)
    @patch("lib.remote_utils.is_dry_run", return_value=False)
    @patch("lib.remote_utils.subprocess.Popen")
    def test_disappearing_session_is_quiet_but_other_failures_block_setup(self, popen, dry, desktop_dry):
        missing = "Failed to get path for session '31': No session '31' known\n"
        for remaining, list_code, pgrep_code, error in (
            ("", 0, 1, None),
            ("31 1000 agent\n", 0, 1, "Could not inspect login session 31"),
            ("", 1, 1, "Command failed"),
            ("", 0, 0, "log out existing XRDP sessions"),
        ):
            with self.subTest(remaining=remaining, list_code=list_code, pgrep_code=pgrep_code):
                popen.reset_mock()
                popen.side_effect = [
                    Mock(returncode=code, communicate=Mock(return_value=(stdout, stderr)))
                    for code, stdout, stderr in (
                        (0, "31 1000 agent\n", ""),
                        (1, "", missing),
                        (list_code, remaining, "Failed to list sessions" if list_code else ""),
                        (pgrep_code, "", ""),
                    )
                ]
                report = SetupReport()
                output = io.StringIO()
                config = SetupConfig(host="vm", username="agent", system_type="agent_vm")
                with contextlib.redirect_stdout(output), report.capture():
                    if error:
                        with self.assertRaisesRegex(RuntimeError, error):
                            assert_desktop_idle(config)
                    else:
                        assert_desktop_idle(config)
                self.assertNotIn(missing.strip(), output.getvalue())
                if not list_code:
                    self.assertEqual(report.notes, ())
                self.assertEqual(popen.call_count, 3 if remaining or list_code else 4)

    @patch("desktop.session_steps.is_dry_run", return_value=False)
    @patch("desktop.session_steps.run")
    def test_unregistered_xrdp_session_also_blocks_migration(self, run, dry):
        run.side_effect = [Mock(stdout=""), Mock(returncode=0)]
        config = SetupConfig(host="vm", username="agent", system_type="agent_workstation")
        with self.assertRaisesRegex(RuntimeError, "XRDP sessions"):
            assert_desktop_idle(config)

    @patch("desktop.session_steps.run")
    def test_container_capability_failure_precedes_system_calls(self, run):
        self.capability.return_value = False
        config = SetupConfig(host="vm", username="agent", system_type="agent_workstation", machine_type="oci")
        with self.assertRaisesRegex(ValueError, "systemd"):
            assert_desktop_idle(config)
        run.assert_not_called()
        self.capability.assert_called_once_with("oci")


class DesktopSetupLogoutTests(unittest.TestCase):
    def setUp(self):
        for patcher in (patch.object(runtime, "configuration"), patch.object(runtime, "session_lock"),
                        patch.object(setup_logout.Path, "exists", return_value=True),
                        patch.object(setup_logout.time, "sleep")):
            patcher.start()
            self.addCleanup(patcher.stop)

    @patch.object(runtime, "status", side_effect=[{"state": "running", "generation": "g", "paused": True},
                                                 {"state": "stopping", "generation": "g"}, {"state": "stopped"}])
    @patch.object(runtime, "request", return_value={"generation": "g", "lease": "l"})
    def test_paused_session_logs_out_once_and_waits(self, request, status):
        self.assertTrue(setup_logout.logout_for_setup())
        actions = [call.args[0]["action"] for call in request.call_args_list]
        self.assertEqual(actions, ["pause", "resume", "acquire", "logout", "release", "pause"])
        self.assertEqual(status.call_count, 3)

    @patch.object(runtime, "status", return_value={"state": "stopped"})
    @patch.object(runtime, "request")
    def test_stopped_desktop_is_not_started(self, request, status):
        self.assertFalse(setup_logout.logout_for_setup())
        request.assert_not_called()

    @patch.object(setup_logout.time, "monotonic", side_effect=[0, 61])
    @patch.object(runtime, "status", return_value={"state": "running", "generation": "g"})
    @patch.object(runtime, "request", return_value={"generation": "g", "lease": "l"})
    def test_canceled_logout_stops_setup_and_leaves_control_paused(self, request, status, clock):
        with self.assertRaisesRegex(RuntimeError, "did not finish"):
            setup_logout.logout_for_setup()
        self.assertEqual(request.call_args.args[0], {"action": "pause"})

    @patch.object(runtime, "status", side_effect=[{"state": "stopping", "generation": "g"},
                                                 {"state": "running", "generation": "new"}])
    @patch.object(runtime, "request")
    def test_reconnect_does_not_log_out_replacement_session(self, request, status):
        with self.assertRaisesRegex(RuntimeError, "restarted"):
            setup_logout.logout_for_setup()
        request.assert_not_called()


class DesktopServiceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.vendor = Path(directory.name) / "vendor.service"
        self.unit = Path(directory.name) / "xrdp-sesman.service"
        self.vendor.write_text(
            "[Unit]\nAfter=network.target\nBindsTo=xrdp.service other.service\n"
            "StopWhenUnneeded=true\n\n[Service]\nType=exec\n"
            "ExecStart=/usr/sbin/xrdp-sesman $SESMAN_OPTIONS --nodaemon\n"
            "[Install]\nWantedBy=multi-user.target\n")
        for name, value in (("SESMAN_VENDOR_UNIT", self.vendor), ("SESMAN_UNIT", self.unit)):
            patcher = patch(f"desktop.session_steps.{name}", value)
            patcher.start()
            self.addCleanup(patcher.stop)
        runner = patch("desktop.session_steps.run", return_value=Mock(stdout="other.service\n"))
        self.run = runner.start()
        self.addCleanup(runner.stop)

    def test_full_override_removes_frontend_dependency_and_refreshes_vendor_changes(self):
        legacy = self.unit.parent / "xrdp-sesman.service.d/shared-desktop.conf"
        legacy.parent.mkdir()
        legacy.write_text("[Unit]\nBindsTo=\nStopWhenUnneeded=false\n")
        configure_session_service()
        self.assertFalse(legacy.exists())
        content = self.unit.read_text()
        self.assertIn("BindsTo=other.service\n", content)
        self.assertNotIn("xrdp.service", content)
        self.assertIn("StopWhenUnneeded=false\n", content)
        self.assertNotIn("StopWhenUnneeded=true", content)
        self.assertIn("$SESMAN_OPTIONS --nodaemon", content)
        self.vendor.write_text(self.vendor.read_text() + "Alias=example.service\n")
        configure_session_service()
        self.assertIn("Alias=example.service", self.unit.read_text())

    def test_custom_override_is_preserved(self):
        self.unit.write_text("[Unit]\nDescription=Administrator unit\n")
        with self.assertRaisesRegex(RuntimeError, "administrator migration"):
            configure_session_service()
        self.assertEqual(self.unit.read_text(), "[Unit]\nDescription=Administrator unit\n")
        self.run.assert_not_called()

    def test_remaining_dropin_dependency_fails_closed(self):
        self.run.return_value.stdout = "xrdp.service\n"
        with self.assertRaisesRegex(RuntimeError, "still ties desktop"):
            configure_session_service()


class DesktopSupervisorTests(unittest.TestCase):
    def test_failed_desktop_launch_removes_control_socket(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(runtime, "configuration", return_value={"desktop": "xfce", "username": "agent"}), \
                patch.object(runtime, "session_lock"), patch.object(runtime.pwd, "getpwnam"), \
                patch.object(runtime.os, "chdir"), patch.object(runtime.os, "chmod"), \
                patch.object(runtime, "runtime_directory", return_value=Path(directory)), \
                patch.dict("os.environ", {"DISPLAY": ":10"}), \
                patch.object(runtime.socket, "socket") as socket, \
                patch.object(runtime.subprocess, "Popen", side_effect=FileNotFoundError("desktop executable missing")):
            path = Path(directory) / "control.sock"
            socket.return_value.__enter__.return_value.bind.side_effect = lambda _: path.touch()
            with self.assertRaisesRegex(FileNotFoundError, "desktop executable missing"):
                runtime.serve()
            self.assertFalse(path.exists())

    @patch.object(runtime, "configuration", return_value={"desktop": "xfce", "username": "agent"})
    @patch.object(runtime, "session_lock")
    @patch.object(runtime.pwd, "getpwnam")
    @patch.object(runtime.os, "chdir")
    @patch.object(runtime.os, "killpg")
    @patch.object(runtime.signal, "signal")
    @patch.object(runtime.subprocess, "Popen")
    @patch.object(runtime.socket, "socket")
    @patch.object(runtime, "receive", return_value={"action": "status"})
    @patch.object(runtime.DesktopSession, "handle", return_value={"state": "running"})
    def test_client_reset_does_not_end_desktop(self, handle, receive, socket, popen,
                                             signal, killpg, chdir, account, lock, config):
        process = popen.return_value
        process.poll.side_effect = [None, None, 0]
        server = socket.return_value.__enter__.return_value
        connection = Mock()
        connection.getsockopt.return_value = struct.pack("3i", 1, runtime.os.getuid(), 1)
        connection.sendall.side_effect = ConnectionResetError("client left")
        connection.__enter__ = Mock(return_value=connection)
        connection.__exit__ = Mock(return_value=False)
        server.accept.return_value = (connection, None)
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(runtime, "runtime_directory", return_value=Path(directory)), \
                patch.dict("os.environ", {"DISPLAY": ":10"}), patch.object(runtime.os, "chmod"):
            self.assertEqual(runtime.serve(), 0)
        # A second request was accepted after the first client disconnected.
        self.assertEqual(server.accept.call_count, 2)


class DesktopCliTests(unittest.TestCase):
    @patch.object(runtime, "status", return_value={"state": "running", "generation": "g"})
    @patch.object(runtime, "request", side_effect=[{"windows": []}, {"lease": "l"},
                                                 {"pid": 42, "launch": "token", "generation": "g"}, {}])
    @patch.object(client, "wait_for_window", side_effect=RuntimeError("Timed out"))
    def test_launch_wait_failure_preserves_pid_and_releases_control(self, wait, request, status):
        parser = argparse.ArgumentParser()
        add_desktop_subparser(parser.add_subparsers())
        args = parser.parse_args(["desktop", "exec", "--wait-window", "Editor", "--", "editor"])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(run_desktop_command(args), 1)
        result = json.loads(output.getvalue())
        self.assertEqual(result["launch"], "token")
        self.assertEqual(result["pid"], 42)
        self.assertEqual(result["error"], "Timed out")
        self.assertEqual(request.call_args.args[0]["action"], "release")

    @patch.object(runtime, "status", return_value={"state": "running", "generation": "current"})
    @patch.object(runtime, "request", return_value={"output": "/tmp/app.png"})
    def test_window_screenshot_routes_without_acquiring_control(self, request, status):
        parser = argparse.ArgumentParser()
        add_desktop_subparser(parser.add_subparsers())
        args = parser.parse_args(["desktop", "screenshot", "--window", "0x123", "--output", "/tmp/app.png"])
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_desktop_command(args), 0)
        request.assert_called_once_with({"action": "screenshot", "generation": "current",
                                        "window": "0x123", "active_window": False, "output": "/tmp/app.png"})

    @patch.object(runtime, "status", return_value={"state": "starting"})
    @patch.object(runtime, "request")
    def test_starting_desktop_gets_accurate_guidance(self, request, status):
        args = argparse.Namespace(desktop_command="exec", argv=["editor"])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(run_desktop_command(args), 1)
        self.assertIn("starting", json.loads(output.getvalue())["error"])
        request.assert_not_called()

    @patch.object(runtime, "status", return_value={"state": "running", "generation": "current"})
    @patch.object(runtime, "request", side_effect=[{"lease": "mine"}, {"pid": 42}, {"released": True}])
    def test_exec_sends_the_invoking_shell_directory(self, request, status):
        args = argparse.Namespace(desktop_command="exec", argv=["--", "editor", "relative.txt"])
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_desktop_command(args), 0)
        self.assertEqual(request.call_args_list[1].args[0]["cwd"], str(Path.cwd()))
        self.assertEqual(request.call_args_list[1].args[0]["argv"], ["editor", "relative.txt"])

    def test_lite_server_reconciles_firewall_before_enabling_rdp(self):
        from plugins.server import build_server_steps
        from security.steps import configure_firewall
        from desktop.xrdp_steps import install_xrdp

        config = SetupConfig(host="vm", username="agent", system_type="server_lite",
                             include_desktop=True, enable_rdp=True)
        steps = [step for _, step in build_server_steps(config)]
        self.assertEqual(steps.count(configure_firewall), 1)
        self.assertLess(steps.index(configure_firewall), steps.index(install_xrdp))

    def test_remote_parser_does_not_turn_headless_forwarding_into_desktop(self):
        parser = create_setup_argument_parser("Remote", for_remote=True)
        args = parser.parse_args(["--system-type", "agent_vm", "--username", "agent"])
        args.host = "localhost"
        config = SetupConfig.from_args(args, "agent_vm")
        self.assertFalse(config.include_desktop)
        args = parser.parse_args(["--system-type", "agent_vm", "--username", "agent", "--desktop", "xfce"])
        args.host = "localhost"
        self.assertTrue(SetupConfig.from_args(args, "agent_vm").include_desktop)

    @patch.object(runtime, "status", return_value={"state": "stopped"})
    @patch.object(runtime, "start")
    def test_screenshot_does_not_implicitly_start_desktop(self, start, status):
        parser = argparse.ArgumentParser()
        add_desktop_subparser(parser.add_subparsers())
        args = parser.parse_args(["desktop", "screenshot", "--output", "/tmp/screen.png"])
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_desktop_command(args), 1)
        start.assert_not_called()


class DesktopPrivateRuntimeTests(unittest.TestCase):
    def test_unconfigured_desktop_explains_how_to_enable_it(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(runtime, "CONFIG_PATH", Path(directory) / "desktop.json"):
            with self.assertRaisesRegex(RuntimeError, "not configured.*--desktop"):
                runtime.configuration()

    def test_runtime_directory_rejects_nonprivate_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            parent.chmod(0o755)
            with patch.object(runtime, "Path", return_value=parent):
                with self.assertRaisesRegex(RuntimeError, "private logind"):
                    runtime.runtime_directory()
            self.assertFalse((parent / "basaltwater-desktop").exists())

    def test_runtime_directory_is_private_and_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            with patch.object(runtime, "Path", return_value=parent):
                first = runtime.runtime_directory()
                self.assertEqual(runtime.runtime_directory(), first)
            self.assertEqual(first.stat().st_mode & 0o777, 0o700)

    def test_runtime_directory_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            (parent / "basaltwater-desktop").symlink_to(parent, target_is_directory=True)
            with patch.object(runtime, "Path", return_value=parent):
                with self.assertRaisesRegex(RuntimeError, "Unsafe"):
                    runtime.runtime_directory()
