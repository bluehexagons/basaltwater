"""Tests for lib/proxmox_shell.py: REPL command dispatch with mocked I/O."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from lib.proxmox_hosts import (
    ProxmoxHost,
    ProxmoxHostFacts,
    ProxmoxStoragePool,
    add_proxmox_host,
    load_proxmox_hosts,
)
from lib.proxmox_shell import ProxmoxShell


def _completed(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["ssh"], returncode=returncode, stdout=stdout, stderr=""
    )


class _ShellFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.workspace = self._tmp.name
        self.outputs: list[str] = []
        self.shell = ProxmoxShell(
            workspace=self.workspace,
            input_func=lambda prompt: "",
            output_func=self.outputs.append,
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def assert_output_contains(self, needle: str) -> None:
        joined = "\n".join(self.outputs)
        self.assertIn(needle, joined, f"missing '{needle}' in: {joined}")


class TestShellHostManagement(_ShellFixture):
    def test_add_then_hosts_lists_it(self) -> None:
        self.shell.dispatch("add pve1 10.0.0.10 root")
        self.shell.dispatch("hosts")
        self.assert_output_contains("pve1")
        self.assert_output_contains("10.0.0.10")
        loaded = load_proxmox_hosts(self.workspace)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].name, "pve1")

    def test_use_selects_active_host(self) -> None:
        self.shell.dispatch("add pve1 10.0.0.10")
        self.shell.dispatch("use pve1")
        self.assertIsNotNone(self.shell.state.active_host)
        assert self.shell.state.active_host is not None
        self.assertEqual(self.shell.state.active_host.name, "pve1")

    def test_use_unknown_host_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.shell.dispatch("use missing")

    @patch("lib.proxmox_shell.probe_proxmox_host")
    def test_probe_caches_host_defaults(self, mock_probe) -> None:
        self.shell.dispatch("add pve1 10.0.0.10")
        self.shell.dispatch("use pve1")
        self.outputs.clear()
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

        self.shell.dispatch("probe")

        loaded = load_proxmox_hosts(self.workspace)
        self.assertEqual(loaded[0].default_storage, "local-lvm")
        self.assertEqual(loaded[0].default_template_storage, "local")
        self.assertEqual(loaded[0].default_bridge, "vmbr0")
        self.assertIsNotNone(loaded[0].facts)
        self.assert_output_contains("Cached host facts")
        self.assert_output_contains("local-lvm")

    @patch("lib.proxmox_shell.probe_proxmox_cluster")
    def test_probe_cluster_registers_all_nodes(self, mock_probe_cluster) -> None:
        mock_probe_cluster.return_value = [
            ProxmoxHost(name="pve1", address="10.0.0.10", tags=["prod"]),
            ProxmoxHost(name="pve2", address="10.0.0.11", tags=["prod"]),
        ]

        self.shell.dispatch("probe-cluster 10.0.0.10 --tag prod")

        loaded = load_proxmox_hosts(self.workspace)
        self.assertEqual([host.name for host in loaded], ["pve1", "pve2"])
        self.assertIsNotNone(self.shell.state.active_host)
        assert self.shell.state.active_host is not None
        self.assertEqual(self.shell.state.active_host.name, "pve1")
        self.assert_output_contains("Discovered 2 Proxmox node(s)")

    def test_remove_clears_active_host(self) -> None:
        self.shell.dispatch("add pve1 10.0.0.10")
        self.shell.dispatch("use pve1")
        self.shell.dispatch("remove pve1")
        self.assertIsNone(self.shell.state.active_host)
        self.assertEqual(load_proxmox_hosts(self.workspace), [])

    def test_remove_unknown_host_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.shell.dispatch("remove missing")


class TestShellRequiresActiveHost(_ShellFixture):
    def test_ls_without_host_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.shell.dispatch("ls")

    def test_status_without_host_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.shell.dispatch("status 100")


class TestShellContainerOps(_ShellFixture):
    def setUp(self) -> None:
        super().setUp()
        add_proxmox_host(
            ProxmoxHost(name="pve1", address="10.0.0.10"), self.workspace
        )
        self.shell.dispatch("use pve1")
        self.outputs.clear()

    @patch("lib.proxmox_manage._ssh_run")
    def test_ls_lists_containers(self, mock_run) -> None:
        mock_run.side_effect = [
            _completed("VMID Status Name\n100 running web\n101 stopped db\n"),
            _completed("VMID NAME STATUS MEM(MB) BOOTDISK(GB) PID\n"),
        ]
        self.shell.dispatch("ls")
        self.assert_output_contains("100")
        self.assert_output_contains("web")
        self.assert_output_contains("101")
        self.assert_output_contains("lxc")

    @patch("lib.proxmox_manage._ssh_run")
    def test_ls_when_empty_says_so(self, mock_run) -> None:
        mock_run.side_effect = [
            _completed("VMID Status Name\n"),
            _completed("VMID NAME STATUS MEM(MB) BOOTDISK(GB) PID\n"),
        ]
        self.shell.dispatch("ls")
        self.assert_output_contains("(no guests)")

    @patch("lib.proxmox_manage._ssh_run")
    def test_status_dispatch(self, mock_run) -> None:
        mock_run.return_value = _completed("status: running\n")
        self.shell.dispatch("status 100")
        self.assert_output_contains("VMID 100")
        self.assert_output_contains("running")

    @patch("lib.proxmox_manage._ssh_run")
    def test_start_dispatch(self, mock_run) -> None:
        mock_run.side_effect = [
            _completed("status: stopped\n"),
            _completed(""),
        ]
        self.shell.dispatch("start 100")
        self.assert_output_contains("Started guest 100")

    @patch("lib.proxmox_manage._ssh_run")
    def test_stop_default_uses_shutdown(self, mock_run) -> None:
        mock_run.side_effect = [
            _completed("status: running\n"),
            _completed(""),
        ]
        self.shell.dispatch("stop 100")
        executed = [c.args[3] for c in mock_run.call_args_list]
        self.assertIn("pct shutdown 100", executed)

    @patch("lib.proxmox_manage._ssh_run")
    def test_stop_force_uses_pct_stop(self, mock_run) -> None:
        mock_run.side_effect = [
            _completed("status: running\n"),
            _completed(""),
        ]
        self.shell.dispatch("stop 100 --force")
        executed = [c.args[3] for c in mock_run.call_args_list]
        self.assertIn("pct stop 100", executed)

    @patch("lib.proxmox_manage._ssh_run")
    def test_health_dispatch_reports_status(self, mock_run) -> None:
        mock_run.side_effect = [
            _completed("status: running\n"),
            _completed("net0: name=eth0,bridge=vmbr0,ip=10.0.0.50/24\n"),
            _completed("OK\n"),
            _completed("OK\n"),
        ]
        self.shell.dispatch("health 100")
        self.assert_output_contains("HEALTHY")
        self.assert_output_contains("10.0.0.50")


class TestShellDestroyConfirmation(_ShellFixture):
    def setUp(self) -> None:
        super().setUp()
        add_proxmox_host(
            ProxmoxHost(name="pve1", address="10.0.0.10"), self.workspace
        )
        self.confirm_calls: list[bool] = []
        self.shell = ProxmoxShell(
            workspace=self.workspace,
            input_func=lambda prompt: "",
            output_func=self.outputs.append,
            confirm_destroy=self._record_confirm,
        )
        self.shell.dispatch("use pve1")
        self.outputs.clear()

    def _record_confirm(self, info, host) -> bool:
        self.confirm_calls.append(True)
        return self._confirm_response

    @patch("lib.proxmox_manage._ssh_run")
    def test_destroy_calls_confirm_and_proceeds_on_yes(self, mock_run) -> None:
        self._confirm_response = True
        mock_run.side_effect = [
            _completed("VMID Status Name\n100 stopped web\n"),  # pct list
            _completed("VMID NAME STATUS MEM(MB) BOOTDISK(GB) PID\n"),  # qm list
            _completed("status: stopped\n"),                     # status pre-destroy
            _completed(""),                                       # destroy
        ]
        self.shell.dispatch("destroy 100")
        self.assertEqual(self.confirm_calls, [True])
        self.assert_output_contains("Destroyed guest 100")

    @patch("lib.proxmox_manage._ssh_run")
    def test_destroy_aborts_when_confirm_false(self, mock_run) -> None:
        self._confirm_response = False
        mock_run.side_effect = [
            _completed("VMID Status Name\n100 stopped web\n"),
            _completed("VMID NAME STATUS MEM(MB) BOOTDISK(GB) PID\n"),
        ]
        self.shell.dispatch("destroy 100")
        self.assert_output_contains("cancelled")
        # Only the list_containers call should have happened.
        self.assertEqual(mock_run.call_count, 2)

    @patch("lib.proxmox_manage._ssh_run")
    def test_destroy_unknown_vmid_raises(self, mock_run) -> None:
        mock_run.side_effect = [
            _completed("VMID Status Name\n"),
            _completed("VMID NAME STATUS MEM(MB) BOOTDISK(GB) PID\n"),
        ]
        with self.assertRaises(ValueError):
            self.shell.dispatch("destroy 999")


class TestShellMigration(_ShellFixture):
    def setUp(self) -> None:
        super().setUp()
        for name, address in (("pve1", "10.0.0.10"), ("pve2", "10.0.0.11")):
            add_proxmox_host(ProxmoxHost(name=name, address=address), self.workspace)
        self.shell.dispatch("use pve1")

    @patch("lib.proxmox_shell.migrate_guest")
    def test_storage_timeout_and_dry_run_are_honored(self, mock_migrate):
        self.shell.dispatch("migrate 100 pve2 --target-storage local --timeout 14400 --dry-run")
        self.assertEqual(mock_migrate.call_args.kwargs, {
            "online": False, "with_local_disks": False, "target_storage": "local",
            "timeout": 14400, "shutdown_timeout": 180, "dry_run": True,
            "workspace": self.workspace, "resume": False,
        })
        self.assert_output_contains("Would migrate")
        self.assertNotIn("Migrated", "\n".join(self.outputs))

    @patch("lib.proxmox_shell.migrate_guest")
    def test_online_local_disk_flags_are_forwarded(self, mock_migrate):
        self.shell.dispatch("migrate 100 pve2 --online --with-local-disks")
        self.assertTrue(mock_migrate.call_args.kwargs["online"])
        self.assertTrue(mock_migrate.call_args.kwargs["with_local_disks"])

    @patch("lib.proxmox_shell.migrate_guest")
    def test_guest_name_and_resume_are_forwarded(self, mock_migrate):
        self.shell.dispatch("migrate agent-one pve2 --resume --shutdown-timeout 240")
        self.assertEqual(mock_migrate.call_args.args[1], "agent-one")
        self.assertTrue(mock_migrate.call_args.kwargs["resume"])
        self.assertEqual(mock_migrate.call_args.kwargs["shutdown_timeout"], 240)

    @patch("lib.proxmox_shell.migrate_guest")
    def test_unknown_and_incomplete_options_do_not_migrate(self, mock_migrate):
        for options in ("--dryrun", "--target-storage", "--timeout", "--timeout nope",
                        "--target-storage --dry-run", "unexpected"):
            with self.subTest(options=options):
                with self.assertRaises(ValueError):
                    self.shell.dispatch(f"migrate 100 pve2 {options}")
        mock_migrate.assert_not_called()


class TestShellMisc(_ShellFixture):
    def test_help_lists_commands(self) -> None:
        self.shell.dispatch("help")
        self.assert_output_contains("destroy")
        self.assert_output_contains("health")
        self.assert_output_contains("probe")

    def test_unknown_command_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.shell.dispatch("nonsense")

    def test_run_quits_on_eof(self) -> None:
        def raising_input(prompt: str) -> str:
            raise EOFError
        shell = ProxmoxShell(
            workspace=self.workspace,
            input_func=raising_input,
            output_func=self.outputs.append,
        )
        self.assertEqual(shell.run(), 0)

    def test_run_executes_then_quits(self) -> None:
        scripted = iter(["help", "quit"])
        shell = ProxmoxShell(
            workspace=self.workspace,
            input_func=lambda prompt: next(scripted),
            output_func=self.outputs.append,
        )
        self.assertEqual(shell.run(), 0)
        self.assert_output_contains("destroy")

    def test_invalid_vmid_raises(self) -> None:
        add_proxmox_host(
            ProxmoxHost(name="pve1", address="10.0.0.10"), self.workspace
        )
        self.shell.dispatch("use pve1")
        with self.assertRaises(ValueError):
            self.shell.dispatch("status notanumber")
        with self.assertRaises(ValueError):
            self.shell.dispatch("status -1")

    def test_host_command_shows_saved_defaults(self) -> None:
        add_proxmox_host(
            ProxmoxHost(
                name="pve1",
                address="10.0.0.10",
                default_storage="local-lvm",
                default_template_storage="local",
                default_bridge="vmbr0",
            ),
            self.workspace,
        )
        self.shell.dispatch("use pve1")
        self.outputs.clear()

        self.shell.dispatch("host")

        self.assert_output_contains("Root pool: local-lvm")
        self.assert_output_contains("Template:  local")
        self.assert_output_contains("Bridge:    vmbr0")


class TestShellLifecycleCommands(_ShellFixture):
    def setUp(self) -> None:
        super().setUp()
        add_proxmox_host(
            ProxmoxHost(name="pve1", address="10.0.0.10"), self.workspace
        )
        self.shell.dispatch("use pve1")

    @patch("lib.proxmox_manage._ssh_run")
    def test_config_dispatch(self, mock_run) -> None:
        mock_run.return_value = _completed("cores: 2\nmemory: 2048\n")
        self.shell.dispatch("config 100")
        self.assert_output_contains("cores")
        self.assert_output_contains("2048")

    @patch("lib.proxmox_manage._ssh_run")
    def test_config_pending_flag(self, mock_run) -> None:
        mock_run.return_value = _completed("cores: 4\n")
        self.shell.dispatch("config 100 --pending")
        self.assert_output_contains("Pending")
        self.assertIn("pct pending", mock_run.call_args.args[3])

    @patch("lib.proxmox_manage._ssh_run")
    def test_set_dispatch(self, mock_run) -> None:
        mock_run.return_value = _completed()
        self.shell.dispatch("set 100 hostname=newbox")
        self.assert_output_contains("Set 1 option")
        self.assertIn("--hostname", mock_run.call_args.args[3])

    def test_set_without_options_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.shell.dispatch("set 100")

    def test_set_bad_format_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.shell.dispatch("set 100 notakeyvalue")

    @patch("lib.proxmox_manage._ssh_run")
    def test_modify_cores(self, mock_run) -> None:
        mock_run.return_value = _completed()
        self.shell.dispatch("modify 100 --cores 4")
        self.assert_output_contains("cores=4")
        self.assertIn("--cores", mock_run.call_args.args[3])

    @patch("lib.proxmox_manage._ssh_run")
    def test_modify_memory_g_suffix(self, mock_run) -> None:
        mock_run.return_value = _completed()
        self.shell.dispatch("modify 100 --memory 2G")
        self.assert_output_contains("memory=2048M")
        self.assertIn("2048", mock_run.call_args.args[3])

    def test_modify_no_flags_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.shell.dispatch("modify 100")

    @patch("lib.proxmox_manage._ssh_run")
    def test_resize_dispatch(self, mock_run) -> None:
        mock_run.return_value = _completed()
        self.shell.dispatch("resize 100 rootfs 20G")
        self.assert_output_contains("Resized rootfs")
        cmd = mock_run.call_args.args[3]
        self.assertIn("pct resize", cmd)
        self.assertIn("rootfs", cmd)
        self.assertIn("20G", cmd)

    def test_resize_wrong_arg_count_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.shell.dispatch("resize 100 rootfs")


if __name__ == "__main__":
    unittest.main()
