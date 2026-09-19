"""Tests for agent VM web-port defaults and firewall reconciliation."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import basaltwater
from lib.arg_parser import create_setup_argument_parser
from lib.config import SetupConfig
from lib.system_types import get_steps_for_system_type
from security.security_steps import configure_firewall


class TestAgentWebPortConfig(unittest.TestCase):
    def test_agent_vm_receives_common_web_ports(self) -> None:
        config = SetupConfig(
            host="agent-vm",
            username="agent",
            system_type="control_plane",
            machine_type="vm",
            agent_tools=["codex"],
        )

        self.assertEqual(config.effective_web_ports(), [80, 443, 8080, 8081])

    def test_additional_ports_are_deduplicated_and_saved(self) -> None:
        config = SetupConfig(
            host="agent-vm",
            username="agent",
            system_type="control_plane",
            machine_type="vm",
            agent_tools=["codex"],
            web_ports=[3000, 5173, 3000],
        )

        self.assertEqual(config.web_ports, [3000, 5173])
        self.assertEqual(
            config.effective_web_ports(),
            [80, 443, 3000, 5173, 8080, 8081],
        )
        self.assertIn("--web-port 3000", config.to_remote_args())
        self.assertIn("--web-port 5173", config.to_setup_command())

    def test_default_ports_can_be_disabled(self) -> None:
        config = SetupConfig(
            host="agent-vm",
            username="agent",
            system_type="control_plane",
            machine_type="vm",
            agent_tools=["codex"],
            web_ports=[3000],
            default_web_ports=False,
        )

        self.assertEqual(config.effective_web_ports(), [3000])
        self.assertIn("--no-default-web-ports", config.to_remote_args())
        self.assertIn("--no-default-web-ports", config.to_setup_command())

    def test_godot_web_bundle_always_opens_its_https_origin(self) -> None:
        config = SetupConfig(
            host="agent-vm",
            username="agent",
            system_type="server_lite",
            machine_type="vm",
            godot_bundles=["web"],
            default_web_ports=False,
        )

        self.assertEqual(config.effective_web_ports(), [8443])

    def test_defaults_do_not_apply_to_hardware_or_server_lite(self) -> None:
        hardware = SetupConfig(
            host="agent-host",
            username="agent",
            system_type="control_plane",
            machine_type="hardware",
            agent_tools=["codex"],
        )
        lite = SetupConfig(
            host="agent-vm",
            username="agent",
            system_type="server_lite",
            machine_type="vm",
            agent_tools=["codex"],
        )

        self.assertEqual(hardware.effective_web_ports(), [])
        self.assertEqual(lite.effective_web_ports(), [])

    def test_parser_accepts_repeated_ports_and_opt_out(self) -> None:
        parser = create_setup_argument_parser("Test")
        args = parser.parse_args(
            [
                "agent-vm",
                "--web-port",
                "3000",
                "--web-port",
                "5173",
                "--no-default-web-ports",
            ]
        )

        self.assertEqual(args.web_ports, [3000, 5173])
        self.assertFalse(args.default_web_ports)

    def test_patch_preserves_default_policy_when_flag_is_omitted(self) -> None:
        parser, _setup_parser, _patch_parser = basaltwater.create_basaltwater_parser()
        omitted = parser.parse_args(["patch", "agent-vm", "agent"])
        disabled = parser.parse_args(
            ["patch", "agent-vm", "agent", "--no-default-web-ports"]
        )

        self.assertIsNone(omitted.default_web_ports)
        self.assertIn("default_web_ports", basaltwater._patch_preserve_keys(omitted))
        self.assertFalse(disabled.default_web_ports)
        self.assertNotIn(
            "default_web_ports",
            basaltwater._patch_preserve_keys(disabled),
        )

    def test_invalid_port_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "between 1 and 65535"):
            SetupConfig(
                host="agent-vm",
                username="agent",
                system_type="control_plane",
                web_ports=[65536],
            )

    def test_server_lite_adds_firewall_only_for_explicit_ports(self) -> None:
        without_ports = SetupConfig(
            host="agent-vm",
            username="agent",
            system_type="server_lite",
            machine_type="vm",
            agent_tools=["codex"],
        )
        with_ports = SetupConfig(
            host="agent-vm",
            username="agent",
            system_type="server_lite",
            machine_type="vm",
            agent_tools=["codex"],
            web_ports=[3000],
        )

        self.assertNotIn(
            "Configuring firewall for requested web ports",
            [name for name, _step in get_steps_for_system_type(without_ports)],
        )
        self.assertIn(
            "Configuring firewall for requested web ports",
            [name for name, _step in get_steps_for_system_type(with_ports)],
        )

        with_godot_web = SetupConfig(
            host="agent-vm",
            username="agent",
            system_type="server_lite",
            machine_type="vm",
            godot_bundles=["web"],
            default_web_ports=False,
        )
        self.assertIn(
            "Configuring firewall for requested web ports",
            [name for name, _step in get_steps_for_system_type(with_godot_web)],
        )


class TestAgentWebPortFirewall(unittest.TestCase):
    @patch("security.security_steps.is_container", return_value=False)
    @patch("security.security_steps.run")
    def test_active_firewall_reconciles_default_and_additional_ports(
        self,
        mock_run,
        _is_container,
    ) -> None:
        def run_side_effect(command: str, **_kwargs: object) -> SimpleNamespace:
            if command.startswith("ufw status 2>"):
                return SimpleNamespace(returncode=0, stdout="")
            return SimpleNamespace(returncode=0, stdout="")

        mock_run.side_effect = run_side_effect
        config = SetupConfig(
            host="agent-vm",
            username="agent",
            system_type="control_plane",
            machine_type="vm",
            agent_tools=["codex"],
            web_ports=[3000],
        )

        configure_firewall(config)

        commands = [call.args[0] for call in mock_run.call_args_list]
        for port in (80, 443, 3000, 8080, 8081):
            self.assertIn(
                f"ufw allow {port}/tcp comment 'basaltwater web TCP {port}'",
                commands,
            )
        self.assertNotIn("apt-get install -y -qq ufw", commands)
        self.assertNotIn("ufw --force enable", commands)

    @patch("security.security_steps.is_container", return_value=False)
    @patch("security.security_steps.run")
    def test_reconciliation_removes_only_stale_managed_web_rules(
        self,
        mock_run,
        _is_container,
    ) -> None:
        status_output = """Status: active
[ 1] 22/tcp LIMIT IN Anywhere
[ 2] 3000/tcp ALLOW IN Anywhere # basaltwater web TCP 3000
[ 3] 9000/tcp ALLOW IN Anywhere # operator rule
[ 4] 8080/tcp ALLOW IN Anywhere # basaltwater web TCP 8080
"""

        def run_side_effect(command: str, **_kwargs: object) -> SimpleNamespace:
            if command.startswith("ufw status 2>"):
                return SimpleNamespace(returncode=0, stdout="")
            if command == "ufw status numbered":
                return SimpleNamespace(returncode=0, stdout=status_output)
            return SimpleNamespace(returncode=0, stdout="")

        mock_run.side_effect = run_side_effect
        config = SetupConfig(
            host="agent-vm",
            username="agent",
            system_type="control_plane",
            machine_type="vm",
            web_ports=[3000],
            default_web_ports=False,
        )

        configure_firewall(config)

        commands = [call.args[0] for call in mock_run.call_args_list]
        self.assertIn("ufw --force delete 4", commands)
        self.assertNotIn("ufw --force delete 2", commands)
        self.assertNotIn("ufw --force delete 3", commands)


if __name__ == "__main__":
    unittest.main()
