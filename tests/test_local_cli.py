"""Tests for focused local maintenance commands."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import os
import sys
import unittest
from subprocess import CompletedProcess
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import basaltwater
from lib.local_cli import run_local_command
from lib.remote_utils import is_dry_run


class TestLocalCommandParser(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.parser, _setup_parser, _patch_parser = basaltwater.create_basaltwater_parser()

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


class TestLocalUpdates(unittest.TestCase):
    def setUp(self) -> None:
        self.args = argparse.Namespace(local_command="update", dry_run=False)
        self.enterContext(patch("lib.local_cli.os.geteuid", return_value=0))
        self.enterContext(patch("lib.local_cli._target_username", return_value="operator"))
        self.proxmox = self.enterContext(patch("lib.local_cli.is_proxmox_host", return_value=True))
        self.generic = self.enterContext(patch("lib.local_cli.update_and_upgrade_packages"))
        self.preflight = self.enterContext(patch("lib.local_cli.preflight_proxmox"))
        self.upgrade = self.enterContext(patch("lib.local_cli.upgrade_proxmox_packages"))
        self.events = []

        @contextmanager
        def locked():
            self.events.append("locked")
            try:
                yield True
            finally:
                self.events.append("released")

        self.lock = self.enterContext(patch("lib.local_cli.maintenance_lock", side_effect=locked))

    def test_explicit_debian_update_does_not_reuse_setup_package_marker(self):
        self.proxmox.return_value = False
        self.assertEqual(run_local_command(self.args), 0)
        self.assertTrue(self.generic.call_args.args[0].refresh_packages)
        self.preflight.assert_not_called()
        self.lock.assert_not_called()

    def test_proxmox_uses_checked_nonremoving_steps_under_one_lock(self):
        self.preflight.side_effect = lambda config: self.events.append("preflight")
        self.upgrade.side_effect = lambda config: self.events.append("upgrade")
        self.assertEqual(run_local_command(self.args), 0)
        self.assertEqual(self.events, ["locked", "preflight", "upgrade", "released"])
        config = self.upgrade.call_args.args[0]
        self.assertEqual((config.system_type, config.username), ("server_proxmox", "root"))
        self.generic.assert_not_called()

    def test_busy_proxmox_lock_refuses_before_package_or_repository_changes(self):
        @contextmanager
        def busy():
            yield False

        self.lock.side_effect = busy
        self.assertEqual(run_local_command(self.args), 1)
        self.preflight.assert_not_called()
        self.upgrade.assert_not_called()
        self.generic.assert_not_called()

    def test_proxmox_preflight_failure_prevents_upgrade_and_releases_lock(self):
        self.preflight.side_effect = RuntimeError("unsupported repository")
        self.assertEqual(run_local_command(self.args), 1)
        self.upgrade.assert_not_called()
        self.generic.assert_not_called()
        self.assertEqual(self.events, ["locked", "released"])

    def test_proxmox_upgrade_failure_is_reported_and_releases_lock(self):
        self.upgrade.side_effect = RuntimeError("health check failed")
        self.assertEqual(run_local_command(self.args), 1)
        self.generic.assert_not_called()
        self.assertEqual(self.events, ["locked", "released"])

    def test_proxmox_dry_run_does_not_create_lock_and_restores_dry_run_state(self):
        previous = is_dry_run()
        self.args.dry_run = True

        def preview(config):
            self.assertTrue(config.dry_run)
            self.assertTrue(is_dry_run())

        self.preflight.side_effect = preview
        self.upgrade.side_effect = preview
        self.assertEqual(run_local_command(self.args), 0)
        self.preflight.assert_called_once()
        self.upgrade.assert_called_once()
        self.generic.assert_not_called()
        self.lock.assert_not_called()
        self.assertEqual(is_dry_run(), previous)

    def test_proxmox_update_requires_root_before_lock_or_mutation(self):
        with patch("lib.local_cli.os.geteuid", return_value=1000):
            self.assertEqual(run_local_command(self.args), 1)
        self.lock.assert_not_called()
        self.preflight.assert_not_called()
        self.upgrade.assert_not_called()
        self.generic.assert_not_called()


if __name__ == "__main__":
    unittest.main()
