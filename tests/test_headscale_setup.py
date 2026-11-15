"""Setup orchestration ordering, offline admission and durable ownership."""

from __future__ import annotations

from contextlib import ExitStack, contextmanager, nullcontext, redirect_stdout
from dataclasses import replace
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys

from lib.config import SetupConfig
from lib.headscale_config import merge_headscale_settings
from lib.headscale_management import ManagementBinding
from lib.headscale_registry import HeadscaleServer, list_servers, mutate_servers
from lib.headscale_setup import preflight_setup, run_setup
from lib.headscale_transport import SSHBinding
from lib.setup_common import run_remote_setup


class SetupTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.workspace = temp.name
        Path(temp.name).chmod(0o700)
        self.server = HeadscaleServer("e23c52b1-1e80-4f91-9a68-f15c530c1d21", "mesh",
                                      "https://vpn.example.com", "managed")
        mutate_servers(self.workspace, lambda records: ([self.server], None))
        self.config = SetupConfig(host="worker.example.com", username="agent", system_type="server_lite",
            ssh_key="/private/root_key", tailscale_server="mesh", tailscale_server_id=self.server.id,
            tailscale_login_server=self.server.url, tailscale_tags=["tag:dns"], unbound=True)
        self.authority = ManagementBinding(self.server.id, self.server.url,
            SSHBinding("control.example.com", "/private/control_key", "/private/known_hosts"))
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(redirect_stdout(io.StringIO()))
        self.credentials = self.stack.enter_context(patch("lib.headscale_setup._credentials"))
        self.resolve = self.stack.enter_context(patch("lib.headscale_setup.resolve_management", return_value=self.authority))
        self.journal = self.stack.enter_context(patch("lib.headscale_setup.inspect_enrollment", return_value=None))
        self.events = []
        self.enroll = self.stack.enter_context(patch("lib.headscale_setup.enroll_machine",
            side_effect=lambda *a, **k: self.events.append("enroll")))
        self.bind = self.stack.enter_context(patch("lib.headscale_setup.bind_management"))

    def prepare(self, config, **kwargs):
        self.assertEqual(kwargs, {"client_preparation": True})
        self.assertIsNone(config.unbound)
        self.assertIsNone(config.deploy_specs)
        self.events.append("prepare")
        return 0

    def final(self, config):
        self.assertIs(config, self.config)
        self.assertTrue(config.unbound)
        self.events.append("final")
        return 0

    def run_flow(self, **kwargs):
        return run_setup(self.config, workspace=self.workspace,
            apply_setup=kwargs.get("prepare", self.prepare), apply_profile=kwargs.get("final", self.final))

    def test_enrollment_precedes_unbound_and_original_profile_survives(self):
        before = self.config.to_dict()
        self.assertEqual(0, self.run_flow())
        self.assertEqual(["prepare", "enroll", "final"], self.events)
        self.assertEqual(before, self.config.to_dict())

    def test_each_stage_failure_stops_following_stages(self):
        self.assertEqual(7, self.run_flow(prepare=lambda *a, **k: 7))
        self.assertEqual([], self.events)
        self.enroll.side_effect = RuntimeError("unconfirmed")
        with self.assertRaisesRegex(RuntimeError, "unconfirmed"):
            self.run_flow()
        self.assertEqual(["prepare"], self.events)
        self.events.clear()
        self.enroll.side_effect = lambda *a, **k: self.events.append("enroll")
        self.assertEqual(9, self.run_flow(final=lambda c: 9))
        self.assertEqual(["prepare", "enroll"], self.events)
        self.bind.assert_not_called()

    def test_power_outcome_remains_inside_setup_target_lock(self):
        active = []
        @contextmanager
        def lock(*args, **kwargs):
            active.append(True)
            try:
                yield
            finally:
                active.clear()

        def apply(config, **kwargs):
            self.assertTrue(active)
            if kwargs:
                return self.prepare(config, **kwargs)
            return self.final(config)

        def power(config):
            self.assertTrue(active)
            self.events.append("power")
            return 8

        with patch("lib.setup_common.resource_lock", side_effect=lock), \
                patch("lib.setup_common._run_remote_setup_locked", side_effect=apply):
            self.assertEqual(8, run_remote_setup(self.config, after_setup=power))
        self.assertEqual(["prepare", "enroll", "final", "power"], self.events)

    def test_missing_authority_or_uncertain_journal_rejects_before_preparation(self):
        self.resolve.side_effect = ValueError("missing authority")
        with self.assertRaisesRegex(ValueError, "authority"):
            self.run_flow()
        self.resolve.side_effect = None
        self.journal.return_value = {"phase": "delivering"}
        with self.assertRaisesRegex(ValueError, "retained enrollment"):
            self.run_flow()
        self.assertEqual([], self.events)

    def test_dry_run_with_builder_is_offline_and_does_not_dispatch(self):
        self.config.dry_run = True
        self.config.build_on = "builder"
        self.config.machine_type = "vm"
        self.config.deploy_specs = [["site.example.com", "https://example.com/repo.git"]]
        before = self.config.to_dict()
        with patch("lib.setup_common.resource_lock", side_effect=AssertionError("lock")), \
                patch("lib.setup_common._run_remote_setup_locked", side_effect=AssertionError("transport")), \
                patch("lib.build_setup.run_build_setup", side_effect=AssertionError("builder")):
            self.assertEqual(0, run_remote_setup(self.config))
        self.credentials.assert_not_called()
        self.enroll.assert_not_called()
        self.assertEqual(before, self.config.to_dict())

    def test_untagged_selection_preserves_existing_preparation_dispatch(self):
        self.config.tailscale_tags = None
        self.config.unbound = None
        with patch("lib.setup_common.resource_lock", return_value=nullcontext()), \
                patch("lib.setup_common._run_remote_setup_locked", return_value=0) as apply:
            self.assertEqual(0, run_remote_setup(self.config))
        apply.assert_called_once_with(self.config)
        self.enroll.assert_not_called()

    def test_internal_target_preparation_does_not_dispatch_or_persist_profile(self):
        import remote_setup

        preparation = SetupConfig(host="localhost", username="agent", system_type="server_lite",
                                  machine_type="vm", tailscale_login_server=self.server.url)
        with patch.object(sys, "argv", ["remote_setup.py", "--system-type", "server_lite",
                "--username", "agent", "--prepare-tailscale-client"]), \
                patch("lib.cachyos.is_cachyos", return_value=False), \
                patch.object(remote_setup, "config_from_remote_args", return_value=preparation), \
                patch("plugins.headscale.prepare_tailscale_client") as prepare, \
                patch.object(remote_setup, "_run_configured_setup", side_effect=AssertionError("profile")), \
                patch.object(remote_setup, "save_setup_config", side_effect=AssertionError("persistence")):
            self.assertEqual(0, remote_setup._run_main())
        prepare.assert_called_once_with(preparation)

    def test_enrolled_rerun_reobserves_without_issuing_key(self):
        from lib import headscale_management as management

        identity = {"node_id": "7", "addresses": ["100.64.0.7"], "login_server": self.server.url,
                    "tags": ["tag:dns"], "online": True, "hostname": "worker"}
        node = {"node_id": "7", "id": "7", "user_id": None, "online": True,
                "addresses": ["100.64.0.7"], "tags": ["tag:dns"]}
        actions = []
        def exchange(binding, service, request):
            actions.append((service, request["action"]))
            if service == "client":
                return {"phase": "enrolled", "identity": identity}
            if request["action"] == "identity":
                return {"endpoint": self.server.url, "version": management.VERSION}
            self.assertEqual("inspect", request["action"])
            return {"nodes": [node]}

        with patch.object(management, "resolve_management", return_value=self.authority), \
                patch.object(management, "_journal", return_value=None), \
                patch.object(management, "resource_lock", return_value=nullcontext()), \
                patch.object(management, "exchange", side_effect=exchange), \
                patch.object(management, "write_private", side_effect=AssertionError("new attempt")):
            result = management.enroll_machine(self.server.id,
                SSHBinding("worker.example.com", "/private/key", "/private/known_hosts"), ["tag:dns"])
        self.assertTrue(result["already_enrolled"])
        self.assertEqual([("management", "identity"), ("client", "inspect"), ("management", "inspect")], actions)

    def test_changed_tags_rejected_and_controller_metadata_omitted_from_remote(self):
        changed = replace(self.config, tailscale_tags=["tag:service"])
        with self.assertRaisesRegex(ValueError, "migration"):
            merge_headscale_settings(self.config, changed)
        restored = SetupConfig.from_dict(self.config.host, self.config.system_type, self.config.to_dict())
        self.assertEqual(["tag:dns"], restored.tailscale_tags)
        self.assertIn("--tailscale-tag tag:dns", restored.to_setup_command())
        self.assertNotIn("--tailscale-tag", " ".join(restored.to_remote_args()))
        with self.assertRaisesRegex(ValueError, "named"):
            replace(self.config, tailscale_server=None, tailscale_server_id=None)

    def hosting(self):
        self.config = SetupConfig(host="control.example.com", username="agent", system_type="server_lite",
            ssh_key="/private/root_key", headscale=True, headscale_name="new-mesh",
            headscale_domain="new-vpn.example.com", headscale_base_domain="mesh.example.net",
            headscale_onboarding_domain="join.example.com", headscale_public_ipv4="198.51.100.34")

    def test_hosting_partial_binding_failure_retains_exact_uuid_and_rename(self):
        self.hosting()
        preflight_setup(self.config, workspace=self.workspace)
        ident = self.config.headscale_registration_id
        self.bind.side_effect = RuntimeError("unconfirmed binding")
        with self.assertRaisesRegex(RuntimeError, "binding"):
            self.run_flow(final=lambda c: 0)
        self.assertIn(ident, [s.id for s in list_servers(self.workspace)])
        self.config.headscale_name = "renamed"
        self.bind.side_effect = None
        self.assertEqual(0, self.run_flow(final=lambda c: 0))
        selected = next(s for s in list_servers(self.workspace) if s.id == ident)
        self.assertEqual("renamed", selected.name)
        self.assertEqual("https://join.example.com", selected.onboarding_url)
        self.assertEqual(ident, self.bind.call_args.args[0])
        self.assertEqual("control.example.com", self.bind.call_args.args[1].host)

    def test_hosting_unknown_collision_and_retained_authority_changes_fail_locally(self):
        self.hosting()
        self.config.headscale_name = "mesh"
        with self.assertRaisesRegex(ValueError, "ownership"):
            preflight_setup(self.config, workspace=self.workspace)
        self.config.headscale_name = "new-mesh"
        preflight_setup(self.config, workspace=self.workspace)
        for field, value in (("ssh_key", "/private/other_key"),
                             ("headscale_domain", "other.example.com")):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "authority changed"):
                preflight_setup(replace(self.config, **{field: value}), workspace=self.workspace)
        self.assertEqual([], self.events)

    def test_hosting_final_failure_does_not_register(self):
        self.hosting()
        self.assertEqual(8, self.run_flow(final=lambda c: 8))
        self.assertEqual([self.server], list_servers(self.workspace))
        self.bind.assert_not_called()
