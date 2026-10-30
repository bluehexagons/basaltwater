"""Tests for top-level network CLI parsing and dispatch."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import basaltwater
from lib.network_inventory import find_network_profile


class TestNetworkCli(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.parser, _, _ = basaltwater.create_basaltwater_parser()

    def test_parser_accepts_network_init(self) -> None:
        args = self.parser.parse_args(
            [
                "network",
                "--workspace",
                "/tmp/workspace",
                "init",
                "homelab",
                "--management",
                "192.168.1.0/24",
                "--control-plane",
                "10.0.0.10",
                "--vlan",
                "20",
                "servers",
                "10.20.0.0/24",
                "guests",
            ]
        )

        self.assertEqual(args.command, "network")
        self.assertEqual(args.workspace, "/tmp/workspace")
        self.assertEqual(args.network_command, "init")
        self.assertEqual(args.profile, "homelab")

    def test_parser_accepts_import_proxmox(self) -> None:
        args = self.parser.parse_args(
            [
                "network",
                "--workspace",
                "/tmp/workspace",
                "import-proxmox",
                "homelab",
                "--tag",
                "prod",
                "--host",
                "pve1",
                "--no-control-plane",
            ]
        )

        self.assertEqual(args.command, "network")
        self.assertEqual(args.network_command, "import-proxmox")
        self.assertEqual(args.profile, "homelab")
        self.assertEqual(args.tags, ["prod"])
        self.assertEqual(args.hosts, ["pve1"])
        self.assertTrue(args.no_control_plane)

    def test_parser_accepts_import_proxmox_guests(self) -> None:
        args = self.parser.parse_args(
            [
                "network",
                "--workspace",
                "/tmp/workspace",
                "import-proxmox-guests",
                "homelab",
                "--tag",
                "prod",
                "--host",
                "pve1",
            ]
        )

        self.assertEqual(args.command, "network")
        self.assertEqual(args.network_command, "import-proxmox-guests")
        self.assertEqual(args.profile, "homelab")
        self.assertEqual(args.tags, ["prod"])
        self.assertEqual(args.hosts, ["pve1"])

    def test_parser_accepts_plan_proxmox(self) -> None:
        args = self.parser.parse_args(
            [
                "network",
                "--workspace",
                "/tmp/workspace",
                "plan-proxmox",
                "homelab",
                "--json",
            ]
        )

        self.assertEqual(args.command, "network")
        self.assertEqual(args.network_command, "plan-proxmox")
        self.assertEqual(args.profile, "homelab")
        self.assertTrue(args.json)

    def test_parser_accepts_plan_proxmox_rendered(self) -> None:
        args = self.parser.parse_args(
            [
                "network",
                "--workspace",
                "/tmp/workspace",
                "plan-proxmox",
                "homelab",
                "--proxmox",
            ]
        )

        self.assertEqual(args.command, "network")
        self.assertEqual(args.network_command, "plan-proxmox")
        self.assertEqual(args.profile, "homelab")
        self.assertTrue(args.proxmox)

    def test_help_epilog_aligns_network_command(self) -> None:
        self.assertIn(
            "network [subcommand]        Manage generic network inventory profiles",
            self.parser.epilog,
        )

    def test_focused_parser_preserves_help_and_workspace(self) -> None:
        focused, _, _ = basaltwater.create_basaltwater_parser(_command="network")
        self.assertEqual(focused.format_help(), self.parser.format_help())
        for words in (["--workspace", "/tmp/ws", "network", "list"],
                      ["network", "--workspace", "/tmp/ws", "list"]):
            self.assertEqual(vars(focused.parse_args(words)), vars(self.parser.parse_args(words)))

    def test_command_selection_falls_back_for_completion_and_ambiguous_options(self) -> None:
        with patch.dict(os.environ, {"_ARGCOMPLETE": "1"}):
            self.assertIsNone(basaltwater._parser_command(["network"]))
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(basaltwater._parser_command(["--unknown", "network"]))
            self.assertEqual(basaltwater._parser_command(["--workspace", "/tmp/ws", "network"]), "network")

    def test_selected_setup_patch_and_refresh_help_match_full_parser(self) -> None:
        def command_help(parser, command):
            subparsers = next(action for action in parser._actions if isinstance(action, argparse._SubParsersAction))
            return subparsers.choices[command].format_help()

        for command in ("setup", "patch", "refresh", "network"):
            with self.subTest(command=command):
                focused, _, _ = basaltwater.create_basaltwater_parser(_command=command)
                self.assertEqual(command_help(focused, command), command_help(self.parser, command))

    def test_init_and_add_host_through_main(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            with patch.object(
                sys,
                "argv",
                [
                    "basaltwater.py",
                    "network",
                    "--workspace",
                    workspace,
                    "init",
                    "homelab",
                    "--management",
                    "192.168.1.0/24",
                    "--guest-network",
                    "10.20.0.0/24",
                ],
            ):
                self.assertEqual(basaltwater.main(), 0)

            with patch.object(
                sys,
                "argv",
                [
                    "basaltwater.py",
                    "network",
                    "--workspace",
                    workspace,
                    "add-host",
                    "homelab",
                    "pve1",
                    "10.0.0.10",
                    "--provider",
                    "proxmox",
                    "--role",
                    "control-plane",
                ],
            ):
                self.assertEqual(basaltwater.main(), 0)

            profile = find_network_profile("homelab", workspace)
            self.assertIsNotNone(profile)
            assert profile is not None
            self.assertEqual(profile.hosts[0].provider, "proxmox")
            self.assertEqual(profile.hosts[0].roles, ["control-plane"])

    def test_plan_proxmox_returns_failure_for_unsafe_profile(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            with patch.object(
                sys,
                "argv",
                [
                    "basaltwater.py",
                    "network",
                    "--workspace",
                    workspace,
                    "init",
                    "homelab",
                    "--control-plane",
                    "10.0.0.10",
                ],
            ):
                self.assertEqual(basaltwater.main(), 0)

            with patch.object(
                sys,
                "argv",
                [
                    "basaltwater.py",
                    "network",
                    "--workspace",
                    workspace,
                    "plan-proxmox",
                    "homelab",
                ],
            ):
                self.assertEqual(basaltwater.main(), 1)


if __name__ == "__main__":
    unittest.main()
