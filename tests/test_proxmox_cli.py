"""Tests for lib/proxmox_cli.py: argparse wiring and command dispatch."""

from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from lib.proxmox_cli import add_proxmox_subparser, run_proxmox_command
from lib.proxmox_hosts import (
    ProxmoxHost,
    ProxmoxHostFacts,
    ProxmoxStoragePool,
    add_proxmox_host,
    load_proxmox_hosts,
)
from lib.proxmox_maintenance import ProxmoxMaintenanceReport


def _make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subs = parser.add_subparsers(dest="command")
    add_proxmox_subparser(subs)
    return parser


def _completed(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["ssh"], returncode=returncode, stdout=stdout, stderr=""
    )


class _CliFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.workspace = self._tmp.name
        self.parser = _make_parser()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, *argv: str) -> tuple[int, str]:
        args = self.parser.parse_args(["proxmox", "--workspace", self.workspace, *argv])
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = run_proxmox_command(args)
        return rc, buf.getvalue()


class TestProxmoxCliHosts(_CliFixture):
    def test_add_then_list(self) -> None:
        rc, _ = self._run("add", "pve1", "10.0.0.10", "-u", "root", "--description", "primary")
        self.assertEqual(rc, 0)
        rc, out = self._run("hosts")
        self.assertEqual(rc, 0)
        self.assertIn("pve1", out)
        self.assertIn("10.0.0.10", out)
        self.assertIn("primary", out)

    def test_hosts_json_uses_common_envelope(self) -> None:
        self._run("add", "pve1", "10.0.0.10")
        rc, out = self._run("hosts", "--json")
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(payload["operation"], "hosts")
        self.assertEqual(payload["resources"][0]["name"], "pve1")

    def test_add_duplicate_without_replace_errors(self) -> None:
        self._run("add", "pve1", "10.0.0.10")
        rc, out = self._run("add", "pve1", "10.0.0.99")
        self.assertEqual(rc, 1)
        self.assertIn("already exists", out)

    def test_add_with_replace_overwrites(self) -> None:
        self._run("add", "pve1", "10.0.0.10")
        rc, _ = self._run("add", "pve1", "10.0.0.99", "--replace")
        self.assertEqual(rc, 0)
        rc, out = self._run("hosts")
        self.assertIn("10.0.0.99", out)
        self.assertNotIn("10.0.0.10", out)

    def test_remove_existing_host(self) -> None:
        self._run("add", "pve1", "10.0.0.10")
        rc, out = self._run("remove", "pve1")
        self.assertEqual(rc, 0)
        self.assertIn("Removed", out)

    def test_remove_missing_host_returns_failure(self) -> None:
        rc, out = self._run("remove", "missing")
        self.assertEqual(rc, 1)
        self.assertIn("No Proxmox host", out)

    @patch("lib.proxmox_cli.probe_proxmox_host")
    def test_probe_updates_cached_defaults(self, mock_probe) -> None:
        add_proxmox_host(
            ProxmoxHost(name="pve1", address="10.0.0.10"),
            self.workspace,
        )
        mock_probe.return_value = ProxmoxHostFacts(
            node_name="pve1",
            bridges=["vmbr0"],
            gateway="10.0.0.1",
            nameservers=["1.1.1.1"],
            storage_pools=[
                ProxmoxStoragePool(
                    name="local-lvm",
                    type="lvmthin",
                    status="active",
                    content=["images", "rootdir"],
                )
            ],
            default_root_storage="local-lvm",
            default_template_storage="local",
            default_bridge="vmbr0",
        )

        rc, out = self._run("probe", "pve1")

        self.assertEqual(rc, 0)
        self.assertIn("local-lvm", out)
        self.assertIn("vmbr0", out)
        loaded = load_proxmox_hosts(self.workspace)
        self.assertEqual(loaded[0].default_storage, "local-lvm")
        self.assertEqual(loaded[0].default_template_storage, "local")

    @patch("lib.proxmox_cli.probe_proxmox_cluster")
    def test_probe_cluster_seeds_discovered_nodes(self, mock_probe_cluster) -> None:
        mock_probe_cluster.return_value = [
            ProxmoxHost(
                name="pve1",
                address="10.0.0.10",
                default_storage="local-lvm",
                tags=["prod"],
            ),
            ProxmoxHost(
                name="pve2",
                address="10.0.0.11",
                default_storage="shared-lvm",
                tags=["prod"],
            ),
        ]

        rc, out = self._run("probe-cluster", "10.0.0.10", "--tag", "prod")

        self.assertEqual(rc, 0)
        self.assertIn("pve1", out)
        self.assertIn("pve2", out)
        self.assertIn("tags=prod", out)
        loaded = load_proxmox_hosts(self.workspace)
        self.assertEqual([host.name for host in loaded], ["pve1", "pve2"])

    @patch("lib.proxmox_cli.run_cluster_update", return_value=0)
    def test_rolling_update_dispatches_to_cluster_updater(self, mock_run_cluster_update) -> None:
        rc, _ = self._run("rolling-update", "pve1", "pve2", "--reboot-timeout", "180")

        self.assertEqual(rc, 0)
        mock_run_cluster_update.assert_called_once_with(
            ["pve1", "pve2"],
            workspace=self.workspace,
            dry_run=False,
            reboot_timeout=180,
            resume=False,
        )


class TestProxmoxCliContainerOps(_CliFixture):
    def setUp(self) -> None:
        super().setUp()
        add_proxmox_host(
            ProxmoxHost(name="pve1", address="10.0.0.10"), self.workspace
        )

    @patch("lib.proxmox_manage._ssh_run")
    def test_ls_lists_containers(self, mock_run) -> None:
        mock_run.side_effect = [
            _completed("VMID Status Name\n100 running web\n"),
            _completed("VMID NAME STATUS MEM(MB) BOOTDISK(GB) PID\n"),
        ]
        rc, out = self._run("ls", "pve1")
        self.assertEqual(rc, 0)
        self.assertIn("100", out)
        self.assertIn("web", out)
        self.assertIn("lxc", out)

    @patch("lib.proxmox_manage._ssh_run")
    def test_ls_json_uses_common_envelope(self, mock_run) -> None:
        mock_run.side_effect = [
            _completed("VMID Status Name\n100 running web\n"),
            _completed("VMID NAME STATUS MEM(MB) BOOTDISK(GB) PID\n"),
        ]
        rc, out = self._run("ls", "pve1", "--json")
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(payload["operation"], "list")
        self.assertEqual(payload["resources"][0]["id"], "100")

    @patch("lib.proxmox_manage._ssh_run")
    def test_status_prints_value(self, mock_run) -> None:
        mock_run.return_value = _completed("status: running\n")
        rc, out = self._run("status", "pve1", "100")
        self.assertEqual(rc, 0)
        self.assertIn("running", out)

    @patch("lib.proxmox_manage._ssh_run")
    def test_status_json_uses_common_envelope(self, mock_run) -> None:
        mock_run.side_effect = [
            _completed("status: running\n"),
            _completed("VMID Status Name\n100 running web\n"),
            _completed("VMID NAME STATUS MEM(MB) BOOTDISK(GB) PID\n"),
        ]
        rc, out = self._run("status", "pve1", "100", "--json")
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(payload["operation"], "status")
        self.assertEqual(payload["resources"][0]["state"], "running")

    @patch("lib.proxmox_manage._ssh_run")
    def test_start_runs_pct_start(self, mock_run) -> None:
        mock_run.side_effect = [
            _completed("status: stopped\n"),
            _completed(""),
        ]
        rc, _ = self._run("start", "pve1", "100")
        self.assertEqual(rc, 0)
        self.assertIn("pct start 100", mock_run.call_args_list[1].args[3])

    @patch("lib.proxmox_manage._ssh_run")
    def test_pause_runs_pct_suspend(self, mock_run) -> None:
        mock_run.side_effect = [
            _completed("status: running\n"),
            _completed(""),
        ]
        rc, out = self._run("pause", "pve1", "100")
        self.assertEqual(rc, 0)
        self.assertIn("Paused guest 100", out)
        self.assertIn("pct suspend 100", mock_run.call_args_list[-1].args[3])

    @patch("lib.proxmox_manage._ssh_run")
    def test_suspend_alias_pauses_vm(self, mock_run) -> None:
        mock_run.side_effect = [
            _completed("CT 100 does not exist", returncode=2),
            _completed("status: running\n"),
            _completed(""),
        ]
        rc, _ = self._run("suspend", "pve1", "100")
        self.assertEqual(rc, 0)
        self.assertIn("qm suspend 100", mock_run.call_args_list[-1].args[3])

    @patch("lib.proxmox_manage._ssh_run")
    def test_resume_runs_pct_resume(self, mock_run) -> None:
        mock_run.side_effect = [
            _completed("status: running\n"),
            _completed(""),
        ]
        rc, out = self._run("resume", "pve1", "100")
        self.assertEqual(rc, 0)
        self.assertIn("Resumed guest 100", out)
        self.assertIn("pct resume 100", mock_run.call_args_list[-1].args[3])

    @patch("lib.proxmox_manage._ssh_run")
    def test_stop_force(self, mock_run) -> None:
        mock_run.side_effect = [
            _completed("status: running\n"),
            _completed(""),
        ]
        rc, _ = self._run("stop", "pve1", "100", "--force")
        self.assertEqual(rc, 0)
        self.assertIn("pct stop 100", mock_run.call_args_list[-1].args[3])

    @patch("lib.proxmox_manage._ssh_run")
    def test_destroy_yes_skips_prompt(self, mock_run) -> None:
        mock_run.side_effect = [
            _completed("status: stopped\n"),
            _completed(""),
        ]
        rc, out = self._run("destroy", "pve1", "100", "-y")
        self.assertEqual(rc, 0)
        self.assertIn("Destroyed guest 100", out)

    @patch("lib.proxmox_manage._ssh_run")
    @patch("builtins.input", return_value="no")
    def test_destroy_no_yes_prompts_and_aborts(self, mock_input, mock_run) -> None:
        rc, out = self._run("destroy", "pve1", "100")
        self.assertEqual(rc, 1)
        self.assertIn("Aborted", out)
        # Should not have run pct destroy.
        for call in mock_run.call_args_list:
            self.assertNotIn("destroy", call.args[3])

    @patch("lib.proxmox_manage._ssh_run")
    @patch("builtins.input", return_value="yes")
    def test_destroy_no_yes_prompts_and_proceeds(self, mock_input, mock_run) -> None:
        mock_run.side_effect = [
            _completed("status: stopped\n"),
            _completed(""),
        ]
        rc, _ = self._run("destroy", "pve1", "100")
        self.assertEqual(rc, 0)
        self.assertIn("pct destroy 100", mock_run.call_args_list[-1].args[3])

    @patch("lib.proxmox_manage._ssh_run")
    def test_health_returns_zero_when_healthy(self, mock_run) -> None:
        mock_run.side_effect = [
            _completed("status: running\n"),
            _completed("net0: name=eth0,bridge=vmbr0,ip=10.0.0.50/24\n"),
            _completed("OK\n"),
            _completed("OK\n"),
        ]
        rc, out = self._run("health", "pve1", "100")
        self.assertEqual(rc, 0)
        self.assertIn("HEALTHY", out)

    @patch("lib.proxmox_manage._ssh_run")
    def test_health_json_uses_common_envelope(self, mock_run) -> None:
        mock_run.side_effect = [
            _completed("status: running\n"),
            _completed("net0: name=eth0,bridge=vmbr0,ip=10.0.0.50/24\n"),
            _completed("OK\n"),
            _completed("OK\n"),
        ]
        rc, out = self._run("health", "pve1", "100", "--json")
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertTrue(payload["resources"][0]["healthy"])

    @patch("lib.proxmox_manage._ssh_run")
    def test_health_returns_one_when_unhealthy(self, mock_run) -> None:
        mock_run.return_value = _completed(returncode=2)
        rc, out = self._run("health", "pve1", "100")
        self.assertEqual(rc, 1)
        self.assertIn("UNHEALTHY", out)

    def test_unknown_host_returns_error(self) -> None:
        rc, out = self._run("status", "missing", "100")
        self.assertEqual(rc, 1)
        self.assertIn("No registered Proxmox host", out)


class TestProxmoxCliAudit(_CliFixture):
    def setUp(self) -> None:
        super().setUp()
        add_proxmox_host(
            ProxmoxHost(name="pve1", address="10.0.0.10"), self.workspace
        )

    @patch("lib.proxmox_cli.collect_maintenance_report")
    def test_audit_prints_human_report(self, mock_collect) -> None:
        mock_collect.return_value = ProxmoxMaintenanceReport(
            host_name="pve1",
            address="10.0.0.10",
            node_name="pve1",
            clustered=False,
            reboot_required=False,
        )

        rc, out = self._run("audit", "pve1")

        self.assertEqual(rc, 0)
        self.assertIn("Proxmox maintenance audit", out)
        self.assertIn("HEALTHY", out)
        self.assertIn("READY", out)

    @patch("lib.proxmox_cli.collect_maintenance_report")
    def test_audit_json_is_machine_readable_and_fails_when_unhealthy(
        self, mock_collect
    ) -> None:
        mock_collect.return_value = ProxmoxMaintenanceReport(
            host_name="pve1",
            address="10.0.0.10",
            node_name="pve1",
            clustered=True,
            quorate=False,
            errors=["Cluster is not quorate"],
        )

        rc, out = self._run("audit", "pve1", "--json")

        self.assertEqual(rc, 1)
        payload = json.loads(out)
        self.assertEqual(payload["operation"], "audit")
        self.assertFalse(payload["resources"][0]["healthy"])
        self.assertEqual(payload["resources"][0]["errors"], ["Cluster is not quorate"])


class TestProxmoxPlanRebalanceApply(_CliFixture):
    """End-to-end tests for `proxmox plan rebalance --apply` wiring.

    Patches the SSH-heavy collaborators so the handler runs in-process.
    """

    _GIB = 1024 ** 3

    def setUp(self) -> None:
        super().setUp()
        add_proxmox_host(
            ProxmoxHost(name="hot", address="10.0.0.10"), self.workspace
        )
        add_proxmox_host(
            ProxmoxHost(name="cool", address="10.0.0.11"), self.workspace
        )

    def _snapshots(self) -> list:
        from lib.proxmox_placement import NodeSnapshot
        from lib.proxmox_summary import NodeSummary
        hot = NodeSnapshot(
            host=ProxmoxHost(name="hot", address="10.0.0.10"),
            summary=NodeSummary(
                node_name="hot", cpu_usage=0.95, cpu_count=8,
                memory_used=14 * self._GIB, memory_total=16 * self._GIB,
                swap_used=0, swap_total=0,
                disk_used=50 * self._GIB, disk_total=500 * self._GIB,
                uptime_seconds=3600, guests_running=3,
            ),
        )
        cool = NodeSnapshot(
            host=ProxmoxHost(name="cool", address="10.0.0.11"),
            summary=NodeSummary(
                node_name="cool", cpu_usage=0.10, cpu_count=8,
                memory_used=2 * self._GIB, memory_total=16 * self._GIB,
                swap_used=0, swap_total=0,
                disk_used=50 * self._GIB, disk_total=500 * self._GIB,
                uptime_seconds=3600, guests_running=1,
            ),
        )
        return [hot, cool]

    def _guests(self) -> dict[str, list]:
        from lib.proxmox_manage import ContainerInfo
        return {
            "hot": [
                ContainerInfo(vmid=101, status="running", name="web", guest_type="vm"),
            ],
            "cool": [
                ContainerInfo(vmid=200, status="running", name="db", guest_type="lxc"),
            ],
        }

    def _patches(self, list_calls: dict[str, list]):
        return [
            patch(
                "lib.proxmox_cli.collect_snapshots",
                return_value=(self._snapshots(), []),
            ),
            patch(
                "lib.proxmox_cli.list_containers",
                side_effect=lambda host: list_calls.get(host.name, []),
            ),
        ]

    def test_apply_migrates_to_top_candidate_when_to_omitted(self) -> None:
        list_calls = self._guests()
        with self._patches(list_calls)[0], self._patches(list_calls)[1], \
             patch("lib.proxmox_cli.migrate_guest") as mock_migrate:
            rc, out = self._run(
                "plan", "rebalance", "--apply", "101", "--yes", "--dry-run"
            )
        self.assertEqual(rc, 0)
        self.assertIn("Would migrate VMID 101 from hot to cool", out)
        mock_migrate.assert_called_once()
        kwargs = mock_migrate.call_args.kwargs
        self.assertTrue(kwargs["dry_run"])
        self.assertFalse(kwargs["online"])

    def test_apply_rejects_vmid_not_on_hot_node(self) -> None:
        list_calls = self._guests()
        with self._patches(list_calls)[0], self._patches(list_calls)[1], \
             patch("lib.proxmox_cli.migrate_guest") as mock_migrate:
            rc, out = self._run(
                "plan", "rebalance", "--apply", "200", "--yes"
            )
        self.assertEqual(rc, 1)
        self.assertIn("not running on any hot node", out)
        mock_migrate.assert_not_called()

    def test_apply_rejects_when_to_is_source(self) -> None:
        list_calls = self._guests()
        with self._patches(list_calls)[0], self._patches(list_calls)[1], \
             patch("lib.proxmox_cli.migrate_guest") as mock_migrate:
            rc, out = self._run(
                "plan", "rebalance", "--apply", "101", "--to", "hot", "--yes"
            )
        self.assertEqual(rc, 1)
        self.assertIn("source host", out)
        mock_migrate.assert_not_called()

    def test_rebalance_without_apply_prints_suggestions(self) -> None:
        list_calls = self._guests()
        with self._patches(list_calls)[0], self._patches(list_calls)[1]:
            rc, out = self._run("plan", "rebalance")
        self.assertEqual(rc, 0)
        self.assertIn("Hot: hot", out)
        self.assertIn("cool", out)


class TestProxmoxCliNotifications(_CliFixture):
    def setUp(self) -> None:
        super().setUp()
        add_proxmox_host(
            ProxmoxHost(name="pve1", address="10.0.0.10"), self.workspace
        )

    @patch("lib.proxmox_manage._ssh_run")
    def test_install_webhook_configures_native_endpoint(self, mock_run) -> None:
        mock_run.return_value = _completed("")
        rc, out = self._run(
            "notifications",
            "install-webhook",
            "pve1",
            "https://notify.example/hook",
            "--endpoint-name",
            "it-webhook",
            "--matcher-name",
            "it-system",
            "--severity",
            "warning",
            "--severity",
            "error",
        )
        self.assertEqual(rc, 0)
        self.assertIn("Configured Proxmox webhook notifications", out)
        self.assertEqual(mock_run.call_count, 2)
        self.assertIn("/cluster/notifications/endpoints/webhook", mock_run.call_args_list[0].args[3])
        self.assertIn("--match-severity warning", mock_run.call_args_list[1].args[3])
        self.assertIn("--match-severity error", mock_run.call_args_list[1].args[3])

    @patch("lib.proxmox_manage._ssh_run")
    def test_install_webhook_dry_run_prints_would_configure(self, mock_run) -> None:
        mock_run.return_value = _completed("")
        rc, out = self._run(
            "notifications",
            "install-webhook",
            "pve1",
            "https://notify.example/hook",
            "--dry-run",
        )
        self.assertEqual(rc, 0)
        self.assertIn("Would configure", out)
        self.assertTrue(all(call.kwargs["dry_run"] for call in mock_run.call_args_list))

    @patch("lib.proxmox_manage._ssh_run")
    def test_install_webhook_send_test(self, mock_run) -> None:
        mock_run.return_value = _completed("")
        rc, _ = self._run(
            "notifications",
            "install-webhook",
            "pve1",
            "https://notify.example/hook",
            "--send-test",
        )
        self.assertEqual(rc, 0)
        self.assertEqual(mock_run.call_count, 3)
        self.assertIn(
            "/cluster/notifications/targets/infra-tools-webhook/test",
            mock_run.call_args_list[-1].args[3],
        )

    @patch("lib.proxmox_manage._ssh_run")
    def test_test_webhook_sends_native_test(self, mock_run) -> None:
        mock_run.return_value = _completed("")
        rc, out = self._run(
            "notifications",
            "test-webhook",
            "pve1",
            "--endpoint-name",
            "it-webhook",
        )
        self.assertEqual(rc, 0)
        self.assertIn("Sent Proxmox test notification", out)
        self.assertIn(
            "/cluster/notifications/targets/it-webhook/test",
            mock_run.call_args.args[3],
        )

    def test_notifications_without_subcommand_errors(self) -> None:
        rc, out = self._run("notifications")
        self.assertEqual(rc, 1)
        self.assertIn("Choose a notifications subcommand", out)


class TestProxmoxCliLifecycle(_CliFixture):
    def setUp(self) -> None:
        super().setUp()
        add_proxmox_host(
            ProxmoxHost(name="pve1", address="10.0.0.10"), self.workspace
        )

    @patch("lib.proxmox_manage._ssh_run")
    def test_config_shows_key_value(self, mock_run) -> None:
        mock_run.return_value = _completed("cores: 2\nmemory: 2048\nhostname: box\n")
        rc, out = self._run("config", "pve1", "100")
        self.assertEqual(rc, 0)
        self.assertIn("cores", out)
        self.assertIn("2048", out)

    @patch("lib.proxmox_manage._ssh_run")
    def test_config_pending_flag(self, mock_run) -> None:
        mock_run.return_value = _completed("cores: 4\n")
        rc, out = self._run("config", "pve1", "100", "--pending")
        self.assertEqual(rc, 0)
        self.assertIn("Pending", out)
        self.assertIn("pct pending", mock_run.call_args.args[3])

    @patch("lib.proxmox_manage._ssh_run")
    def test_reconfigure_sets_option(self, mock_run) -> None:
        mock_run.return_value = _completed()
        rc, out = self._run("reconfigure", "pve1", "100", "--set", "hostname=newbox")
        self.assertEqual(rc, 0)
        self.assertIn("Set 1 option", out)
        self.assertIn("--hostname", mock_run.call_args.args[3])

    def test_reconfigure_without_options_errors(self) -> None:
        rc, out = self._run("reconfigure", "pve1", "100")
        self.assertEqual(rc, 1)
        self.assertIn("--set", out)

    def test_reconfigure_bad_format_errors(self) -> None:
        rc, out = self._run("reconfigure", "pve1", "100", "--set", "no-equals-sign")
        self.assertEqual(rc, 1)
        self.assertIn("KEY=VALUE", out)

    @patch("lib.proxmox_manage._ssh_run")
    def test_modify_cores(self, mock_run) -> None:
        mock_run.return_value = _completed()
        rc, out = self._run("modify", "pve1", "100", "--cores", "4")
        self.assertEqual(rc, 0)
        self.assertIn("cores=4", out)
        self.assertIn("--cores", mock_run.call_args.args[3])

    @patch("lib.proxmox_manage._ssh_run")
    def test_modify_memory_with_g_suffix(self, mock_run) -> None:
        mock_run.return_value = _completed()
        rc, out = self._run("modify", "pve1", "100", "--memory", "4G")
        self.assertEqual(rc, 0)
        self.assertIn("memory=4096M", out)
        self.assertIn("4096", mock_run.call_args.args[3])

    def test_modify_without_flags_errors(self) -> None:
        rc, out = self._run("modify", "pve1", "100")
        self.assertEqual(rc, 1)
        self.assertIn("--cores", out)

    @patch("lib.proxmox_manage._ssh_run")
    def test_modify_dry_run(self, mock_run) -> None:
        mock_run.return_value = _completed()
        rc, out = self._run("modify", "pve1", "100", "--cores", "2", "--dry-run")
        self.assertEqual(rc, 0)
        self.assertIn("Would modify", out)

    @patch("lib.proxmox_manage._ssh_run")
    def test_resize_disk(self, mock_run) -> None:
        mock_run.return_value = _completed()
        rc, out = self._run("resize-disk", "pve1", "100", "rootfs", "20G")
        self.assertEqual(rc, 0)
        self.assertIn("Resized", out)
        cmd = mock_run.call_args.args[3]
        self.assertIn("pct resize", cmd)
        self.assertIn("rootfs", cmd)
        self.assertIn("20G", cmd)

    @patch("lib.proxmox_manage._ssh_run")
    def test_resize_disk_dry_run(self, mock_run) -> None:
        mock_run.return_value = _completed()
        rc, out = self._run("resize-disk", "pve1", "100", "rootfs", "20G", "--dry-run")
        self.assertEqual(rc, 0)
        self.assertIn("Would resize", out)


if __name__ == "__main__":
    unittest.main()
