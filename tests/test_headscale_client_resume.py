"""Managed resume safety and running privacy contracts, without system changes."""

from __future__ import annotations

from contextlib import ExitStack, nullcontext
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import tailscale_steps as steps
from common.service_tools import headscale_client_remote as remote
from lib import headscale_client as client
from tests.test_headscale_client import URL, OTHER, observed


class ResumeTests(unittest.TestCase):
    def setUp(self):
        stack = self.enterContext(ExitStack())
        root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        self.root = root
        self.native = root / "native.state"
        self.native.write_text("opaque identity, never parsed")
        self.native.chmod(0o600)
        self.dropin = root / "privacy.conf"
        self.dropin.write_text(steps.PRIVACY)
        self.dropin.chmod(0o644)
        for module, name, value in ((client, "CLIENT_STATE", str(root / "binding" / "client.json")),
                                    (client, "NATIVE_STATE", str(self.native)),
                                    (client, "CONTROL_CONFIG", str(root / "no-server")),
                                    (steps, "DROPIN", str(self.dropin))):
            stack.enter_context(patch.object(module, name, value))
        stack.enter_context(patch.object(client, "client_lock", side_effect=nullcontext))
        stack.enter_context(patch.object(client, "resolver_lifecycle_guard", side_effect=nullcontext))
        stack.enter_context(patch.object(steps, "preflight", return_value="trixie"))
        stack.enter_context(patch.object(steps, "is_dry_run", return_value=False))
        stack.enter_context(patch.object(steps.shutil, "which", return_value="/mock/tailscale"))
        stack.enter_context(patch.object(client, "_object", return_value={"short": "1.104.7"}))
        self.privacy = stack.enter_context(patch.object(client, "require_private_daemon"))
        self.commands = []
        self.active = False
        self.command = stack.enter_context(patch.object(client, "native", side_effect=self.command_result))
        self.observe = stack.enter_context(patch.object(client, "observe_client", return_value=client._identity(observed(), {"ControlURL": URL}, URL, True)))
        client.save_binding(URL, "enrolled", "node123")

    def command_result(self, args, **kwargs):
        self.commands.append(args)
        if args[:3] == ["systemctl", "enable", "--now"]:
            self.active = True
        if args[:3] == ["systemctl", "disable", "--now"]:
            self.active = False
        if "--property=ActiveState,UnitFileState" in args:
            return "ActiveState=inactive\nUnitFileState=disabled\n"
        return "active" if self.active else "inactive"

    def enable(self, **kwargs):
        steps.configure_tailscale(SimpleNamespace(tailscale=True, tailscale_login_server=URL, **kwargs))

    def test_disable_enable_retains_exact_identity_without_credentials(self):
        client.disable_client(URL)
        self.enable()
        self.assertTrue(self.active)
        self.assertEqual(client.read_binding(), {"version": 1, "url": URL, "node_id": "node123", "phase": "enrolled"})
        self.assertIn(["systemctl", "enable", "--now", "tailscaled"], self.commands)
        self.assertFalse(any("logout" in c or "--auth-key" in str(c) for c in self.commands))

    def test_wrong_binding_unowned_and_insecure_native_fail_before_start(self):
        for kind in ("endpoint", "unowned", "mode", "symlink", "hardlink", "owner", "parent"):
            with self.subTest(kind=kind):
                client.save_binding(URL, "enrolled", "node123")
                self.native.unlink(missing_ok=True)
                self.native.write_text("opaque")
                self.native.chmod(0o600)
                if kind == "endpoint":
                    client.save_binding(OTHER, "enrolled", "node123")
                elif kind == "unowned":
                    Path(client.CLIENT_STATE).unlink()
                elif kind == "mode":
                    self.native.chmod(0o644)
                elif kind == "symlink":
                    self.native.unlink()
                    self.native.symlink_to(self.dropin)
                elif kind == "hardlink":
                    os.link(self.native, self.root / "linked")
                elif kind == "parent":
                    self.root.chmod(0o755)
                owner = patch.object(client.os, "geteuid", return_value=-1) if kind == "owner" else nullcontext()
                self.commands.clear()
                with owner, self.assertRaises((ValueError, OSError)):
                    self.enable()
                self.assertFalse(any("enable" in c for c in self.commands))
                self.root.chmod(0o700)

    def test_prepared_and_left_reenable_without_enrollment_or_phase_change(self):
        for phase in ("prepared", "left"):
            with self.subTest(phase=phase):
                client.save_binding(URL, phase)
                before = Path(client.CLIENT_STATE).read_bytes()
                self.active = False
                self.commands.clear()
                status = {"BackendState": "NeedsLogin", "HaveNodeKey": False}
                prefs = {"ControlURL": "https://controlplane.tailscale.com"}
                with patch.object(client, "snapshot", return_value=(status, prefs)):
                    self.enable()
                self.assertTrue(self.active)
                self.assertEqual(Path(client.CLIENT_STATE).read_bytes(), before)
                self.observe.assert_not_called()
                self.assertFalse(any(c[0] == "tailscale" for c in self.commands))

    def test_unenrolled_start_rejects_unexpected_native_identity_and_quiesces(self):
        for status in ({"BackendState": "Running", "HaveNodeKey": True},
                       {"BackendState": "NeedsLogin", "HaveNodeKey": True}):
            client.save_binding(URL, "left")
            with patch.object(client, "snapshot", return_value=(status, {"ControlURL": URL})):
                with self.assertRaisesRegex(ValueError, "unexpected identity"):
                    self.enable()
            self.assertFalse(self.active)
            self.assertEqual(client.read_binding()["phase"], "left")

    def test_unenrolled_lost_start_ack_quiesces_and_retains_phase(self):
        client.save_binding(URL, "prepared")
        def command(args, **kwargs):
            result = self.command_result(args, **kwargs)
            if "enable" in args:
                raise client.ClientError("lost acknowledgement")
            return result
        self.command.side_effect = command
        with self.assertRaisesRegex(ValueError, "lost acknowledgement"):
            self.enable()
        self.assertFalse(self.active)
        self.assertEqual(client.read_binding()["phase"], "prepared")

    def test_wrong_node_endpoint_and_lost_observation_quiesce_and_block_retry(self):
        for failure in (client.ClientError("lost observation"),
                        client._identity(observed(), {"ControlURL": OTHER}, OTHER, True),
                        client.ClientIdentity("other", ("100.64.0.5",), URL, (), True, "peer")):
            with self.subTest(failure=failure):
                client.save_binding(URL, "enrolled", "node123")
                self.observe.side_effect = failure if isinstance(failure, Exception) else None
                self.observe.return_value = failure
                with self.assertRaises(ValueError):
                    self.enable()
                self.assertFalse(self.active)
                self.assertEqual(client.read_binding()["phase"], "resuming")
                self.commands.clear()
                with self.assertRaises(ValueError):
                    self.enable()
                self.assertEqual(self.commands, [])

    def test_start_lost_ack_quiesces_without_clearing_marker(self):
        def command(args, **kwargs):
            result = self.command_result(args, **kwargs)
            if "enable" in args:
                raise client.ClientError("lost start acknowledgement")
            return result
        self.command.side_effect = command
        with self.assertRaises(ValueError):
            self.enable()
        self.assertFalse(self.active)
        self.assertEqual(client.read_binding()["phase"], "resuming")

    def test_security_floor_and_privacy_dropin_fail_before_start(self):
        for version in ("1.80.0", "1.105.0", None):
            with patch.object(client, "_object", return_value={"short": version}), self.assertRaises(ValueError):
                self.enable()
        self.dropin.write_text("[Service]\n")
        with self.assertRaises(ValueError):
            self.enable()
        self.assertFalse(any("enable" in c for c in self.commands))

    def test_guard_blocks_resume_before_marker_or_service_changes(self):
        with patch.object(client, "resolver_lifecycle_guard", side_effect=ValueError("advertised")), self.assertRaises(ValueError):
            self.enable()
        self.assertEqual(client.read_binding()["phase"], "enrolled")
        self.assertFalse(any("enable" in c for c in self.commands))

    def test_binding_links_and_directory_mode_are_not_trusted(self):
        state = Path(client.CLIENT_STATE)
        os.link(state, self.root / "binding-link")
        with self.assertRaises(ValueError):
            client.read_binding()
        (self.root / "binding-link").unlink()
        state.parent.chmod(0o755)
        with self.assertRaises(ValueError):
            client.read_binding()
        state.parent.chmod(0o700)
        state.unlink()
        state.symlink_to(self.native)
        with self.assertRaises(ValueError):
            client.read_binding()

    def test_stopped_identity_is_checked_before_up_and_after_up(self):
        stopped = client._identity(observed("Stopped"), {"ControlURL": URL}, URL, False)
        running = self.observe.return_value
        self.observe.side_effect = [stopped, running]
        self.enable()
        self.assertIn(["tailscale", "up"], self.commands)

    def test_dry_run_no_observations_or_writes(self):
        before = Path(client.CLIENT_STATE).read_bytes()
        self.enable(dry_run=True)
        self.assertEqual(self.commands, [])
        self.observe.assert_not_called()
        self.assertEqual(Path(client.CLIENT_STATE).read_bytes(), before)

    def test_malformed_binding_phases_and_tags_are_value_errors(self):
        for phase in ([], {}, None, 3):
            Path(client.CLIENT_STATE).write_text(json.dumps({"version": 1, "url": URL, "phase": phase, "node_id": ""}))
            with self.subTest(phase=phase), self.assertRaises(ValueError):
                client.read_binding()
        client.save_binding(URL, "prepared")
        for tags in ([[]], [None], "tag:service", 4):
            with self.subTest(tags=tags), self.assertRaises(ValueError):
                client.enroll_client(URL, expected_tags=tags)
        self.assertEqual(self.commands, [])


class PrivacyTests(unittest.TestCase):
    def test_running_environment_required_for_adoption_and_enrollment(self):
        for environment in (b"OTHER=true\0", b"TS_NO_LOGS_NO_SUPPORT=true\0"):
            fields = 'ActiveState=active\nMainPID=123\nEnvironment=TS_NO_LOGS_NO_SUPPORT=true\n'
            with patch.object(client, "native", return_value=fields), \
                    patch.object(client, "check_native_state"), \
                    patch("builtins.open", return_value=io.BytesIO(environment)), \
                    patch.object(remote, "check_directory_path"), \
                    patch.object(remote.os, "lstat", return_value=SimpleNamespace(st_mode=0o100600, st_uid=0, st_nlink=1)):
                if environment.startswith(b"OTHER"):
                    with self.assertRaises(ValueError):
                        client.require_private_daemon()
                else:
                    client.require_private_daemon()

    def test_privacy_failure_blocks_identity_dependent_actions(self):
        with patch.object(client, "client_lock", side_effect=nullcontext), \
                patch.object(client, "resolver_lifecycle_guard", side_effect=nullcontext), \
                patch.object(client, "reject_control_host"), \
                patch.object(client, "read_binding", return_value=None), \
                patch.object(client, "owned", return_value={"phase": "prepared", "node_id": ""}), \
                patch.object(client, "require_private_daemon", side_effect=ValueError("file alone insufficient")), \
                patch.object(client, "native") as native, patch.object(client, "snapshot") as snapshot:
            for action in (lambda: client.adopt_client(URL, expected_node_id="node123"),
                           lambda: client.enroll_client(URL), lambda: client.reconcile(URL),
                           lambda: client.leave_client(URL, expected_node_id="node123")):
                with self.assertRaises(ValueError):
                    action()
            native.assert_not_called()
            snapshot.assert_not_called()
