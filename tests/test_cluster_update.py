"""Tests for rolling cluster updates."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lib.cluster_update import run_cluster_update
from lib import cluster_update
from lib.config import SetupConfig
from lib.proxmox_maintenance import ProxmoxMaintenanceReport
from lib.proxmox_manage import ContainerInfo


def _config(host: str) -> SetupConfig:
    return SetupConfig(
        host=host,
        username="admin",
        system_type="server_proxmox",
    )


def _maintenance_report(
    *,
    reboot_required: bool = False,
    running_guests: list[ContainerInfo] | None = None,
    errors: list[str] | None = None,
) -> ProxmoxMaintenanceReport:
    return ProxmoxMaintenanceReport(
        host_name="pve",
        address="10.0.0.10",
        node_name="pve",
        clustered=False,
        reboot_required=reboot_required,
        running_guests=list(running_guests or []),
        errors=list(errors or []),
    )


class TestClusterUpdate(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.workspace = temp.name
        env = patch.dict(os.environ, {"INFRA_TOOLS_WORKSPACE": temp.name})
        env.start()
        self.addCleanup(env.stop)
        policy = patch("lib.cluster_update._rolling_policy")
        policy.start()
        self.addCleanup(policy.stop)

    @patch("lib.cluster_update._maintenance_report", return_value=_maintenance_report())
    @patch("lib.cluster_update.prepare_validated_runtime_config")
    @patch("lib.cluster_update.load_setup_command")
    def test_preflight_failure_aborts_without_changes(
        self,
        mock_load_setup,
        mock_prepare,
        mock_maintenance,
    ) -> None:
        configs = {
            "pve1": _config("10.0.0.10"),
            "pve2": _config("10.0.0.11"),
        }
        mock_load_setup.side_effect = lambda target: configs.get(target)
        mock_prepare.side_effect = [
            configs["pve1"],
            ValueError("Missing workspace credential 'fileshare'"),
        ]

        with patch("infra_tools._execute_patch_config") as mock_execute:
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = run_cluster_update(["pve1", "pve2"], reboot_timeout=120)

        self.assertEqual(rc, 1)
        self.assertFalse(mock_execute.called)
        output = buf.getvalue()
        self.assertIn("Preflight failed; no systems were changed.", output)
        self.assertIn("Missing workspace credential 'fileshare'", output)

    @patch("lib.cluster_update._reboot_and_wait")
    @patch("lib.cluster_update._maintenance_report")
    @patch("lib.cluster_update.prepare_validated_runtime_config")
    @patch("lib.cluster_update.load_setup_command")
    def test_updates_targets_in_order_and_reboots_when_needed(
        self,
        mock_load_setup,
        mock_prepare,
        mock_maintenance,
        mock_reboot_and_wait,
    ) -> None:
        configs = {
            "pve1": _config("10.0.0.10"),
            "pve2": _config("10.0.0.11"),
        }
        mock_load_setup.side_effect = lambda target: configs.get(target)
        mock_prepare.side_effect = [configs["pve1"], configs["pve2"]]
        mock_maintenance.side_effect = [
            _maintenance_report(),
            _maintenance_report(),
            _maintenance_report(),
            _maintenance_report(reboot_required=True),
            _maintenance_report(running_guests=[ContainerInfo(vmid=100, status="running", name="restarted", guest_type="vm")]),
            _maintenance_report(),
            _maintenance_report(),
        ]

        with patch("infra_tools._execute_patch_config", side_effect=[0, 0]) as mock_execute:
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = run_cluster_update(["pve1", "pve2"], reboot_timeout=180)

        self.assertEqual(rc, 0)
        self.assertEqual(
            [call.args[0].host for call in mock_execute.call_args_list],
            ["10.0.0.10", "10.0.0.11"],
        )
        mock_reboot_and_wait.assert_called_once_with(configs["pve1"], 180)
        output = buf.getvalue()
        self.assertIn("UPDATED   pve1 [10.0.0.10] (reboot-required, rebooted)", output)
        self.assertIn("verified", output)
        self.assertIn("UPDATED   pve2 [10.0.0.11] - No reboot required", output)

    @patch("lib.cluster_update._maintenance_report", return_value=_maintenance_report())
    @patch("lib.cluster_update.prepare_validated_runtime_config")
    @patch("lib.cluster_update.load_setup_command")
    def test_failure_skips_remaining_targets(
        self,
        mock_load_setup,
        mock_prepare,
        _mock_maintenance,
    ) -> None:
        configs = {
            "pve1": _config("10.0.0.10"),
            "pve2": _config("10.0.0.11"),
            "pve3": _config("10.0.0.12"),
        }
        mock_load_setup.side_effect = lambda target: configs.get(target)
        mock_prepare.side_effect = [configs["pve1"], configs["pve2"], configs["pve3"]]

        with patch("infra_tools._execute_patch_config", side_effect=[0, 1]) as mock_execute:
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = run_cluster_update(["pve1", "pve2", "pve3"])

        self.assertEqual(rc, 1)
        self.assertEqual(mock_execute.call_count, 2)
        output = buf.getvalue()
        self.assertIn("FAILED    pve2 [10.0.0.11] - Patch run failed", output)
        self.assertIn("SKIPPED   pve3 [10.0.0.12] - Skipped after failure on pve2", output)

    @patch("lib.cluster_update._maintenance_report")
    @patch("lib.cluster_update.prepare_validated_runtime_config")
    @patch("lib.cluster_update.load_setup_command")
    def test_unhealthy_node_aborts_before_any_changes(
        self, mock_load_setup, mock_prepare, mock_maintenance
    ) -> None:
        config = _config("10.0.0.10")
        mock_load_setup.return_value = config
        mock_prepare.return_value = config
        mock_maintenance.return_value = _maintenance_report(errors=["Cluster is not quorate"])

        with patch("infra_tools._execute_patch_config") as mock_execute:
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = run_cluster_update(["pve1"])

        self.assertEqual(rc, 1)
        mock_execute.assert_not_called()
        self.assertIn("Cluster is not quorate", buf.getvalue())

    @patch("lib.cluster_update._reboot_and_wait")
    @patch("lib.cluster_update._maintenance_report")
    @patch("lib.cluster_update.prepare_validated_runtime_config")
    @patch("lib.cluster_update.load_setup_command")
    def test_running_guest_blocks_reboot_and_later_nodes(
        self,
        mock_load_setup,
        mock_prepare,
        mock_maintenance,
        mock_reboot_and_wait,
    ) -> None:
        configs = {
            "pve1": _config("10.0.0.10"),
            "pve2": _config("10.0.0.11"),
        }
        mock_load_setup.side_effect = lambda target: configs[target]
        mock_prepare.side_effect = [configs["pve1"], configs["pve2"]]
        running = _maintenance_report(
            running_guests=[
                ContainerInfo(vmid=100, status="running", name="web", guest_type="vm")
            ]
        )
        reboot_blocked = _maintenance_report(
            reboot_required=True,
            running_guests=running.running_guests,
        )
        mock_maintenance.side_effect = [running, _maintenance_report(), reboot_blocked]

        with patch("infra_tools._execute_patch_config", return_value=0) as mock_execute:
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = run_cluster_update(["pve1", "pve2"])

        self.assertEqual(rc, 1)
        self.assertEqual(mock_execute.call_count, 0)
        mock_reboot_and_wait.assert_not_called()
        self.assertIn("Evacuate or shut down guests before updating: running guests: 100", buf.getvalue())

    @patch("lib.cluster_update._maintenance_report", return_value=_maintenance_report())
    @patch("lib.cluster_update.prepare_validated_runtime_config")
    @patch("lib.cluster_update.load_setup_command")
    def test_resume_skips_completed_nodes_and_preserves_final_results(self, load, _prepare, _report):
        configs = {"pve1": _config("10.0.0.10"), "pve2": _config("10.0.0.11")}
        load.side_effect = configs.get
        with patch("infra_tools._execute_patch_config", side_effect=[0, 1]) as execute:
            self.assertEqual(run_cluster_update(list(configs)), 1)
        with patch("infra_tools._execute_patch_config", return_value=0) as execute:
            with self.assertRaisesRegex(ValueError, "Unfinished"):
                run_cluster_update(list(configs))
            execute.assert_not_called()
            self.assertEqual(run_cluster_update(list(configs), resume=True), 0)
            self.assertEqual([call.args[0].host for call in execute.call_args_list], ["10.0.0.11"])
        with open(os.path.join(self.workspace, "cluster-update-last.json")) as stream:
            saved = json.load(stream)
        self.assertEqual([node["phase"] for node in saved["nodes"]], ["complete", "complete"])
        self.assertFalse(os.path.exists(os.path.join(self.workspace, "cluster-update.json")))

    @patch("lib.cluster_update._maintenance_report", return_value=_maintenance_report())
    @patch("lib.cluster_update.prepare_validated_runtime_config")
    @patch("lib.cluster_update.load_setup_command", return_value=_config("10.0.0.10"))
    def test_interrupted_mutation_blocks_automatic_replay(self, _load, _prepare, _report):
        with patch("infra_tools._execute_patch_config", side_effect=KeyboardInterrupt):
            self.assertEqual(run_cluster_update(["pve1"]), 1)
        with patch("infra_tools._execute_patch_config") as execute:
            with self.assertRaisesRegex(ValueError, "manual recovery"):
                run_cluster_update(["pve1"], resume=True)
            execute.assert_not_called()


class TestRollingPolicyAndDeadlines(unittest.TestCase):
    def test_policy_rejects_ha_ceph_unknown_and_command_failure(self):
        for output, code in (("{\"ha\":true,\"ceph\":false}", 0), ("{\"ha\":false,\"ceph\":true}", 0), ("{}", 0), ("not json", 0), ("", 255)):
            with self.subTest(output=output), patch.object(cluster_update, "_ssh_result", return_value=subprocess.CompletedProcess([], code, output)), self.assertRaises(RuntimeError):
                cluster_update._rolling_policy(_config("10.0.0.10"))

    def test_ssh_command_has_overall_timeout(self):
        with patch.object(cluster_update, "run") as run:
            cluster_update._ssh_result(_config("10.0.0.10"), "true", timeout=3)
        self.assertEqual(run.call_args.kwargs["timeout"], 3)

    def test_poll_uses_remaining_budget_without_final_extra_probe(self):
        clock = [0.0]
        def probe(_config, timeout):
            self.assertEqual(timeout, 2)
            clock[0] += 2
            return False
        with patch.object(cluster_update.time, "monotonic", side_effect=lambda: clock[0]), patch.object(cluster_update.time, "sleep"), patch.object(cluster_update, "_ssh_available", side_effect=probe) as available:
            self.assertFalse(cluster_update._wait_for_ssh_state(_config("10.0.0.10"), available=True, timeout=2))
        available.assert_called_once()

    def test_failed_reboot_request_does_not_wait_for_fake_reboot(self):
        with patch.object(cluster_update, "_ssh_result", return_value=subprocess.CompletedProcess([], 255)), patch.object(cluster_update, "_wait_for_ssh_state") as wait, self.assertRaisesRegex(RuntimeError, "rejected"):
            cluster_update._reboot_and_wait(_config("10.0.0.10"), 30)
        wait.assert_not_called()
