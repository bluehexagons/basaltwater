"""Tests for focused local maintenance commands."""

from __future__ import annotations

import argparse
import os
import sys
import unittest
from subprocess import CompletedProcess
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import basaltwater
from lib.local_cli import run_local_command


class TestLocalCommandParser(unittest.TestCase):
    def setUp(self) -> None:
        self.parser, _setup_parser, _patch_parser = basaltwater.create_basaltwater_parser()

    def test_parses_static_ip_and_dns_options(self):
        args = self.parser.parse_args(
            [
                "local",
                "ip",
                "192.168.10.20/24",
                "--gateway",
                "192.168.10.1",
                "--dns",
                "1.1.1.1",
                "--dns",
                "1.0.0.1",
                "--interface",
                "enp1s0",
            ]
        )

        self.assertEqual(args.local_command, "ip")
        self.assertEqual(args.address, "192.168.10.20/24")
        self.assertEqual(args.dns, ["1.1.1.1", "1.0.0.1"])
        self.assertEqual(args.interface, "enp1s0")

    def test_parses_local_desktop_and_browser_commands(self):
        desktop = self.parser.parse_args(["local", "desktop", "cinnamon", "--dark"])
        browser = self.parser.parse_args(["local", "browser", "firefox", "--no-default"])

        self.assertEqual(desktop.environment, "cinnamon")
        self.assertTrue(desktop.dark)
        self.assertEqual(browser.browser, "firefox")
        self.assertTrue(browser.no_default)


class TestLocalCommandExecution(unittest.TestCase):
    def test_invalid_package_is_rejected_before_running_a_step(self):
        args = argparse.Namespace(
            local_command="install",
            packages=["git; touch /tmp/unexpected"],
            dry_run=False,
        )
        with patch("lib.local_cli._run_step") as run_step:
            result = run_local_command(args)

        self.assertEqual(result, 1)
        run_step.assert_not_called()

    def test_static_network_command_reuses_shared_network_step(self):
        config = object()
        args = argparse.Namespace(
            local_command="network",
            ipv4="192.168.10.20/24",
            ipv6=None,
            gateway="192.168.10.1",
            gateway6=None,
            dns=["1.1.1.1"],
            interface="enp1s0",
            dry_run=True,
        )
        with (
            patch("lib.local_cli._make_config", return_value=config) as make_config,
            patch("lib.local_cli._run_step", return_value=0) as run_step,
        ):
            result = run_local_command(args)

        self.assertEqual(result, 0)
        make_config.assert_called_once_with(
            dry_run=True,
            static_ipv4="192.168.10.20/24",
            static_ipv6=None,
            network_gateway4="192.168.10.1",
            network_gateway6=None,
            network_dns=["1.1.1.1"],
            network_interface="enp1s0",
        )
        run_step.assert_called_once()

    def test_ip_without_an_address_reports_current_addresses(self):
        args = argparse.Namespace(
            local_command="ip",
            address=None,
            ipv6=None,
            gateway=None,
            gateway6=None,
            dns=None,
            interface=None,
            dry_run=False,
        )
        result = CompletedProcess(args=["ip"], returncode=0, stdout="eth0 UP 192.168.1.2/24\n", stderr="")
        with patch("lib.local_cli.run", return_value=result):
            with patch("builtins.print") as print_output:
                exit_code = run_local_command(args)

        self.assertEqual(exit_code, 0)
        print_output.assert_called_once_with("eth0 UP 192.168.1.2/24")


if __name__ == "__main__":
    unittest.main()
