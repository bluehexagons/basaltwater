"""Tests for read-only Proxmox maintenance preflight reporting."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lib.proxmox_hosts import ProxmoxHost
from lib.proxmox_maintenance import (
    ProxmoxMaintenanceReport,
    collect_maintenance_report,
    collect_local_maintenance_report,
    format_maintenance_report,
)


def _result(
    stdout: str = "",
    returncode: int = 0,
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def _active_services() -> str:
    return "active\nactive\nactive\nactive\nactive\n"


def _storage_status(name: str = "local", *, enabled: int = 1, active: int = 1) -> str:
    return json.dumps([{"storage": name, "enabled": enabled, "active": active}])


def _healthy_memory_diagnostics() -> list[subprocess.CompletedProcess[str]]:
    return [
        _result(
            json.dumps(
                {
                    "memory": {"used": 4 * 1024 ** 3, "total": 8 * 1024 ** 3},
                    "swap": {"used": 0, "total": 8 * 1024 ** 3},
                }
            )
        ),
        _result(f"/dev/dm-0 partition {8 * 1024 ** 3} 0\n"),
        _result("10\n"),
        _result("-1 boot-old\n 0 boot-current\n"),
        _result(),
    ]


class TestCollectMaintenanceReport(unittest.TestCase):
    def setUp(self) -> None:
        self.host = ProxmoxHost(name="pve1", address="10.0.0.10")

    @patch("lib.proxmox_maintenance._run")
    @patch("lib.proxmox_maintenance.run", side_effect=TimeoutError("local command timed out"))
    def test_local_probe_uses_no_ssh_and_fails_closed(self, local, remote) -> None:
        report = collect_local_maintenance_report()
        self.assertFalse(report.healthy)
        self.assertIn("local command timed out", report.errors[0])
        remote.assert_not_called()
        self.assertEqual(local.call_args.args[0], "hostname -s")

    @patch("lib.proxmox_maintenance._run")
    def test_collects_healthy_standalone_node(self, mock_run) -> None:
        mock_run.side_effect = [
            _result("pve1\n"),
            _result(_active_services()),
            _result(returncode=1),
            _result("[]\n"),
            _result("[]"),
            _result("[]"),
            _result(_storage_status()),
            _result("5000000\n"),
            _result(returncode=1),
        ] + _healthy_memory_diagnostics()

        report = collect_maintenance_report(self.host)

        self.assertTrue(report.healthy)
        self.assertTrue(report.reboot_safe)
        self.assertFalse(report.clustered)
        self.assertEqual(report.storage_states, {"local": "active"})
        self.assertFalse(report.reboot_required)
        self.assertIn(
            "pvesh get /nodes/$(hostname -s)/storage --output-format json",
            [call.args[1] for call in mock_run.call_args_list],
        )

    @patch("lib.proxmox_maintenance.ssh_batch_mode", return_value=False)
    @patch("lib.proxmox_maintenance.subprocess.run")
    @patch("lib.proxmox_maintenance.build_ssh_command", return_value=["ssh"])
    def test_run_uses_saved_key_and_allows_interactive_auth(
        self, mock_build, mock_run, _mock_batch_mode
    ) -> None:
        host = ProxmoxHost(
            name="pve1", address="10.0.0.10", user="root", ssh_key="/tmp/key"
        )
        mock_run.return_value = _result("pve1\n")

        from lib.proxmox_maintenance import _run

        _run(host, "hostname -s")

        mock_build.assert_called_once()
        kwargs = mock_build.call_args.kwargs
        self.assertEqual(
            mock_build.call_args.args[:3], ("10.0.0.10", "root", "/tmp/key")
        )
        self.assertFalse(kwargs["batch_mode"])
        self.assertTrue(kwargs["control_path"].endswith(".sock"))

    @patch("lib.proxmox_maintenance._run")
    def test_reports_cluster_quorum_and_running_guests(self, mock_run) -> None:
        mock_run.side_effect = [
            _result("pve1\n"),
            _result(_active_services()),
            _result(),
            _result("Cluster information\nQuorate: Yes\n"),
            _result("[]\n"),
            _result('[{"vmid": 100, "name": "web", "status": "running"}]'),
            _result('[{"vmid": 200, "name": "db", "status": "stopped"}]'),
            _result(_storage_status()),
            _result("5000000\n"),
            _result(),
        ] + _healthy_memory_diagnostics()

        report = collect_maintenance_report(self.host)

        self.assertTrue(report.healthy)
        self.assertTrue(report.clustered)
        self.assertTrue(report.quorate)
        self.assertFalse(report.reboot_safe)
        self.assertEqual([guest.vmid for guest in report.running_guests], [100])
        self.assertTrue(report.reboot_required)

    @patch("lib.proxmox_maintenance._run")
    def test_fails_for_active_tasks_locks_storage_and_low_space(self, mock_run) -> None:
        tasks = json.dumps([{"type": "vzdump", "id": "100", "user": "root@pam"}])
        mock_run.side_effect = [
            _result("pve1\n"),
            _result("active\nactive\nfailed\nactive\nactive\n", returncode=3),
            _result(returncode=1),
            _result(tasks),
            _result('[{"vmid": 100, "name": "web", "status": "running", "lock": "backup"}]'),
            _result("[]"),
            _result(_storage_status("backup", active=0)),
            _result("1000\n"),
            _result(returncode=1),
        ] + _healthy_memory_diagnostics()

        report = collect_maintenance_report(self.host)

        self.assertFalse(report.healthy)
        self.assertFalse(report.reboot_safe)
        self.assertIn("vzdump:100 (root@pam)", report.active_tasks)
        self.assertTrue(any("pveproxy" in error for error in report.errors))
        self.assertTrue(any("locked guest" in error for error in report.errors))
        self.assertTrue(any("Storage backup" in error for error in report.errors))
        self.assertTrue(any("less than 4 GiB" in error for error in report.errors))

    @patch("lib.proxmox_maintenance._run")
    def test_reports_memory_and_previous_boot_risks(self, mock_run) -> None:
        mock_run.side_effect = [
            _result("pve1\n"),
            _result(_active_services()),
            _result(returncode=1),
            _result("[]\n"),
            _result("[]"),
            _result("[]"),
            _result(_storage_status()),
            _result("5000000\n"),
            _result(returncode=1),
            _result(
                json.dumps(
                    {
                        "memory": {
                            "used": 7800 * 1024 ** 2,
                            "total": 8192 * 1024 ** 2,
                        },
                        "swap": {
                            "used": 5 * 1024 ** 3,
                            "total": 8 * 1024 ** 3,
                        },
                    }
                )
            ),
            _result(f"/dev/zvol/rpool/swap partition {8 * 1024 ** 3} 0\n"),
            _result("60\n"),
            _result(" 0 boot-current\n"),
            _result("[10.0] Out of memory: Killed process 123 (qemu)\n"),
        ]

        report = collect_maintenance_report(self.host)

        self.assertFalse(report.healthy)
        self.assertEqual(report.swappiness, 60)
        self.assertFalse(report.previous_boot_available)
        self.assertEqual(len(report.previous_boot_findings), 1)
        self.assertTrue(any("ZFS zvol-backed swap" in error for error in report.errors))
        self.assertTrue(any("at least 90%" in warning for warning in report.warnings))
        self.assertTrue(any("at least 50%" in warning for warning in report.warnings))

    @patch("lib.proxmox_maintenance._run")
    def test_ssh_failure_stops_further_probes(self, mock_run) -> None:
        mock_run.return_value = _result(returncode=255, stderr="connection refused")

        report = collect_maintenance_report(self.host)

        self.assertFalse(report.healthy)
        self.assertIn("connection refused", report.errors[0])
        mock_run.assert_called_once()

    @patch("lib.proxmox_maintenance._run")
    def test_stopped_locked_vm_blocks_maintenance_and_reboot(self, command) -> None:
        command.side_effect = self._inventory_results(
            '[{"vmid": 200, "name": "db", "status": "stopped", "lock": "snapshot-delete"}]'
        )
        report = collect_maintenance_report(self.host)
        self.assertFalse(report.healthy)
        self.assertFalse(report.reboot_safe)
        self.assertEqual(report.locked_guests[0].vmid, 200)
        self.assertEqual(report.locked_guests[0].guest_type, "vm")
        self.assertEqual(report.locked_guests[0].lock, "snapshot-delete")

    @patch("lib.proxmox_maintenance._run")
    def test_invalid_vm_inventory_never_reports_evacuated_node(self, command) -> None:
        for inventory in ("", "{}", "garbage", '[{"vmid": 200}]', '[{"vmid": 200, "status": "unknown"}]'):
            with self.subTest(inventory=inventory):
                command.side_effect = self._inventory_results(inventory)
                report = collect_maintenance_report(self.host)
                self.assertFalse(report.healthy)
                self.assertFalse(report.reboot_safe)
                self.assertTrue(any("guest inventory" in error for error in report.errors))

    @patch("lib.proxmox_maintenance._run")
    def test_empty_or_invalid_task_response_blocks_maintenance(self, command) -> None:
        for tasks in ("", " ", "null", "{}", '["vzdump"]', "[null]"):
            with self.subTest(tasks=tasks):
                results = self._inventory_results("[]")
                results[3] = _result(tasks)
                command.side_effect = results
                report = collect_maintenance_report(self.host)
                self.assertFalse(report.healthy)
                self.assertFalse(report.reboot_safe)
                self.assertTrue(any("task" in error for error in report.errors))

    @patch("lib.proxmox_maintenance._run")
    def test_invalid_storage_response_never_passes_partial_inventory(self, command) -> None:
        valid = {"storage": "local", "enabled": 1, "active": 1}
        malformed_entries = (
            None, {}, {"storage": "backup", "enabled": 1},
            {"storage": "backup", "enabled": None, "active": 1},
            {"storage": "backup", "enabled": 1, "active": "1"},
            {"storage": "backup", "enabled": 2, "active": 1},
            {"storage": "bad/name", "enabled": 1, "active": 1}, valid,
        )
        for storage in ("", "garbage", "{}", "[]", *(
            json.dumps([valid, invalid]) for invalid in malformed_entries
        )):
            with self.subTest(storage=storage):
                results = self._inventory_results("[]")
                results[6] = _result(storage)
                command.side_effect = results
                report = collect_maintenance_report(self.host)
                self.assertFalse(report.healthy)
                self.assertFalse(report.reboot_safe)
                self.assertTrue(any("storage" in error.lower() for error in report.errors))

    @patch("lib.proxmox_maintenance._run")
    def test_disabled_storage_is_reported_without_blocking_enabled_pool(self, command) -> None:
        results = self._inventory_results("[]")
        results[6] = _result(json.dumps([
            {"storage": "local", "enabled": 1, "active": 1},
            {"storage": "other-node", "enabled": 0, "active": 0},
        ]))
        command.side_effect = results
        report = collect_maintenance_report(self.host)
        self.assertTrue(report.healthy)
        self.assertEqual(report.storage_states, {"local": "active", "other-node": "disabled"})

    @patch("lib.proxmox_maintenance._run")
    def test_all_disabled_storage_blocks_maintenance(self, command) -> None:
        results = self._inventory_results("[]")
        results[6] = _result(_storage_status(enabled=0, active=0))
        command.side_effect = results
        report = collect_maintenance_report(self.host)
        self.assertFalse(report.healthy)
        self.assertIn("No enabled Proxmox storage pools were reported", report.errors)

    @patch("lib.proxmox_maintenance._run")
    def test_service_probe_failure_blocks_even_with_active_stdout(self, command) -> None:
        results = self._inventory_results("[]")
        results[1] = _result(_active_services(), returncode=1, stderr="probe failed")
        command.side_effect = results
        report = collect_maintenance_report(self.host)
        self.assertFalse(report.healthy)
        self.assertTrue(any("probe failed" in error for error in report.errors))

    @staticmethod
    def _inventory_results(vm_inventory: str) -> list[subprocess.CompletedProcess[str]]:
        return [
            _result("pve1\n"), _result(_active_services()), _result(returncode=1),
            _result("[]"), _result("[]"), _result(vm_inventory),
            _result(_storage_status()), _result("5000000\n"),
            _result(returncode=1),
        ] + _healthy_memory_diagnostics()


class TestFormatMaintenanceReport(unittest.TestCase):
    def test_formats_operator_summary(self) -> None:
        report = ProxmoxMaintenanceReport(
            host_name="pve1",
            address="10.0.0.10",
            node_name="pve1",
            clustered=False,
            root_free_bytes=8 * 1024 ** 3,
            reboot_required=False,
            memory_used_bytes=4 * 1024 ** 3,
            memory_total_bytes=8 * 1024 ** 3,
            swap_used_bytes=0,
            swap_total_bytes=8 * 1024 ** 3,
            swappiness=10,
            previous_boot_available=True,
        )

        output = format_maintenance_report(report)

        self.assertIn("HEALTHY", output)
        self.assertIn("READY", output)
        self.assertIn("standalone", output)
        self.assertIn("8.0 GiB", output)
        self.assertIn("4.0 GiB / 8.0 GiB", output)
        self.assertIn("swappiness:     10", output)


if __name__ == "__main__":
    unittest.main()
