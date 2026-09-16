"""Broker setup arguments, posture, and isolated service boundaries."""

from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import shlex
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from common.privilege_broker_steps import render_units
from common.web_panel_steps import build_web_panel_manifest
from infra_tools import _patch_preserve_keys, create_infra_tools_parser
from lib.arg_parser import add_setup_arguments
from lib.cache import merge_setup_configs
from lib.config import SetupConfig
from plugins.common import extend_web_panel_steps


class SetupTests(unittest.TestCase):
    def config(self, **kwargs):
        return SetupConfig(host="vm.example", username="agent", system_type="agent_vm", **kwargs)

    def test_password_hash_is_private_transient_and_survives_remote_parsing(self):
        parser, _, _ = create_infra_tools_parser()
        args = parser.parse_args(["setup", "agent_vm", "vm.example", "agent", "--privilege-broker",
                                  "https://vm.example:9444", "--privilege-broker-password", "test-only independent password"])
        config = SetupConfig.from_args(args, "agent_vm")
        self.assertNotIn("test-only independent password", repr(config))
        self.assertNotIn("privilege_broker_auth", config.to_dict())
        self.assertNotIn("--privilege-broker-auth", " ".join(config.to_setup_command()))
        remote = argparse.ArgumentParser()
        add_setup_arguments(remote, for_remote=True)
        args = remote.parse_args(shlex.split(" ".join(config.to_remote_args())))
        args.host = "localhost"
        received = SetupConfig.from_args(args, "agent_vm")
        self.assertEqual(received.privilege_broker_auth, config.privilege_broker_auth)
        self.assertEqual(received.privilege_broker, config.privilege_broker)

    def test_omitted_patch_preserves_and_explicit_disable_removes(self):
        parser, _, _ = create_infra_tools_parser()
        cached = self.config(privilege_broker="https://vm.example:9444")
        for flags, expected in (([], cached.privilege_broker), (["--no-privilege-broker"], None)):
            args = parser.parse_args(["patch", "vm.example", "agent", *flags])
            config = merge_setup_configs(cached, SetupConfig.from_args(args, "agent_vm"), preserve_keys=_patch_preserve_keys(args))
            self.assertEqual(config.privilege_broker, expected)

    def test_password_only_patch_rotates_without_losing_origin(self):
        parser, _, _ = create_infra_tools_parser()
        cached = self.config(privilege_broker="https://vm.example:9444")
        args = parser.parse_args(["patch", "vm.example", "agent", "--privilege-broker-password", "test-only changed password"])
        config = merge_setup_configs(cached, SetupConfig.from_args(args, "agent_vm"), preserve_keys=_patch_preserve_keys(args))
        self.assertEqual(config.privilege_broker, cached.privilege_broker)
        self.assertEqual(json.loads(config.privilege_broker_auth)["username"], "agent")

    def test_rejects_conflicting_postures_and_ports(self):
        for extra in ({"nopasswd": True}, {"harden_agent": True}, {"harden_user": True},
                      {"machine_type": "oci"}, {"web_panel_port": 9444}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.config(privilege_broker="https://vm.example:9444", **extra)
        with self.assertRaises(ValueError):
            self.config(privilege_broker="http://vm.example:9444")
        with self.assertRaises(ValueError):
            self.config(privilege_broker="https://vm.example:8444")

    def test_port_firewall_panel_link_and_step_order(self):
        config = self.config(privilege_broker="https://vm.example:9444", web_panel_port=443, enable_ssl=True)
        self.assertIn(9444, config.effective_web_ports())
        services = build_web_panel_manifest(config, ["vm.example"])["services"]
        self.assertIn("https://vm.example:9444/", [service["url"] for service in services])
        steps = []
        extend_web_panel_steps(config, steps)
        self.assertEqual([step.__name__ for _, step in steps], ["configure_privilege_broker", "configure_web_panel"])

    def test_units_have_distinct_identity_and_private_credentials(self):
        units = render_units()
        broker = units["infra-tools-privilege-broker"]
        web = units["infra-tools-privilege-approval"]
        self.assertIn("User=root", broker)
        self.assertIn("User=infra-approval", web)
        self.assertIn("LoadCredential=auth:", web)
        self.assertNotIn("SupplementaryGroups", web)
        self.assertIn("python3 -I /opt/infra_tools/", broker)
        self.assertIn("StateDirectoryMode=0700", broker)
        self.assertIn("ProtectSystem=strict", web)

    @patch("common.privilege_broker_steps.run")
    @patch("common.privilege_broker_steps.is_dry_run", return_value=True)
    def test_dry_run_does_not_mutate_system(self, _dry, run):
        from common.privilege_broker_steps import configure_privilege_broker
        configure_privilege_broker(self.config(privilege_broker="https://vm.example:9444"))
        run.assert_not_called()


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        from common import privilege_broker_steps as steps
        from lib.privilege_auth import password_record
        self.steps = steps
        self.stack = self.enterContext(ExitStack())
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.directory = self.root / "config"
        self.directory.mkdir()
        self.units = self.root / "units"
        self.units.mkdir()
        self.policy_path = self.directory / "policy.json"
        self.auth_path = self.directory / "auth.json"
        self.polkit_path = self.root / "broker.rules"
        self.record = password_record("agent", "test-only approval password")
        self.config = SetupConfig(host="vm.example", username="agent", system_type="agent_vm",
                                  privilege_broker="https://vm.example:9444",
                                  privilege_broker_auth=json.dumps(self.record))
        self.run = Mock(return_value=SimpleNamespace(returncode=1))
        self.ready = Mock(return_value=True)
        constants = {"CONFIG_DIR": str(self.directory), "POLICY_PATH": str(self.policy_path),
                     "AUTH_PATH": str(self.auth_path), "POLKIT_PATH": str(self.polkit_path),
                     "UNIT_ROOT": str(self.units)}
        for name, value in constants.items():
            self.stack.enter_context(patch.object(steps, name, value))
        functions = {"is_dry_run": Mock(return_value=False), "can_manage_system_services": Mock(return_value=True),
                     "is_vm": Mock(return_value=True), "protected_path": Mock(), "_approval_account": Mock(),
                     "run": self.run, "is_service_active": Mock(return_value=True), "_https_ready": self.ready}
        for name, value in functions.items():
            self.stack.enter_context(patch.object(steps, name, value))
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
        self.stack.enter_context(patch("common.godot_web_steps.configure_internal_web_host",
            return_value=("", "", str(cert), str(key), False)))

    def test_install_preserve_policy_and_rotate_password(self):
        self.steps.configure_privilege_broker(self.config)
        original = json.loads(self.policy_path.read_text())
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

    def test_disable_removes_authority_but_retains_policy(self):
        self.steps.configure_privilege_broker(self.config)
        self.config.disable_privilege_broker = True
        self.config.privilege_broker_auth = None
        self.steps.configure_privilege_broker(self.config)
        self.assertFalse(self.auth_path.exists())
        self.assertFalse(self.polkit_path.exists())
        self.assertEqual(list(self.units.iterdir()), [])
        self.assertTrue(self.policy_path.exists())

    def test_startup_failure_stops_both_services(self):
        self.ready.return_value = False
        with self.assertRaises(RuntimeError):
            self.steps.configure_privilege_broker(self.config)
        stopped = [call.args[0] for call in self.run.call_args_list if "disable" in call.args[0]]
        self.assertEqual(len(stopped), 2)

    def test_unmanaged_unit_is_never_overwritten(self):
        path = self.units / (self.steps.BROKER + ".service")
        path.write_text("administrator service")
        with self.assertRaisesRegex(ValueError, "unmanaged"):
            self.steps.configure_privilege_broker(self.config)
        self.assertEqual(path.read_text(), "administrator service")

    def test_sudo_grants_and_missing_password_fail_before_install(self):
        self.run.return_value.returncode = 0
        with self.assertRaisesRegex(ValueError, "sudoers"):
            self.steps.configure_privilege_broker(self.config)
        self.run.return_value.returncode = 1
        self.config.privilege_broker_auth = None
        with self.assertRaisesRegex(ValueError, "First installation"):
            self.steps.configure_privilege_broker(self.config)
        self.assertFalse(self.policy_path.exists())
