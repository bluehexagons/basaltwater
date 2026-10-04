"""Portal session contracts with mocked system calls; no real desktop access."""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import struct
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

from desktop import native_session as native
from desktop import native_handoff
from desktop import client
from desktop.portal import Portal, REMOTE, SCREENCAST
from lib import desktop_cli


class NativeControlTests(unittest.TestCase):
    def setUp(self):
        self.portal = Mock(fd=42)
        self.session = native.NativeSession(self.portal)
        self.session.state = "running"
        self.session.node = 17
        self.session.logical_size = [1280, 720]
        self.session.geometry = [2560, 1440]
        self.session.captured = time.monotonic()
        self.payload = {"generation": self.session.generation}
        self.lease = self.session.handle({**self.payload, "action": "acquire"})["lease"]
        self.payload.update(lease=self.lease, geometry=self.session.geometry)

    def test_scaled_pointer_targets_the_consent_selected_stream(self):
        result = self.session.handle({**self.payload, "action": "input", "kind": "click", "x": 400, "y": 200})
        self.assertEqual(result["geometry"], [2560, 1440])
        self.portal.notify.assert_any_call("NotifyPointerMotionAbsolute", "udd", 17, 200.0, 100.0)
        self.portal.notify.assert_any_call("NotifyPointerButton", "iu", 272, 1)
        self.portal.notify.assert_any_call("NotifyPointerButton", "iu", 272, 0)

    def test_pause_revokes_lease_and_references_without_stopping_desktop(self):
        self.session.elements["ref"] = {"pid":42, "observed_at":time.monotonic()}
        self.session.handle({"action":"pause"})
        self.assertFalse(self.session.elements)
        with self.assertRaisesRegex(RuntimeError, "paused"):
            self.session.handle({**self.payload, "action":"input", "kind":"key", "key":"Return"})
        self.session.handle({"action":"resume"})
        with self.assertRaisesRegex(RuntimeError, "lease"):
            self.session.handle({**self.payload, "action":"input", "kind":"key", "key":"Return"})
        self.portal.notify.assert_not_called()

    def test_stale_generation_lease_capture_and_out_of_bounds_reject_input(self):
        cases = [
            {"generation":"old"}, {"lease":"old"}, {"geometry":[1280,720]},
            {"x":-1}, {"x":2560}, {"button":8},
        ]
        for fields in cases:
            with self.subTest(fields=fields), self.assertRaises((ValueError,RuntimeError)):
                self.session.handle({**self.payload,"action":"input","kind":"click","x":1,"y":1,**fields})
        self.session.captured = time.monotonic() - 61
        with self.assertRaisesRegex(ValueError, "recent screenshot"):
            self.session.handle({**self.payload,"action":"input","kind":"click","x":1,"y":1})
        self.portal.notify.assert_not_called()

    def test_expiration_and_portal_revocation_block_further_actions(self):
        self.session.until = time.monotonic() - 1
        with self.assertRaisesRegex(RuntimeError, "not ready"):
            self.session.handle({**self.payload,"action":"exec","argv":["editor"],"cwd":"/"})
        self.session.close()
        self.assertTrue(self.session.stopping.is_set())
        self.assertEqual(self.session.state,"stopped")
        self.assertIsNone(self.session.lease)
        self.portal.notify.assert_not_called()

    def test_text_releases_pressed_keys_on_failed_delivery(self):
        self.portal.notify.side_effect = [RuntimeError("revoked"), None]
        with self.assertRaisesRegex(RuntimeError,"revoked"):
            self.session.handle({**self.payload,"action":"input","kind":"text","text":"a"})
        self.assertEqual(self.portal.notify.call_args.args, ("NotifyKeyboardKeysym","iu",ord("a"),0))

    def test_failed_release_still_releases_modifiers_and_stops_control(self):
        self.portal.notify.side_effect = [None, None, RuntimeError("release failed"), None]
        with patch.object(native, "keysyms", return_value=[100, 101]), self.assertRaisesRegex(RuntimeError, "control stopped"):
            self.session.handle({**self.payload, "action":"input", "kind":"key", "key":"ctrl+s"})
        self.assertEqual(self.portal.notify.call_args.args, ("NotifyKeyboardKeysym", "iu", 100, 0))
        self.assertTrue(self.session.stopping.is_set())

    def test_launch_enables_only_task_accessibility_and_preserves_cwd(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"NO_AT_BRIDGE":"1", "GTK_MODULES":"existing", "AT_SPI_BUS_ADDRESS":"foreign"}), patch.object(native.subprocess, "Popen", return_value=Mock(pid=42)) as launch:
            result = self.session.handle({**self.payload, "action":"exec", "argv":["editor", "task.svg"], "cwd":directory})
            env = launch.call_args.kwargs["env"]
            self.assertEqual(env["NO_AT_BRIDGE"], "0")
            self.assertEqual(env["GTK_MODULES"], "existing:atk-bridge")
            self.assertNotIn("AT_SPI_BUS_ADDRESS", env)
            self.assertEqual(os.environ["NO_AT_BRIDGE"], "1")
            self.assertEqual(launch.call_args.kwargs["cwd"], directory)
            self.assertEqual(result["pid"], 42)

    def test_observed_reference_is_pid_scoped_and_expires_after_mutations(self):
        self.session.elements["observed"]={"pid":42,"observed_at":time.monotonic()}
        with patch.object(native.accessibility,"worker_request",return_value={"requested":"invoke"}) as worker:
            self.session.handle({**self.payload,"action":"element","ref":"observed","operation":"invoke","action_name":"click","pid":99})
        self.assertEqual(worker.call_args.args[0]["pid"],42)
        self.assertFalse(self.session.elements)
        with self.assertRaisesRegex(ValueError,"stale"):
            self.session.handle({**self.payload,"action":"element","ref":"observed","operation":"invoke"})

    def test_old_accessibility_reference_is_rejected_before_worker(self):
        self.session.elements["old"]={"pid":42,"observed_at":time.monotonic()-61}
        with patch.object(native.accessibility,"worker_request") as worker, self.assertRaisesRegex(ValueError,"stale"):
            self.session.handle({**self.payload,"action":"element","ref":"old","operation":"focus"})
        worker.assert_not_called()

    def test_capture_preserves_existing_artifacts_and_removes_failed_partial(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory,"screen.png")
            path.write_bytes(b"keep")
            with patch.object(native.subprocess,"run") as run, self.assertRaises(FileExistsError):
                self.session.handle({**self.payload,"action":"screenshot","output":str(path)})
            self.assertEqual(path.read_bytes(),b"keep")
            run.assert_not_called()
            path.unlink()
            with patch.object(native.subprocess,"run",side_effect=subprocess.TimeoutExpired("capture",12)), self.assertRaises(subprocess.TimeoutExpired):
                self.session.handle({**self.payload,"action":"screenshot","output":str(path)})
            self.assertFalse(path.exists())

    def test_capture_is_private_and_passes_only_portal_and_output_descriptors(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory,"screen.png")
            def capture(argv,**kwargs):
                fd=int(argv[-1])
                self.assertEqual(kwargs["pass_fds"],(42,fd))
                self.assertEqual(os.fstat(fd).st_mode & 0o777,0o600)
                os.write(fd,b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + struct.pack(">II",1280,720))
                return subprocess.CompletedProcess(argv,0,json.dumps({"geometry":[1280,720]}),"")
            with patch.object(native.subprocess,"run",side_effect=capture):
                result=self.session.handle({**self.payload,"action":"screenshot","output":str(path)})
            self.assertEqual(result["geometry"],[1280,720])
            self.assertTrue(path.read_bytes().startswith(b"\x89PNG"))

    def test_invalid_capture_artifact_is_removed_without_updating_observation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "screen.png")
            with patch.object(native.subprocess, "run", return_value=subprocess.CompletedProcess([],0,json.dumps({"geometry":[1280,720]}),"")), self.assertRaisesRegex(RuntimeError,"PNG geometry"):
                self.session.handle({**self.payload,"action":"screenshot","output":str(path)})
            self.assertFalse(path.exists())
            self.assertEqual(self.session.geometry,[2560,1440])

    def test_window_capture_never_falls_back_to_whole_monitor(self):
        with self.assertRaisesRegex(ValueError,"window capture is unavailable"):
            self.session.handle({**self.payload,"action":"screenshot","window":"other","output":"/tmp/no.png"})
        self.portal.notify.assert_not_called()

    def test_pending_consent_does_not_allow_launch_or_input(self):
        self.session.state="awaiting-consent"
        with self.assertRaisesRegex(RuntimeError,"permission dialog"):
            self.session.handle({**self.payload,"action":"input","kind":"text","text":"hello"})
        self.portal.notify.assert_not_called()

    def test_timed_out_queued_request_cannot_deliver_late_input(self):
        with self.assertRaisesRegex(RuntimeError,"request expired"):
            self.session.handle({**self.payload,"deadline":time.monotonic()-1,"action":"input","kind":"text","text":"late"})
        self.portal.notify.assert_not_called()


class NativeHandoffTests(unittest.TestCase):
    def test_only_live_running_session_reports_enabled_input(self):
        cases = [
            ({"state": "awaiting-consent", "detail": "Approve KDE"}, "Waiting for KDE consent: Approve KDE"),
            ({"state": "failed", "detail": "Permission denied", "paused": True}, "Native desktop control failed: Permission denied"),
            ({"state": "stopped", "paused": True}, "Automation stopped; KDE and applications are preserved"),
            ({"stopped": True}, "Automation stopped; KDE and applications are preserved"),
            ({"state": "running", "expires_in": 0}, "Automation expired; start a new session with KDE consent"),
            ({"state": "running", "expires_in": 30, "paused": True}, "Agent input paused — you have control"),
            ({"state": "running", "expires_in": 30}, "Agent input enabled (30 seconds remaining)"),
            ({"error": "Socket closed"}, "Native desktop control unavailable: Socket closed"),
            ({}, "Native desktop control unavailable: Inspect desktop status"),
        ]
        for status, expected in cases:
            with self.subTest(status=status):
                self.assertEqual(native_handoff.describe(status), expected)

    def test_human_requests_wait_for_poll_instead_of_being_discarded(self):
        gtk, glib = Mock(), Mock()
        label = Mock()
        gtk.Label.side_effect = [label, Mock()]
        buttons = [Mock(), Mock(), Mock()]
        gtk.Button.side_effect = buttons
        workers, responses = [], []
        glib.idle_add.side_effect = lambda callback, result: responses.append((callback, result))

        def thread(*, target, daemon):
            return Mock(start=lambda: workers.append(target))

        def interact():
            # The initial status poll is still in flight when the human clicks.
            for button in (buttons[0], buttons[2]):
                button.connect.call_args.args[1](button)
            self.assertEqual(len(workers), 1)
            self.assertEqual(label.set_text.call_args.args, ("Control request pending: stop",))
            # Another poll must neither start nor displace the queued controls.
            glib.timeout_add_seconds.call_args.args[1]()
            workers.pop(0)()
            callback, result = responses.pop(0)
            callback(result)
            self.assertEqual(label.set_text.call_args.args, ("Updating desktop control: pause",))
            workers.pop(0)()
            callback, result = responses.pop(0)
            callback(result)
            self.assertEqual(label.set_text.call_args.args, ("Updating desktop control: stop",))
            workers.pop(0)()
            callback, result = responses.pop(0)
            callback(result)
            self.assertEqual(label.set_text.call_args.args, ("Automation stopped; KDE and applications are preserved",))
            self.assertFalse(workers)

        gtk.main.side_effect = interact
        with patch.dict(sys.modules, {"gi": Mock(), "gi.repository": SimpleNamespace(Gtk=gtk, GLib=glib)}), \
                patch.object(native_handoff.threading, "Thread", side_effect=thread), \
                patch.object(native, "status", return_value={"state": "running", "expires_in": 30}) as status, \
                patch.object(native, "request", side_effect=[
                    {"state": "running", "expires_in": 30, "paused": True}, {"stopped": True}
                ]) as request:
            self.assertEqual(native_handoff.main(), 0)
        status.assert_called_once_with()
        self.assertEqual(request.call_args_list, [call({"action": "pause"}), call({"action": "stop"})])


class NativeCliTests(unittest.TestCase):
    def test_native_start_and_status_report_failed_consent_without_claiming_success(self):
        parser = argparse.ArgumentParser()
        desktop_cli.add_desktop_subparser(parser.add_subparsers())
        for command in ("start", "status"):
            args = parser.parse_args(["desktop", "--native", command])
            for state, expected in (("awaiting-consent", 0), ("running", 0), ("failed", 1)):
                with self.subTest(command=command, state=state), \
                        patch.object(native, command, return_value={"state": state, "detail": "portal result"}), \
                        patch.object(desktop_cli.runtime, command) as xrdp, redirect_stdout(StringIO()) as output:
                    self.assertEqual(desktop_cli.run_desktop_command(args), expected)
                    self.assertEqual(json.loads(output.getvalue())["detail"], "portal result")
                    xrdp.assert_not_called()

    def test_default_start_keeps_debian_backend_when_cachyos_is_detected(self):
        parser = argparse.ArgumentParser()
        desktop_cli.add_desktop_subparser(parser.add_subparsers())
        args = parser.parse_args(["desktop", "start"])
        with patch("lib.cachyos.is_cachyos", return_value=True), \
                patch.object(native, "start") as portal, \
                patch.object(desktop_cli.runtime, "start", return_value={"state": "running"}) as xrdp, \
                redirect_stdout(StringIO()):
            self.assertEqual(desktop_cli.run_desktop_command(args), 0)
        xrdp.assert_called_once_with()
        portal.assert_not_called()

    def test_session_check_rejects_foreign_socket_ssh_and_non_kde(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(native,"runtime_directory",return_value=Path(directory,"native")), patch.object(native.os,"getuid",return_value=1000), patch.object(native.os,"geteuid",return_value=1000), patch("lib.cachyos.is_cachyos",return_value=True), patch("lib.cachyos_doctor._owned_socket",return_value=True) as owned, patch.dict(os.environ,{"XDG_SESSION_TYPE":"wayland","XDG_CURRENT_DESKTOP":"KDE","WAYLAND_DISPLAY":"wayland-0"},clear=True):
            self.assertEqual(native.check_session(),"wayland-0")
            owned.return_value=False
            with self.assertRaisesRegex(RuntimeError,"session bus"):
                native.check_session()
            owned.return_value=True
            with patch.dict(os.environ,{"SSH_CONNECTION":"remote"}), self.assertRaisesRegex(RuntimeError,"locally"):
                native.check_session()
            with patch.dict(os.environ,{"XDG_CURRENT_DESKTOP":"GNOME"}), self.assertRaisesRegex(RuntimeError,"KDE"):
                native.check_session()

    def test_native_status_does_not_touch_the_xrdp_backend_or_start_portal(self):
        parser=argparse.ArgumentParser()
        desktop_cli.add_desktop_subparser(parser.add_subparsers())
        args=parser.parse_args(["desktop","--native","status"])
        with patch.object(native,"status",return_value={"state":"stopped"}), patch.object(native,"start") as start, patch.object(desktop_cli.runtime,"status") as xrdp, redirect_stdout(StringIO()):
            self.assertEqual(desktop_cli.run_desktop_command(args),0)
        start.assert_not_called()
        xrdp.assert_not_called()

    def test_paced_input_uses_the_selected_backend_and_is_revocable(self):
        backend=Mock()
        backend.request.side_effect=[{},RuntimeError("human paused")]
        with patch.object(client.time,"sleep"):
            result=client.send_action({"action":"input","generation":"g","lease":"l","geometry":[1280,720],"kind":"text","text":"abc","delay_ms":10},backend=backend)
        self.assertEqual(result["submitted_characters"],1)
        self.assertIn("human paused",result["error"])
        self.assertEqual(backend.request.call_count,2)


class PortalContractTests(unittest.TestCase):
    def setUp(self):
        self.portal = Portal.__new__(Portal)
        self.portal.bus = Mock()
        self.portal.bus.get_unique_name.return_value = ":1.42"
        self.portal.Gio = Mock()
        self.portal.GLib = Mock()
        self.portal.pending = set()
        self.portal.closed = Mock()
        self.portal.session = None
        self.portal.fd = None

    def test_response_subscribes_before_call_and_rejects_denial(self):
        def call(*args, **kwargs):
            subscription = self.portal.bus.signal_subscribe.call_args
            path = subscription.args[3]
            callback = subscription.args[-1]
            callback(None,None,None,None,None,Mock(unpack=lambda:(1,{})))
            return Mock(unpack=lambda:(path,))
        with patch.object(self.portal,"call",side_effect=call), self.assertRaisesRegex(RuntimeError,"denied"):
            self.portal.response(REMOTE,"Start","(osa{sv})",("session",""),{})
        self.portal.bus.signal_unsubscribe.assert_called_once()
        self.assertFalse(self.portal.pending)

    def test_missing_response_is_closed_on_timeout(self):
        event = Mock()
        event.wait.return_value = False
        def call(*args, **kwargs):
            path = self.portal.bus.signal_subscribe.call_args.args[3]
            return Mock(unpack=lambda:(path,))
        with patch.object(self.portal,"call",side_effect=call) as calls, patch("desktop.portal.threading.Event",return_value=event), self.assertRaisesRegex(RuntimeError,"timed out"):
            self.portal.response(REMOTE,"Start","(osa{sv})",("session",""),{})
        self.assertEqual(calls.call_args.args[:2],("org.freedesktop.portal.Request","Close"))
        self.assertFalse(self.portal.pending)

    def test_incomplete_device_consent_never_opens_pipewire(self):
        with patch.object(self.portal,"response",side_effect=[{"session_handle":"session"},{},{},{"devices":1,"streams":[(17,{"size":[1280,720]})]}]) as response, self.assertRaisesRegex(RuntimeError,"keyboard and pointer"):
            self.portal.start()
        self.portal.bus.call_with_unix_fd_list_sync.assert_not_called()
        self.assertEqual(response.call_args_list[2].args[:2],(SCREENCAST,"SelectSources"))

    def test_portal_owner_loss_revokes_control(self):
        self.portal.owner_changed(None,None,None,None,None,Mock(unpack=lambda:("portal",":1.42","")))
        self.portal.closed.assert_called_once()


class NativePackageTests(unittest.TestCase):
    def test_app_selection_installs_dependencies_without_starting_control(self):
        from common import cachyos_steps as steps
        from lib.config import SetupConfig
        with tempfile.TemporaryDirectory() as directory, patch.object(steps,"_home",return_value=Path(directory)), patch.object(steps.shutil,"which",return_value=None):
            base = SetupConfig(host="localhost",username="human",system_type="agent_cachyos")
            self.assertFalse(set(steps.CACHYOS_AUTOMATION_PACKAGES) & set(steps.cachyos_packages(base)))
            base.install_inkscape = True
            self.assertTrue(set(steps.CACHYOS_AUTOMATION_PACKAGES) <= set(steps.cachyos_packages(base)))

    def test_individual_graphical_selections_include_automation_dependencies(self):
        from common import cachyos_steps as steps
        from lib.config import SetupConfig

        for field in ("install_godot", "install_material_maker", "install_moonlight", "install_sysadmin_tools"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory, \
                    patch.object(steps, "_home", return_value=Path(directory)), \
                    patch.object(steps.shutil, "which", return_value="/usr/bin/tool"):
                config = SetupConfig(host="localhost", username="human", system_type="agent_cachyos",
                                     **{field: True})
                self.assertTrue(set(steps.CACHYOS_AUTOMATION_PACKAGES) <= set(steps.cachyos_packages(config)))

    def test_publishing_cli_selections_do_not_require_desktop_automation(self):
        from common import cachyos_steps as steps
        from lib.config import SetupConfig

        with tempfile.TemporaryDirectory() as directory, \
                patch.object(steps, "_home", return_value=Path(directory)), \
                patch.object(steps.shutil, "which", return_value=None):
            config = SetupConfig(host="localhost", username="human", system_type="agent_cachyos",
                                 install_butler=True, install_steamcmd=True)
            self.assertFalse(set(steps.CACHYOS_AUTOMATION_PACKAGES) & set(steps.cachyos_packages(config)))


if __name__ == "__main__":
    unittest.main()
