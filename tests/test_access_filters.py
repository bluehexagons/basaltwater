"""Tests for generic inbound access-source filtering."""

from __future__ import annotations

import json
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lib.arg_parser import create_setup_argument_parser
from lib.config import SetupConfig
from lib.validation import validate_samba_settings, validate_web_interface_settings
from plugins.proxmox import build_server_proxmox_steps
from security.security_steps import (
    configure_firewall,
    configure_proxmox_management_firewall,
)
from smb.samba_steps import configure_samba_firewall
from web.gogs_steps import generate_gogs_app_ini


class TestAccessSourceConfig(unittest.TestCase):
    def test_parser_accepts_multiple_values_and_repeated_flags(self) -> None:
        parser = create_setup_argument_parser("test")

        args = parser.parse_args(
            [
                "host.example.test",
                "--access-source",
                "192.168.1.0/24",
                "10.0.0.5",
                "--access-source",
                "2001:db8::10",
                "--lan-access",
            ]
        )

        self.assertEqual(
            args.access_sources,
            ["192.168.1.0/24", "10.0.0.5", "2001:db8::10"],
        )
        self.assertTrue(args.lan_access)

    def test_lan_and_service_sources_are_combined_and_deduplicated(self) -> None:
        config = SetupConfig(
            host="192.168.1.42",
            username="admin",
            system_type="workstation_dev",
            lan_access=True,
            access_sources=["192.168.1.42/24", "203.0.113.10"],
            rdp_allowed_sources=["10.0.0.0/8", "198.51.100.0/24"],
            web_interface_sources=["172.16.0.0/12", "198.51.100.20"],
            gogs_sources=["192.168.0.0/16", "198.51.100.30"],
            samba_sources=["10.10.0.0/16", "198.51.100.40"],
        )

        self.assertEqual(
            config.effective_access_sources(),
            ["192.168.1.0/24", "203.0.113.10"],
        )
        self.assertEqual(
            config.effective_rdp_sources(),
            [
                "192.168.1.0/24",
                "203.0.113.10",
                "10.0.0.0/8",
                "198.51.100.0/24",
            ],
        )
        self.assertEqual(config.effective_web_interface_sources()[-1], "198.51.100.20")
        self.assertEqual(config.effective_gogs_sources()[-1], "198.51.100.30")
        self.assertEqual(config.effective_samba_sources()[-1], "198.51.100.40")

    def test_lan_access_covers_common_private_ipv4_blocks(self) -> None:
        for host, expected in (
            ("10.20.30.40", "10.20.30.0/24"),
            ("172.20.8.9", "172.20.8.0/24"),
            ("192.168.68.25", "192.168.68.0/24"),
        ):
            with self.subTest(host=host):
                config = SetupConfig(
                    host=host,
                    username="admin",
                    system_type="server_lite",
                    lan_access=True,
                )

                self.assertEqual(config.effective_access_sources(), [expected])

    def test_lan_access_honors_explicit_static_prefixes(self) -> None:
        config = SetupConfig(
            host="203.0.113.10",
            username="admin",
            system_type="server_lite",
            static_ipv4="10.20.30.40/20",
            static_ipv6="fd12:3456:789a:1::10/60",
            lan_access=True,
        )

        self.assertEqual(
            config.effective_access_sources(),
            ["10.20.16.0/20", "fd12:3456:789a::/60"],
        )

    def test_lan_access_never_expands_an_explicit_prefix_beyond_private_space(
        self,
    ) -> None:
        config = SetupConfig(
            host="203.0.113.10",
            username="admin",
            system_type="server_lite",
            static_ipv4="192.168.1.10/15",
            static_ipv6="fd12:3456::10/6",
            lan_access=True,
        )

        self.assertEqual(
            config.effective_access_sources(),
            ["192.168.0.0/16", "fc00::/7"],
        )

    def test_lan_access_infers_ula_prefix(self) -> None:
        config = SetupConfig(
            host="fd12:3456:789a:1::10",
            username="admin",
            system_type="server_lite",
            lan_access=True,
        )

        self.assertEqual(
            config.effective_access_sources(),
            ["fd12:3456:789a:1::/64"],
        )

    @patch("lib.config.socket.getaddrinfo")
    def test_lan_access_infers_private_network_from_hostname(self, mock_resolve) -> None:
        mock_resolve.return_value = [
            (
                2,
                1,
                6,
                "",
                ("192.168.50.12", 0),
            )
        ]
        config = SetupConfig(
            host="agent-lan-resolution.example.test",
            username="admin",
            system_type="server_lite",
            lan_access=True,
        )

        self.assertEqual(
            config.effective_access_sources(),
            ["192.168.50.0/24"],
        )

    def test_remote_args_expand_lan_access_to_inferred_sources(self) -> None:
        config = SetupConfig(
            host="10.20.30.40",
            username="admin",
            system_type="server_lite",
            lan_access=True,
            access_sources=["100.64.0.0/10"],
        )

        remote_args = config.to_remote_args()

        self.assertNotIn("--lan-access", remote_args)
        self.assertIn("--access-source 10.20.30.0/24", remote_args)
        self.assertIn("--access-source 100.64.0.0/10", remote_args)
        self.assertIn("--lan-access", config.to_setup_command())

    def test_clearing_custom_sources_retains_expanded_lan_access(self) -> None:
        config = SetupConfig(
            host="10.20.30.40",
            username="admin",
            system_type="server_lite",
            lan_access=True,
            clear_access_sources=True,
        )

        remote_args = config.to_remote_args()

        self.assertIn("--access-source 10.20.30.0/24", remote_args)
        self.assertNotIn("--no-access-source", remote_args)
        self.assertIn("--no-access-source", config.to_setup_command())

    def test_lan_access_requires_an_inferable_private_target(self) -> None:
        config = SetupConfig(
            host="203.0.113.10",
            username="admin",
            system_type="server_lite",
            lan_access=True,
        )

        with self.assertRaisesRegex(
            ValueError,
            "could not infer a private target subnet",
        ):
            config.to_remote_args()

    def test_samba_source_and_metadata_cache_round_trip(self) -> None:
        parser = create_setup_argument_parser("test")
        args = parser.parse_args(
            [
                "host.example.test",
                "--samba",
                "--samba-source",
                "192.168.10.0/24",
                "--samba-metadata-cache",
                "/srv/ssd/samba-cache",
            ]
        )

        config = SetupConfig.from_args(args, "server_lite")

        self.assertEqual(config.samba_sources, ["192.168.10.0/24"])
        self.assertEqual(config.samba_metadata_cache, "/srv/ssd/samba-cache")
        self.assertIn("--samba-source 192.168.10.0/24", config.to_remote_args())
        self.assertIn(
            "--samba-metadata-cache /srv/ssd/samba-cache",
            config.to_remote_args(),
        )

    def test_samba_specific_options_require_samba(self) -> None:
        with self.assertRaisesRegex(ValueError, "--samba-source requires --samba"):
            validate_samba_settings(
                SetupConfig(
                    host="host.example.test",
                    username="admin",
                    system_type="server_lite",
                    samba_sources=["192.168.1.0/24"],
                )
            )

        with self.assertRaisesRegex(
            ValueError,
            "--samba-metadata-cache requires --samba",
        ):
            validate_samba_settings(
                SetupConfig(
                    host="host.example.test",
                    username="admin",
                    system_type="server_lite",
                    samba_metadata_cache="/srv/ssd/samba-cache",
                )
            )

    def test_samba_metadata_cache_must_not_overlap_share(self) -> None:
        with self.assertRaisesRegex(ValueError, "must not overlap share path"):
            validate_samba_settings(
                SetupConfig(
                    host="host.example.test",
                    username="admin",
                    system_type="server_lite",
                    enable_samba=True,
                    samba_metadata_cache="/srv/data/cache",
                    samba_shares=[
                        ["write", "data", "/srv/data", "alice:secret"]
                    ],
                )
            )

    def test_generic_source_allows_non_loopback_web_bind(self) -> None:
        config = SetupConfig(
            host="host.example.test",
            username="admin",
            system_type="agent_vm",
            web_interfaces=["t3code"],
            install_codex=True,
            access_sources=["192.168.1.0/24"],
        )

        validate_web_interface_settings(config)

        self.assertEqual(config.web_interface_host, "0.0.0.0")

    def test_generic_sources_use_network_value_validation(self) -> None:
        config = SetupConfig(
            host="host.example.test",
            username="admin",
            system_type="server_lite",
            access_sources=["not-a-network"],
        )

        with self.assertRaisesRegex(ValueError, "Invalid access source"):
            validate_web_interface_settings(config)

    def test_no_lan_access_round_trips_as_explicit_reconciliation(self) -> None:
        parser = create_setup_argument_parser("test")
        args = parser.parse_args(["host.example.test", "--no-lan-access"])

        config = SetupConfig.from_args(args, "server_lite")

        self.assertFalse(config.lan_access)
        self.assertTrue(config.clear_lan_access)
        self.assertIn("--no-lan-access", config.to_remote_args())


class TestGenericUfwFiltering(unittest.TestCase):
    @patch("security.security_steps.is_container", return_value=False)
    @patch("security.security_steps.run")
    def test_generic_source_restricts_ssh_rdp_and_web_ports(self, mock_run, _container) -> None:
        def run_side_effect(command: str, **_kwargs: object) -> SimpleNamespace:
            if command.startswith("ufw status 2>"):
                return SimpleNamespace(returncode=0, stdout="")
            return SimpleNamespace(returncode=0, stdout="")

        mock_run.side_effect = run_side_effect
        config = SetupConfig(
            host="host.example.test",
            username="admin",
            system_type="workstation_dev",
            access_sources=["192.168.1.42/24"],
            enable_rdp=True,
            rdp_allowed_sources=["198.51.100.0/24"],
            web_ports=[8080],
            default_web_ports=False,
        )

        configure_firewall(config)

        commands = [call.args[0] for call in mock_run.call_args_list]
        ssh_rule = (
            "ufw allow from 192.168.1.0/24 to any port 22 proto tcp "
            "comment 'basaltwater SSH trusted source 192.168.1.0/24'"
        )
        self.assertIn(ssh_rule, commands)
        self.assertLess(commands.index(ssh_rule), commands.index("ufw delete limit ssh"))
        self.assertIn(
            "ufw limit from 192.168.1.0/24 to any port 3389 proto tcp "
            "comment 'basaltwater RDP source 192.168.1.0/24'",
            commands,
        )
        self.assertIn(
            "ufw limit from 198.51.100.0/24 to any port 3389 proto tcp "
            "comment 'basaltwater RDP source 198.51.100.0/24'",
            commands,
        )
        web_rule = (
            "ufw allow from 192.168.1.0/24 to any port 8080 proto tcp "
            "comment 'basaltwater web TCP 8080 source 192.168.1.0/24'"
        )
        self.assertIn(web_rule, commands)
        self.assertLess(commands.index(web_rule), commands.index("ufw delete allow 8080/tcp"))

    @patch("security.security_steps.is_container", return_value=False)
    @patch("security.security_steps.run")
    def test_trusted_ssh_source_replaces_legacy_rate_limit(
        self,
        mock_run,
        _container,
    ) -> None:
        status = """Status: active
[ 1] 22/tcp LIMIT IN 192.168.1.0/24 # basaltwater SSH source 192.168.1.0/24
[ 2] 22/tcp ALLOW IN 198.51.100.10 # operator rule
[ 3] 22/tcp ALLOW IN 192.168.1.0/24 # basaltwater SSH trusted source 192.168.1.0/24
"""

        def run_side_effect(command: str, **_kwargs: object) -> SimpleNamespace:
            if command.startswith("ufw status 2>"):
                return SimpleNamespace(returncode=0, stdout="")
            return SimpleNamespace(
                returncode=0,
                stdout=status if command == "ufw status numbered" else "",
            )

        mock_run.side_effect = run_side_effect
        configure_firewall(
            SetupConfig(
                host="host.example.test",
                username="admin",
                system_type="server_lite",
                access_sources=["192.168.1.0/24"],
            )
        )

        commands = [call.args[0] for call in mock_run.call_args_list]
        self.assertIn(
            "ufw allow from 192.168.1.0/24 to any port 22 proto tcp "
            "comment 'basaltwater SSH trusted source 192.168.1.0/24'",
            commands,
        )
        self.assertIn("ufw --force delete 1", commands)
        self.assertNotIn("ufw --force delete 2", commands)
        self.assertNotIn("ufw --force delete 3", commands)

    @patch("smb.samba_steps.run")
    def test_generic_source_restricts_samba_and_removes_stale_rules(self, mock_run) -> None:
        status = (
            "[ 2] 445/tcp ALLOW IN 10.0.0.0/8 "
            "# basaltwater Samba 445/tcp source 10.0.0.0/8\n"
        )

        def run_side_effect(command: str, **_kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(
                returncode=0,
                stdout=status if command == "ufw status numbered" else "",
            )

        mock_run.side_effect = run_side_effect
        configure_samba_firewall(
            SetupConfig(
                host="host.example.test",
                username="admin",
                system_type="server_lite",
                machine_type="vm",
                access_sources=["192.168.1.0/24"],
            )
        )

        commands = [call.args[0] for call in mock_run.call_args_list]
        self.assertIn(
            "ufw allow from 192.168.1.0/24 to any port 445 proto tcp "
            "comment 'basaltwater Samba 445/tcp source 192.168.1.0/24'",
            commands,
        )
        self.assertIn("ufw delete allow 445/tcp", commands)
        self.assertIn("ufw --force delete 2", commands)

    @patch("smb.samba_steps.run")
    def test_samba_source_does_not_restrict_other_services(self, mock_run) -> None:
        mock_run.return_value = SimpleNamespace(returncode=0, stdout="")
        config = SetupConfig(
            host="host.example.test",
            username="admin",
            system_type="server_lite",
            machine_type="vm",
            enable_samba=True,
            samba_sources=["192.168.20.0/24"],
        )

        configure_samba_firewall(config)

        commands = [call.args[0] for call in mock_run.call_args_list]
        self.assertIn(
            "ufw allow from 192.168.20.0/24 to any port 445 proto tcp "
            "comment 'basaltwater Samba 445/tcp source 192.168.20.0/24'",
            commands,
        )
        self.assertEqual(config.effective_access_sources(), [])

    @patch("web.gogs_steps._load_or_create_gogs_secret_key", return_value="secret")
    def test_generic_source_exposes_hostless_gogs_listener(self, _secret) -> None:
        config = SetupConfig(
            host="host.example.test",
            username="admin",
            system_type="server_web",
            gogs=[":3000"],
            access_sources=["192.168.1.0/24"],
        )

        content = generate_gogs_app_ini(
            config,
            git_home="/home/git",
            data_path="/srv/gogs",
            domain="",
            port=3000,
        )

        self.assertIn("HTTP_ADDR = 0.0.0.0", content)
        self.assertIn("EXTERNAL_URL = http://host.example.test:3000/", content)


class TestProxmoxManagementFilter(unittest.TestCase):
    @patch("security.security_steps.run")
    def test_reconciles_owned_entries_and_preserves_operator_entries(self, mock_run) -> None:
        existing = [
            {"cidr": "10.0.0.0/8", "comment": "operator entry"},
            {
                "cidr": "172.16.0.0/12",
                "comment": "basaltwater access source 172.16.0.0/12",
            },
        ]

        def run_side_effect(command: str, **_kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    json.dumps(existing)
                    if command.startswith("pvesh get ")
                    else ""
                ),
            )

        mock_run.side_effect = run_side_effect
        configure_proxmox_management_firewall(
            SetupConfig(
                host="pve.example.test",
                username="root",
                system_type="server_proxmox",
                access_sources=["192.168.1.0/24"],
            )
        )

        commands = [call.args[0] for call in mock_run.call_args_list]
        add_command = next(
            command
            for command in commands
            if command.startswith("pvesh create /cluster/firewall/ipset/management")
        )
        self.assertIn("--cidr 192.168.1.0/24", add_command)
        self.assertIn(
            "pvesh delete /cluster/firewall/ipset/management/172.16.0.0%2F12",
            commands,
        )
        self.assertFalse(any("10.0.0.0" in command for command in commands[1:]))
        self.assertEqual(
            commands[-1],
            "pvesh set /cluster/firewall/options --enable 1",
        )

    @patch("security.security_steps.run")
    def test_clear_removes_only_owned_entries_without_enabling_firewall(self, mock_run) -> None:
        existing = [
            {
                "cidr": "192.168.1.0/24",
                "comment": "basaltwater access source 192.168.1.0/24",
            }
        ]
        mock_run.side_effect = lambda command, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps(existing) if command.startswith("pvesh get ") else "",
        )

        configure_proxmox_management_firewall(
            SetupConfig(
                host="pve.example.test",
                username="root",
                system_type="server_proxmox",
                clear_access_sources=True,
            )
        )

        commands = [call.args[0] for call in mock_run.call_args_list]
        self.assertIn(
            "pvesh delete /cluster/firewall/ipset/management/192.168.1.0%2F24",
            commands,
        )
        self.assertFalse(any("/options --enable" in command for command in commands))

    def test_proxmox_step_is_added_only_for_explicit_filter_reconciliation(self) -> None:
        plain_steps = build_server_proxmox_steps(
            SetupConfig(
                host="pve.example.test",
                username="root",
                system_type="server_proxmox",
            )
        )
        filtered_steps = build_server_proxmox_steps(
            SetupConfig(
                host="192.168.1.10",
                username="root",
                system_type="server_proxmox",
                lan_access=True,
            )
        )

        self.assertNotIn(
            "Configuring Proxmox management access filter",
            [name for name, _step in plain_steps],
        )
        self.assertIn(
            "Configuring Proxmox management access filter",
            [name for name, _step in filtered_steps],
        )


if __name__ == "__main__":
    unittest.main()
