"""Real controller entrypoints reject invalid final requests before side effects."""

from __future__ import annotations

from contextlib import ExitStack, redirect_stdout
from dataclasses import replace
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import basaltwater
from lib.config import SetupConfig
from lib.concurrency import ResourceBusyError, ResourceLockSafetyError
from lib.headscale_registry import HeadscaleServer, mutate_servers


class ControllerTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.workspace = temp.name
        Path(temp.name).chmod(0o700)
        self.server = HeadscaleServer("e23c52b1-1e80-4f91-9a68-f15c530c1d21", "Office",
                                      "https://vpn.example.com", "external")
        self.records(self.server)
        self.cached = SetupConfig(host="target.example.com", username="agent", system_type="server_lite",
                                  tailscale_server="Office", tailscale_server_id=self.server.id,
                                  tailscale_login_server=self.server.url)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(redirect_stdout(io.StringIO()))
        self.load = self.stack.enter_context(patch("basaltwater.load_setup_command", return_value=self.cached))
        self.stack.enter_context(patch("basaltwater.prompt_for_missing_passwords"))
        self.enroll = self.stack.enter_context(patch("lib.build_enrollment_cli.prepare_cli_build_enrollments"))
        self.remote = self.stack.enter_context(patch("basaltwater.run_remote_setup", return_value=0))
        self.save = self.stack.enter_context(patch("basaltwater.save_setup_command"))
        self.credentials = self.stack.enter_context(patch("basaltwater.store_cli_credentials"))
        self.provision = self.stack.enter_context(patch("lib.proxmox_vm.provision_vm"))

    def records(self, *records):
        mutate_servers(self.workspace, lambda current: (list(records), None))

    def args(self, command, *flags):
        parser, _, _ = basaltwater.create_basaltwater_parser(_command=command)
        argv = [command]
        if command == "setup":
            argv.append("server_lite")
        return parser.parse_args([*argv, "target.example.com", "agent", "--workspace", self.workspace, *flags])

    def run_command(self, command, *flags):
        return getattr(basaltwater, f"run_{command}_command")(self.args(command, *flags))

    def test_patch_opt_out_retains_binding_even_after_reference_removal(self):
        self.records()
        with patch("basaltwater._execute_patch_config", return_value=0) as execute:
            self.assertEqual(0, self.run_command("patch", "--no-tailscale"))
        config = execute.call_args.args[0]
        self.assertIs(config.tailscale, False)
        self.assertIsNone(config.tailscale_server)
        self.assertEqual(self.server.id, config.tailscale_server_id)
        self.assertEqual(self.server.url, config.tailscale_login_server)

    def test_omission_and_explicit_selection_follow_rename_in_both_flows(self):
        self.records(replace(self.server, name="Home"))
        for command in ("setup", "patch"):
            for flags in ((), ("--tailscale", "Home")):
                with self.subTest(command=command, flags=flags):
                    self.assertEqual(0, self.run_command(command, *flags, "--dry-run"))
                    config = self.remote.call_args.args[0]
                    self.assertEqual("Home", config.tailscale_server)
                    self.assertEqual(self.server.id, config.tailscale_server_id)
        self.save.assert_not_called()
        self.credentials.assert_not_called()

    def test_direct_url_is_not_overwritten_and_identity_changes_are_rejected(self):
        for command in ("setup", "patch"):
            for url in (self.server.url, "https://other.example.com"):
                with self.subTest(command=command, url=url):
                    self.assertEqual(1, self.run_command(command, "--tailscale-login-server", url))
        self.assert_no_mutations()

    def test_removed_replaced_and_changed_endpoint_fail_before_provisioning(self):
        candidates = ((), (replace(self.server, id="092f1108-5af3-49f1-9c38-434b3d3b355c"),),
                      (replace(self.server, url="https://other.example.com"),))
        for records in candidates:
            self.records(*records)
            for command in ("setup", "patch"):
                self.assertEqual(1, self.run_command(command))
        self.assert_no_mutations()

    def assert_no_mutations(self):
        for mocked in (self.remote, self.save, self.enroll, self.credentials, self.provision):
            mocked.assert_not_called()

    def test_incomplete_hosting_and_conflicting_merged_ingress_fail_early(self):
        for command in ("setup", "patch"):
            self.load.return_value = None if command == "setup" else SetupConfig(
                host=self.cached.host, username="agent", system_type="server_lite")
            self.assertEqual(1, self.run_command(command, "--headscale"))
            flags = ("--headscale", "--headscale-domain", "vpn.example.com",
                     "--headscale-base-domain", "mesh.example.net", "--headscale-public-ipv4",
                     "198.51.100.34", "--cloudflare")
            self.assertEqual(1, self.run_command(command, *flags))
        self.assert_no_mutations()

    def test_direct_and_omitted_dry_runs_create_no_registry_or_locks(self):
        self.load.return_value = None
        before = sorted(Path(self.workspace).iterdir())
        with patch("lib.concurrency.resource_lock", side_effect=AssertionError("lock")):
            self.assertEqual(0, self.run_command("setup", "--tailscale-login-server", self.server.url, "--dry-run"))
            self.assertEqual(self.server.url, self.remote.call_args.args[0].tailscale_login_server)
            self.assertEqual(0, self.run_command("setup", "--dry-run"))
            self.assertIsNone(self.remote.call_args.args[0].tailscale)
        self.assertEqual(before, sorted(Path(self.workspace).iterdir()))
        self.save.assert_not_called()
        self.credentials.assert_not_called()

    def test_cached_direct_selection_preserves_omission_and_opt_out(self):
        self.cached.tailscale_server = None
        self.cached.tailscale_server_id = None
        for command in ("setup", "patch"):
            self.assertEqual(0, self.run_command(command, "--dry-run"))
            self.assertEqual(self.server.url, self.remote.call_args.args[0].tailscale_login_server)
            self.assertEqual(0, self.run_command(command, "--no-tailscale", "--dry-run"))
            self.assertIs(self.remote.call_args.args[0].tailscale, False)
            self.assertEqual(self.server.url, self.remote.call_args.args[0].tailscale_login_server)

    def test_corrupt_registry_is_preserved_on_controller_failure(self):
        path = Path(self.workspace) / "headscale-servers.json"
        path.write_bytes(b"{corrupt-private-state")
        for command in ("setup", "patch"):
            self.assertEqual(1, self.run_command(command))
        self.assertEqual(b"{corrupt-private-state", path.read_bytes())
        self.assert_no_mutations()

    def test_complete_validation_precedes_provisioning_discovery(self):
        self.load.return_value = None
        args = self.args("setup", "--headscale")
        args.host = "198.51.100.35"
        args.hosted_node = "pve.example.com"
        args.machine_type = "vm"
        args.container_memory = "1G"
        args.container_storage = [["root", "local-lvm", "8G"]]
        with patch("basaltwater._load_cached_provisioning_metadata", side_effect=AssertionError("provider discovery")):
            self.assertEqual(1, basaltwater.run_setup_command(args))
        self.assert_no_mutations()

    def test_setup_and_patch_completion_use_selected_workspace(self):
        for command in ("setup", "patch"):
            parser, setup, patch_parser = basaltwater.create_basaltwater_parser()
            selected = setup if command == "setup" else patch_parser
            action = next(a for a in selected._actions if a.dest == "tailscale_server")
            self.assertEqual(["Office"], action.completer("off", self.args(command)))

    def test_remote_lock_errors_return_failure_without_tracebacks(self):
        for error in (ResourceBusyError, ResourceLockSafetyError):
            self.remote.side_effect = error("private lock evidence")
            for command in ("setup", "patch"):
                self.assertEqual(1, self.run_command(command, "--dry-run"))
        self.save.assert_not_called()

    def test_automatic_dry_run_precedes_runtime_builder_and_provider_work(self):
        self.cached.tailscale_tags = ["tag:service"]
        flags = ("--dry-run", "--key", "/private/root_key")
        with patch("lib.headscale_setup.resolve_management") as authority, \
                patch("lib.headscale_setup.inspect_enrollment", return_value=None), \
                patch("lib.headscale_setup._credentials", side_effect=AssertionError("credentials")), \
                patch("basaltwater._prepare_runtime_config_for_cli", side_effect=AssertionError("runtime")):
            authority.return_value.endpoint = self.server.url
            self.assertEqual(0, self.run_command("setup", *flags))
        self.assert_no_mutations()

    def test_fresh_automatic_dry_run_does_not_create_cache_directory(self):
        from lib.cache import load_setup_command

        self.load.side_effect = load_setup_command
        cache = Path(self.workspace) / "setups"
        flags = ("--headscale", "--headscale-name", "mesh", "--headscale-domain", "control.example.com",
                 "--headscale-base-domain", "mesh.example.net", "--headscale-public-ipv4", "192.0.2.10",
                 "--dry-run")
        with patch("lib.cache.get_setup_cache_dir", return_value=str(cache)):
            self.assertEqual(0, self.run_command("setup", *flags))
        self.assertFalse(cache.exists())
        self.assert_no_mutations()

    def test_automatic_requested_restart_failure_is_saved_as_failure(self):
        self.cached.tailscale_tags = ["tag:service"]
        self.remote.side_effect = lambda config, *, after_setup: after_setup(config)
        with patch("lib.headscale_setup.preflight_setup"), \
                patch("basaltwater.restart_after_setup", return_value=8) as restart:
            self.assertEqual(1, self.run_command("setup", "--restart-if-needed"))
        restart.assert_called_once()
        self.assertIs(self.save.call_args.args[3], False)

    def test_automatic_final_failure_is_saved_and_never_requests_restart(self):
        self.cached.tailscale_tags = ["tag:service"]
        self.remote.return_value = 9
        with patch("lib.headscale_setup.preflight_setup"), \
                patch("basaltwater.restart_after_setup") as restart:
            self.assertEqual(1, self.run_command("setup", "--restart-if-needed"))
        restart.assert_not_called()
        self.assertIs(self.save.call_args.args[3], False)

    def test_automatic_flow_provisions_once_and_rejects_authority_before_provider(self):
        self.cached.tailscale_tags = ["tag:service"]
        args = self.args("setup", "--key", "/private/root_key")
        args.host = "198.51.100.35"
        args.hosted_node = "pve.example.com"
        args.machine_type = "vm"
        args.container_memory = "1G"
        args.container_storage = [["root", "local-lvm", "8G"]]
        with patch("basaltwater._prepare_runtime_config_for_cli", side_effect=lambda config: config), \
                patch("lib.headscale_setup.preflight_setup", side_effect=ValueError("authority")):
            self.assertEqual(1, basaltwater.run_setup_command(args))
        self.provision.assert_not_called()
        with patch("basaltwater._prepare_runtime_config_for_cli", side_effect=lambda config: config), \
                patch("lib.headscale_setup.preflight_setup"):
            self.assertEqual(0, basaltwater.run_setup_command(args))
        self.provision.assert_called_once()
