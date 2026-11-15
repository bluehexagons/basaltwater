"""Offline native client and Debian setup contracts."""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import tailscale_steps as steps
from lib import headscale_client as client

URL = "https://vpn.example.com"
OTHER = "https://other.example.com"


def observed(state="Running", tags=None):
    return {"Version": "1.104.7-tabcdef123-gabcdef123", "TUN": True,
            "BackendState": state, "AuthURL": "private-pending-id",
            "Self": None if state not in {"Running", "Stopped"} else {"ID": "node123", "HostName": "peer", "Online": state == "Running",
                     "TailscaleIPs": ["100.64.0.5", "fd7a:115c:a1e0::5"], "Tags": tags}}


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name, value in (("CLIENT_STATE", str(self.root / "state" / "client.json")),
                            ("CONTROL_CONFIG", str(self.root / "headscale.yaml"))):
            p = patch.object(client, name, value)
            p.start()
            self.addCleanup(p.stop)
        for name in ("client_lock", "resolver_lifecycle_guard"):
            p = patch.object(client, name, side_effect=nullcontext)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(client, "require_private_daemon")
        p.start()
        self.addCleanup(p.stop)

    def test_observation_redacts_and_types(self):
        with patch.object(client, "native", side_effect=[json.dumps({"short": "1.104.7"}),
                          json.dumps(observed()), json.dumps({"ControlURL": URL})]):
            identity = client.observe_client(URL)
        self.assertEqual(identity.node_id, "node123")
        self.assertEqual(identity.addresses, ("100.64.0.5", "fd7a:115c:a1e0::5"))
        self.assertNotIn("private-pending-id", repr(identity))

    def test_invalid_observations(self):
        cases = [("Version", "1.80.0"), ("Version", "1.104.6"),
                 ("BackendState", "Surprise"), ("BackendState", []), ("TUN", False)]
        for field, value in cases:
            status = observed()
            status[field] = value
            with self.subTest(field=field), patch.object(client, "native", side_effect=[
                json.dumps({"short": "1.104.7"}), json.dumps(status), json.dumps({"ControlURL": URL})]):
                with self.assertRaises(client.ClientError):
                    client.observe_client(URL)
        for key, value in (("Online", "true"), ("ID", 1), ("TailscaleIPs", ["8.8.8.8"]),
                           ("TailscaleIPs", ["100.64.0.5", "100.64.0.5"]),
                           ("Tags", ["admin"]), ("HostName", "peer\n")):
            status = observed()
            status["Self"][key] = value
            with self.subTest(key=key), self.assertRaises((client.ClientError, ValueError)):
                client._identity(status, {"ControlURL": URL}, URL, True)

    def test_wrong_network_and_offline(self):
        with self.assertRaises(client.ClientError):
            client._identity(observed(), {"ControlURL": OTHER}, URL, True)
        with self.assertRaises(client.ClientError):
            client._identity(observed("Stopped"), {"ControlURL": URL}, URL, True)
        self.assertFalse(client._identity(observed("Stopped"), {"ControlURL": URL}, URL, False).online)

    def test_private_binding_corruption_and_unowned(self):
        with self.assertRaises(client.ClientError):
            client.owned(URL)
        client.save_binding(URL, "prepared")
        with self.assertRaises(client.ClientError):
            client.owned(OTHER)
        Path(client.CLIENT_STATE).chmod(0o644)
        with self.assertRaises(client.ClientError):
            client.read_binding()
        Path(client.CLIENT_STATE).chmod(0o600)
        Path(client.CLIENT_STATE).write_text('{"version":900}')
        with self.assertRaises(client.ClientError):
            client.read_binding()

    def test_actual_control_host(self):
        Path(client.CONTROL_CONFIG).write_text('server_url: "' + URL + '"\n')
        Path(client.CONTROL_CONFIG).chmod(0o640)
        with self.assertRaises(client.ClientError):
            client.reject_control_host(URL)
        client.reject_control_host(OTHER)
        Path(client.CONTROL_CONFIG).write_text(json.dumps({"server_url": URL}))
        with self.assertRaises(client.ClientError):
            client.reject_control_host(URL)
        client.reject_control_host(OTHER)

    def test_adoption_exact_node(self):
        identity = client._identity(observed(), {"ControlURL": URL}, URL, True)
        with patch.object(client, "observe_client", return_value=identity):
            with self.assertRaises(client.ClientError):
                client.adopt_client(URL, expected_node_id="other")
            self.assertEqual(client.adopt_client(URL, expected_node_id="node123"), identity)
        self.assertEqual(client.read_binding()["node_id"], "node123")

    def key(self):
        path = self.root / "key"
        path.write_text("synthetic-private-machine-key")
        path.chmod(0o600)
        return path

    def test_machine_success_idempotent_secret_absence(self):
        client.save_binding(URL, "prepared")
        key = self.key()
        identity = client._identity(observed(tags=["tag:dns"]), {"ControlURL": URL}, URL, True)
        with patch.object(client, "snapshot", return_value=(observed("NeedsLogin"), {"ControlURL": ""})), \
                patch.object(client, "observe_client", return_value=identity), patch.object(client, "native") as native:
            self.assertEqual(client.enroll_client(URL, key_file=str(key), expected_tags=("tag:dns",)), identity)
        self.assertFalse(key.exists())
        self.assertIn("--auth-key=file:" + str(key), native.call_args.args[0])
        self.assertNotIn("synthetic-private-machine-key", str(native.call_args_list) + Path(client.CLIENT_STATE).read_text())
        with patch.object(client, "snapshot", return_value=(observed(), {"ControlURL": URL})), \
                patch.object(client, "observe_client", return_value=identity), patch.object(client, "native") as native:
            client.enroll_client(URL, expected_tags=("tag:dns",))
            native.assert_not_called()

    def test_interrupted_machine_never_replays(self):
        client.save_binding(URL, "prepared")
        key = self.key()
        with patch.object(client, "snapshot", return_value=(observed("NeedsLogin"), {"ControlURL": URL})), \
                patch.object(client, "native", side_effect=client.ClientError("uncertain")) as native:
            with self.assertRaises(client.ClientError):
                client.enroll_client(URL, key_file=str(key), expected_tags=("tag:dns",))
            self.assertFalse(key.exists())
            self.assertEqual(client.read_binding()["phase"], "joining")
            with self.assertRaises(client.ClientError):
                client.enroll_client(URL, key_file=str(key), expected_tags=("tag:dns",))
            self.assertEqual(native.call_count, 1)

    def test_reconcile_lost_ack(self):
        client.save_binding(URL, "joining")
        identity = client._identity(observed(), {"ControlURL": URL}, URL, True)
        with patch.object(client, "observe_client", return_value=identity):
            client.reconcile(URL)
        self.assertEqual(client.read_binding()["phase"], "enrolled")

    def test_unsafe_keys(self):
        client.save_binding(URL, "prepared")
        for kind in ("permissions", "symlink", "hardlink", "owner"):
            key = self.key()
            if kind == "permissions":
                key.chmod(0o644)
            elif kind == "symlink":
                key.unlink()
                key.symlink_to(self.root / "target")
            elif kind == "hardlink":
                os.link(key, self.root / "linked")
            owner = patch.object(client.os, "geteuid", return_value=-1) if kind == "owner" else nullcontext()
            with self.subTest(kind=kind), owner, patch.object(client, "snapshot", return_value=(observed("NeedsLogin"), {"ControlURL": URL})), patch.object(client, "native") as native:
                with self.assertRaises((ValueError, OSError, client.ClientError)):
                    client.enroll_client(URL, key_file=str(key), expected_tags=("tag:dns",))
                native.assert_not_called()
            key.unlink()

    def test_personal_requires_private_terminal_and_preserves_pending(self):
        client.save_binding(URL, "prepared")
        with patch.object(client, "snapshot", return_value=(observed("NeedsLogin"), {"ControlURL": URL})), \
                patch.object(client.os, "isatty", return_value=False):
            with self.assertRaises(client.ClientError):
                client.enroll_client(URL, interactive=True)
        with patch.object(client, "snapshot", return_value=(observed("NeedsLogin"), {"ControlURL": URL})), \
                patch.object(client.os, "isatty", return_value=True), \
                patch.object(client.subprocess, "run", side_effect=subprocess.TimeoutExpired("tailscale", 65)):
            with self.assertRaises(client.ClientError):
                client.enroll_client(URL, interactive=True)
        self.assertEqual(client.read_binding()["phase"], "joining")

    def test_disable_guard_held_and_no_logout(self):
        client.save_binding(URL, "enrolled", "node123")
        held = []
        @contextmanager
        def guard():
            held.append(True)
            yield
            held.pop()
        def native(args):
            self.assertTrue(held)
            self.assertNotIn("logout", args)
            return "ActiveState=inactive\nUnitFileState=disabled\n" if "--property=ActiveState,UnitFileState" in args else "inactive"
        with patch.object(client, "resolver_lifecycle_guard", guard), patch.object(client, "native", side_effect=native):
            client.disable_client(URL)
        self.assertEqual(client.read_binding()["phase"], "enrolled")

    def test_disable_requires_observed_stopped_and_disabled_state(self):
        client.save_binding(URL, "enrolled", "node123")
        with patch.object(client, "native", side_effect=["inactive", "", "ActiveState=active\nUnitFileState=enabled\n"]):
            with self.assertRaisesRegex(client.ClientError, "not verified"):
                client.disable_client(URL)

    def test_dns_guard_blocks_all_teardown(self):
        with patch.object(client, "resolver_lifecycle_guard", side_effect=ValueError("advertised")), patch.object(client, "native") as native:
            for call in (lambda: client.disable_client(URL), lambda: client.leave_client(URL, expected_node_id="node123"),
                         lambda: client.migrate_client(URL, OTHER, expected_node_id="node123")):
                with self.assertRaises(ValueError):
                    call()
            native.assert_not_called()

    def test_leave_and_migration(self):
        for migration in (False, True):
            client.save_binding(URL, "enrolled", "node123")
            identity = client._identity(observed(), {"ControlURL": URL}, URL, True)
            with patch.object(client, "observe_client", return_value=identity), \
                    patch.object(client, "snapshot", return_value=(observed("NeedsLogin"), {"ControlURL": URL})), patch.object(client, "native") as native:
                if migration:
                    client.migrate_client(URL, OTHER, expected_node_id="node123")
                else:
                    client.leave_client(URL, expected_node_id="node123")
                native.assert_called_once_with(["tailscale", "logout"])
            self.assertEqual(client.read_binding()["phase"], "left")
            self.assertEqual(client.read_binding()["url"], OTHER if migration else URL)

    def test_pending_abandon_requires_confirmation_and_observation(self):
        client.save_binding(URL, "joining")
        with patch.object(client, "native") as native:
            with self.assertRaises(client.ClientError):
                client.abandon_enrollment(URL)
            native.assert_not_called()
        with patch.object(client, "snapshot", return_value=(observed("NeedsLogin"), {"ControlURL": URL})), patch.object(client, "native") as native:
            client.abandon_enrollment(URL, confirm=True)
            native.assert_not_called()
        self.assertEqual(client.read_binding()["phase"], "left")

    def test_logout_uncertainty_retains_intent_and_recovers_without_replay(self):
        identity = client._identity(observed(), {"ControlURL": URL}, URL, True)
        for migration in (False, True):
            for failure in ("reply", "observation"):
                with self.subTest(migration=migration, failure=failure):
                    client.save_binding(URL, "enrolled", "node123")
                    def logout(args):
                        self.assertEqual(args, ["tailscale", "logout"])
                        intent = client.read_binding()
                        self.assertEqual(intent["phase"], "leaving")
                        self.assertEqual(intent["node_id"], "node123")
                        self.assertEqual(intent["url"], URL)
                        if failure == "reply":
                            raise client.ClientError("lost reply")
                    with patch.object(client, "observe_client", return_value=identity), \
                            patch.object(client, "native", side_effect=logout), \
                            patch.object(client, "snapshot", side_effect=client.ClientError("lost observation")):
                        with self.assertRaises(client.ClientError):
                            if migration:
                                client.migrate_client(URL, OTHER, expected_node_id="node123")
                            else:
                                client.leave_client(URL, expected_node_id="node123")
                    before = Path(client.CLIENT_STATE).read_bytes()
                    with patch.object(client, "native") as native, \
                            patch.object(client, "observe_client", return_value=identity), \
                            patch.object(client, "snapshot", return_value=(observed("NeedsLogin"), {"ControlURL": URL})):
                        for action in (lambda: client.reconcile(URL),
                                       lambda: client.leave_client(URL, expected_node_id="node123"),
                                       lambda: client.migrate_client(URL, OTHER, expected_node_id="node123"),
                                       lambda: client.enroll_client(URL),
                                       lambda: client.abandon_enrollment(URL, confirm=True)):
                            with self.assertRaises(client.ClientError):
                                action()
                            self.assertEqual(Path(client.CLIENT_STATE).read_bytes(), before)
                        self.assertIsNone(client.reconcile(URL, confirm_logged_out=True))
                        native.assert_not_called()
                    self.assertEqual(client.read_binding()["url"], OTHER if migration else URL)
                    self.assertEqual(client.read_binding()["phase"], "left")

    def test_logout_reconciliation_refuses_uncertain_or_changed_native_state(self):
        client.save_binding(URL, "leaving", "node123", recovery={"destination_url": OTHER})
        before = Path(client.CLIENT_STATE).read_bytes()
        for status, prefs in ((observed(), {"ControlURL": URL}),
                              (observed("NeedsLogin"), {"ControlURL": OTHER}),
                              ({**observed("NeedsLogin"), "HaveNodeKey": True}, {"ControlURL": URL}),
                              ({**observed("NeedsLogin"), "Self": observed()["Self"]}, {"ControlURL": URL})):
            with self.subTest(status=status, prefs=prefs), patch.object(client, "native") as native, \
                    patch.object(client, "snapshot", return_value=(status, prefs)), self.assertRaises(client.ClientError):
                client.reconcile(URL, confirm_logged_out=True)
            native.assert_not_called()
            self.assertEqual(Path(client.CLIENT_STATE).read_bytes(), before)

    def test_early_up_failure_retains_verified_prestate_for_confirmed_abandonment(self):
        for prestate in (None, "", "https://controlplane.tailscale.com", URL, OTHER):
            with self.subTest(prestate=prestate):
                client.save_binding(URL, "left" if prestate == OTHER else "prepared")
                key = self.key()
                with patch.object(client, "snapshot", return_value=(observed("NeedsLogin"), {"ControlURL": prestate})), \
                        patch.object(client, "native", side_effect=client.ClientError("early up failure")) as native:
                    with self.assertRaises(client.ClientError):
                        client.enroll_client(URL, key_file=str(key), expected_tags=("tag:dns",))
                    self.assertFalse(key.exists())
                    self.assertEqual(client.read_binding()["recovery"], {"prior_url": prestate})
                    with self.assertRaises(ValueError):
                        client.enroll_client(URL, key_file=str(key), expected_tags=("tag:dns",))
                    self.assertEqual(native.call_count, 1)
                before = Path(client.CLIENT_STATE).read_bytes()
                for state, endpoint in ((observed(), prestate), (observed("Stopped"), URL),
                                        (observed("NeedsLogin"), "https://third.example.com"),
                                        ({**observed("NeedsLogin"), "HaveNodeKey": True}, prestate),
                                        (observed("NeedsMachineAuth"), URL)):
                    with patch.object(client, "snapshot", return_value=(state, {"ControlURL": endpoint})), \
                            patch.object(client, "native") as native, self.assertRaises(client.ClientError):
                        client.abandon_enrollment(URL, confirm=True)
                    native.assert_not_called()
                    self.assertEqual(Path(client.CLIENT_STATE).read_bytes(), before)
                with patch.object(client, "snapshot", return_value=(observed("NeedsLogin"), {"ControlURL": prestate})), \
                        patch.object(client, "native") as native:
                    with self.assertRaises(client.ClientError):
                        client.abandon_enrollment(URL)
                    client.abandon_enrollment(URL, confirm=True)
                    native.assert_not_called()
                self.assertEqual(client.read_binding()["phase"], "left")

    def test_publication_exclusion_preserves_all_teardown_intents(self):
        from lib import headscale_dns_binding as dns
        client.save_binding(URL, "leaving", "node123", recovery={"destination_url": OTHER})
        before = Path(client.CLIENT_STATE).read_bytes()
        lease = SimpleNamespace(binding=SimpleNamespace(advertised=True))
        # A publication reservation has the same durable advertised guard as a
        # completed publication; neither may be bypassed by recovery.
        with patch.object(client, "resolver_lifecycle_guard", dns.resolver_lifecycle_guard), \
                patch.object(dns, "dns_binding_lease", return_value=nullcontext(lease)), \
                patch.object(client, "native") as native, patch.object(client, "snapshot") as snapshot:
            for action in (lambda: client.disable_client(URL),
                           lambda: client.leave_client(URL, expected_node_id="node123"),
                           lambda: client.migrate_client(URL, OTHER, expected_node_id="node123"),
                           lambda: client.abandon_enrollment(URL, confirm=True),
                           lambda: client.reconcile(URL, confirm_logged_out=True)):
                with self.assertRaisesRegex(ValueError, "advertised DNS resolver"):
                    action()
                self.assertEqual(Path(client.CLIENT_STATE).read_bytes(), before)
            native.assert_not_called()
            snapshot.assert_not_called()

    def test_recovery_schema_rejects_unverified_or_extra_evidence(self):
        client.save_binding(URL, "joining", recovery={"prior_url": OTHER})
        valid = client.read_binding()
        for changes in ({"version": 1}, {"phase": "enrolled", "node_id": "node123"},
                        {"recovery": {"prior_url": "http://other.example.com"}},
                        {"recovery": {"prior_url": OTHER, "key": "forbidden"}}):
            value = {**valid, **changes}
            Path(client.CLIENT_STATE).write_text(json.dumps(value))
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                client.read_binding()
            self.assertEqual(json.loads(Path(client.CLIENT_STATE).read_text()), value)

    def test_native_bootstrap_default_is_not_an_enrolled_network(self):
        client.check_network(observed("NeedsLogin"), {"ControlURL": "https://controlplane.tailscale.com"}, URL, "prepared")
        for phase in ("joining", "enrolled"):
            with self.assertRaises(client.ClientError):
                client.check_network(observed("NeedsLogin"), {"ControlURL": "https://controlplane.tailscale.com"}, URL, phase)

    def test_bounded_command_rejects_secret_output_without_disclosure(self):
        # Exercise the real adapter while mocking every process/descriptor call.
        with patch.object(client.subprocess, "Popen") as popen, \
                patch.object(client.selectors, "DefaultSelector") as selector, \
                patch.object(client.os, "read", return_value=b"private-auth-url"), \
                patch.object(client.os, "killpg") as kill:
            proc = popen.return_value.__enter__.return_value
            proc.poll.return_value = None
            selection = selector.return_value.__enter__.return_value
            selection.get_map.return_value = {1: True}
            selection.select.return_value = [(SimpleNamespace(fd=1), None)]
            with self.assertRaises(client.ClientError) as error:
                client.native(["tailscale", "debug", "prefs"], limit=2)
            self.assertNotIn("private-auth-url", str(error.exception))
            kill.assert_called_once()


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = self.root / "client" / "binding.json"
        (self.root / "native.state").write_text("opaque native identity")
        (self.root / "native.state").chmod(0o600)
        dropin = self.root / "privacy.conf"
        dropin.write_text(steps.PRIVACY)
        dropin.chmod(0o644)
        replacements = ((client, "CLIENT_STATE", str(self.state)),
                        (client, "NATIVE_STATE", str(self.root / "native.state")),
                        (steps, "DROPIN", str(dropin)))
        for module, name, value in replacements:
            p = patch.object(module, name, value)
            p.start()
            self.addCleanup(p.stop)

    def setup_context(self, state, url=URL):
        from contextlib import ExitStack
        stack = ExitStack()
        stack.enter_context(patch.object(steps, "is_dry_run", return_value=False))
        stack.enter_context(patch.object(steps, "preflight", return_value="trixie"))
        stack.enter_context(patch.object(client, "client_lock", side_effect=nullcontext))
        stack.enter_context(patch.object(client, "resolver_lifecycle_guard", side_effect=nullcontext))
        stack.enter_context(patch.object(client, "reject_control_host"))
        stack.enter_context(patch.object(client, "require_private_daemon"))
        stack.enter_context(patch.object(client, "_object", return_value={"short": "1.104.7"}))
        stack.enter_context(patch.object(steps.shutil, "which", return_value="/mock/tailscale"))
        stack.enter_context(patch.object(client, "snapshot", return_value=(observed(state), {"ControlURL": url})))
        return stack

    def test_setup_same_network_preserves_identity(self):
        client.save_binding(URL, "enrolled", "node123")
        config = SimpleNamespace(tailscale=True, tailscale_login_server=URL)
        identity = client._identity(observed(), {"ControlURL": URL}, URL, True)
        with self.setup_context("Running"), patch.object(client, "observe_client", return_value=identity), \
                patch.object(client, "native", return_value="active") as native, patch.object(steps, "run") as run:
            steps.configure_tailscale(config)
        self.assertEqual(client.read_binding()["node_id"], "node123")
        self.assertFalse(any("logout" in c.args[0] or "--auth-key" in str(c) for c in native.call_args_list))
        run.assert_called_once_with(["systemctl", "enable", "--now", "tailscaled"])

    def test_setup_wrong_observed_network_has_no_mutations(self):
        client.save_binding(URL, "enrolled", "node123")
        config = SimpleNamespace(tailscale=True, tailscale_login_server=URL)
        with self.setup_context("Running", OTHER), patch.object(client, "native", return_value="active"), patch.object(steps, "run") as run:
            with self.assertRaises(client.ClientError):
                steps.configure_tailscale(config)
            run.assert_not_called()

    def test_setup_pending_join_is_not_replayed(self):
        client.save_binding(URL, "joining")
        config = SimpleNamespace(tailscale=True, tailscale_login_server=URL)
        with self.setup_context("NeedsLogin"), patch.object(client, "native", return_value="active") as native, patch.object(steps, "run"):
            steps.configure_tailscale(config)
        self.assertEqual(client.read_binding()["phase"], "joining")
        self.assertFalse(any(c.args[0][:2] == ["tailscale", "up"] for c in native.call_args_list))

    def test_setup_inactive_retained_state_requires_review(self):
        client.save_binding(URL, "enrolled", "node123")
        Path(client.NATIVE_STATE).write_text("opaque native state")
        Path(client.NATIVE_STATE).chmod(0o644)
        config = SimpleNamespace(tailscale=True, tailscale_login_server=URL)
        with self.setup_context("Stopped"), patch.object(client, "native", return_value="inactive"), patch.object(steps, "run") as run:
            with self.assertRaises(client.ClientError):
                steps.configure_tailscale(config)
            run.assert_not_called()

    def test_fresh_install_does_not_probe_missing_unit(self):
        Path(client.NATIVE_STATE).unlink()
        config = SimpleNamespace(tailscale=True, tailscale_login_server=URL)
        with self.setup_context("NeedsLogin"), patch.object(steps.shutil, "which", return_value=None), \
                patch.object(steps, "install_client") as install, patch.object(steps, "run"), \
                patch.object(client, "native") as native:
            steps.configure_tailscale(config)
            install.assert_called_once_with("trixie")
            native.assert_not_called()
        self.assertEqual(client.read_binding()["phase"], "prepared")

    def test_none_and_dry_run_inert(self):
        with patch.object(steps, "preflight") as preflight, patch.object(steps, "is_dry_run", return_value=True):
            steps.configure_tailscale(SimpleNamespace(tailscale=True))
            steps.configure_tailscale(SimpleNamespace(tailscale=None))
            preflight.assert_not_called()
        with patch.object(steps, "preflight") as preflight, patch.object(steps, "is_dry_run", return_value=False):
            steps.configure_tailscale(SimpleNamespace(tailscale=True, dry_run=True))
            preflight.assert_not_called()

    def test_capability_failures(self):
        config = SimpleNamespace(machine_type="vm")
        with patch.object(steps, "validate_headscale_settings"), patch.object(steps, "get_debian_codename", return_value="trixie"), \
                patch.object(steps.os, "geteuid", return_value=0), patch.object(steps, "can_manage_system_services", return_value=False):
            with self.assertRaises(client.ClientError):
                steps.preflight(config)

    def test_install_privacy_before_postinst(self):
        calls = []
        def run(args, **kwargs):
            calls.append(args)
            if args[0] == "curl":
                Path(args[args.index("--output") + 1]).write_bytes(b"synthetic-key")
        with patch.object(steps, "_owned_file", side_effect=lambda p, c: calls.append([p, c])), \
                patch.object(steps, "run", side_effect=run), patch.object(steps, "KEY_SHA256", hashlib.sha256(b"synthetic-key").hexdigest()), \
                patch.object(steps, "missing_packages", return_value=[]), patch.object(client, "native", return_value="  Candidate: 1.104.7\n"), \
                patch.object(client, "_object", return_value={"short": "1.104.7"}):
            steps.install_client("trixie")
        self.assertEqual(calls[0], [steps.DROPIN, steps.PRIVACY])
        self.assertEqual(calls[1], ["systemctl", "daemon-reload"])
        install = next(c for c in calls if c[0] == "apt-get" and "install" in c)
        self.assertIn("APT::Get::AllowUnauthenticated=false", install)
        self.assertNotIn("hold", str(calls))


if __name__ == "__main__":
    unittest.main()
