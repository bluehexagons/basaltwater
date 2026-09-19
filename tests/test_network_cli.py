"""Tests for top-level network CLI parsing and dispatch."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import basaltwater
from lib.network_inventory import find_network_profile


class TestNetworkCli(unittest.TestCase):
    def test_parser_accepts_network_init(self) -> None:
        parser, _setup_parser, _patch_parser = basaltwater.create_basaltwater_parser()

        args = parser.parse_args(
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
        parser, _setup_parser, _patch_parser = basaltwater.create_basaltwater_parser()

        args = parser.parse_args(
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
        parser, _setup_parser, _patch_parser = basaltwater.create_basaltwater_parser()

        args = parser.parse_args(
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
        parser, _setup_parser, _patch_parser = basaltwater.create_basaltwater_parser()

        args = parser.parse_args(
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
        parser, _setup_parser, _patch_parser = basaltwater.create_basaltwater_parser()

        args = parser.parse_args(
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
        parser, _setup_parser, _patch_parser = basaltwater.create_basaltwater_parser()

        self.assertIn(
            "network [subcommand]        Manage generic network inventory profiles",
            parser.epilog,
        )

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
