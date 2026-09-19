"""Tests for lib/proxmox_manage.py: container CRUD and health checks (mocked)."""

from __future__ import annotations

import base64
import os
import subprocess
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from lib.proxmox_hosts import ProxmoxHost
from lib.proxmox_manage import (
    ContainerInfo,
    DEFAULT_NOTIFICATION_ENDPOINT,
    GuestAutostart,
    ProxmoxManageError,
    _build_webhook_notification_commands,
    _parse_listsnapshot,
    _parse_pct_list,
    _parse_qm_list,
    configure_guest_autostart,
    delete_snapshot,
    destroy_container,
    get_container_config,
    get_container_ip,
    get_container_pending,
    get_container_status,
    get_guest_autostart,
    get_guest_stats,
    health_check,
    install_webhook_notifications,
    list_containers,
    list_snapshots,
    modify_container,
    reboot_guest,
    reconfigure_container,
    resize_container_disk,
    rollback_guest,
    send_webhook_test_notification,
    snapshot_guest,
    start_container,
    stop_container,
    unlock_guest,
    ProxmoxWebhookNotificationConfig,
)


def _completed(stdout: str = "", stderr: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["ssh"], returncode=returncode, stdout=stdout, stderr=stderr
    )


def _host(**overrides) -> ProxmoxHost:
    base = {"name": "pve1", "address": "10.0.0.10", "user": "root"}
    base.update(overrides)
    return ProxmoxHost(**base)


class TestParsePctList(unittest.TestCase):
    def test_parses_standard_header(self) -> None:
        out = (
            "VMID       Status     Lock         Name\n"
            "100        running                 web\n"
            "101        stopped    backup       db\n"
        )
        rows = _parse_pct_list(out)
        self.assertEqual(rows, [
            ContainerInfo(vmid=100, status="running", name="web", lock=None),
            ContainerInfo(vmid=101, status="stopped", name="db", lock="backup"),
        ])

    def test_handles_no_header(self) -> None:
        rows = _parse_pct_list("100 running web\n")
        self.assertEqual(rows, [
            ContainerInfo(vmid=100, status="running", name="web", lock=None)
        ])

    def test_skips_empty_and_invalid_lines(self) -> None:
        out = (
            "VMID       Status     Name\n"
            "\n"
            "notanumber stopped foo\n"
            "200 running api\n"
        )
        rows = _parse_pct_list(out)
        self.assertEqual([r.vmid for r in rows], [200])

    def test_handles_two_column_rows(self) -> None:
        rows = _parse_pct_list("VMID Status\n100 running\n")
        self.assertEqual(rows, [
            ContainerInfo(vmid=100, status="running", name="", lock=None)
        ])


class TestParseQmList(unittest.TestCase):
    def test_parses_standard_header(self) -> None:
        out = (
            "VMID NAME                 STATUS     MEM(MB)    BOOTDISK(GB) PID\n"
            "101  web-01-vm            running    4096       32.00        123\n"
            "102  db-01-vm             stopped    2048       16.00        0\n"
        )
        rows = _parse_qm_list(out)
        self.assertEqual(rows, [
            ContainerInfo(vmid=101, status="running", name="web-01-vm", guest_type="vm"),
            ContainerInfo(vmid=102, status="stopped", name="db-01-vm", guest_type="vm"),
        ])

    def test_ignores_non_qm_headers(self) -> None:
        self.assertEqual(_parse_qm_list("VMID Status Name\n100 running web\n"), [])


class TestListContainers(unittest.TestCase):
    @patch("lib.proxmox_manage._ssh_run")
    def test_list_containers_sorts_by_vmid(self, mock_run: MagicMock) -> None:
        mock_run.return_value = _completed(
            "VMID Status Name\n200 running b\n100 stopped a\n"
        )
        rows = list_containers(_host())
        self.assertEqual([r.vmid for r in rows], [100, 200])

    @patch("lib.proxmox_manage._ssh_run")
    def test_list_containers_includes_qm_guests(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = [
            _completed("VMID Status Name\n200 running lxc-web\n"),
            _completed(
                "VMID NAME STATUS MEM(MB) BOOTDISK(GB) PID\n"
                "101 vm-web running 4096 32.00 123\n"
            ),
        ]
        rows = list_containers(_host())
        self.assertEqual([(row.vmid, row.guest_type) for row in rows], [(101, "vm"), (200, "lxc")])

    @patch("lib.proxmox_manage._ssh_run")
    def test_list_containers_raises_on_failure(self, mock_run: MagicMock) -> None:
        mock_run.return_value = _completed(stderr="boom", returncode=1)
        with self.assertRaises(ProxmoxManageError):
            list_containers(_host())

    def test_list_containers_dry_run_returns_empty(self) -> None:
        with patch("lib.proxmox_manage._ssh_run") as mock_run:
            self.assertEqual(list_containers(_host(), dry_run=True), [])
            mock_run.assert_not_called()


class TestContainerIp(unittest.TestCase):
    @patch("lib.proxmox_manage._ssh_run")
    def test_extracts_ip_from_net0(self, mock_run: MagicMock) -> None:
        mock_run.return_value = _completed(
            "arch: amd64\n"
            "net0: name=eth0,bridge=vmbr0,ip=10.0.0.50/24,gw=10.0.0.1,type=veth\n"
        )
        self.assertEqual(get_container_ip(_host(), 100), "10.0.0.50")

    @patch("lib.proxmox_manage._ssh_run")
    def test_returns_none_when_no_net0(self, mock_run: MagicMock) -> None:
        mock_run.return_value = _completed("arch: amd64\n")
        self.assertIsNone(get_container_ip(_host(), 100))

    @patch("lib.proxmox_manage._ssh_run")
    def test_returns_none_on_command_failure(self, mock_run: MagicMock) -> None:
        mock_run.return_value = _completed(returncode=2)
        self.assertIsNone(get_container_ip(_host(), 100))

    @patch("lib.proxmox_manage._ssh_run")
    def test_falls_back_to_qm_config(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = [
            _completed(stderr="CT 100 does not exist", returncode=2),
            _completed("ipconfig0: ip=10.0.0.60/24,gw=10.0.0.1\n"),
        ]
        self.assertEqual(get_container_ip(_host(), 100), "10.0.0.60")


class TestStatus(unittest.TestCase):
    @patch("lib.proxmox_manage._ssh_run")
    def test_returns_status_after_colon(self, mock_run: MagicMock) -> None:
        mock_run.return_value = _completed("status: running\n")
        self.assertEqual(get_container_status(_host(), 100), "running")

    @patch("lib.proxmox_manage._ssh_run")
    def test_raises_on_failure(self, mock_run: MagicMock) -> None:
        mock_run.return_value = _completed(stderr="not found", returncode=2)
        with self.assertRaises(ProxmoxManageError):
            get_container_status(_host(), 100)

    @patch("lib.proxmox_manage._ssh_run")
    def test_falls_back_to_qm_status(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = [
            _completed(stderr="CT 100 does not exist", returncode=2),
            _completed("status: running\n"),
        ]
        self.assertEqual(get_container_status(_host(), 100), "running")


class TestGuestStats(unittest.TestCase):
    @patch("lib.proxmox_manage._ssh_run")
    def test_parses_verbose_container_status(self, mock_run: MagicMock) -> None:
        mock_run.return_value = _completed(
            "status: running\n"
            "cpu: 0.375\n"
            "cpus: 2\n"
            "mem: 3221225472\n"
            "maxmem: 4294967296\n"
            "swap: 134217728\n"
            "maxswap: 1073741824\n"
            "disk: 10737418240\n"
            "maxdisk: 34359738368\n"
            "diskread: 123456\n"
            "diskwrite: 654321\n"
            "netin: 111\n"
            "netout: 222\n"
            "uptime: 3661\n"
        )

        stats = get_guest_stats(_host(), 100)

        self.assertEqual(stats.guest_type, "lxc")
        self.assertEqual(stats.status, "running")
        self.assertEqual(stats.cpu_usage, 0.375)
        self.assertEqual(stats.cpu_count, 2)
        self.assertEqual(stats.memory_used, 3221225472)
        self.assertEqual(stats.network_out, 222)
        self.assertEqual(stats.uptime_seconds, 3661)
        self.assertIn("pct status 100 --verbose", mock_run.call_args.args[3])

    @patch("lib.proxmox_manage._ssh_run")
    def test_falls_back_to_verbose_qm_status(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = [
            _completed(stderr="CT 100 does not exist", returncode=2),
            _completed("status: stopped\ncpu: unavailable\nmaxmem: 4294967296\n"),
        ]

        stats = get_guest_stats(_host(), 100)

        self.assertEqual(stats.guest_type, "vm")
        self.assertEqual(stats.status, "stopped")
        self.assertEqual(stats.cpu_usage, 0.0)
        self.assertEqual(stats.memory_total, 4294967296)
        self.assertIn("qm status 100 --verbose", mock_run.call_args.args[3])

    @patch("lib.proxmox_manage._ssh_run")
    def test_raises_when_verbose_status_fails(self, mock_run: MagicMock) -> None:
        mock_run.return_value = _completed(stderr="locked", returncode=1)

        with self.assertRaisesRegex(ProxmoxManageError, "status 100 --verbose"):
            get_guest_stats(_host(), 100)


class TestGuestAutostart(unittest.TestCase):
    @patch("lib.proxmox_manage.get_container_config")
    def test_parses_autostart_and_ordering(self, mock_config: MagicMock) -> None:
        mock_config.return_value = {
            "onboot": "1",
            "startup": "order=2,up=45,down=120",
        }

        settings = get_guest_autostart(_host(), 100)

        self.assertEqual(
            settings,
            GuestAutostart(
                enabled=True,
                order=2,
                start_delay=45,
                shutdown_timeout=120,
            ),
        )

    @patch("lib.proxmox_manage.reconfigure_container")
    def test_enables_autostart_without_replacing_schedule(
        self,
        mock_reconfigure: MagicMock,
    ) -> None:
        configure_guest_autostart(_host(), 100, enabled=True)

        mock_reconfigure.assert_called_once_with(_host(), 100, {"onboot": "1"})

    @patch("lib.proxmox_manage.reconfigure_container")
    @patch("lib.proxmox_manage.get_guest_autostart")
    def test_updates_one_schedule_field_and_preserves_others(
        self,
        mock_current: MagicMock,
        mock_reconfigure: MagicMock,
    ) -> None:
        mock_current.return_value = GuestAutostart(
            enabled=True,
            order=3,
            start_delay=30,
            shutdown_timeout=90,
        )

        configure_guest_autostart(_host(), 100, enabled=True, order=1)

        mock_reconfigure.assert_called_once_with(
            _host(),
            100,
            {"onboot": "1", "startup": "order=1,up=30,down=90"},
        )

    def test_rejects_schedule_when_disabling(self) -> None:
        with self.assertRaisesRegex(ValueError, "require enabled=True"):
            configure_guest_autostart(_host(), 100, enabled=False, order=1)

    def test_rejects_negative_schedule_value(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be >= 0"):
            configure_guest_autostart(_host(), 100, enabled=True, start_delay=-1)


class TestStartStop(unittest.TestCase):
    @patch("lib.proxmox_manage._ssh_run")
    def test_start_skips_when_already_running(self, mock_run: MagicMock) -> None:
        mock_run.return_value = _completed("status: running\n")
        start_container(_host(), 100)
        # Only the status query, no pct start.
        self.assertEqual(mock_run.call_count, 1)
        self.assertIn("pct status", mock_run.call_args_list[0][0][3])

    @patch("lib.proxmox_manage._ssh_run")
    def test_start_runs_pct_start_when_stopped(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = [
            _completed("status: stopped\n"),
            _completed(""),
        ]
        start_container(_host(), 100)
        self.assertEqual(mock_run.call_count, 2)
        self.assertIn("pct start 100", mock_run.call_args_list[1][0][3])

    @patch("lib.proxmox_manage._ssh_run")
    def test_start_raises_on_failure(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = [
            _completed("status: stopped\n"),
            _completed(stderr="boom", returncode=1),
        ]
        with self.assertRaises(ProxmoxManageError):
            start_container(_host(), 100)

    @patch("lib.proxmox_manage._ssh_run")
    def test_start_runs_qm_start_for_vm(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = [
            _completed(stderr="CT 100 does not exist", returncode=2),
            _completed("status: stopped\n"),
            _completed(""),
        ]
        start_container(_host(), 100)
        self.assertIn("qm start 100", mock_run.call_args_list[-1][0][3])

    @patch("lib.proxmox_manage._ssh_run")
    def test_stop_uses_shutdown_by_default(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = [
            _completed("status: running\n"),
            _completed(""),
        ]
        stop_container(_host(), 100)
        self.assertIn("pct shutdown 100", mock_run.call_args_list[1][0][3])

    @patch("lib.proxmox_manage._ssh_run")
    def test_stop_force_uses_pct_stop(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = [
            _completed("status: running\n"),
            _completed(""),
        ]
        stop_container(_host(), 100, force=True)
        self.assertIn("pct stop 100", mock_run.call_args_list[1][0][3])

    @patch("lib.proxmox_manage._ssh_run")
    def test_stop_skips_when_already_stopped(self, mock_run: MagicMock) -> None:
        mock_run.return_value = _completed("status: stopped\n")
        stop_container(_host(), 100)
        self.assertEqual(mock_run.call_count, 1)

    @patch("lib.proxmox_manage._ssh_run")
    def test_stop_force_uses_qm_stop_for_vm(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = [
            _completed(stderr="CT 100 does not exist", returncode=2),
            _completed("status: running\n"),
            _completed(""),
        ]
        stop_container(_host(), 100, force=True)
        self.assertIn("qm stop 100", mock_run.call_args_list[-1][0][3])

    @patch("lib.proxmox_manage._ssh_run")
    def test_shutdown_passes_timeout(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = [
            _completed("status: running\n"),
            _completed(""),
        ]
        stop_container(_host(), 100, timeout=45)
        self.assertEqual(
            mock_run.call_args_list[-1].args[3],
            "pct shutdown 100 --timeout 45",
        )

    def test_shutdown_rejects_negative_timeout(self) -> None:
        with self.assertRaisesRegex(ValueError, "timeout must be >= 0"):
            stop_container(_host(), 100, timeout=-1)


class TestReboot(unittest.TestCase):
    @patch("lib.proxmox_manage._ssh_run")
    def test_reboots_running_container(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = [
            _completed("status: running\n"),
            _completed(""),
        ]

        reboot_guest(_host(), 100)

        self.assertEqual(mock_run.call_args_list[-1].args[3], "pct reboot 100")

    @patch("lib.proxmox_manage._ssh_run")
    def test_reboots_running_vm_with_timeout(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = [
            _completed(stderr="CT 100 does not exist", returncode=2),
            _completed("status: running\n"),
            _completed(""),
        ]

        reboot_guest(_host(), 100, timeout=30)

        self.assertEqual(
            mock_run.call_args_list[-1].args[3],
            "qm reboot 100 --timeout 30",
        )

    @patch("lib.proxmox_manage._ssh_run")
    def test_refuses_to_reboot_stopped_guest(self, mock_run: MagicMock) -> None:
        mock_run.return_value = _completed("status: stopped\n")

        with self.assertRaisesRegex(ProxmoxManageError, "use the start command"):
            reboot_guest(_host(), 100)

        self.assertEqual(mock_run.call_count, 1)

    @patch("lib.proxmox_manage._ssh_run")
    def test_reboot_raises_on_command_failure(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = [
            _completed("status: running\n"),
            _completed(stderr="locked", returncode=1),
        ]

        with self.assertRaisesRegex(ProxmoxManageError, "pct reboot 100 failed"):
            reboot_guest(_host(), 100)

    def test_reboot_rejects_negative_timeout(self) -> None:
        with self.assertRaisesRegex(ValueError, "timeout must be >= 0"):
            reboot_guest(_host(), 100, timeout=-1)


class TestDestroy(unittest.TestCase):
    @patch("lib.proxmox_manage._ssh_run")
    def test_destroy_stops_running_container_first(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = [
            _completed("status: running\n"),  # initial status
            _completed("status: running\n"),  # status before stop
            _completed(""),                    # shutdown
            _completed(""),                    # destroy
        ]
        destroy_container(_host(), 100)
        executed = [call.args[3] for call in mock_run.call_args_list]
        self.assertIn("pct shutdown 100", executed)
        self.assertEqual(executed[-1], "pct destroy 100 --purge")

    @patch("lib.proxmox_manage._ssh_run")
    def test_destroy_no_purge_omits_flag(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = [
            _completed("status: stopped\n"),
            _completed(""),
        ]
        destroy_container(_host(), 100, purge=False)
        self.assertEqual(mock_run.call_args_list[-1].args[3], "pct destroy 100")

    @patch("lib.proxmox_manage._ssh_run")
    def test_destroy_force_adds_force_flag(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = [
            _completed("status: stopped\n"),
            _completed(""),
        ]
        destroy_container(_host(), 100, force=True)
        last = mock_run.call_args_list[-1].args[3]
        self.assertIn("--force", last)
        self.assertIn("--purge", last)

    @patch("lib.proxmox_manage._ssh_run")
    def test_destroy_raises_on_pct_failure(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = [
            _completed("status: stopped\n"),
            _completed(stderr="locked", returncode=1),
        ]
        with self.assertRaises(ProxmoxManageError):
            destroy_container(_host(), 100)

    @patch("lib.proxmox_manage._ssh_run")
    def test_destroy_vm_uses_qm_destroy(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = [
            _completed(stderr="CT 100 does not exist", returncode=2),
            _completed("status: stopped\n"),
            _completed(""),
        ]
        destroy_container(_host(), 100)
        self.assertIn("qm destroy 100", mock_run.call_args_list[-1].args[3])
        self.assertIn("--purge 1", mock_run.call_args_list[-1].args[3])

    @patch("lib.proxmox_manage._ssh_run")
    def test_destroy_fails_closed_when_status_cannot_be_read(
        self, mock_run: MagicMock
    ) -> None:
        mock_run.return_value = _completed(
            stderr="ssh: connect to host 10.0.0.10 port 22: Network is unreachable",
            returncode=255,
        )

        with self.assertRaisesRegex(ProxmoxManageError, "pct status 100 failed"):
            destroy_container(_host(), 100)

        mock_run.assert_called_once()


class TestWebhookNotifications(unittest.TestCase):
    def test_builds_native_pvesh_endpoint_and_matcher_commands(self) -> None:
        commands = _build_webhook_notification_commands(
            ProxmoxWebhookNotificationConfig(
                endpoint_name="basaltwater-webhook",
                matcher_name="basaltwater-system",
                url="https://notify.example/hook",
                severities=["warning", "error"],
            )
        )
        self.assertEqual(len(commands), 2)
        self.assertIn("/cluster/notifications/endpoints/webhook", commands[0])
        self.assertIn("--url https://notify.example/hook", commands[0])
        self.assertIn("--method post", commands[0])
        self.assertIn("name=Content-Type,value=", commands[0])
        encoded_body = commands[0].split("--body ", 1)[1].split(" ", 1)[0]
        body = base64.b64decode(encoded_body).decode("utf-8")
        self.assertIn('"job": "proxmox"', body)
        self.assertIn("/cluster/notifications/matchers", commands[1])
        self.assertIn("--target basaltwater-webhook", commands[1])
        self.assertIn("--match-severity warning", commands[1])
        self.assertIn("--match-severity error", commands[1])

    @patch("lib.proxmox_manage._ssh_run")
    def test_install_webhook_notifications_runs_endpoint_and_matcher(self, mock_run: MagicMock) -> None:
        mock_run.return_value = _completed("")
        config = install_webhook_notifications(
            _host(),
            "https://notify.example/hook?token=secret",
            endpoint_name="it-webhook",
            matcher_name="it-system",
            severities=["error"],
        )
        self.assertEqual(config.endpoint_name, "it-webhook")
        self.assertEqual(config.matcher_name, "it-system")
        self.assertEqual(mock_run.call_count, 2)
        endpoint_call = mock_run.call_args_list[0]
        self.assertIn("https://notify.example/hook?token=secret", endpoint_call.args[3])
        self.assertNotIn("token=secret", endpoint_call.kwargs["log_cmd"])
        self.assertIn("<redacted-query>", endpoint_call.kwargs["log_cmd"])
        matcher_call = mock_run.call_args_list[1]
        self.assertIn("--match-severity error", matcher_call.args[3])

    @patch("lib.proxmox_manage._ssh_run")
    def test_install_webhook_notifications_can_send_test(self, mock_run: MagicMock) -> None:
        mock_run.return_value = _completed("")
        install_webhook_notifications(
            _host(),
            "https://notify.example/hook",
            send_test=True,
        )
        self.assertEqual(mock_run.call_count, 3)
        self.assertIn(
            f"/cluster/notifications/targets/{DEFAULT_NOTIFICATION_ENDPOINT}/test",
            mock_run.call_args_list[-1].args[3],
        )

    @patch("lib.proxmox_manage._ssh_run")
    def test_send_webhook_test_notification(self, mock_run: MagicMock) -> None:
        mock_run.return_value = _completed("")
        send_webhook_test_notification(_host(), "it-webhook")
        self.assertIn(
            "/cluster/notifications/targets/it-webhook/test",
            mock_run.call_args.args[3],
        )

    @patch("lib.proxmox_manage._ssh_run")
    def test_install_webhook_notifications_raises_on_pvesh_failure(self, mock_run: MagicMock) -> None:
        mock_run.return_value = _completed(stderr="bad endpoint", returncode=1)
        with self.assertRaises(ProxmoxManageError):
            install_webhook_notifications(_host(), "https://notify.example/hook")

    def test_install_webhook_notifications_validates_url_and_names(self) -> None:
        with self.assertRaisesRegex(ValueError, "Invalid webhook URL"):
            install_webhook_notifications(_host(), "not-a-url", dry_run=True)
        with self.assertRaisesRegex(ValueError, "endpoint name"):
            install_webhook_notifications(
                _host(), "https://notify.example/hook", endpoint_name="1bad", dry_run=True
            )
        with self.assertRaisesRegex(ValueError, "severities"):
            install_webhook_notifications(
                _host(), "https://notify.example/hook", severities=["critical"], dry_run=True
            )


class TestHealthCheck(unittest.TestCase):
    @patch("lib.proxmox_manage._ssh_run")
    def test_running_container_with_passing_probes_is_healthy(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = [
            _completed("status: running\n"),
            _completed("net0: name=eth0,bridge=vmbr0,ip=10.0.0.50/24,gw=10.0.0.1\n"),
            _completed("OK\n"),
            _completed("OK\n"),
        ]
        report = health_check(_host(), 100)
        self.assertEqual(report.status, "running")
        self.assertEqual(report.ip, "10.0.0.50")
        self.assertTrue(report.pingable)
        self.assertTrue(report.ssh_open)
        self.assertTrue(report.healthy)

    @patch("lib.proxmox_manage._ssh_run")
    def test_failed_ping_is_unhealthy(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = [
            _completed("status: running\n"),
            _completed("net0: name=eth0,bridge=vmbr0,ip=10.0.0.50/24\n"),
            _completed("FAIL\n"),
            _completed("OK\n"),
        ]
        report = health_check(_host(), 100)
        self.assertFalse(report.pingable)
        self.assertFalse(report.healthy)

    @patch("lib.proxmox_manage._ssh_run")
    def test_stopped_container_is_unhealthy(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = [
            _completed("status: stopped\n"),
            _completed("net0: name=eth0,bridge=vmbr0,ip=10.0.0.50/24\n"),
        ]
        report = health_check(_host(), 100)
        self.assertEqual(report.status, "stopped")
        self.assertFalse(report.healthy)
        self.assertTrue(any("not running" in n for n in report.notes))

    @patch("lib.proxmox_manage._ssh_run")
    def test_no_ip_short_circuits(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = [
            _completed("status: running\n"),
            _completed(""),  # no net0
        ]
        report = health_check(_host(), 100)
        self.assertIsNone(report.ip)
        self.assertFalse(report.healthy)
        self.assertTrue(any("No IPv4" in n for n in report.notes))

    @patch("lib.proxmox_manage._ssh_run")
    def test_status_failure_short_circuits(self, mock_run: MagicMock) -> None:
        mock_run.return_value = _completed(stderr="bad vmid", returncode=2)
        report = health_check(_host(), 100)
        self.assertEqual(report.status, "unknown")
        self.assertFalse(report.healthy)
        self.assertTrue(report.notes)

    @patch("lib.proxmox_manage._ssh_run")
    def test_skip_ssh_probe(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = [
            _completed("status: running\n"),
            _completed("net0: name=eth0,bridge=vmbr0,ip=10.0.0.50/24\n"),
            _completed("OK\n"),
        ]
        report = health_check(_host(), 100, probe_ssh=False)
        self.assertIsNone(report.ssh_open)
        self.assertTrue(report.healthy)

    @patch("lib.proxmox_manage._ssh_run")
    def test_vm_health_sets_guest_type(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = [
            _completed(stderr="CT 100 does not exist", returncode=2),
            _completed("status: running\n"),
            _completed(stderr="CT 100 does not exist", returncode=2),
            _completed("ipconfig0: ip=10.0.0.60/24,gw=10.0.0.1\n"),
            _completed("OK\n"),
            _completed("OK\n"),
        ]
        report = health_check(_host(), 100)
        self.assertEqual(report.guest_type, "vm")
        self.assertTrue(report.healthy)


class TestGetContainerConfig(unittest.TestCase):
    @patch("lib.proxmox_manage._ssh_run")
    def test_parses_key_value_output(self, mock_run: MagicMock) -> None:
        mock_run.return_value = _completed(
            "arch: amd64\ncores: 2\nmemory: 2048\nhostname: mybox\n"
        )
        config = get_container_config(_host(), 100)
        self.assertEqual(config["arch"], "amd64")
        self.assertEqual(config["cores"], "2")
        self.assertEqual(config["memory"], "2048")
        self.assertEqual(config["hostname"], "mybox")

    @patch("lib.proxmox_manage._ssh_run")
    def test_skips_comment_lines(self, mock_run: MagicMock) -> None:
        mock_run.return_value = _completed(
            "# generated by pct\ncores: 4\n"
        )
        config = get_container_config(_host(), 100)
        self.assertNotIn("# generated by pct", config)
        self.assertEqual(config["cores"], "4")

    @patch("lib.proxmox_manage._ssh_run")
    def test_raises_on_failure(self, mock_run: MagicMock) -> None:
        mock_run.return_value = _completed(stderr="no such container", returncode=1)
        with self.assertRaises(ProxmoxManageError):
            get_container_config(_host(), 999)

    @patch("lib.proxmox_manage._ssh_run")
    def test_dry_run_returns_empty(self, mock_run: MagicMock) -> None:
        result = get_container_config(_host(), 100, dry_run=True)
        self.assertEqual(result, {})
        mock_run.assert_not_called()

    @patch("lib.proxmox_manage._ssh_run")
    def test_falls_back_to_qm_config(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = [
            _completed(stderr="CT 999 does not exist", returncode=2),
            _completed("cores: 4\nmemory: 4096\n"),
        ]
        config = get_container_config(_host(), 999)
        self.assertEqual(config["cores"], "4")


class TestGetContainerPending(unittest.TestCase):
    @patch("lib.proxmox_manage._ssh_run")
    def test_parses_pending_output(self, mock_run: MagicMock) -> None:
        mock_run.return_value = _completed("cores: 4\nmemory: 8192\n")
        pending = get_container_pending(_host(), 100)
        self.assertEqual(pending["cores"], "4")
        self.assertEqual(pending["memory"], "8192")

    @patch("lib.proxmox_manage._ssh_run")
    def test_raises_on_failure(self, mock_run: MagicMock) -> None:
        mock_run.return_value = _completed(returncode=1)
        with self.assertRaises(ProxmoxManageError):
            get_container_pending(_host(), 100)

    @patch("lib.proxmox_manage._ssh_run")
    def test_dry_run_returns_empty(self, mock_run: MagicMock) -> None:
        result = get_container_pending(_host(), 100, dry_run=True)
        self.assertEqual(result, {})
        mock_run.assert_not_called()

    @patch("lib.proxmox_manage._ssh_run")
    def test_falls_back_to_qm_pending(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = [
            _completed(stderr="CT 100 does not exist", returncode=2),
            _completed("cores: 8\n"),
        ]
        pending = get_container_pending(_host(), 100)
        self.assertEqual(pending["cores"], "8")


class TestReconfigureContainer(unittest.TestCase):
    @patch("lib.proxmox_manage._ssh_run")
    def test_builds_pct_set_command(self, mock_run: MagicMock) -> None:
        mock_run.return_value = _completed()
        reconfigure_container(_host(), 100, {"cores": "4", "memory": "2048"})
        self.assertTrue(mock_run.called)
        cmd = mock_run.call_args[0][3]
        self.assertIn("pct", cmd)
        self.assertIn("set", cmd)
        self.assertIn("100", cmd)
        self.assertIn("--cores", cmd)
        self.assertIn("4", cmd)

    @patch("lib.proxmox_manage._ssh_run")
    def test_no_options_is_noop(self, mock_run: MagicMock) -> None:
        reconfigure_container(_host(), 100, {})
        mock_run.assert_not_called()

    def test_invalid_option_name_raises(self) -> None:
        with self.assertRaises(ValueError):
            reconfigure_container(_host(), 100, {"INVALID!": "val"})

    @patch("lib.proxmox_manage._ssh_run")
    def test_raises_on_remote_failure(self, mock_run: MagicMock) -> None:
        mock_run.return_value = _completed(returncode=1, stderr="error")
        with self.assertRaises(ProxmoxManageError):
            reconfigure_container(_host(), 100, {"cores": "4"})

    @patch("lib.proxmox_manage._ssh_run")
    def test_dry_run_passes_through(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = [
            _completed("status: running\n"),
            _completed(),
        ]
        reconfigure_container(_host(), 100, {"cores": "2"}, dry_run=True)
        _, kwargs = mock_run.call_args
        self.assertTrue(kwargs.get("dry_run"))

    @patch("lib.proxmox_manage._ssh_run")
    def test_vm_uses_qm_set(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = [
            _completed(stderr="CT 100 does not exist", returncode=2),
            _completed("status: running\n"),
            _completed(),
        ]
        reconfigure_container(_host(), 100, {"cores": "2"})
        self.assertIn("qm", mock_run.call_args.args[3])
        self.assertIn("set", mock_run.call_args.args[3])


class TestModifyContainer(unittest.TestCase):
    @patch("lib.proxmox_manage._ssh_run")
    def test_set_cores(self, mock_run: MagicMock) -> None:
        mock_run.return_value = _completed()
        modify_container(_host(), 100, cores=4)
        cmd = mock_run.call_args[0][3]
        self.assertIn("--cores", cmd)
        self.assertIn("4", cmd)
        self.assertNotIn("--memory", cmd)

    @patch("lib.proxmox_manage._ssh_run")
    def test_set_memory(self, mock_run: MagicMock) -> None:
        mock_run.return_value = _completed()
        modify_container(_host(), 100, memory_mb=4096)
        cmd = mock_run.call_args[0][3]
        self.assertIn("--memory", cmd)
        self.assertIn("4096", cmd)

    @patch("lib.proxmox_manage._ssh_run")
    def test_set_both(self, mock_run: MagicMock) -> None:
        mock_run.return_value = _completed()
        modify_container(_host(), 100, cores=2, memory_mb=1024)
        cmd = mock_run.call_args[0][3]
        self.assertIn("--cores", cmd)
        self.assertIn("--memory", cmd)

    def test_neither_raises(self) -> None:
        with self.assertRaises(ValueError):
            modify_container(_host(), 100)

    def test_invalid_cores_raises(self) -> None:
        with self.assertRaises(ValueError):
            modify_container(_host(), 100, cores=0)

    def test_invalid_memory_raises(self) -> None:
        with self.assertRaises(ValueError):
            modify_container(_host(), 100, memory_mb=8)


class TestResizeContainerDisk(unittest.TestCase):
    @patch("lib.proxmox_manage._ssh_run")
    def test_builds_pct_resize_command(self, mock_run: MagicMock) -> None:
        mock_run.return_value = _completed()
        resize_container_disk(_host(), 100, "rootfs", "20G")
        cmd = mock_run.call_args[0][3]
        self.assertIn("pct", cmd)
        self.assertIn("resize", cmd)
        self.assertIn("100", cmd)
        self.assertIn("rootfs", cmd)
        self.assertIn("20G", cmd)

    def test_invalid_size_format_raises(self) -> None:
        with self.assertRaises(ValueError):
            resize_container_disk(_host(), 100, "rootfs", "20GB")

    def test_invalid_volume_name_raises(self) -> None:
        with self.assertRaises(ValueError):
            resize_container_disk(_host(), 100, "ROOT FS!", "20G")

    @patch("lib.proxmox_manage._ssh_run")
    def test_raises_on_remote_failure(self, mock_run: MagicMock) -> None:
        mock_run.return_value = _completed(returncode=1, stderr="can't shrink")
        with self.assertRaises(ProxmoxManageError):
            resize_container_disk(_host(), 100, "rootfs", "20G")

    @patch("lib.proxmox_manage._ssh_run")
    def test_dry_run_passes_through(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = [
            _completed("status: running\n"),
            _completed(),
        ]
        resize_container_disk(_host(), 100, "rootfs", "20G", dry_run=True)
        _, kwargs = mock_run.call_args
        self.assertTrue(kwargs.get("dry_run"))

    @patch("lib.proxmox_manage._ssh_run")
    def test_vm_uses_qm_resize(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = [
            _completed(stderr="CT 100 does not exist", returncode=2),
            _completed("status: running\n"),
            _completed(),
        ]
        resize_container_disk(_host(), 100, "scsi0", "20G")
        self.assertIn("qm", mock_run.call_args.args[3])
        self.assertIn("resize", mock_run.call_args.args[3])


class TestParseListsnapshot(unittest.TestCase):
    def test_parses_pct_format(self) -> None:
        out = (
            "-> current (no snapshot)\n"
            "   snap1 (2024-01-01 12:00:00)\n"
            "   snap2 before-upgrade\n"
        )
        snaps = _parse_listsnapshot(out)
        names = [s.name for s in snaps]
        self.assertIn("snap1", names)
        self.assertIn("snap2", names)

    def test_marks_current_entry(self) -> None:
        out = "-> current\n   snap1\n"
        snaps = _parse_listsnapshot(out)
        current = [s for s in snaps if s.is_current]
        self.assertEqual(len(current), 1)
        self.assertEqual(current[0].name, "current")

    def test_empty_output(self) -> None:
        self.assertEqual(_parse_listsnapshot(""), [])

    def test_skips_header_line(self) -> None:
        out = "snapname parent state description\nsnap1\n"
        snaps = _parse_listsnapshot(out)
        names = [s.name for s in snaps]
        self.assertNotIn("snapname", names)
        self.assertIn("snap1", names)


class TestSnapshotGuest(unittest.TestCase):
    @patch("lib.proxmox_manage._ssh_run")
    def test_creates_pct_snapshot(self, mock_run: MagicMock) -> None:
        mock_run.return_value = _completed()
        snapshot_guest(_host(), 100, "snap1")
        cmd = mock_run.call_args[0][3]
        self.assertIn("pct", cmd)
        self.assertIn("snapshot", cmd)
        self.assertIn("100", cmd)
        self.assertIn("snap1", cmd)

    @patch("lib.proxmox_manage._ssh_run")
    def test_includes_description(self, mock_run: MagicMock) -> None:
        mock_run.return_value = _completed()
        snapshot_guest(_host(), 100, "snap1", description="before upgrade")
        cmd = mock_run.call_args[0][3]
        self.assertIn("--description", cmd)
        self.assertIn("before upgrade", cmd)

    @patch("lib.proxmox_manage._ssh_run")
    def test_vm_uses_qm_snapshot(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = [
            _completed(stderr="CT 100 does not exist", returncode=2),
            _completed(),
        ]
        snapshot_guest(_host(), 100, "snap1")
        self.assertIn("qm", mock_run.call_args[0][3])

    def test_invalid_name_raises(self) -> None:
        with self.assertRaises(ValueError):
            snapshot_guest(_host(), 100, "bad name!")

    @patch("lib.proxmox_manage._ssh_run")
    def test_raises_on_remote_failure(self, mock_run: MagicMock) -> None:
        mock_run.return_value = _completed(returncode=1, stderr="error")
        with self.assertRaises(ProxmoxManageError):
            snapshot_guest(_host(), 100, "snap1")


class TestRollbackGuest(unittest.TestCase):
    @patch("lib.proxmox_manage._ssh_run")
    def test_builds_pct_rollback_command(self, mock_run: MagicMock) -> None:
        mock_run.return_value = _completed()
        rollback_guest(_host(), 100, "snap1")
        cmd = mock_run.call_args[0][3]
        self.assertIn("pct", cmd)
        self.assertIn("rollback", cmd)
        self.assertIn("snap1", cmd)

    def test_invalid_name_raises(self) -> None:
        with self.assertRaises(ValueError):
            rollback_guest(_host(), 100, "bad-name!")

    @patch("lib.proxmox_manage._ssh_run")
    def test_raises_on_remote_failure(self, mock_run: MagicMock) -> None:
        mock_run.return_value = _completed(returncode=1, stderr="snap not found")
        with self.assertRaises(ProxmoxManageError):
            rollback_guest(_host(), 100, "snap1")


class TestDeleteSnapshot(unittest.TestCase):
    @patch("lib.proxmox_manage._ssh_run")
    def test_builds_pct_delsnapshot_command(self, mock_run: MagicMock) -> None:
        mock_run.return_value = _completed()
        delete_snapshot(_host(), 100, "snap1")
        cmd = mock_run.call_args[0][3]
        self.assertIn("pct", cmd)
        self.assertIn("delsnapshot", cmd)
        self.assertIn("snap1", cmd)

    def test_invalid_name_raises(self) -> None:
        with self.assertRaises(ValueError):
            delete_snapshot(_host(), 100, "bad name!")


class TestListSnapshots(unittest.TestCase):
    @patch("lib.proxmox_manage._ssh_run")
    def test_returns_parsed_list(self, mock_run: MagicMock) -> None:
        mock_run.return_value = _completed(stdout="snap1\nsnap2\n")
        snaps = list_snapshots(_host(), 100)
        self.assertEqual([s.name for s in snaps], ["snap1", "snap2"])

    def test_dry_run_returns_empty(self) -> None:
        snaps = list_snapshots(_host(), 100, dry_run=True)
        self.assertEqual(snaps, [])

    @patch("lib.proxmox_manage._ssh_run")
    def test_raises_on_failure(self, mock_run: MagicMock) -> None:
        mock_run.return_value = _completed(returncode=1, stderr="error")
        with self.assertRaises(ProxmoxManageError):
            list_snapshots(_host(), 100)


class TestUnlockGuest(unittest.TestCase):
    @patch("lib.proxmox_manage._ssh_run")
    def test_builds_pct_unlock_command(self, mock_run: MagicMock) -> None:
        mock_run.return_value = _completed()
        unlock_guest(_host(), 100)
        cmd = mock_run.call_args[0][3]
        self.assertIn("pct", cmd)
        self.assertIn("unlock", cmd)
        self.assertIn("100", cmd)

    @patch("lib.proxmox_manage._ssh_run")
    def test_vm_uses_qm_unlock(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = [
            _completed(stderr="CT 100 does not exist", returncode=2),
            _completed(),
        ]
        unlock_guest(_host(), 100)
        self.assertIn("qm", mock_run.call_args[0][3])
        self.assertIn("unlock", mock_run.call_args[0][3])

    @patch("lib.proxmox_manage._ssh_run")
    def test_raises_on_failure(self, mock_run: MagicMock) -> None:
        mock_run.return_value = _completed(returncode=1, stderr="no lock")
        with self.assertRaises(ProxmoxManageError):
            unlock_guest(_host(), 100)


class TestDryRunGuestRouting(unittest.TestCase):
    @patch("lib.proxmox_manage._ssh_run")
    def test_reconfigure_dry_run_does_not_probe_guest_status(self, mock_run: MagicMock) -> None:
        mock_run.return_value = _completed()

        reconfigure_container(_host(), 100, {"cores": "2"}, dry_run=True)

        self.assertEqual(mock_run.call_count, 1)
        self.assertTrue(mock_run.call_args.kwargs.get("dry_run"))
        self.assertIn("pct set 100 --cores 2", mock_run.call_args.args[3])
        self.assertIn("qm set 100 --cores 2", mock_run.call_args.kwargs.get("log_cmd", ""))


if __name__ == "__main__":
    unittest.main()
