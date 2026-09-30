"""Safety and recovery tests for the optional post-setup restart workflow."""

from __future__ import annotations

from contextlib import redirect_stdout
import io
import shlex
import subprocess
import unittest
from unittest.mock import MagicMock, patch

import basaltwater
from lib.config import SetupConfig
from lib.proxmox_maintenance import ProxmoxMaintenanceReport
from lib.state_read import StateReadError
from lib import setup_reboot


OLD_BOOT_ID = "12345678-1234-4234-8234-123456789abc"
NEW_BOOT_ID = "87654321-4321-4321-8321-cba987654321"


def _config(**options: object) -> SetupConfig:
    return SetupConfig(**{
        "host": "example.test", "username": "admin", "system_type": "server_web",
        **options,
    })


def _result(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["ssh"], returncode, stdout, "")


class RestartStatusTests(unittest.TestCase):
    def test_clear_and_unsupported_targets_do_not_inspect_proxmox(self) -> None:
        with (
            patch.object(setup_reboot.os.path, "isfile", return_value=False),
            patch.object(setup_reboot, "can_restart_system") as capability,
            patch.object(setup_reboot.shutil, "which") as which,
        ):
            self.assertEqual(setup_reboot._local_restart_status(), "clear")
            capability.assert_not_called()
            which.assert_not_called()

        with (
            patch.object(setup_reboot.os.path, "isfile", return_value=True),
            patch.object(setup_reboot, "can_restart_system", return_value=False),
            patch.object(setup_reboot.shutil, "which") as which,
        ):
            self.assertEqual(setup_reboot._local_restart_status(), "unsupported")
            which.assert_not_called()

    def test_proxmox_is_detected_from_target_files_or_tools(self) -> None:
        for directory, binary, expected in (
            (False, None, "needed"),
            (True, None, "needed-proxmox"),
            (False, "/usr/bin/pveversion", "needed-proxmox"),
        ):
            with (
                self.subTest(directory=directory, binary=binary),
                patch.object(setup_reboot.os.path, "isfile", return_value=True),
                patch.object(setup_reboot.os.path, "isdir", return_value=directory),
                patch.object(setup_reboot, "can_restart_system", return_value=True),
                patch.object(setup_reboot.shutil, "which", return_value=binary),
            ):
                self.assertEqual(setup_reboot._local_restart_status(), expected)

    def test_remote_probe_uses_shared_target_checks(self) -> None:
        with patch.object(setup_reboot, "_ssh_result", return_value=_result("needed-proxmox\n")) as ssh:
            self.assertEqual(setup_reboot._restart_status(_config()), "needed-proxmox")
        command = shlex.split(ssh.call_args.args[1])
        self.assertEqual(command[:2], ["python3", "-c"])
        self.assertIn("from lib.setup_reboot import _local_restart_status", command[2])

    def test_unknown_failed_and_timed_out_probes_fail_closed(self) -> None:
        for result in (_result("garbage"), _result("needed", returncode=255)):
            with self.subTest(result=result), patch.object(setup_reboot, "_ssh_result", return_value=result):
                self.assertIsNone(setup_reboot._restart_status(_config()))
        with patch.object(setup_reboot, "_ssh_result", side_effect=subprocess.TimeoutExpired("ssh", 30)):
            self.assertIsNone(setup_reboot._restart_status(_config()))

    def test_invalid_local_state_stops_restart_with_an_error(self) -> None:
        output = io.StringIO()
        with (
            patch.object(setup_reboot.os.path, "isfile", return_value=True),
            patch.object(setup_reboot, "can_restart_system", side_effect=StateReadError("/state/machine.json", "invalid")),
            patch.object(setup_reboot, "_request_restart") as request,
            redirect_stdout(output),
        ):
            self.assertEqual(setup_reboot.restart_after_setup(_config(host="localhost")), 1)
        request.assert_not_called()
        self.assertIn("Error checking restart status", output.getvalue())

    def test_ssh_probe_preserves_strict_trust_and_root_control_connection(self) -> None:
        with (
            patch.object(setup_reboot, "get_ssh_control_path", return_value="/private/control.sock") as control,
            patch.object(setup_reboot, "ssh_batch_mode", return_value=True),
            patch("lib.ssh_utils.get_workspace_known_hosts_path", return_value="/private/known_hosts"),
            patch.object(setup_reboot, "run", return_value=_result()) as run,
        ):
            config = _config(ssh_key="/keys/private")
            setup_reboot._ssh_result(config, "read-only-command", timeout=12)
        control.assert_called_once_with(config.host, "root", config.ssh_key)
        command = run.call_args.args[0]
        self.assertIn("StrictHostKeyChecking=yes", command)
        self.assertIn("UserKnownHostsFile=/private/known_hosts", command)
        self.assertIn("BatchMode=yes", command)
        self.assertIn("ControlPath=/private/control.sock", command)
        self.assertEqual(command[-2:], ["root@example.test", "read-only-command"])
        self.assertEqual(run.call_args.kwargs["timeout"], 12)


class RestartWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.status = self.enterContext(patch.object(setup_reboot, "_restart_status", return_value="needed"))
        self.request = self.enterContext(patch.object(setup_reboot, "_request_restart", return_value=True))
        self.boot = self.enterContext(patch.object(setup_reboot, "_boot_id", return_value=OLD_BOOT_ID))
        self.wait = self.enterContext(patch.object(setup_reboot, "_wait_for_remote_restart", return_value=True))
        self.health = self.enterContext(patch("lib.sysadmin_health.run_health", return_value=0))

    def test_dry_run_and_invalid_wait_do_not_probe_or_restart(self) -> None:
        self.assertEqual(setup_reboot.restart_after_setup(_config(dry_run=True), wait_for_restart=True), 0)
        for host in ("localhost", "127.0.0.1", "::1"):
            self.assertEqual(setup_reboot.restart_after_setup(_config(host=host), wait_for_restart=True), 1)
        self.status.assert_not_called()
        self.request.assert_not_called()

    def test_invalid_identity_does_not_probe(self) -> None:
        for options in ({"host": "-unsafe"}, {"username": "unsafe;user"}):
            with self.subTest(options=options):
                self.assertEqual(setup_reboot.restart_after_setup(_config(**options)), 1)
        self.status.assert_not_called()
        self.request.assert_not_called()

    def test_clear_unsupported_and_unknown_status_do_not_restart(self) -> None:
        for status, expected in (("clear", 0), ("unsupported", 0), (None, 1)):
            with self.subTest(status=status):
                self.status.return_value = status
                self.assertEqual(setup_reboot.restart_after_setup(_config(), wait_for_restart=True), expected)
        self.request.assert_not_called()
        self.boot.assert_not_called()
        self.wait.assert_not_called()
        self.health.assert_not_called()

    def test_request_without_wait_skips_boot_and_health_probes(self) -> None:
        config = _config()
        self.assertEqual(setup_reboot.restart_after_setup(config), 0)
        self.request.assert_called_once_with(config)
        self.boot.assert_not_called()
        self.wait.assert_not_called()
        self.health.assert_not_called()

    def test_wait_checks_new_boot_before_health_and_propagates_health_failure(self) -> None:
        order = MagicMock()
        for name, mock in (("boot", self.boot), ("request", self.request), ("wait", self.wait), ("health", self.health)):
            order.attach_mock(mock, name)
        self.health.return_value = 7
        config = _config(ssh_key="/keys/private")
        self.assertEqual(setup_reboot.restart_after_setup(config, wait_for_restart=True), 7)
        self.assertEqual([call[0] for call in order.mock_calls], ["boot", "request", "wait", "health"])
        self.wait.assert_called_once_with(config, OLD_BOOT_ID)
        self.health.assert_called_once_with(config.host, config.username, config.ssh_key)

    def test_boot_probe_failure_prevents_restart(self) -> None:
        self.boot.return_value = None
        self.assertEqual(setup_reboot.restart_after_setup(_config(), wait_for_restart=True), 1)
        self.request.assert_not_called()
        self.wait.assert_not_called()
        self.health.assert_not_called()

    def test_failed_request_or_wait_does_not_run_health(self) -> None:
        self.request.return_value = False
        self.assertEqual(setup_reboot.restart_after_setup(_config(), wait_for_restart=True), 1)
        self.wait.assert_not_called()
        self.request.return_value = True
        self.wait.return_value = False
        self.assertEqual(setup_reboot.restart_after_setup(_config(), wait_for_restart=True), 1)
        self.health.assert_not_called()

    def test_proxmox_safety_cannot_be_bypassed_with_another_profile(self) -> None:
        for status, profile in (("needed", "server_proxmox"), ("needed-proxmox", "custom_steps")):
            with self.subTest(status=status, profile=profile):
                self.status.return_value = status
                config = _config(system_type=profile)
                with patch.object(setup_reboot, "_check_proxmox_reboot_safety", return_value=False) as safety:
                    self.assertEqual(setup_reboot.restart_after_setup(config), 1)
                safety.assert_called_once_with(config)
        self.request.assert_not_called()

    def test_safe_proxmox_target_can_restart(self) -> None:
        self.status.return_value = "needed-proxmox"
        with patch.object(setup_reboot, "_check_proxmox_reboot_safety", return_value=True):
            self.assertEqual(setup_reboot.restart_after_setup(_config()), 0)
        self.request.assert_called_once()


class RestartTransportTests(unittest.TestCase):
    def test_boot_probe_rejects_banners_and_malformed_values(self) -> None:
        for output in ("", "banner", OLD_BOOT_ID.replace("-", ""), f"banner\n{OLD_BOOT_ID}"):
            with self.subTest(output=output), patch.object(setup_reboot, "_ssh_result", return_value=_result(output)):
                self.assertIsNone(setup_reboot._boot_id(_config()))
        with patch.object(setup_reboot, "_ssh_result", return_value=_result(OLD_BOOT_ID.upper() + "\n")):
            self.assertEqual(setup_reboot._boot_id(_config()), OLD_BOOT_ID)

    def test_wait_tolerates_disconnects_and_ignores_invalid_or_unchanged_boot_ids(self) -> None:
        results = (
            subprocess.TimeoutExpired("ssh", 20), _result(returncode=255),
            _result(OLD_BOOT_ID), _result("varying SSH banner"), _result(NEW_BOOT_ID),
        )
        with (
            patch.object(setup_reboot, "_ssh_result", side_effect=results) as ssh,
            patch.object(setup_reboot.time, "monotonic", return_value=0),
            patch.object(setup_reboot.time, "sleep") as sleep,
        ):
            self.assertTrue(setup_reboot._wait_for_remote_restart(_config(), OLD_BOOT_ID))
        self.assertEqual(ssh.call_count, len(results))
        self.assertEqual(sleep.call_count, len(results) - 1)

    def test_wait_deadline_bounds_probes_and_reports_failure(self) -> None:
        with (
            patch.object(setup_reboot, "_RESTART_WAIT_SECONDS", 10),
            patch.object(setup_reboot.time, "monotonic", side_effect=(0, 9, 10, 10)),
            patch.object(setup_reboot.time, "sleep"),
            patch.object(setup_reboot, "_ssh_result", return_value=_result("invalid")) as ssh,
        ):
            self.assertFalse(setup_reboot._wait_for_remote_restart(_config(), OLD_BOOT_ID))
        self.assertEqual(ssh.call_args.kwargs["timeout"], 1)

    def test_restart_uses_delayed_systemd_request_and_reports_transport_failures(self) -> None:
        for host in ("localhost", "example.test"):
            with (
                self.subTest(host=host),
                patch.object(setup_reboot, "run", return_value=_result()) as local,
                patch.object(setup_reboot, "_ssh_result", return_value=_result()) as remote,
            ):
                self.assertTrue(setup_reboot._request_restart(_config(host=host)))
                command = local.call_args.args[0] if host == "localhost" else shlex.split(remote.call_args.args[1])
                self.assertIn("--on-active=2s", command)
                self.assertEqual(command[-3:], ["/usr/bin/systemctl", "reboot", "--no-wall"])
        with patch.object(setup_reboot, "_ssh_result", return_value=_result(returncode=255)):
            self.assertFalse(setup_reboot._request_restart(_config()))
        with patch.object(setup_reboot, "_ssh_result", side_effect=OSError("SSH unavailable")):
            self.assertFalse(setup_reboot._request_restart(_config()))

    def test_proxmox_report_errors_and_running_guests_block_restart(self) -> None:
        config = _config(system_type="server_proxmox")
        report = ProxmoxMaintenanceReport(config.host, config.host)
        with patch("lib.proxmox_maintenance.collect_maintenance_report", return_value=report):
            self.assertTrue(setup_reboot._check_proxmox_reboot_safety(config))
            report.errors.append("active tasks")
            self.assertFalse(setup_reboot._check_proxmox_reboot_safety(config))
            report.errors.clear()
            report.running_guests.append(MagicMock(vmid=101))
            self.assertFalse(setup_reboot._check_proxmox_reboot_safety(config))
            report.running_guests.clear()
            report.locked_guests.append(MagicMock(vmid=102))
            self.assertFalse(setup_reboot._check_proxmox_reboot_safety(config))
        with patch("lib.proxmox_maintenance.collect_maintenance_report", side_effect=OSError("inspection failed")):
            self.assertFalse(setup_reboot._check_proxmox_reboot_safety(config))
        with patch("lib.proxmox_maintenance.collect_maintenance_report") as collect:
            self.assertFalse(setup_reboot._check_proxmox_reboot_safety(_config(host="localhost")))
            collect.assert_not_called()


class RestartCliTests(unittest.TestCase):
    def test_invalid_wait_flags_fail_before_setup_side_effects(self) -> None:
        parser, _, _ = basaltwater.create_basaltwater_parser()
        for flags in (
            ["server_web", "example.test", "--wait-for-restart"],
            ["server_web", "localhost", "--restart-if-needed", "--wait-for-restart"],
        ):
            with (
                self.subTest(flags=flags),
                patch.object(basaltwater, "run_remote_setup") as setup,
                patch.object(basaltwater, "prompt_for_missing_passwords") as passwords,
                patch.object(basaltwater, "restart_after_setup") as restart,
            ):
                self.assertEqual(basaltwater.run_setup_command(parser.parse_args(["setup", *flags])), 1)
                setup.assert_not_called()
                passwords.assert_not_called()
                restart.assert_not_called()


if __name__ == "__main__":
    unittest.main()
