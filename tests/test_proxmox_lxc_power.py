"""Regression coverage for recovering stopped managed LXC setup targets."""

from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

import basaltwater
from lib.config import SetupConfig
from lib.proxmox_node import (
    ContainerAlreadyExists,
    ProvisionError,
    _provision_container_locked,
    ensure_container_running,
)


def _config(**overrides: object) -> SetupConfig:
    values: dict[str, object] = {
        "host": "10.0.0.50",
        "username": "agent",
        "system_type": "server_lite",
        "machine_type": "unprivileged",
        "hosted_node": "10.0.0.10",
        "system_hostname": "offline-lxc",
        "container_memory": "1G",
        "container_storage": [["root", "local-lvm", "6G"]],
        "static_ipv4": "10.0.0.50/24",
        "network_gateway4": "10.0.0.1",
        "network_dns": ["1.1.1.1"],
    }
    values.update(overrides)
    return SetupConfig(**values)


class TestOfflineLXCSetup(unittest.TestCase):
    def setUp(self) -> None:
        self.enterContext(patch("lib.proxmox_node.guest_provisioning_locks"))
        self.enterContext(patch("lib.proxmox_node.remote_proxmox_locks"))
        self.wait = self.enterContext(patch("lib.proxmox_node._wait_for_container_ssh"))
        self.command = self.enterContext(patch("lib.proxmox_node._ssh_run"))
        self.command.side_effect = self._command
        self.status = "stopped"
        self.name = "offline-lxc"
        self.address = "10.0.0.50"
        self.inventory = "VMID Status Lock Name\n112 stopped offline-lxc\n"
        self.lock = ""
        self.failure = ""
        self.events: list[str] = []
        self.wait.side_effect = lambda *a, **kw: self.events.append("wait")

    def _command(self, node, user, opts, command, **kwargs):
        self.events.append(command)
        if command == self.failure:
            return subprocess.CompletedProcess(command, 1, "", "provider failure")
        if command == "pct list":
            output = self.inventory
        elif command.startswith("pct config "):
            output = (
                f"hostname: {self.name}\nnet0: name=eth0,ip={self.address}/24\n"
                f"lock: {self.lock}\n"
            )
        elif command == "pct status 112":
            output = f"status: {self.status}\n"
        elif command == "pct start 112":
            self.assertEqual(kwargs["timeout"], 300)
            output = ""
        else:
            raise AssertionError(f"Unexpected provider command: {command}")
        return subprocess.CompletedProcess(command, 0, output, "")

    def test_stopped_container_starts_before_waiting_for_ssh(self) -> None:
        ensure_container_running(_config())

        self.assertLess(self.events.index("pct start 112"), self.events.index("wait"))
        self.wait.assert_called_once_with(
            "10.0.0.50", "10.0.0.10", "root", self.command.call_args.args[2],
            timeout=300,
        )

    def test_running_container_waits_without_power_operation(self) -> None:
        self.status = "running"
        ensure_container_running(_config())

        self.assertNotIn("pct start 112", self.events)
        self.wait.assert_called_once()

    def test_uncached_provisioning_recovers_before_reporting_existing(self) -> None:
        with patch("lib.proxmox_node.auto_detect_bridge", return_value="vmbr0"):
            with self.assertRaises(ContainerAlreadyExists):
                _provision_container_locked(_config())

        self.assertLess(self.events.index("pct start 112"), self.events.index("wait"))
        self.assertFalse(any("create" in event for event in self.events))

    def test_wrong_name_or_address_never_starts_another_container(self) -> None:
        for name, address in (("other-lxc", "10.0.0.50"), ("offline-lxc", "10.0.0.51")):
            with self.subTest(name=name, address=address):
                self.name, self.address = name, address
                with self.assertRaisesRegex(ProvisionError, "exactly one"):
                    ensure_container_running(_config())
        self.assertNotIn("pct status 112", self.events)
        self.wait.assert_not_called()

    def test_duplicate_addresses_abort_before_power_operation(self) -> None:
        self.inventory += "113 stopped offline-lxc\n"
        with self.assertRaisesRegex(ProvisionError, "exactly one"):
            ensure_container_running(_config())
        self.assertNotIn("pct status 112", self.events)

    def test_provider_errors_never_wait_or_destroy_guest(self) -> None:
        for command in ("pct list", "pct config 112", "pct status 112", "pct start 112"):
            with self.subTest(command=command):
                self.failure = command
                with self.assertRaisesRegex(ProvisionError, "Failed"):
                    ensure_container_running(_config())
        self.wait.assert_not_called()
        self.assertFalse(any("destroy" in event or "unlock" in event for event in self.events))

    def test_locked_or_unknown_status_never_starts(self) -> None:
        for lock, status in (("backup", "stopped"), ("", "unknown")):
            with self.subTest(lock=lock, status=status):
                self.lock, self.status = lock, status
                with self.assertRaises(ProvisionError):
                    ensure_container_running(_config())
        self.assertNotIn("pct start 112", self.events)
        self.wait.assert_not_called()

    def test_ssh_timeout_preserves_existing_container(self) -> None:
        self.wait.side_effect = ProvisionError("SSH timed out")
        with self.assertRaisesRegex(ProvisionError, "SSH timed out"):
            ensure_container_running(_config())
        self.assertFalse(any("destroy" in event or "stop" in event for event in self.events))

    def test_dry_run_never_contacts_provider(self) -> None:
        ensure_container_running(_config(dry_run=True))
        self.command.assert_not_called()
        self.wait.assert_not_called()


class TestCachedLXCDispatch(unittest.TestCase):
    def test_start_precedes_trust_refresh_and_setup_and_errors_abort(self) -> None:
        for error in (None, ProvisionError("start failed"), subprocess.TimeoutExpired("ssh", 300)):
            with self.subTest(error=error):
                config = _config()
                parser, _, _ = basaltwater.create_basaltwater_parser()
                args = parser.parse_args([
                    "setup", "server_lite", "10.0.0.50", "agent",
                    "--machine", "unprivileged", "--provision-on", "10.0.0.10",
                ])
                events: list[str] = []

                def start(_config):
                    events.append("start")
                    if error:
                        raise error

                with (
                    patch("basaltwater.prompt_for_missing_passwords"),
                    patch("basaltwater.SetupConfig.from_args", return_value=config),
                    patch("basaltwater.load_setup_command", return_value=config),
                    patch("basaltwater._prepare_runtime_config_for_cli", return_value=config),
                    patch("basaltwater.print_setup_summary"),
                    patch("lib.proxmox_node.ensure_container_running", side_effect=start),
                    patch("lib.proxmox_node.provision_container") as provision,
                    patch("basaltwater.refresh_managed_guest_host_keys", side_effect=lambda *a, **kw: events.append("trust")),
                    patch("basaltwater.ensure_guest_ipv4_route"),
                    patch("basaltwater.store_cli_credentials"),
                    patch("basaltwater.save_setup_command"),
                    patch("basaltwater.register_proxmox_setup_host"),
                    patch("basaltwater.print_service_access_summary"),
                    patch("basaltwater.run_remote_setup", side_effect=lambda c: events.append("setup") or 0),
                ):
                    result = basaltwater.run_setup_command(args)

                self.assertEqual(result, 1 if error else 0)
                self.assertEqual(events, ["start"] if error else ["start", "trust", "setup"])
                provision.assert_not_called()


if __name__ == "__main__":
    unittest.main()
