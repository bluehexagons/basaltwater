"""Broker setup arguments, posture, and isolated service boundaries."""

from __future__ import annotations

import argparse
from contextlib import ExitStack, redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import shlex
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from common.privilege_broker_steps import _requester_has_sudo_grants, render_units
from common.web_panel_steps import build_web_panel_manifest, render_web_panel_nginx
from basaltwater import _patch_preserve_keys, create_basaltwater_parser, run_patch_command, run_setup_command
from lib.arg_parser import add_setup_arguments
from lib.cache import load_setup_command, merge_setup_configs, save_setup_command
from lib.config import SetupConfig
from lib.privilege_auth import authenticate
from lib.privilege_setup import DEFAULT_PRIVILEGE_BROKER_PORT, approval_origin, broker_enabled, privilege_broker_origin
from lib.privilege_policy import DEFAULT_REQUEST_TTL_SECONDS
from plugins.common import extend_web_panel_steps


class SetupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.parser, _, _ = create_basaltwater_parser()

    def config(self, **kwargs):
        return SetupConfig(host="vm.example", username="agent", system_type="agent_vm", **kwargs)

    def test_default_port_is_private_transient_and_survives_remote_parsing(self):
        args = self.parser.parse_args(["setup", "agent_vm", "vm.example", "agent", "--approval-panel",
                                  "--approval-panel-password", "test-only independent password"])
        config = SetupConfig.from_args(args, "agent_vm")
        self.assertNotIn("test-only independent password", repr(config))
        self.assertNotIn("privilege_broker_auth", config.to_dict())
        self.assertNotIn("privilege_broker_host", config.to_dict())
        self.assertNotIn("--privilege-broker-auth", " ".join(config.to_setup_command()))
        self.assertEqual(config.privilege_broker_port, DEFAULT_PRIVILEGE_BROKER_PORT)
        self.assertEqual(privilege_broker_origin(config), "https://vm.example:9444")
        self.assertIn("--approval-panel", config.to_setup_command())
        remote = argparse.ArgumentParser()
        add_setup_arguments(remote, for_remote=True)
        args = remote.parse_args(shlex.split(" ".join(config.to_remote_args())))
        args.host = "localhost"
        received = SetupConfig.from_args(args, "agent_vm")
        self.assertEqual(received.privilege_broker_auth, config.privilege_broker_auth)
        self.assertEqual(received.privilege_broker_port, DEFAULT_PRIVILEGE_BROKER_PORT)
        self.assertEqual(privilege_broker_origin(received), "https://vm.example:9444")

    def test_custom_port_is_accepted_and_url_is_rejected(self):
        args = self.parser.parse_args(["setup", "agent_vm", "vm.example", "agent", "--approval-panel", "9445"])
        config = SetupConfig.from_args(args, "agent_vm")
        self.assertEqual(config.privilege_broker_port, 9445)
        self.assertEqual(privilege_broker_origin(config), "https://vm.example:9445")
        self.assertIn("--approval-panel 9445", config.to_setup_command())
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.parser.parse_args(["setup", "agent_vm", "vm.example", "agent", "--approval-panel",
                               "https://vm.example:9444"])

    def test_standalone_port_cannot_use_the_internal_reenable_sentinel(self):
        for port in ("0", "-1", "1023", "65536"):
            with self.subTest(port=port), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                self.parser.parse_args(["setup", "agent_vm", "vm.example", "agent", "--approval-panel", port])

    def test_ipv4_cidr_host_uses_address_for_approval_origin(self):
        config = SetupConfig(host="10.100.0.106/16", username="agent", system_type="agent_vm",
                             privilege_broker_port=9444)
        self.assertEqual(privilege_broker_origin(config), "https://10.100.0.106:9444")

    def test_malformed_cidr_host_is_rejected(self):
        for host in ("10.100.0.106/33", "10.100.0.999/16", "vm.example/16"):
            with self.subTest(host=host):
                with self.assertRaises(ValueError):
                    SetupConfig(host=host, username="agent", system_type="agent_vm",
                                privilege_broker_port=9444)

    def test_explicit_privilege_broker_host_takes_precedence(self):
        config = SetupConfig(host="10.100.0.106/16", username="agent", system_type="agent_vm",
                             privilege_broker_host="approval.example", privilege_broker_port=9444)
        self.assertEqual(privilege_broker_origin(config), "https://approval.example:9444")

    def test_command_request_keeps_the_top_level_command(self):
        args = self.parser.parse_args([
            "agent", "privilege", "request", "command.run", "--reason", "Test", "--command", "/usr/bin/true",
        ])
        self.assertEqual(args.command, "agent")
        self.assertEqual(args.privilege_argv, ["/usr/bin/true"])

    def test_omitted_patch_preserves_and_explicit_disable_removes(self):
        cached = self.config(privilege_broker_port=9444)
        for flags, expected in (([], cached.privilege_broker_port), (["--no-privilege-broker"], None)):
            args = self.parser.parse_args(["patch", "vm.example", "agent", *flags])
            config = merge_setup_configs(cached, SetupConfig.from_args(args, "agent_vm"), preserve_keys=_patch_preserve_keys(args))
            self.assertEqual(config.privilege_broker_port, expected)

    def test_password_only_patch_rotates_without_losing_origin(self):
        cached = self.config(privilege_broker_port=9444)
        args = self.parser.parse_args(["patch", "vm.example", "agent", "--privilege-broker-password", "test-only changed password"])
        config = merge_setup_configs(cached, SetupConfig.from_args(args, "agent_vm"), preserve_keys=_patch_preserve_keys(args))
        self.assertEqual(config.privilege_broker_port, cached.privilege_broker_port)
        self.assertEqual(json.loads(config.privilege_broker_auth)["username"], "agent")

    def test_rejects_conflicting_postures_and_ports(self):
        for extra in ({"nopasswd": True}, {"harden_agent": True}, {"harden_user": True},
                      {"machine_type": "oci"}, {"web_panel_port": 9444}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.config(privilege_broker_port=9444, **extra)
        with self.assertRaises(ValueError):
            self.config(privilege_broker_port=1023)
        with self.assertRaises(ValueError):
            self.config(privilege_broker_port=8444)

    def test_port_firewall_panel_link_and_step_order(self):
        config = self.config(privilege_broker_port=9444, web_panel_port=443, enable_ssl=True)
        self.assertIn(9444, config.effective_web_ports())
        services = build_web_panel_manifest(config, ["vm.example"])["services"]
        self.assertIn("https://vm.example:9444/", [service["url"] for service in services])
        steps = []
        extend_web_panel_steps(config, steps)
        self.assertEqual([step.__name__ for _, step in steps], ["configure_privilege_broker", "configure_web_panel"])

    def test_runner_host_approvals_do_not_select_agent_workloads(self):
        from plugins.server import build_github_runner_steps

        args = self.parser.parse_args(["setup", "server_github_runner", "vm.example", "operator",
                                 "--ci-repository", "owner/project", "--ci-allowed-user", "owner",
                                 "--web-panel", "--ssl", "--approval-panel"])
        config = SetupConfig.from_args(args, "server_github_runner")
        self.assertFalse(config.has_agent_features())
        self.assertEqual(config.selected_agent_tools(), [])
        self.assertNotIn(8080, config.effective_web_ports())
        manifest = build_web_panel_manifest(config, ["vm.example"])
        self.assertEqual(manifest["features"]["agent_tools"], [])
        self.assertFalse(manifest["features"]["git"])
        self.assertIn("https://vm.example:9444/", [service["url"] for service in manifest["services"]])
        names = [step.__name__ for _, step in build_github_runner_steps(config)]
        self.assertLess(names.index("setup_user"), names.index("configure_agent_user_security"))
        self.assertLess(names.index("configure_agent_user_security"), names.index("configure_privilege_broker"))
        received = argparse.ArgumentParser()
        add_setup_arguments(received, for_remote=True)
        remote = received.parse_args(shlex.split(" ".join(config.to_remote_args())))
        remote.host = "localhost"
        self.assertEqual(SetupConfig.from_args(remote, "server_github_runner").privilege_broker_port, 9444)
        for flag in ("--no-privilege-broker", "--no-approval-panel"):
            with self.subTest(flag=flag):
                args = self.parser.parse_args(["patch", "vm.example", "operator", flag])
                with patch("basaltwater.load_setup_command", return_value=config), patch("basaltwater._execute_patch_config", return_value=0) as execute:
                    self.assertEqual(run_patch_command(args), 0)
                merged = execute.call_args.args[0]
                self.assertIsNone(merged.privilege_broker_port)
                self.assertEqual(broker_enabled(merged), flag == "--no-approval-panel")
                disabled = [step.__name__ for _, step in build_github_runner_steps(merged)]
                self.assertIn("configure_agent_user_security", disabled)
                self.assertIn("configure_privilege_broker", disabled)

    def test_runner_patch_preserves_or_replaces_the_complete_scope(self):
        from lib.github_runner_config import runner_repositories

        for cached_scope in ({"ci_repository": "owner/one"}, {"ci_repositories": ["owner/one", "owner/two"]},
                             {"ci_organization": "owner", "ci_runner_group": "Private CI"}):
            cached = SetupConfig(host="vm.example", username="operator", system_type="server_github_runner",
                                 ci_allowed_users=["alice"], ci_labels=["ci"], **cached_scope)
            for flags in (["--approval-panel"], ["--ci-repository", "owner/three"],
                          ["--ci-organization", "other"], ["--ci-repository", "owner/one", "--ci-repository", "owner/two"]):
                with self.subTest(cached=cached_scope, flags=flags):
                    args = self.parser.parse_args(["patch", "vm.example", "operator", *flags])
                    with patch("basaltwater.load_setup_command", return_value=cached), patch("basaltwater._execute_patch_config", return_value=0) as execute:
                        self.assertEqual(run_patch_command(args), 0)
                    updated = execute.call_args.args[0]
                    self.assertEqual(updated.ci_allowed_users, ["alice"])
                    self.assertEqual(updated.ci_labels, ["ci"])
                    if flags[0] == "--approval-panel":
                        self.assertEqual(runner_repositories(updated), runner_repositories(cached))
                        self.assertEqual(updated.ci_organization, cached.ci_organization)
                        self.assertEqual(updated.ci_runner_group, cached.ci_runner_group)
                        self.assertEqual(updated.privilege_broker_port, 9444)
                    elif flags[0] == "--ci-organization":
                        self.assertEqual(updated.ci_organization, "other")
                        self.assertEqual(runner_repositories(updated), [])
                    else:
                        self.assertEqual(updated.ci_organization, "")
                        self.assertEqual(updated.ci_runner_group, "")
                        self.assertEqual(runner_repositories(updated), flags[1::2])

    def test_runner_approvals_require_a_separate_non_root_vm_operator(self):
        from lib.github_runner_config import ACCOUNT

        for extra in ({"username": "root"}, {"username": ACCOUNT}, {"machine_type": "hardware"},
                      {"nopasswd": True}, {"agent_tools": ["codex"]}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                SetupConfig(**{"host": "vm.example", "username": "operator", "system_type": "server_github_runner",
                               "ci_repository": "owner/project", "ci_allowed_users": ["owner"],
                               "privilege_broker_port": 9444, **extra})

    def test_units_have_distinct_identity_and_private_credentials(self):
        units = render_units()
        broker = units["basaltwater-privilege-broker"]
        web = units["basaltwater-privilege-approval"]
        self.assertIn("User=root", broker)
        self.assertIn("User=basaltwater-approval", web)
        self.assertIn("LoadCredential=auth:", web)
        self.assertNotIn("SupplementaryGroups", web)
        self.assertIn("python3 -I /opt/basaltwater/", broker)
        self.assertIn("StateDirectoryMode=0700", broker)
        self.assertIn("ProtectSystem=strict", broker)
        self.assertNotIn("ReadWritePaths=/", broker)
        self.assertIn("ProtectSystem=strict", web)

    def test_panel_defaults_opt_out_and_reenable_survive_patch_and_transport(self):
        cached = self.config(web_panel_port=80)
        self.assertTrue(broker_enabled(cached))
        self.assertIsNone(cached.privilege_broker_port)
        self.assertIsNone(cached.privilege_broker_auth)
        self.assertEqual(approval_origin(cached), "http://vm.example:80")
        self.assertTrue(build_web_panel_manifest(cached, ["vm.example"])["features"]["approvals"])
        for flags, enabled in ((["--no-privilege-broker"], False), ([], False), (["--privilege-broker"], True)):
            args = self.parser.parse_args(["patch", "vm.example", "agent", *flags])
            cached = merge_setup_configs(cached, SetupConfig.from_args(args, "agent_vm"),
                                         preserve_keys=_patch_preserve_keys(args))
            self.assertEqual(broker_enabled(cached), enabled)
            remote = argparse.ArgumentParser()
            add_setup_arguments(remote, for_remote=True)
            parsed = remote.parse_args(shlex.split(" ".join(cached.to_remote_args())))
            parsed.host = "localhost"
            received = SetupConfig.from_args(parsed, "agent_vm")
            self.assertEqual(broker_enabled(received), enabled)
            if enabled:
                self.assertEqual(approval_origin(received), "http://vm.example:80")

    def test_remove_standalone_retains_default_panel_approvals(self):
        cached = self.config(web_panel_port=443, enable_ssl=True, privilege_broker_port=9444)
        args = self.parser.parse_args(["patch", "vm.example", "agent", "--no-approval-panel"])
        updated = merge_setup_configs(cached, SetupConfig.from_args(args, "agent_vm"),
                                      preserve_keys=_patch_preserve_keys(args))
        self.assertTrue(broker_enabled(updated))
        self.assertIsNone(updated.privilege_broker_port)
        self.assertIn("--no-approval-panel", updated.to_setup_command())

    def test_disabling_engine_after_removing_standalone_serializes_one_selection(self):
        config = self.config(web_panel_port=80, disable_approval_panel=True, disable_privilege_broker=True)
        remote = argparse.ArgumentParser()
        add_setup_arguments(remote, for_remote=True)
        args = remote.parse_args(shlex.split(" ".join(config.to_remote_args())))
        args.host = "localhost"
        self.assertFalse(broker_enabled(SetupConfig.from_args(args, "agent_vm")))
        self.assertNotIn("--no-approval-panel", config.to_setup_command())

    def test_refresh_recovers_panel_origin_with_or_without_existing_policy(self):
        from lib import refresh

        for existing_policy in (False, True):
            with self.subTest(existing_policy=existing_policy), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                saved, manifest, policy_path = (root / name for name in ("setup.json", "panel.json", "policy.json"))
                config = self.config(web_panel_port=443, enable_ssl=True)
                saved.write_text(json.dumps({**config.to_dict(), "system_type": "agent_vm"}))
                saved.chmod(0o600)
                manifest.write_text(json.dumps({"panel_url": "https://vm.example/"}))
                if existing_policy:
                    policy_path.write_text("{}")
                with (patch.object(refresh, "SETUP_CONFIG_FILE", str(saved)),
                      patch.object(refresh, "_validate_setup_config", return_value=None),
                      patch("lib.privilege_policy.POLICY_PATH", str(policy_path)),
                      patch("lib.privilege_policy.protected_path") as protect,
                      patch("lib.privilege_policy.load_policy", return_value={
                          "origin": "https://vm.example:443", "requester_uid": 1000}),
                      patch.object(refresh.pwd, "getpwnam", return_value=SimpleNamespace(pw_uid=1000)),
                      patch("common.web_panel_steps.WEB_PANEL_MANIFEST", str(manifest))):
                    restored = refresh._debian_setup()
                self.assertEqual(approval_origin(restored), "https://vm.example:443")
                protect.assert_any_call(str(manifest))

    def test_integrated_units_have_no_standalone_credential_or_listener(self):
        web = render_units(panel_origin="https://vm.example:443", standalone=False,
                           username="agent")["basaltwater-privilege-approval"]
        self.assertIn("User=basaltwater-approval", web)
        self.assertIn("SupplementaryGroups=www-data", web)
        self.assertIn("RuntimeDirectoryMode=0711", web)
        self.assertIn("BASALTWATER_STANDALONE=0", web)
        self.assertNotIn("LoadCredential", web)

    def test_nginx_approval_routes_require_panel_auth_and_use_the_isolated_socket(self):
        from lib.privilege_policy import PANEL_APPROVAL_SOCKET

        document = render_web_panel_nginx(["vm.example"], 443, approvals=True)
        self.assertIn('auth_basic_user_file ', document)
        route_pattern = 'location ~ "^/(approvals(?:/admin(?:/[a-z-]+)?)?$|requests/[a-f0-9]{32}$)"'
        self.assertIn(route_pattern, document)
        route = document.split(route_pattern, 1)[1].split('\n    }', 1)[0]
        self.assertIn('proxy_pass http://unix:' + PANEL_APPROVAL_SOCKET, route)
        self.assertIn('proxy_set_header X-Basaltwater-User $remote_user;', route)
        self.assertIn('proxy_set_header Authorization "";', route)
        self.assertNotIn('auth_basic off', route)
        self.assertNotIn(PANEL_APPROVAL_SOCKET, render_web_panel_nginx(["vm.example"], 443))

    @patch("common.privilege_broker_steps.run")
    def test_sudo_denial_output_is_authoritative(self, run):
        run.return_value = SimpleNamespace(
            returncode=2,
            stdout="",
            stderr="User agent is not allowed to run sudo on agent-2.",
        )

        self.assertFalse(_requester_has_sudo_grants("agent"))

    @patch("common.privilege_broker_steps.run")
    def test_sudo_query_failure_is_not_treated_as_a_denial(self, run):
        run.return_value = SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="sudo: /etc/sudoers is unreadable",
        )

        with self.assertRaisesRegex(RuntimeError, "Could not determine"):
            _requester_has_sudo_grants("agent")

    @patch("common.privilege_broker_steps.run")
    @patch("common.privilege_broker_steps.is_dry_run", return_value=True)
    def test_dry_run_does_not_mutate_system(self, _dry, run):
        from common.privilege_broker_steps import configure_privilege_broker
        configure_privilege_broker(self.config(privilege_broker_port=9444))
        run.assert_not_called()


class PasswordCLITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parser, _, _ = create_basaltwater_parser()
        cls.remote_parser = argparse.ArgumentParser()
        add_setup_arguments(cls.remote_parser, for_remote=True)

    def setUp(self):
        self.workspace = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.dict(os.environ, {"BASALTWATER_WORKSPACE": str(self.workspace)}))
        self.output = self.enterContext(patch("sys.stdout", new_callable=io.StringIO))
        self.enterContext(patch("basaltwater.prompt_for_missing_passwords"))
        self.enterContext(patch("basaltwater.store_cli_credentials"))
        self.remote = self.enterContext(patch("basaltwater.run_remote_setup", return_value=0))
        self.prompt = self.enterContext(patch("getpass.getpass", return_value="test-only independent password"))
        for name in ("subprocess.run", "subprocess.Popen", "socket.socket", "socket.getaddrinfo"):
            self.enterContext(patch(name, side_effect=AssertionError("Unexpected system action: " + name)))

    def run_command(self, command, *flags):
        positionals = ["agent_vm", "vm.example", "agent"] if command == "setup" else ["vm.example", "agent"]
        args = self.parser.parse_args([command, *positionals, "--timezone", "UTC", *flags])
        run = run_setup_command if command == "setup" else run_patch_command
        return run(args)

    def assert_password_transport(self, port):
        import base64

        config = self.remote.call_args.args[0]
        self.assertEqual(config.privilege_broker_port, port)
        parsed = self.remote_parser.parse_args(shlex.split(" ".join(config.to_remote_args())))
        parsed.host = "localhost"
        received = SetupConfig.from_args(parsed, "agent_vm")
        self.assertEqual(received.privilege_broker_port, port)
        self.assertEqual(received.privilege_broker_auth, config.privilege_broker_auth)
        header = "Basic " + base64.b64encode(b"agent:test-only independent password").decode()
        self.assertTrue(authenticate(header, json.loads(received.privilege_broker_auth)))
        saved = load_setup_command("vm.example")
        self.assertEqual(saved.privilege_broker_port, port)
        self.assertIsNone(saved.privilege_broker_auth)
        self.assertNotIn("test-only independent password", self.output.getvalue())
        for path in self.workspace.rglob("*.json"):
            self.assertNotIn("test-only independent password", path.read_text())
            self.assertNotIn(config.privilege_broker_auth, path.read_text())

    def test_setup_password_selects_standalone_with_either_alias_and_panel_mode(self):
        cases = (
            (["--web-panel"], "--privilege-broker-password", 9444),
            (["--web-panel", "--privilege-broker"], "--privilege-broker-password", 9444),
            ([], "--approval-panel-password", 9444),
            (["--privilege-broker"], "--approval-panel-password", 9444),
            (["--web-panel", "--approval-panel", "9445"], "--approval-panel-password", 9445),
        )
        for flags, password_flag, port in cases:
            with self.subTest(flags=flags, password_flag=password_flag):
                self.assertEqual(self.run_command("setup", *flags, password_flag,
                                                 "test-only independent password"), 0, self.output.getvalue())
                self.assert_password_transport(port)
        self.prompt.assert_not_called()

    def test_prompt_only_password_selects_standalone_and_dry_run_never_prompts(self):
        self.assertEqual(self.run_command("setup", "--web-panel", "--privilege-broker-password"), 0,
                         self.output.getvalue())
        self.prompt.assert_called_once()
        self.assert_password_transport(9444)
        self.prompt.reset_mock()
        self.assertEqual(self.run_command("setup", "--web-panel", "--approval-panel-password", "--dry-run"), 0,
                         self.output.getvalue())
        self.prompt.assert_not_called()
        config = self.remote.call_args.args[0]
        self.assertEqual(config.privilege_broker_port, 9444)
        self.assertIsNone(config.privilege_broker_auth)

    def test_password_patch_selects_or_preserves_standalone_and_explicit_port_wins(self):
        for saved_port, flags, expected in (
            (None, [], 9444), (9445, [], 9445),
            (9445, ["--privilege-broker"], 9445), (9445, ["--approval-panel", "9446"], 9446),
        ):
            with self.subTest(saved_port=saved_port, flags=flags):
                save_setup_command(SetupConfig(host="vm.example", username="agent", system_type="agent_vm",
                                               web_panel_port=80, privilege_broker_port=saved_port))
                self.assertEqual(self.run_command("patch", *flags, "--privilege-broker-password",
                                                 "test-only independent password"), 0, self.output.getvalue())
                self.assert_password_transport(expected)

    def test_password_conflicts_with_explicit_removal_before_prompt_or_execution(self):
        for command in ("setup", "patch"):
            for flag in ("--no-privilege-broker", "--no-approval-panel"):
                with self.subTest(command=command, flag=flag):
                    save_setup_command(SetupConfig(host="vm.example", username="agent", system_type="agent_vm",
                                                   web_panel_port=80))
                    self.assertEqual(self.run_command(command, flag, "--approval-panel-password"), 1)
                    self.assertIn("Cannot change approval", self.output.getvalue())
        self.prompt.assert_not_called()
        self.remote.assert_not_called()

    def test_implied_standalone_validates_posture_machine_and_port_before_execution(self):
        for flags, error in ((["--nopasswd"], "cannot be combined"),
                             (["--machine", "hardware"], "supported on VMs only"),
                             (["--web-panel", "9444"], "conflicts with another managed interface")):
            with self.subTest(flags=flags):
                self.output.seek(0)
                self.output.truncate()
                self.assertEqual(self.run_command("setup", *flags, "--approval-panel-password",
                                                 "test-only independent password"), 1)
                self.assertIn(error, self.output.getvalue())
        self.remote.assert_not_called()


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        from common import privilege_broker_steps as steps
        from lib.privilege_auth import password_record
        self.steps = steps
        self.stack = self.enterContext(ExitStack())
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.directory = self.root / "config"
        self.directory.mkdir(mode=0o755)
        self.units = self.root / "units"
        self.units.mkdir(mode=0o755)
        self.policy_path = self.directory / "policy.json"
        self.auth_path = self.directory / "auth.json"
        self.polkit_path = self.root / "polkit" / "rules.d" / "broker.rules"
        self.polkit_path.parent.mkdir(parents=True, mode=0o755)
        for directory in (self.polkit_path.parent.parent, self.polkit_path.parent):
            directory.chmod(0o755)
        self.record = password_record("agent", "test-only approval password")
        self.config = SetupConfig(host="vm.example", username="agent", system_type="agent_vm",
                                  privilege_broker_port=9444,
                                  privilege_broker_auth=json.dumps(self.record))
        self.run = Mock(return_value=SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="User agent is not allowed to run sudo on vm.example.",
        ))
        self.states = {name: dict(LoadState="not-found", ActiveState="inactive", SubState="dead", UnitFileState="")
                       for name in (steps.BROKER, steps.WEB)}
        self.command_failure = lambda _command: None
        self.run.side_effect = self.system_command
        self.ready = Mock(return_value=True)
        constants = {"CONFIG_DIR": str(self.directory), "POLICY_PATH": str(self.policy_path),
                     "AUTH_PATH": str(self.auth_path), "POLKIT_PATH": str(self.polkit_path),
                     "UNIT_ROOT": str(self.units)}
        for name, value in constants.items():
            self.stack.enter_context(patch.object(steps, name, value))
        functions = {"is_dry_run": Mock(return_value=False), "can_manage_system_services": Mock(return_value=True),
                     "is_vm": Mock(return_value=True), "protected_path": Mock(), "_approval_account": Mock(),
                     "run": self.run, "is_service_active": Mock(side_effect=lambda name: self.states[name]["ActiveState"] == "active"),
                     "_https_ready": self.ready}
        for name, value in functions.items():
            self.stack.enter_context(patch.object(steps, name, value))
        self.stack.enter_context(patch("lib.unit_transaction.run", self.run))
        real_fstat = os.fstat

        def target_fstat(descriptor):
            metadata = real_fstat(descriptor)
            if stat.S_ISDIR(metadata.st_mode):
                values = list(metadata)
                values[4] = 0  # Model root-owned directories without host chown.
                return os.stat_result(values)
            return metadata

        self.stack.enter_context(patch.object(steps.os, "fstat", side_effect=target_fstat))
        self.chown = self.stack.enter_context(patch.object(steps.os, "fchown"))
        self.stack.enter_context(patch.object(steps.os, "walk", return_value=[]))
        self.stack.enter_context(patch.object(steps.pwd, "getpwnam",
            return_value=SimpleNamespace(pw_name="agent", pw_uid=1000, pw_gid=1000)))
        self.stack.enter_context(patch("common.agent_security_steps._account_groups", return_value=set()))
        self.stack.enter_context(patch.object(steps.time, "sleep"))
        self.stack.enter_context(patch.object(steps, "load_policy",
            side_effect=lambda: json.loads(self.policy_path.read_text())))
        cert = self.root / "cert"
        cert.write_text("test certificate fixture")
        key = self.root / "key"
        key.write_text("test key fixture")
        self.gateway = self.stack.enter_context(patch("common.godot_web_steps.configure_internal_certificate",
            return_value=(str(cert), str(key), False, False)))
        self.stack.enter_context(patch.object(steps, "_panel_ready", return_value=True))
        self.stack.enter_context(patch("common.godot_web_steps.discover_local_web_identities", return_value=[]))

    def system_command(self, command, **_kwargs):
        failure = self.command_failure(command)
        if failure is not None:
            raise failure
        if command[0] == "sudo":
            return self.run.return_value
        if command[:2] == ["systemd-analyze", "verify"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        self.assertEqual(command[0], "systemctl")
        action = command[1]
        if action == "daemon-reload":
            for name, state in self.states.items():
                if (self.units / (name + ".service")).exists():
                    state["LoadState"] = "loaded"
                    if not state["UnitFileState"]:
                        state["UnitFileState"] = "disabled"
                else:
                    state["LoadState"] = "not-found"
                    state["UnitFileState"] = ""
        else:
            name = next(value.removesuffix(".service") for value in command[2:] if value.endswith(".service"))
            state = self.states[name]
            if action == "show":
                return SimpleNamespace(returncode=0, stdout="\n".join(f"{key}={value}" for key, value in state.items()), stderr="")
            if action == "stop":
                state.update(ActiveState="inactive", SubState="dead")
            elif action == "disable":
                state["UnitFileState"] = "disabled" if (self.units / (name + ".service")).exists() else ""
            elif action == "enable":
                state["UnitFileState"] = "enabled-runtime" if "--runtime" in command else "enabled"
            elif action == "restart":
                state.update(LoadState="loaded", ActiveState="active", SubState="running")
            else:
                self.fail("Unexpected system command: " + repr(command))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def broker_snapshot(self):
        paths = [self.policy_path, self.auth_path, self.directory / "server.crt", self.directory / "server.key",
                 self.polkit_path, *(self.units / (name + ".service") for name in self.states)]
        return {path: (path.read_bytes(), stat.S_IMODE(path.stat().st_mode), path.stat().st_uid, path.stat().st_gid)
                for path in paths if path.exists()}

    def test_install_preserve_policy_and_rotate_password(self):
        self.steps.configure_privilege_broker(self.config)
        original = json.loads(self.policy_path.read_text())
        self.assertEqual(original["ttl_seconds"], DEFAULT_REQUEST_TTL_SECONDS)
        original["ttl_seconds"] = 300  # Preserve an existing administrator choice.
        original["services"] = {"reviewed.service": "allow"}
        self.policy_path.write_text(json.dumps(original))
        self.config.privilege_broker_auth = None
        self.steps.configure_privilege_broker(self.config)
        self.assertEqual(json.loads(self.policy_path.read_text()), original)
        self.assertEqual(json.loads(self.auth_path.read_text()), self.record)
        self.assertEqual(self.auth_path.stat().st_mode & 0o777, 0o600)
        from lib.privilege_auth import password_record
        changed = password_record("agent", "different test-only password")
        self.config.privilege_broker_auth = json.dumps(changed)
        self.steps.configure_privilege_broker(self.config)
        self.assertEqual(json.loads(self.auth_path.read_text()), changed)
        self.assertTrue(self.polkit_path.read_text().startswith("// Managed"))

    def test_integrated_install_needs_no_independent_password_vm_or_gateway(self):
        self.config = SetupConfig(host="vm.example", username="agent", system_type="server_lite",
                                  machine_type="hardware", web_panel_port=80)
        with patch.object(self.steps, "is_vm", return_value=False):
            self.steps.configure_privilege_broker(self.config)
        self.gateway.assert_not_called()
        self.assertFalse(self.auth_path.exists())
        self.assertFalse(self.polkit_path.exists())
        self.assertFalse(any(call.args[0][0] == "sudo" for call in self.run.call_args_list))
        self.assertEqual(json.loads(self.policy_path.read_text())["origin"], "http://vm.example:80")
        self.assertIn("BASALTWATER_STANDALONE=0", (self.units / (self.steps.WEB + ".service")).read_text())

    def test_removing_standalone_preserves_engine_and_removes_private_credentials(self):
        self.steps.configure_privilege_broker(self.config)
        self.config.web_panel_port = 443
        self.config.enable_ssl = True
        self.config.privilege_broker_auth = None
        self.config.disable_approval_panel = True
        self.steps.configure_privilege_broker(self.config)
        self.assertTrue(all(state["ActiveState"] == "active" for state in self.states.values()))
        for path in (self.auth_path, self.directory / "server.crt", self.directory / "server.key", self.polkit_path):
            self.assertFalse(path.exists())
        self.assertEqual(json.loads(self.policy_path.read_text())["origin"], "https://vm.example:443")

    def test_root_panel_uses_a_dedicated_non_root_requester(self):
        self.config = SetupConfig(host="vm.example", username="root", system_type="server_proxmox",
                                  web_panel_port=443, enable_ssl=True)
        account = SimpleNamespace(pw_name="basaltwater-web-panel", pw_uid=997, pw_gid=997)
        with patch("common.web_panel_steps._ensure_web_panel_service_account", return_value=account):
            self.steps.configure_privilege_broker(self.config)
        self.assertEqual(json.loads(self.policy_path.read_text())["requester_uid"], 997)
        self.assertFalse(self.auth_path.exists())
        self.assertIn("BASALTWATER_PANEL_USER=root", (self.units / (self.steps.WEB + ".service")).read_text())

    def test_failed_transition_to_main_panel_restores_standalone_authority(self):
        self.steps.configure_privilege_broker(self.config)
        original = self.broker_snapshot()
        self.config = SetupConfig(host="vm.example", username="agent", system_type="agent_vm", web_panel_port=80)
        with patch.object(self.steps, "_panel_ready", return_value=False), self.assertRaises(RuntimeError):
            self.steps.configure_privilege_broker(self.config)
        self.assertEqual(self.broker_snapshot(), original)
        self.assertTrue(all(state["ActiveState"] == "active" for state in self.states.values()))

    def test_runner_policy_grants_only_the_operator_requester_access(self):
        self.config.system_type = "server_github_runner"
        lookup = self.stack.enter_context(patch.object(self.steps.pwd, "getpwnam",
            side_effect=lambda name: SimpleNamespace(pw_name=name, pw_uid=1000 if name == "agent" else 1001, pw_gid=1000)))
        self.steps.configure_privilege_broker(self.config)
        policy = json.loads(self.policy_path.read_text())
        self.assertEqual(policy["requester_uid"], 1000)
        self.assertEqual(policy["services"], {})
        self.assertNotIn("basaltwater-runner", [call.args[0] for call in lookup.call_args_list])

    def test_private_umask_keeps_policy_accessible_and_credentials_private(self):
        self.directory.rmdir()
        old_umask = os.umask(0o077)
        try:
            self.steps.configure_privilege_broker(self.config)
        finally:
            os.umask(old_umask)

        for path in (self.root, self.directory):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE(self.policy_path.stat().st_mode), 0o644)
        for name in ("auth.json", "server.crt", "server.key"):
            self.assertEqual(stat.S_IMODE((self.directory / name).stat().st_mode), 0o600)

    def test_rerun_repairs_directory_traversal_and_preserves_policy(self):
        self.steps.configure_privilege_broker(self.config)
        original = json.loads(self.policy_path.read_text())
        original["services"] = {"reviewed.service": "allow"}
        self.policy_path.write_text(json.dumps(original))
        self.root.chmod(0o700)
        self.directory.chmod(0o700)
        self.config.privilege_broker_auth = None

        self.steps.configure_privilege_broker(self.config)

        self.assertEqual(json.loads(self.policy_path.read_text()), original)
        for path in (self.root, self.directory):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o755)

    def test_unsafe_directory_is_rejected_before_install(self):
        real_fstat = os.fstat
        for uid, mode in ((1000, 0o755), (0, 0o775), (0, 0o757)):
            def unsafe_directory(descriptor):
                metadata = real_fstat(descriptor)
                if stat.S_ISDIR(metadata.st_mode):
                    values = list(metadata)
                    values[0] = stat.S_IFDIR | mode
                    values[4] = uid
                    return os.stat_result(values)
                return metadata
            with self.subTest(uid=uid, mode=mode), patch.object(
                self.steps.os, "fstat",
                side_effect=unsafe_directory,
            ):
                with self.assertRaisesRegex(ValueError, "Unsafe privilege configuration directory"):
                    self.steps.configure_privilege_broker(self.config)
                self.assertFalse(self.policy_path.exists())
                self.chown.assert_not_called()

    def test_linked_directory_is_rejected_without_changing_target(self):
        self.directory.rmdir()
        target = self.root / "elsewhere"
        target.mkdir(mode=0o700)
        self.directory.symlink_to(target, target_is_directory=True)

        with self.assertRaisesRegex(ValueError, "symlink or non-directory"):
            self.steps.configure_privilege_broker(self.config)

        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o700)
        self.assertFalse(self.policy_path.exists())

    def test_disable_removes_authority_but_retains_policy(self):
        self.steps.configure_privilege_broker(self.config)
        self.config.disable_privilege_broker = True
        self.config.privilege_broker_auth = None
        self.steps.configure_privilege_broker(self.config)
        self.assertFalse(self.auth_path.exists())
        self.assertFalse(self.polkit_path.exists())
        self.assertEqual(list(self.units.glob("*.service")), [])
        self.assertTrue(self.policy_path.exists())

    def test_startup_failure_stops_both_services(self):
        self.ready.return_value = False
        with self.assertRaises(RuntimeError):
            self.steps.configure_privilege_broker(self.config)
        stopped = [call.args[0] for call in self.run.call_args_list if "disable" in call.args[0]]
        self.assertEqual(len(stopped), 2)
        for state in self.states.values():
            self.assertEqual(state["ActiveState"], "inactive")
            self.assertEqual(state["UnitFileState"], "")
        self.assertEqual(self.broker_snapshot(), {})

    def test_first_install_failure_preserves_gateway_shared_parent_and_allows_retry(self):
        shared = self.root / "basaltwater"
        self.assertFalse(shared.exists())
        self.directory = shared / "privilege-broker"
        self.policy_path = self.directory / "policy.json"
        self.auth_path = self.directory / "auth.json"
        self.stack.enter_context(patch.multiple(self.steps, CONFIG_DIR=str(self.directory),
            POLICY_PATH=str(self.policy_path), AUTH_PATH=str(self.auth_path)))
        gateway_file = shared / "internal-web" / "gateway.conf"
        gateway_result = self.gateway.return_value

        def configure_gateway(*_args, **_kwargs):
            gateway_file.parent.mkdir(parents=True, exist_ok=True)
            shared.chmod(0o750)
            gateway_file.write_text("retained gateway configuration")
            return gateway_result

        self.gateway.side_effect = configure_gateway
        self.ready.return_value = False
        with self.assertRaisesRegex(RuntimeError, "services did not become active"):
            self.steps.configure_privilege_broker(self.config)

        self.assertEqual(gateway_file.read_text(), "retained gateway configuration")
        self.assertEqual(stat.S_IMODE(shared.stat().st_mode), 0o750)
        self.assertFalse(self.directory.exists())
        self.assertEqual(self.broker_snapshot(), {})
        for state in self.states.values():
            self.assertEqual(state["ActiveState"], "inactive")
            self.assertEqual(state["UnitFileState"], "")
        self.assertFalse((self.units / ".basaltwater-privilege-operation.json").exists())
        self.assertEqual(list(self.units.glob(".basaltwater-privilege-*/previous.json")), [])

        self.ready.return_value = True
        self.steps.configure_privilege_broker(self.config)
        self.assertTrue(self.policy_path.exists())
        self.assertEqual(gateway_file.read_text(), "retained gateway configuration")
        self.assertTrue(all(state["ActiveState"] == "active" and state["UnitFileState"] == "enabled"
                            for state in self.states.values()))

    def test_unmanaged_unit_is_never_overwritten(self):
        path = self.units / (self.steps.BROKER + ".service")
        path.write_text("administrator service")
        with self.assertRaisesRegex(ValueError, "unmanaged"):
            self.steps.configure_privilege_broker(self.config)
        self.assertEqual(path.read_text(), "administrator service")

    def test_disable_stops_loaded_active_units_even_without_fragments(self):
        self.steps.configure_privilege_broker(self.config)
        for name in self.states:
            (self.units / (name + ".service")).unlink()
        self.run.reset_mock()
        self.config.disable_privilege_broker = True
        self.config.privilege_broker_auth = None

        self.steps.configure_privilege_broker(self.config)

        for name, state in self.states.items():
            self.run.assert_any_call(["systemctl", "stop", name + ".service"])
            self.assertEqual(state["ActiveState"], "inactive")
        self.assertFalse(self.auth_path.exists())
        self.assertTrue(self.policy_path.exists())

    def test_stop_requires_observed_inactive_and_preserves_auth_on_failure(self):
        self.steps.configure_privilege_broker(self.config)
        original = self.auth_path.read_bytes()
        real_command = self.system_command

        def ignored_stop(command, **kwargs):
            if command[1] == "stop":
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            return real_command(command, **kwargs)

        self.run.side_effect = ignored_stop
        self.config.disable_privilege_broker = True
        self.config.privilege_broker_auth = None
        with self.assertRaises(self.steps.BrokerRecoveryError):
            self.steps.configure_privilege_broker(self.config)
        self.assertEqual(self.auth_path.read_bytes(), original)
        self.assertTrue((self.units / ".basaltwater-privilege-operation.json").exists())

    def test_stop_command_failure_preserves_credentials_and_recovery_guard(self):
        self.steps.configure_privilege_broker(self.config)
        original = self.broker_snapshot()
        self.command_failure = lambda command: RuntimeError("stop failed") if command[1] == "stop" else None
        self.config.disable_privilege_broker = True
        self.config.privilege_broker_auth = None

        with self.assertRaises(self.steps.BrokerRecoveryError):
            self.steps.configure_privilege_broker(self.config)

        self.assertEqual(self.broker_snapshot(), original)
        self.assertTrue((self.units / ".basaltwater-privilege-operation.json").exists())

    def test_unknown_post_stop_state_retains_auth_and_blocks_disable_success(self):
        self.steps.configure_privilege_broker(self.config)
        original = self.broker_snapshot()
        real_command = self.system_command

        def unknown_after_stop(command, **kwargs):
            result = real_command(command, **kwargs)
            if command == ["systemctl", "stop", self.steps.WEB + ".service"]:
                self.states[self.steps.WEB]["ActiveState"] = "unknown"
            return result

        self.run.side_effect = unknown_after_stop
        self.config.disable_privilege_broker = True
        self.config.privilege_broker_auth = None
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(self.steps.BrokerRecoveryError):
            self.steps.configure_privilege_broker(self.config)
        self.assertNotIn("disabled", output.getvalue())
        self.assertEqual(self.broker_snapshot(), original)

    def test_gateway_and_candidate_validation_failures_leave_healthy_services_running(self):
        self.steps.configure_privilege_broker(self.config)
        original = self.broker_snapshot()
        for stage in ("gateway", "unit-validation"):
            with self.subTest(stage=stage), ExitStack() as patches:
                self.run.reset_mock()
                if stage == "gateway":
                    patches.enter_context(patch("common.godot_web_steps.configure_internal_certificate",
                                                side_effect=RuntimeError("gateway failed")))
                else:
                    self.command_failure = lambda command: RuntimeError("validation failed") if command[0] == "systemd-analyze" else None
                with self.assertRaises(RuntimeError):
                    self.steps.configure_privilege_broker(self.config)
                self.assertEqual(self.broker_snapshot(), original)
                self.assertTrue(all(state["ActiveState"] == "active" and state["UnitFileState"] == "enabled" for state in self.states.values()))
                self.assertFalse(any(call.args[0][1] in {"stop", "disable", "restart"} for call in self.run.call_args_list))
                self.assertFalse((self.units / ".basaltwater-privilege-operation.json").exists())

    def test_unknown_and_failed_state_queries_never_remove_authority(self):
        self.steps.configure_privilege_broker(self.config)
        original = self.broker_snapshot()
        self.config.disable_privilege_broker = True
        self.config.privilege_broker_auth = None
        state = self.states[self.steps.WEB]
        for failure in ("unknown", "query-error"):
            with self.subTest(failure=failure):
                if failure == "unknown":
                    state["ActiveState"] = "unknown"
                else:
                    state["ActiveState"] = "active"
                    self.command_failure = lambda command: RuntimeError("query failed") if command[1] == "show" else None
                self.run.reset_mock()
                with self.assertRaises(RuntimeError):
                    self.steps.configure_privilege_broker(self.config)
                self.assertEqual(self.broker_snapshot(), original)
                self.assertFalse(any(call.args[0][1] in {"stop", "disable"} for call in self.run.call_args_list))

    def test_rotation_restarts_and_expires_requests_without_replaying_approvals(self):
        from common.service_tools.privilege_broker import Broker
        from lib.privilege_auth import password_record
        self.steps.configure_privilege_broker(self.config)
        previous = self.policy_path.read_bytes()
        audit = self.root / "requests.sqlite3"
        runner = Mock()
        broker = Broker(str(audit), lambda: json.loads(self.policy_path.read_text()), runner)
        self.addCleanup(lambda: broker.db.close())
        pending = broker.request(1000, "system.reboot", {}, "test pending request")
        approved = broker.request(1000, "system.reboot", {}, "test approved request")
        broker.decide(approved["id"], approved["digest"], True, "operator")
        terminal = broker.request(1000, "system.reboot", {}, "test denied request")
        broker.decide(terminal["id"], terminal["digest"], False, "operator")
        events = [tuple(row) for row in broker.db.execute("SELECT * FROM events ORDER BY sequence")]
        real_command = self.system_command

        def process_lifecycle(command, **kwargs):
            nonlocal broker
            result = real_command(command, **kwargs)
            if command == ["systemctl", "restart", self.steps.BROKER + ".service"]:
                broker.db.close()
                broker = Broker(str(audit), lambda: json.loads(self.policy_path.read_text()), runner)
            return result

        self.run.side_effect = process_lifecycle
        self.config.privilege_broker_auth = json.dumps(password_record("agent", "new test-only password"))
        self.run.reset_mock()

        self.steps.configure_privilege_broker(self.config)

        commands = [call.args[0] for call in self.run.call_args_list]
        for name in self.states:
            stop = commands.index(["systemctl", "stop", name + ".service"])
            restart = commands.index(["systemctl", "restart", name + ".service"])
            self.assertLess(stop, restart)
        self.assertEqual(self.policy_path.read_bytes(), previous)
        for request in (pending, approved):
            self.assertEqual(broker.status(request["id"])["state"], "expired")
            with self.assertRaises(ValueError):
                broker.decide(request["id"], request["digest"], True, "operator")
        self.assertEqual(broker.status(terminal["id"])["state"], "denied")
        self.assertEqual([tuple(row) for row in broker.db.execute("SELECT * FROM events ORDER BY sequence")][:len(events)], events)
        self.assertFalse(broker.execute_next())
        runner.assert_not_called()
        self.assertEqual(json.loads(self.auth_path.read_text()), json.loads(self.config.privilege_broker_auth))

    def test_healthy_rerun_restores_files_modes_and_activation_for_each_failure(self):
        from lib.privilege_auth import password_record
        self.steps.configure_privilege_broker(self.config)
        # Distinct safe modes make restoration different from normal publication.
        (self.directory / "server.key").chmod(0o400)
        (self.units / (self.steps.WEB + ".service")).chmod(0o640)
        self.directory.chmod(0o750)
        original = self.broker_snapshot()
        self.config.privilege_broker_port = 9445
        self.config.privilege_broker_auth = json.dumps(password_record("agent", "rotated test-only password"))
        write_json = self.steps.write_json_atomic
        for stage in ("policy", "auth", "unit", "activation", "readiness", "interruption"):
            with self.subTest(stage=stage), ExitStack() as patches:
                def fail_publication(path, *args, **kwargs):
                    if path == str(self.policy_path) and stage in {"policy", "interruption"}:
                        if stage == "interruption":
                            raise KeyboardInterrupt()
                        raise OSError("policy publication failed")
                    if path == str(self.auth_path) and stage == "auth":
                        # Model a failure after the atomic rename took effect.
                        write_json(path, *args, **kwargs)
                        raise OSError("auth publication durability failed")
                    return write_json(path, *args, **kwargs)
                patches.enter_context(patch.object(self.steps, "write_json_atomic", side_effect=fail_publication))
                if stage == "unit":
                    patches.enter_context(patch.object(self.steps, "_managed_write", side_effect=OSError("unit publication failed")))
                if stage == "activation":
                    failed = False
                    def fail_activation(command):
                        nonlocal failed
                        if command == ["systemctl", "restart", self.steps.WEB + ".service"] and not failed:
                            failed = True
                            return RuntimeError("new activation failed")
                        return None
                    self.command_failure = fail_activation
                self.ready.side_effect = lambda origin, _cert: origin == "https://vm.example:9444"
                expected = KeyboardInterrupt if stage == "interruption" else (RuntimeError if stage in {"activation", "readiness"} else OSError)
                with self.assertRaises(expected):
                    self.steps.configure_privilege_broker(self.config)
                self.assertEqual(self.broker_snapshot(), original)
                self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o750)
                for state in self.states.values():
                    self.assertEqual(state["ActiveState"], "active")
                    self.assertEqual(state["UnitFileState"], "enabled")
                self.assertFalse((self.units / ".basaltwater-privilege-operation.json").exists())
                self.assertEqual(list(self.units.glob(".basaltwater-privilege-*/previous.json")), [])
                self.command_failure = lambda _command: None

    def test_failed_restore_retains_private_evidence_and_blocks_retry(self):
        self.steps.configure_privilege_broker(self.config)
        previous_auth = self.auth_path.read_text()
        self.config.privilege_broker_port = 9445
        self.ready.side_effect = lambda origin, _cert: origin == "https://vm.example:9444"
        write_text = self.steps.write_text_atomic

        def fail_restore(path, content, **kwargs):
            if path == str(self.auth_path) and content == previous_auth:
                raise OSError("restore failed")
            return write_text(path, content, **kwargs)

        output = io.StringIO()
        with redirect_stdout(output), patch.object(self.steps, "write_text_atomic", side_effect=fail_restore):
            with self.assertRaises(self.steps.BrokerRecoveryError) as caught:
                self.steps.configure_privilege_broker(self.config)
        marker_path = self.units / ".basaltwater-privilege-operation.json"
        marker_bytes = marker_path.read_bytes()
        marker = json.loads(marker_bytes)
        self.assertEqual(marker["status"], "recovery_required")
        backup = Path(marker["context"]["backup_dir"])
        snapshot = backup / "previous.json"
        self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(snapshot.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(marker_path.stat().st_mode), 0o600)
        self.assertEqual(json.loads(snapshot.read_text())["files"][str(self.auth_path)]["content"], previous_auth)
        for field in ("hash", "salt"):
            self.assertNotIn(self.record[field], str(caught.exception) + marker_path.read_text() + output.getvalue())
        self.assertTrue(all(state["ActiveState"] == "inactive" for state in self.states.values()))
        self.run.reset_mock()
        with self.assertRaisesRegex(ValueError, "Unfinished"):
            self.steps.configure_privilege_broker(self.config)
        self.run.assert_not_called()
        self.assertEqual(marker_path.read_bytes(), marker_bytes)
        self.assertTrue(snapshot.exists())

    def test_rerun_restores_runtime_enablement_and_inactive_units(self):
        self.steps.configure_privilege_broker(self.config)
        self.states[self.steps.BROKER]["UnitFileState"] = "enabled-runtime"
        self.states[self.steps.WEB].update(ActiveState="inactive", SubState="dead", UnitFileState="disabled")
        original = self.broker_snapshot()
        with patch.object(self.steps, "_managed_write", side_effect=OSError("publication failed")):
            with self.assertRaises(OSError):
                self.steps.configure_privilege_broker(self.config)
        self.assertEqual(self.broker_snapshot(), original)
        self.assertEqual(self.states[self.steps.BROKER]["UnitFileState"], "enabled-runtime")
        self.assertEqual(self.states[self.steps.BROKER]["ActiveState"], "active")
        self.assertEqual(self.states[self.steps.WEB]["UnitFileState"], "disabled")
        self.assertEqual(self.states[self.steps.WEB]["ActiveState"], "inactive")

    def test_first_install_interruption_removes_partial_credentials_and_units(self):
        self.directory.rmdir()
        write_json = self.steps.write_json_atomic
        def interrupt_after_auth(path, *args, **kwargs):
            write_json(path, *args, **kwargs)
            if path == str(self.auth_path):
                raise KeyboardInterrupt()
        with patch.object(self.steps, "write_json_atomic", side_effect=interrupt_after_auth):
            with self.assertRaises(KeyboardInterrupt):
                self.steps.configure_privilege_broker(self.config)
        self.assertEqual(self.broker_snapshot(), {})
        self.assertFalse(self.directory.exists())
        self.assertTrue(all(state["ActiveState"] == "inactive" and state["UnitFileState"] == "" for state in self.states.values()))
        self.assertFalse((self.units / ".basaltwater-privilege-operation.json").exists())

    def test_sudo_grants_and_missing_password_fail_before_install(self):
        self.run.return_value.returncode = 0
        self.run.return_value.stderr = ""
        with self.assertRaisesRegex(ValueError, "sudoers"):
            self.steps.configure_privilege_broker(self.config)
        self.run.return_value.returncode = 1
        self.run.return_value.stderr = "User agent is not allowed to run sudo on vm.example."
        self.config.privilege_broker_auth = None
        with self.assertRaisesRegex(ValueError, "First installation"):
            self.steps.configure_privilege_broker(self.config)
        self.assertFalse(self.policy_path.exists())
