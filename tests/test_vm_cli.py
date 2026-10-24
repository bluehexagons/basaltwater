"""Tests for the provider-neutral VM command surface."""

from __future__ import annotations

import argparse
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from lib.config import SetupConfig
from lib.proxmox_backup import BackupInfo
from lib.proxmox_hosts import ProxmoxHost, add_proxmox_host
from lib.proxmox_manage import (
    ContainerInfo,
    GuestAutostart,
    GuestStats,
    HealthReport,
    ProxmoxManageError,
    SnapshotInfo,
)
from lib.vm_cli import add_vm_subparser, run_vm_command


class VMCLITest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = self.temp_dir.name
        add_proxmox_host(
            ProxmoxHost(name="pve1", address="10.0.0.10"), self.workspace
        )
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        add_vm_subparser(subparsers)
        self.parser = parser
        self.saved_vm = SetupConfig(
            host="10.0.0.50",
            username="agent",
            system_type="server_dev",
            machine_type="vm",
            system_hostname="agent-node",
            friendly_name="agent-dev-01",
            hosted_node="10.0.0.10",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _run(self, *argv: str) -> tuple[int, str]:
        args = self.parser.parse_args(["vm", "--workspace", self.workspace, *argv])
        output = io.StringIO()
        with redirect_stdout(output):
            result = run_vm_command(args)
        return result, output.getvalue()

    @patch("lib.vm_cli.list_containers")
    def test_list_uses_neutral_json_envelope(self, mock_list) -> None:
        mock_list.return_value = [
            ContainerInfo(vmid=100, status="running", name="web", guest_type="lxc"),
            ContainerInfo(vmid=101, status="stopped", name="build", guest_type="vm"),
        ]
        result, output = self._run("list", "pve1", "--json")
        self.assertEqual(result, 0)
        payload = json.loads(output)
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["provider"], "proxmox")
        self.assertEqual(payload["operation"], "list")
        self.assertEqual(payload["resources"][1]["kind"], "vm")

    @patch("lib.vm_cli.list_containers")
    def test_json_inventory_keeps_provider_diagnostics_on_stderr(self, mock_list) -> None:
        def inventory(_host):
            print("Running guest inventory")
            print("Provider warning", file=sys.stderr)
            return [ContainerInfo(101, "running", "build", guest_type="vm")]

        mock_list.side_effect = inventory
        diagnostics = io.StringIO()
        with redirect_stderr(diagnostics):
            result, output = self._run("list", "pve1", "--json")

        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output)["resources"][0]["id"], "101")
        self.assertEqual(
            diagnostics.getvalue(), "Running guest inventory\nProvider warning\n"
        )

    @patch("lib.vm_cli.get_container_status")
    @patch("lib.vm_cli.start_container")
    @patch("lib.vm_cli.list_containers")
    def test_json_lifecycle_separates_mutation_and_observation_progress(
        self, mock_list, mock_start, mock_status
    ) -> None:
        mock_list.return_value = [
            ContainerInfo(101, "stopped", "build", guest_type="vm")
        ]
        mock_start.side_effect = lambda *_: print("Starting guest")

        def observe(*_):
            print("Checking resulting state")
            return "running"

        mock_status.side_effect = observe
        diagnostics = io.StringIO()
        with redirect_stderr(diagnostics):
            result, output = self._run("start", "pve1", "101", "--json")

        self.assertEqual(result, 0)
        payload = json.loads(output)
        self.assertEqual(payload["operation"], "start")
        self.assertEqual(payload["resources"][0]["state"], "running")
        self.assertEqual(
            diagnostics.getvalue(), "Starting guest\nChecking resulting state\n"
        )

    @patch("lib.vm_cli.list_containers")
    def test_json_failure_leaves_stdout_empty_and_restores_text_output(
        self, mock_list
    ) -> None:
        def fail(_host):
            print("Reading guest inventory")
            raise ProxmoxManageError("Inventory unavailable")

        mock_list.side_effect = fail
        diagnostics = io.StringIO()
        with redirect_stderr(diagnostics):
            result, output = self._run("list", "pve1", "--json")
            mock_list.side_effect = None
            mock_list.return_value = []
            text_result, text_output = self._run("list", "pve1")

        self.assertEqual(result, 1)
        self.assertEqual(output, "")
        self.assertEqual(
            diagnostics.getvalue(),
            "Reading guest inventory\nError: Inventory unavailable\n",
        )
        self.assertEqual(text_result, 0)
        self.assertIn("No guests found.", text_output)

    @patch("lib.vm_cli.list_containers")
    def test_json_output_stream_is_restored_after_unexpected_error(self, mock_list) -> None:
        mock_list.side_effect = RuntimeError("unexpected provider failure")
        with self.assertRaisesRegex(RuntimeError, "unexpected provider failure"):
            self._run("list", "pve1", "--json")
        mock_list.side_effect = None
        mock_list.return_value = []

        result, output = self._run("list", "pve1", "--json")

        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output)["resources"], [])

    @patch("lib.vm_cli.get_container_config", return_value={"cores": "2"})
    @patch("lib.vm_cli.get_container_status", return_value="running")
    @patch("lib.vm_cli.list_containers")
    def test_show_includes_provider_config(
        self, mock_list, _mock_status, _mock_config
    ) -> None:
        mock_list.return_value = [
            ContainerInfo(vmid=100, status="running", name="web", guest_type="lxc")
        ]
        result, output = self._run("show", "pve1", "100", "--json")
        self.assertEqual(result, 0)
        payload = json.loads(output)
        self.assertEqual(payload["resources"][0]["config"], {"cores": "2"})

    @patch("lib.vm_cli.health_check")
    @patch("lib.vm_cli.list_containers")
    def test_unhealthy_health_check_returns_failure(
        self, mock_list, mock_health
    ) -> None:
        mock_list.return_value = [
            ContainerInfo(vmid=100, status="stopped", name="web", guest_type="vm")
        ]
        mock_health.return_value = HealthReport(
            vmid=100,
            status="stopped",
            guest_type="vm",
            notes=["Guest is not running (status=stopped)"],
        )
        result, output = self._run("health", "pve1", "100", "--json")
        self.assertEqual(result, 1)
        self.assertFalse(json.loads(output)["resources"][0]["healthy"])

    @patch("lib.vm_cli.list_snapshots")
    @patch("lib.vm_cli.list_containers")
    def test_snapshot_list(self, mock_list, mock_snapshots) -> None:
        mock_list.return_value = [
            ContainerInfo(vmid=100, status="running", name="web", guest_type="vm")
        ]
        mock_snapshots.return_value = [SnapshotInfo("before", "initial")]
        result, output = self._run("snapshot", "list", "pve1", "100", "--json")
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output)["resources"][0]["name"], "before")

    @patch("lib.vm_cli.list_backups")
    @patch("lib.vm_cli.list_containers")
    def test_backup_list(self, mock_list, mock_backups) -> None:
        mock_list.return_value = [
            ContainerInfo(vmid=100, status="running", name="web", guest_type="vm")
        ]
        mock_backups.return_value = [
            BackupInfo("local:backup/vzdump-qemu-100.vma.zst", 100, 123)
        ]
        result, output = self._run("backup", "list", "pve1", "100", "--json")
        self.assertEqual(result, 0)
        self.assertEqual(
            json.loads(output)["resources"][0]["storage"],
            "local",
        )

    @patch("lib.vm_cli.get_container_config", return_value={"cores": "4"})
    @patch("lib.vm_cli.get_container_status", return_value="running")
    @patch("lib.vm_cli.get_container_ip", return_value="10.0.0.50")
    @patch("lib.vm_cli.list_containers")
    @patch("lib.vm_cli.load_all_setup_commands")
    def test_show_resolves_saved_local_vm_name(
        self,
        mock_load,
        mock_list,
        _mock_ip,
        _mock_status,
        _mock_config,
    ) -> None:
        mock_load.return_value = [self.saved_vm]
        mock_list.return_value = [
            ContainerInfo(
                vmid=101,
                status="running",
                name="agent-node",
                guest_type="vm",
            )
        ]

        result, output = self._run("show", "agent-dev-01", "--json")

        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output)["resources"][0]["id"], "101")
        mock_load.assert_called_once_with(self.workspace)

    @patch("lib.vm_cli.get_container_status", return_value="running")
    @patch("lib.vm_cli.get_container_ip", return_value="10.0.0.50")
    @patch("lib.vm_cli.list_containers")
    @patch("lib.vm_cli.load_all_setup_commands")
    def test_status_resolves_saved_local_vm_name(
        self,
        mock_load,
        mock_list,
        _mock_ip,
        _mock_status,
    ) -> None:
        mock_load.return_value = [self.saved_vm]
        mock_list.return_value = [
            ContainerInfo(
                vmid=101,
                status="running",
                name="agent-node",
                guest_type="vm",
            )
        ]

        result, output = self._run("status", "agent-dev-01", "--json")

        self.assertEqual(result, 0)
        payload = json.loads(output)
        self.assertEqual(payload["operation"], "status")
        self.assertEqual(payload["resources"][0]["id"], "101")
        self.assertEqual(payload["resources"][0]["state"], "running")

    @patch("lib.vm_cli.get_container_status", return_value="running")
    @patch("lib.vm_cli.start_container")
    @patch("lib.vm_cli.get_container_ip", return_value="10.0.0.50")
    @patch("lib.vm_cli.list_containers")
    @patch("lib.vm_cli.load_all_setup_commands")
    def test_start_resolves_saved_local_vm_name(
        self,
        mock_load,
        mock_list,
        _mock_ip,
        mock_start,
        _mock_status,
    ) -> None:
        mock_load.return_value = [self.saved_vm]
        mock_list.return_value = [
            ContainerInfo(
                vmid=101,
                status="stopped",
                name="agent-node",
                guest_type="vm",
            )
        ]

        result, output = self._run("start", "agent-dev-01", "--json")

        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output)["operation"], "start")
        host, vmid = mock_start.call_args.args
        self.assertEqual(host.name, "pve1")
        self.assertEqual(vmid, 101)

    @patch("lib.vm_cli.get_guest_stats")
    @patch("lib.vm_cli.get_container_ip", return_value="10.0.0.50")
    @patch("lib.vm_cli.list_containers")
    @patch("lib.vm_cli.load_all_setup_commands")
    def test_stats_reports_resource_pressure_for_saved_local_name(
        self,
        mock_load,
        mock_list,
        _mock_ip,
        mock_stats,
    ) -> None:
        mock_load.return_value = [self.saved_vm]
        mock_list.return_value = [
            ContainerInfo(
                vmid=101,
                status="running",
                name="agent-node",
                guest_type="vm",
            )
        ]
        mock_stats.return_value = GuestStats(
            vmid=101,
            guest_type="vm",
            status="running",
            cpu_usage=0.91,
            cpu_count=2,
            memory_used=7 * 1024 ** 3,
            memory_total=8 * 1024 ** 3,
            disk_used=29 * 1024 ** 3,
            disk_total=32 * 1024 ** 3,
            disk_read=2 * 1024 ** 3,
            disk_written=1024 ** 3,
            network_in=512 * 1024 ** 2,
            network_out=256 * 1024 ** 2,
            uptime_seconds=90061,
        )

        result, output = self._run("stats", "agent-dev-01", "--json")

        self.assertEqual(result, 0)
        payload = json.loads(output)
        self.assertEqual(payload["operation"], "stats")
        resource = payload["resources"][0]
        self.assertEqual(resource["memory_total"], 8 * 1024 ** 3)
        self.assertIn("CPU usage is at or above 90%", resource["warnings"])
        self.assertIn("Memory usage is at or above 85%", resource["warnings"])
        self.assertIn("Guest disk usage is at or above 90%", resource["warnings"])

    @patch("lib.vm_cli.get_guest_autostart")
    @patch("lib.vm_cli.get_container_ip", return_value="10.0.0.50")
    @patch("lib.vm_cli.list_containers")
    @patch("lib.vm_cli.load_all_setup_commands")
    def test_autostart_shows_saved_vm_settings(
        self,
        mock_load,
        mock_list,
        _mock_ip,
        mock_autostart,
    ) -> None:
        mock_load.return_value = [self.saved_vm]
        mock_list.return_value = [
            ContainerInfo(
                vmid=101,
                status="running",
                name="agent-node",
                guest_type="vm",
            )
        ]
        mock_autostart.return_value = GuestAutostart(
            enabled=True,
            order=1,
            start_delay=30,
            shutdown_timeout=120,
        )

        result, output = self._run("autostart", "agent-dev-01", "--json")

        self.assertEqual(result, 0)
        payload = json.loads(output)
        self.assertEqual(payload["operation"], "autostart.show")
        self.assertTrue(payload["resources"][0]["enabled"])
        self.assertEqual(payload["resources"][0]["start_delay"], 30)

    @patch("lib.vm_cli.get_guest_autostart")
    @patch("lib.vm_cli.configure_guest_autostart")
    @patch("lib.vm_cli.list_containers")
    def test_autostart_enable_configures_typed_startup_order(
        self,
        mock_list,
        mock_configure,
        mock_autostart,
    ) -> None:
        mock_list.return_value = [
            ContainerInfo(
                vmid=101,
                status="stopped",
                name="agent-node",
                guest_type="vm",
            )
        ]
        mock_autostart.return_value = GuestAutostart(
            enabled=True,
            order=2,
            start_delay=45,
            shutdown_timeout=120,
        )

        result, output = self._run(
            "autostart",
            "pve1",
            "101",
            "--enable",
            "--order",
            "2",
            "--start-delay",
            "45",
            "--shutdown-timeout",
            "120",
            "--json",
        )

        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output)["operation"], "autostart.configure")
        mock_configure.assert_called_once()
        self.assertEqual(
            mock_configure.call_args.kwargs,
            {
                "enabled": True,
                "order": 2,
                "start_delay": 45,
                "shutdown_timeout": 120,
            },
        )

    @patch("lib.vm_cli.get_guest_autostart")
    @patch("lib.vm_cli.configure_guest_autostart")
    @patch("lib.vm_cli.list_containers")
    def test_autostart_disable_preserves_existing_schedule(
        self,
        mock_list,
        mock_configure,
        mock_autostart,
    ) -> None:
        mock_list.return_value = [
            ContainerInfo(
                vmid=101,
                status="stopped",
                name="agent-node",
                guest_type="vm",
            )
        ]
        mock_autostart.return_value = GuestAutostart(
            enabled=False,
            order=2,
            start_delay=45,
            shutdown_timeout=120,
        )

        result, output = self._run(
            "autostart",
            "pve1",
            "101",
            "--disable",
            "--json",
        )

        self.assertEqual(result, 0)
        self.assertFalse(json.loads(output)["resources"][0]["enabled"])
        mock_configure.assert_called_once()
        self.assertEqual(
            mock_configure.call_args.kwargs,
            {
                "enabled": False,
                "order": None,
                "start_delay": None,
                "shutdown_timeout": None,
            },
        )

    @patch("lib.vm_cli.configure_guest_autostart")
    @patch("lib.vm_cli.list_containers")
    def test_autostart_schedule_requires_enable(
        self,
        mock_list,
        mock_configure,
    ) -> None:
        mock_list.return_value = [
            ContainerInfo(
                vmid=101,
                status="stopped",
                name="agent-node",
                guest_type="vm",
            )
        ]

        result, output = self._run("autostart", "pve1", "101", "--order", "2")

        self.assertEqual(result, 1)
        self.assertIn("require --enable", output)
        mock_configure.assert_not_called()

    def test_lifecycle_commands_use_provider_backend(self) -> None:
        guest = ContainerInfo(
            vmid=101,
            status="running",
            name="agent-node",
            guest_type="vm",
        )
        cases = [
            (("pause", "pve1", "101", "--json"), "suspend_guest", {}, "pause", "paused"),
            (("resume", "pve1", "101", "--json"), "resume_guest", {}, "resume", "running"),
            (
                ("shutdown", "pve1", "101", "--timeout", "45", "--json"),
                "stop_container",
                {"force": False, "timeout": 45},
                "shutdown",
                "stopped",
            ),
            (
                ("stop", "pve1", "101", "--json"),
                "stop_container",
                {"force": True},
                "stop",
                "stopped",
            ),
            (
                ("reboot", "pve1", "101", "--timeout", "30", "--json"),
                "reboot_guest",
                {"timeout": 30},
                "reboot",
                "running",
            ),
        ]

        for argv, backend_name, expected_kwargs, operation, state in cases:
            with self.subTest(command=argv[0]):
                with (
                    patch("lib.vm_cli.list_containers", return_value=[guest]),
                    patch("lib.vm_cli.get_container_status", return_value=state),
                    patch(f"lib.vm_cli.{backend_name}") as mock_backend,
                ):
                    result, output = self._run(*argv)

                self.assertEqual(result, 0)
                payload = json.loads(output)
                self.assertEqual(payload["operation"], operation)
                self.assertEqual(payload["resources"][0]["state"], state)
                self.assertEqual(mock_backend.call_args.args[1], 101)
                self.assertEqual(mock_backend.call_args.kwargs, expected_kwargs)

    def test_lifecycle_aliases_select_expected_handlers(self) -> None:
        suspend = self.parser.parse_args(["vm", "suspend", "pve1", "101"])
        restart = self.parser.parse_args(["vm", "restart", "pve1", "101"])

        self.assertEqual(suspend._handler.__name__, "_cmd_pause")
        self.assertEqual(restart._handler.__name__, "_cmd_reboot")

    @patch("lib.vm_cli.destroy_container")
    @patch("lib.vm_cli.get_container_ip", return_value="10.0.0.50")
    @patch("lib.vm_cli.list_containers")
    @patch("lib.vm_cli.load_all_setup_commands")
    def test_destroy_resolves_saved_local_vm_name(
        self,
        mock_load,
        mock_list,
        _mock_ip,
        mock_destroy,
    ) -> None:
        guest = ContainerInfo(
            vmid=101,
            status="running",
            name="agent-node",
            guest_type="vm",
        )
        mock_load.return_value = [self.saved_vm]
        mock_list.side_effect = [[guest], []]

        result, output = self._run("destroy", "agent-dev-01", "--yes", "--force")

        self.assertEqual(result, 0)
        self.assertIn("Destroyed QEMU VM 101 ('agent-node') on pve1", output)
        mock_destroy.assert_called_once()
        host, vmid = mock_destroy.call_args.args
        self.assertEqual(host.name, "pve1")
        self.assertEqual(vmid, 101)
        self.assertEqual(mock_destroy.call_args.kwargs, {"force": True})

    @patch("lib.vm_cli.destroy_container")
    @patch("lib.vm_cli.get_container_ip", return_value="10.0.0.99")
    @patch("lib.vm_cli.list_containers")
    @patch("lib.vm_cli.load_all_setup_commands")
    def test_destroy_refuses_saved_name_when_guest_address_does_not_match(
        self,
        mock_load,
        mock_list,
        _mock_ip,
        mock_destroy,
    ) -> None:
        mock_load.return_value = [self.saved_vm]
        mock_list.return_value = [
            ContainerInfo(
                vmid=101,
                status="running",
                name="agent-node",
                guest_type="vm",
            )
        ]

        result, output = self._run("destroy", "agent-dev-01", "--yes")

        self.assertEqual(result, 1)
        self.assertIn("Refusing saved setup 'agent-dev-01'", output)
        self.assertIn("expected 10.0.0.50", output)
        mock_destroy.assert_not_called()

    @patch("lib.vm_cli.destroy_container")
    @patch("lib.vm_cli.get_container_ip", return_value="10.0.0.50")
    @patch("lib.vm_cli.list_containers")
    @patch("lib.vm_cli.load_all_setup_commands")
    def test_destroy_fails_when_vm_remains_after_provider_command(
        self,
        mock_load,
        mock_list,
        _mock_ip,
        mock_destroy,
    ) -> None:
        guest = ContainerInfo(
            vmid=101,
            status="stopped",
            name="agent-node",
            guest_type="vm",
        )
        mock_load.return_value = [self.saved_vm]
        mock_list.side_effect = [[guest], [guest]]

        result, output = self._run("destroy", "agent-dev-01", "--yes")

        self.assertEqual(result, 1)
        self.assertIn("still exists on pve1 after destroy completed", output)
        mock_destroy.assert_called_once()

    @patch("lib.vm_cli.destroy_container")
    @patch("lib.vm_cli.load_all_setup_commands")
    def test_destroy_rejects_ambiguous_saved_vm_name(
        self,
        mock_load,
        mock_destroy,
    ) -> None:
        second = SetupConfig(
            host="10.0.0.51",
            username="agent",
            system_type="server_dev",
            machine_type="vm",
            friendly_name="agent-dev-01",
            hosted_node="10.0.0.10",
        )
        mock_load.return_value = [self.saved_vm, second]

        result, output = self._run("destroy", "agent-dev-01", "--yes")

        self.assertEqual(result, 1)
        self.assertIn("is ambiguous across: 10.0.0.50, 10.0.0.51", output)
        mock_destroy.assert_not_called()

    @patch("lib.vm_cli.destroy_container")
    @patch("lib.vm_cli.list_containers")
    def test_destroy_rejects_lxc_from_explicit_host_id(
        self,
        mock_list,
        mock_destroy,
    ) -> None:
        mock_list.return_value = [
            ContainerInfo(vmid=100, status="stopped", name="web", guest_type="lxc")
        ]

        result, output = self._run("destroy", "pve1", "100", "--yes")

        self.assertEqual(result, 1)
        self.assertIn("is lxc, not a QEMU VM", output)
        mock_destroy.assert_not_called()

    @patch("lib.vm_cli.destroy_container")
    @patch("lib.vm_cli.get_container_ip", return_value="10.0.0.50")
    @patch("lib.vm_cli.list_containers")
    @patch("lib.vm_cli.load_all_setup_commands")
    @patch("builtins.input", return_value="no")
    def test_destroy_saved_name_prompts_and_aborts(
        self,
        _mock_input,
        mock_load,
        mock_list,
        _mock_ip,
        mock_destroy,
    ) -> None:
        mock_load.return_value = [self.saved_vm]
        mock_list.return_value = [
            ContainerInfo(
                vmid=101,
                status="running",
                name="agent-node",
                guest_type="vm",
            )
        ]

        result, output = self._run("destroy", "agent-dev-01")

        self.assertEqual(result, 1)
        self.assertIn("Aborted", output)
        prompt = _mock_input.call_args.args[0]
        self.assertIn("VM 101 ('agent-node', saved as 'agent-dev-01')", prompt)
        self.assertIn("pve1 (10.0.0.10)", prompt)
        mock_destroy.assert_not_called()

    def test_invalid_id_is_rejected_by_argparse(self) -> None:
        with self.assertRaises(SystemExit):
            self.parser.parse_args(["vm", "show", "pve1", "0"])

    def test_negative_lifecycle_timeout_is_rejected_by_argparse(self) -> None:
        with self.assertRaises(SystemExit):
            self.parser.parse_args(
                ["vm", "shutdown", "pve1", "100", "--timeout", "-1"]
            )


if __name__ == "__main__":
    unittest.main()
