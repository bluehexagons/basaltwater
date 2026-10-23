"""Regression tests for starting offline setup targets and shutting down afterward."""

from __future__ import annotations

from argparse import Namespace
import json
import subprocess
import unittest
from unittest.mock import patch

import basaltwater
from lib.config import SetupConfig
from lib.proxmox_vm import ProvisionError, ensure_vm_running
from lib.proxmox_vm_restart import request_vm_shutdown
from lib.setup_reboot import shutdown_after_setup


def _config(**overrides: object) -> SetupConfig:
    values: dict[str, object] = {
        "host": "10.0.0.50",
        "username": "admin",
        "system_type": "server_lite",
        "machine_type": "vm",
        "hosted_node": "10.0.0.10",
        "system_hostname": "offline-vm",
    }
    values.update(overrides)
    return SetupConfig(**values)


class TestOfflineVMSetup(unittest.TestCase):
    def setUp(self) -> None:
        self.enterContext(patch("lib.proxmox_vm.guest_provisioning_locks"))
        self.remote_locks = self.enterContext(patch("lib.proxmox_vm.remote_proxmox_locks"))
        self.wait = self.enterContext(patch("lib.proxmox_vm._wait_for_guest_ssh"))
        self.capacity = self.enterContext(
            patch("lib.proxmox_vm._report_memory_capacity", return_value=True)
        )
        self.command = self.enterContext(patch("lib.proxmox_vm._ssh_run"))
        self.command.side_effect = self._command
        self.status = "stopped"
        self.name = "offline-vm"
        self.address = "10.0.0.50"
        self.inventory = "VMID NAME STATUS\n112 offline-vm stopped\n"
        self.probe_ready = True
        self.power_error = False
        self.events: list[str] = []
        self.wait.side_effect = lambda *a, **kw: self.events.append("wait")
        self.capacity.side_effect = lambda **kw: self.events.append("capacity") or True

    def _command(self, node, user, ssh_opts, command, **kwargs):
        self.events.append(command)
        if command == "qm list":
            output = self.inventory
        elif command == "qm config 112":
            output = (
                f"name: {self.name}\nmemory: 4096\nballoon: 2048\n"
                f"ipconfig0: ip={self.address}/24,gw=10.0.0.1\n"
            )
        elif command == "qm status 112":
            output = f"status: {self.status}\n"
        elif command in {"qm start 112", "qm resume 112"}:
            return subprocess.CompletedProcess(
                command, 1 if self.power_error else 0, "", "power operation failed",
            )
        elif command.startswith("timeout 3 bash"):
            return subprocess.CompletedProcess(
                command, 0 if self.probe_ready else 1,
                "READY\n" if self.probe_ready else "", "",
            )
        else:
            raise AssertionError(f"Unexpected provider command: {command}")
        return subprocess.CompletedProcess(command, 0, output, "")

    def test_stopped_vm_checks_capacity_then_starts_and_waits(self) -> None:
        ensure_vm_running(_config())

        self.assertLess(self.events.index("capacity"), self.events.index("qm start 112"))
        self.assertLess(self.events.index("qm start 112"), self.events.index("wait"))
        self.wait.assert_called_once_with(
            "10.0.0.50", "10.0.0.10", "root", self.command.call_args.args[2],
            timeout=300, dry_run=False, label="VM",
        )
        self.assertEqual(self.remote_locks.call_count, 2)

    def test_paused_vm_resumes_and_waits_without_allocating_again(self) -> None:
        self.status = "paused"

        ensure_vm_running(_config())

        self.assertIn("qm resume 112", self.events)
        self.assertNotIn("qm start 112", self.events)
        self.capacity.assert_not_called()
        self.wait.assert_called_once()

    def test_running_vm_waits_when_ssh_is_still_starting(self) -> None:
        self.status = "running"
        self.probe_ready = False

        ensure_vm_running(_config())

        self.wait.assert_called_once()
        self.assertFalse(any(command.startswith("qm start") for command in self.events))

    def test_reachable_running_vm_needs_no_power_operation(self) -> None:
        self.status = "running"

        ensure_vm_running(_config())

        self.wait.assert_not_called()
        self.capacity.assert_not_called()
        self.assertFalse(any(command.startswith(("qm start", "qm resume")) for command in self.events))

    def test_memory_admission_failure_blocks_start(self) -> None:
        self.capacity.side_effect = None
        self.capacity.return_value = False

        with self.assertRaisesRegex(ProvisionError, "allow-memory-overcommit"):
            ensure_vm_running(_config())

        self.assertNotIn("qm start 112", self.events)
        self.wait.assert_not_called()

    def test_failed_start_or_resume_does_not_wait_or_force_recovery(self) -> None:
        self.power_error = True
        for status, action in (("stopped", "start"), ("paused", "resume")):
            with self.subTest(status=status):
                self.status = status
                with self.assertRaisesRegex(ProvisionError, f"Failed to {action} VM"):
                    ensure_vm_running(_config())

        self.wait.assert_not_called()
        self.assertFalse(any("destroy" in command or "unlock" in command for command in self.events))

    def test_ssh_timeout_preserves_the_existing_vm(self) -> None:
        self.wait.side_effect = ProvisionError("SSH timed out")

        with self.assertRaisesRegex(ProvisionError, "SSH timed out"):
            ensure_vm_running(_config())

        self.assertIn("qm start 112", self.events)
        self.assertFalse(any("destroy" in command or "stop" in command for command in self.events))

    def test_identity_must_match_before_power_operations(self) -> None:
        for name, address in (("copied-vm", "10.0.0.50"), ("offline-vm", "10.0.0.51")):
            with self.subTest(name=name, address=address):
                self.name, self.address = name, address
                with self.assertRaises(ProvisionError):
                    ensure_vm_running(_config())

        self.assertNotIn("qm status 112", self.events)
        self.wait.assert_not_called()
        self.capacity.assert_not_called()

    def test_missing_saved_vm_is_not_recreated(self) -> None:
        self.inventory = "VMID NAME STATUS\n"

        with self.assertRaisesRegex(ProvisionError, "No VM matching saved name"):
            ensure_vm_running(_config())

        self.assertEqual(self.events, ["qm list"])

    def test_unknown_state_is_not_started(self) -> None:
        self.status = "unknown"

        with self.assertRaises(ProvisionError):
            ensure_vm_running(_config())

        self.assertNotIn("qm start 112", self.events)
        self.wait.assert_not_called()

    def test_dry_run_does_not_contact_or_lock_provider(self) -> None:
        ensure_vm_running(_config(dry_run=True))

        self.command.assert_not_called()
        self.remote_locks.assert_not_called()
        self.wait.assert_not_called()


class TestSetupVMShutdown(unittest.TestCase):
    def setUp(self) -> None:
        self.command = self.enterContext(patch("lib.proxmox_vm_restart._provider_command"))
        self.command.side_effect = self._command
        self.inventory: object = [{"vmid": 112, "name": "offline-vm", "status": "running"}]
        self.observed = {"ipconfig0": "ip=10.0.0.50/24,gw=10.0.0.1"}
        self.final_status: object = {"status": "stopped"}
        self.shutdown_error: Exception | None = None

    def _command(self, config, command, **kwargs):
        responses = {
            "pvesh get /nodes/localhost/qemu --output-format json": self.inventory,
            "pvesh get /nodes/localhost/qemu/112/config --output-format json": self.observed,
            "pvesh get /nodes/localhost/qemu/112/status/current --output-format json": self.final_status,
        }
        if command in responses:
            return json.dumps(responses[command])
        if command == "qm shutdown 112 --timeout 180 --forceStop 0":
            self.assertEqual(kwargs, {"timeout": 240})
            if self.shutdown_error is not None:
                raise self.shutdown_error
            return ""
        raise AssertionError(f"Unexpected provider command: {command}")

    def _shutdown_commands(self) -> list[str]:
        return [call.args[1] for call in self.command.call_args_list if call.args[1].startswith("qm ")]

    def test_graceful_shutdown_verifies_result_and_skips_restart_inspection(self) -> None:
        self.assertEqual(shutdown_after_setup(_config()), 0)

        self.assertEqual(self._shutdown_commands(), ["qm shutdown 112 --timeout 180 --forceStop 0"])
        self.assertTrue(self.command.call_args.args[1].endswith("status/current --output-format json"))
        self.assertFalse(any("/pending" in call.args[1] for call in self.command.call_args_list))

    def test_failed_shutdown_verification_is_reported(self) -> None:
        for status in ({"status": "running"}, {}, []):
            with self.subTest(status=status):
                self.final_status = status
                self.assertEqual(shutdown_after_setup(_config()), 1)

    def test_wrong_name_or_address_blocks_shutdown(self) -> None:
        self.inventory = [{"vmid": 112, "name": "copied-vm", "status": "running"}]
        with self.assertRaisesRegex(ValueError, "exactly one"):
            request_vm_shutdown(_config())
        self.inventory = [{"vmid": 112, "name": "offline-vm", "status": "running"}]
        self.observed = {"ipconfig0": "ip=10.0.0.51/24"}
        with self.assertRaisesRegex(ValueError, "setup address"):
            request_vm_shutdown(_config())

        self.assertEqual(self._shutdown_commands(), [])

    def test_duplicate_names_block_shutdown(self) -> None:
        self.inventory = [
            {"vmid": 112, "name": "offline-vm", "status": "running"},
            {"vmid": 113, "name": "offline-vm", "status": "running"},
        ]
        self.assertEqual(shutdown_after_setup(_config()), 1)
        self.assertEqual(self._shutdown_commands(), [])

    def test_locked_vm_blocks_shutdown(self) -> None:
        self.observed["lock"] = "backup"

        self.assertEqual(shutdown_after_setup(_config()), 1)

        self.assertEqual(self._shutdown_commands(), [])

    def test_already_stopped_vm_is_successful_without_another_request(self) -> None:
        self.inventory = [{"vmid": 112, "name": "offline-vm", "status": "stopped"}]

        self.assertEqual(shutdown_after_setup(_config()), 0)

        self.assertEqual(self._shutdown_commands(), [])

    def test_provider_timeout_is_reported_without_force_stop(self) -> None:
        self.shutdown_error = subprocess.TimeoutExpired("ssh", 240)

        self.assertEqual(shutdown_after_setup(_config()), 1)

        self.assertEqual(self._shutdown_commands(), ["qm shutdown 112 --timeout 180 --forceStop 0"])

    def test_dry_run_makes_no_provider_request(self) -> None:
        self.assertEqual(shutdown_after_setup(_config(dry_run=True)), 0)

        self.command.assert_not_called()

    def test_only_provisioned_vms_accept_shutdown(self) -> None:
        for config in (_config(hosted_node=None), _config(machine_type="unprivileged")):
            with self.subTest(config=config.machine_type):
                self.assertEqual(shutdown_after_setup(config), 1)
        self.command.assert_not_called()

    def test_shutdown_cli_flag_is_not_saved_in_setup_configuration(self) -> None:
        parser, _, _ = basaltwater.create_basaltwater_parser()
        args = parser.parse_args([
            "setup", "server_lite", "10.0.0.50", "admin",
            "--provision-on", "10.0.0.10", "--shutdown",
        ])

        self.assertTrue(args.shutdown)
        self.assertNotIn("shutdown", SetupConfig.from_args(args, args.system_type).to_dict())

    def test_cli_rejects_shutdown_on_cachyos_before_setup(self) -> None:
        args = Namespace(system_type="agent_cachyos", host="localhost", shutdown=True)
        with patch("basaltwater.prompt_for_missing_passwords") as prompt, \
             patch("lib.cachyos.run_cachyos_command") as setup:
            self.assertEqual(basaltwater.run_setup_command(args), 1)

        prompt.assert_not_called()
        setup.assert_not_called()


if __name__ == "__main__":
    unittest.main()
