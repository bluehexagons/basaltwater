"""Guest management preserves schedules and validates probe/snapshot inputs."""

from __future__ import annotations

import shlex
from contextlib import redirect_stdout
from io import StringIO
from subprocess import CompletedProcess, TimeoutExpired
import traceback
import unittest
from unittest.mock import patch

from lib.proxmox_hosts import ProxmoxHost
from lib.proxmox_manage import (
    ProxmoxManageError, configure_guest_autostart, delete_snapshot,
    health_check, rollback_guest, snapshot_guest,
    install_webhook_notifications, send_webhook_test_notification,
)


class TestManagementSafety(unittest.TestCase):
    def setUp(self) -> None:
        self.host = ProxmoxHost(name="pve1", address="192.0.2.10")

    def test_partial_schedule_update_preserves_implicit_order_and_zero(self) -> None:
        for startup in ("2,up=30,down=90", "order=2,up=30,down=90"):
            with self.subTest(startup=startup), patch(
                "lib.proxmox_manage.get_container_config",
                return_value={"onboot": "1", "startup": startup},
            ), patch("lib.proxmox_manage.reconfigure_container") as update:
                configure_guest_autostart(self.host, 100, enabled=True, start_delay=0)
                update.assert_called_once_with(
                    self.host, 100, {"onboot": "1", "startup": "order=2,up=0,down=90"},
                )

    def test_invalid_schedule_blocks_update(self) -> None:
        for startup in ("order=2,order=3", "up=bad", "order=-1", "unknown=2", "up=30,"):
            with self.subTest(startup=startup), patch(
                "lib.proxmox_manage.get_container_config", return_value={"startup": startup},
            ), patch("lib.proxmox_manage.reconfigure_container") as update:
                with self.assertRaisesRegex(ProxmoxManageError, "Invalid startup"):
                    configure_guest_autostart(self.host, 100, enabled=True, start_delay=0)
                update.assert_not_called()

    def test_dynamic_or_invalid_addresses_never_reach_network_probes(self) -> None:
        for field in ("net0", "ipconfig0"):
            for address in ("dhcp", "manual", "999.0.0.1/24", "-localhost", "'$(id)'", "::1/128"):
                with self.subTest(field=field, address=address), patch(
                    "lib.proxmox_manage._get_guest_status", return_value=("vm", "running"),
                ), patch("lib.proxmox_manage._run_on_host", return_value=CompletedProcess(
                    [], 0, f"{field}: ip={address}\n", "",
                )) as run:
                    report = health_check(self.host, 100)
                    self.assertIsNone(report.ip)
                    self.assertIsNone(report.pingable)
                    self.assertIsNone(report.ssh_open)
                    self.assertFalse(report.healthy)
                    self.assertEqual(run.call_count, 1)  # Configuration read only.

    def test_static_address_and_probe_command(self) -> None:
        with patch("lib.proxmox_manage._get_guest_status", return_value=("lxc", "running")), patch(
            "lib.proxmox_manage._run_on_host", side_effect=[
                CompletedProcess([], 0, "net0: ip=192.0.2.50/24\n", ""),
                CompletedProcess([], 0, "OK\n", ""),
                CompletedProcess([], 0, "OK\n", ""),
            ],
        ) as run:
            self.assertTrue(health_check(self.host, 100).healthy)
            command = shlex.split(run.call_args.args[1])
            self.assertEqual(command[:5], ["timeout", "3", "bash", "-c", "</dev/tcp/192.0.2.50/22"])

    def test_invalid_probe_timeout_fails_before_ssh(self) -> None:
        with patch("lib.proxmox_manage._run_on_host") as run:
            for timeout in (0, -1, 0.5, True, "bad"):
                with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                    health_check(self.host, 100, timeout=timeout)
            run.assert_not_called()

    def test_snapshot_names_follow_native_configuration_id_rules(self) -> None:
        for operation in (snapshot_guest, rollback_guest, delete_snapshot):
            with self.subTest(operation=operation.__name__), patch(
                "lib.proxmox_manage._run_on_host", return_value=CompletedProcess([], 0, "", ""),
            ) as run:
                for name in ("pre-modify", "Ab_01", "a" * 40):
                    operation(self.host, 100, name)
                    self.assertIn(name, shlex.split(run.call_args.args[1]))
                run.reset_mock()
                for name in ("1snapshot", "_snapshot", "a", "a" * 41, "snap\n", "bad name", "éx"):
                    with self.subTest(name=name), self.assertRaises(ValueError):
                        operation(self.host, 100, name)
                run.assert_not_called()


class TestWebhookPrivacy(unittest.TestCase):
    def setUp(self) -> None:
        self.host = ProxmoxHost(name="pve1", address="192.0.2.10")
        self.url = "https://user:fake-password@notify.example/fake-path-token?key=fake-query#fake-fragment"

    @patch("lib.proxmox_manage._ssh_run", return_value=CompletedProcess([], 0, "", ""))
    def test_actual_url_is_sent_but_all_display_components_are_redacted(self, run):
        install_webhook_notifications(self.host, self.url)
        call = run.call_args_list[0]
        self.assertIn(self.url, call.args[3])
        for secret in ("fake-password", "fake-path-token", "fake-query", "fake-fragment"):
            self.assertNotIn(secret, call.kwargs["log_cmd"])
        self.assertTrue(all(call.kwargs["quiet"] for call in run.call_args_list))

    def test_native_stderr_is_not_printed_by_ssh_helper(self) -> None:
        output = StringIO()
        with patch("lib.proxmox_manage._ssh_opts", return_value=[]), patch(
            "lib.proxmox_guest._proxmox_ssh_command", return_value=["ssh", "placeholder"],
        ), patch("lib.proxmox_guest.subprocess.run", return_value=CompletedProcess(
            [], 1, "", self.url,
        )), redirect_stdout(output):
            with self.assertRaises(ProxmoxManageError):
                install_webhook_notifications(self.host, self.url)
        self.assertNotIn(self.url, output.getvalue())

    def test_dry_run_shows_redacted_plan_without_execution(self) -> None:
        output = StringIO()
        with patch("lib.proxmox_manage._ssh_opts", return_value=[]), patch(
            "lib.proxmox_guest._proxmox_ssh_command",
        ) as command, redirect_stdout(output):
            install_webhook_notifications(self.host, self.url, dry_run=True)
        self.assertIn("--url", output.getvalue())
        self.assertIn("redacted-url", output.getvalue())
        for secret in ("fake-password", "fake-path-token", "fake-query", "fake-fragment"):
            self.assertNotIn(secret, output.getvalue())
        command.assert_not_called()

    def test_failures_do_not_echo_native_diagnostics_or_transport_commands(self) -> None:
        for operation in (
            lambda: install_webhook_notifications(self.host, self.url),
            lambda: send_webhook_test_notification(self.host),
        ):
            for outcome in (CompletedProcess([], 1, self.url, self.url),
                            TimeoutExpired(["ssh", self.url], 120, stderr=self.url),
                            OSError(self.url)):
                with self.subTest(outcome=type(outcome).__name__), patch(
                    "lib.proxmox_manage._ssh_run",
                    **({"side_effect": outcome} if isinstance(outcome, Exception) else {"return_value": outcome}),
                ) as run:
                    try:
                        operation()
                    except ProxmoxManageError:
                        formatted = traceback.format_exc()
                        self.assertNotIn(self.url, formatted)
                    else:
                        self.fail("Notification failure was not reported")
                    self.assertTrue(run.call_args.kwargs["quiet"])
                    run.assert_called_once()  # No fallback or replay after uncertainty.

    def test_invalid_urls_and_control_suffixes_fail_before_ssh(self) -> None:
        with patch("lib.proxmox_manage._ssh_run") as run:
            for url in ("https://user:fake-password@/hook", "https://notify.example:bad/fake-path-token",
                        "https://notify.example/fake-path-token\n", "https://[broken/fake-path-token",
                        "https://bad host/fake-path-token"):
                with self.subTest(url=url), self.assertRaises(ValueError) as raised:
                    install_webhook_notifications(self.host, url)
                self.assertNotIn("fake-password", str(raised.exception))
                self.assertNotIn("fake-path-token", str(raised.exception))
            for field in ("endpoint_name", "matcher_name"):
                with self.subTest(field=field), self.assertRaises(ValueError):
                    install_webhook_notifications(self.host, self.url, **{field: "valid-name\n"})
            run.assert_not_called()

    @patch("lib.proxmox_manage._ssh_run", return_value=CompletedProcess([], 0, "", ""))
    def test_ipv6_webhook_host_remains_supported(self, run):
        url = "https://[2001:db8::1]:9443/hook"
        install_webhook_notifications(self.host, url)
        self.assertIn(url, run.call_args_list[0].args[3])
