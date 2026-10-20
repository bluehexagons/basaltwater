"""Tests for shared recurring-maintenance systemd provisioning."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lib.maintenance_systemd import configure_maintenance_timer
from lib.config import SetupConfig
from common import common_steps
from security import security_steps


class TestConfigureMaintenanceTimer(unittest.TestCase):
    def test_writes_hardened_units_and_verifies_timer(self):
        with tempfile.TemporaryDirectory() as unit_dir, patch(
            "lib.maintenance_systemd.SYSTEMD_DIR", unit_dir
        ), patch("lib.maintenance_systemd.replace_units") as mock_run:

            configured = configure_maintenance_timer(
                service_name="auto-update-test",
                service_desc="Auto-update test runtime",
                timer_desc="Auto-update test runtime weekly",
                script_path="/opt/basaltwater/test/update.py",
                schedule="Sun *-*-* 03:00:00",
                check_name="Test runtime",
                user="test-user",
                environment={"UPDATE_POLICY": 'safe"value'},
            )

            self.assertTrue(configured)
            service_content = mock_run.call_args.args[0]["auto-update-test.service"]
            timer_content = mock_run.call_args.args[0]["auto-update-test.timer"]

        self.assertIn("Wants=network-online.target", service_content)
        self.assertIn("After=network-online.target", service_content)
        self.assertIn("TimeoutStartSec=4h", service_content)
        self.assertIn("User=test-user", service_content)
        self.assertIn('Environment="UPDATE_POLICY=safe\\"value"', service_content)
        self.assertIn("Persistent=true", timer_content)
        self.assertIn("AccuracySec=1min", timer_content)
        self.assertIn("RandomizedDelaySec=30min", timer_content)
        self.assertEqual(mock_run.call_args.kwargs["activate"], ("auto-update-test.timer",))

    def test_skips_when_prerequisite_is_missing(self):
        with tempfile.TemporaryDirectory() as unit_dir, patch(
            "lib.maintenance_systemd.SYSTEMD_DIR", unit_dir
        ), patch("lib.maintenance_systemd.replace_units") as mock_run:
            configured = configure_maintenance_timer(
                service_name="auto-update-test",
                service_desc="Auto-update test runtime",
                timer_desc="Auto-update test runtime weekly",
                script_path="/opt/basaltwater/test/update.py",
                schedule="weekly",
                check_name="Test runtime",
                check_path="/missing/test-runtime",
            )

            self.assertTrue(configured)
            self.assertEqual(os.listdir(unit_dir), [])
            mock_run.assert_not_called()

    def test_reports_daemon_reload_failure_without_disabling_existing_timer(self):
        with tempfile.TemporaryDirectory() as unit_dir, patch(
            "lib.maintenance_systemd.SYSTEMD_DIR", unit_dir
        ), patch("lib.maintenance_systemd.replace_units") as mock_run:
            mock_run.side_effect = RuntimeError("activation failed; restored")

            configured = configure_maintenance_timer(
                service_name="auto-update-test",
                service_desc="Auto-update test runtime",
                timer_desc="Auto-update test runtime weekly",
                script_path="/opt/basaltwater/test/update.py",
                schedule="weekly",
                check_name="Test runtime",
            )

        self.assertFalse(configured)
        mock_run.assert_called_once()

    def test_rejects_injected_unit_values(self):
        with self.assertRaisesRegex(ValueError, "control characters"):
            configure_maintenance_timer(
                service_name="auto-update-test",
                service_desc="Auto-update test runtime\nExecStart=/bin/false",
                timer_desc="Auto-update test runtime weekly",
                script_path="/opt/basaltwater/test/update.py",
                schedule="weekly",
                check_name="Test runtime",
            )

    def test_supports_boot_and_calendar_triggers_without_network_dependency(self):
        with tempfile.TemporaryDirectory() as unit_dir, patch(
            "lib.maintenance_systemd.SYSTEMD_DIR", unit_dir
        ), patch("lib.maintenance_systemd.replace_units") as mock_run:
            configured = configure_maintenance_timer(
                service_name="restart-check",
                service_desc="Restart check",
                timer_desc="Restart check timer",
                script_path="/opt/basaltwater/check.py",
                schedule="daily",
                on_boot_sec="30min",
                check_name="Restart",
                network_online=False,
            )
            service_content = mock_run.call_args.args[0]["restart-check.service"]
            timer_content = mock_run.call_args.args[0]["restart-check.timer"]

        self.assertTrue(configured)
        self.assertNotIn("network-online.target", service_content)
        self.assertIn("OnBootSec=30min", timer_content)
        self.assertIn("OnCalendar=daily", timer_content)

    def test_sandboxes_user_service_with_bounded_writable_path(self):
        with tempfile.TemporaryDirectory() as unit_dir, patch(
            "lib.maintenance_systemd.SYSTEMD_DIR", unit_dir
        ), patch("lib.maintenance_systemd.replace_units") as mock_run:
            configured = configure_maintenance_timer(
                service_name="credential-check",
                service_desc="Credential check",
                timer_desc="Credential check timer",
                script_path="/opt/basaltwater/check.py",
                schedule="daily",
                check_name="Credential",
                user="agent",
                sandbox_user_service=True,
                writable_paths=("/home/agent/.config/tool",),
            )
            service_content = mock_run.call_args.args[0]["credential-check.service"]

        self.assertTrue(configured)
        self.assertIn("User=agent", service_content)
        self.assertIn("UMask=0077", service_content)
        self.assertIn("NoNewPrivileges=true", service_content)
        self.assertIn("ProtectSystem=strict", service_content)
        self.assertIn("ProtectHome=read-only", service_content)
        self.assertIn(
            'ReadWritePaths="-/home/agent/.config/tool"',
            service_content,
        )

    def test_preserves_literal_environment_and_paths(self):
        with patch("lib.maintenance_systemd.replace_units") as replace:
            self.assertTrue(configure_maintenance_timer(
                service_name="literal-path-check",
                service_desc="Literal path check",
                timer_desc="Literal path check daily",
                script_path='/opt/tool %h/$HOME/with\\backslash/and "quote".py',
                schedule="daily",
                check_name="Literal path",
                user="agent",
                environment={"DATA": 'https://example.test/a%20b?q="value"\\$HOME'},
                sandbox_user_service=True,
                writable_paths=("/home/literal%h/with space/and'quote\\data",),
            ))
        content = replace.call_args.args[0]["literal-path-check.service"]
        self.assertIn(
            'Environment="DATA=https://example.test/a%%20b?q=\\"value\\"\\\\$HOME"\n', content,
        )
        self.assertIn(
            'ExecStart=/usr/bin/python3 "/opt/tool %%h/$$HOME/with\\\\backslash/and \\"quote\\".py"\n',
            content,
        )
        self.assertIn(
            'ReadWritePaths="-/home/literal%%h/with space/and\'quote\\\\data"\n', content,
        )


class TestMaintenanceTimerCallers(unittest.TestCase):
    def test_optional_update_timer_failures_reach_setup(self):
        config = SetupConfig("example.test", "agent", "server_web")
        for configure, name in ((common_steps.configure_auto_update_uv, "uv"),
                                (common_steps.configure_auto_update_gogs, "Gogs")):
            with (
                self.subTest(name=name),
                patch.object(common_steps, "get_user_home", return_value="/home/agent"),
                patch("lib.maintenance_systemd.os.path.exists", return_value=True),
                patch("lib.maintenance_systemd.is_dry_run", return_value=False),
                patch("lib.maintenance_systemd.replace_units", side_effect=RuntimeError("activation failed; restored")) as replace,
            ):
                with self.assertRaisesRegex(RuntimeError, name + " auto-update timer failed verification"):
                    configure(config)
                replace.assert_called_once()

    def test_optional_update_missing_prerequisites_and_dry_runs_remain_skips(self):
        config = SetupConfig("example.test", "agent", "server_web")
        for configure in (common_steps.configure_auto_update_uv, common_steps.configure_auto_update_gogs):
            for installed, dry_run in ((False, False), (True, True)):
                with (
                    self.subTest(configure=configure.__name__, installed=installed, dry_run=dry_run),
                    patch.object(common_steps, "get_user_home", return_value="/home/agent"),
                    patch("lib.maintenance_systemd.os.path.exists", return_value=installed),
                    patch("lib.maintenance_systemd.is_dry_run", return_value=dry_run),
                    patch("lib.maintenance_systemd.replace_units") as replace,
                ):
                    configure(config)
                    replace.assert_not_called()

    def test_lxc_restart_timer_is_installed_without_kernel_hook_even_when_policy_disabled(self):
        config = SetupConfig("example.test", "agent", "server_web", machine_type="unprivileged",
                             auto_restart=False, auto_restart_force_days=0)
        with (
            patch("lib.machine_state.get_machine_type", return_value="unprivileged"),
            patch.object(security_steps, "is_dry_run", return_value=False),
            patch("lib.maintenance_systemd.is_dry_run", return_value=False),
            patch("lib.maintenance_systemd.replace_units") as replace,
            patch.object(security_steps, "install_kernel_restart_hook") as hook,
        ):
            security_steps.configure_auto_restart(config)
        hook.assert_not_called()
        self.assertEqual(replace.call_args.kwargs["activate"], ("auto-restart-if-needed.timer",))
        timer = replace.call_args.args[0]["auto-restart-if-needed.timer"]
        self.assertIn("OnCalendar=*-*-* 02:00:00", timer)
        self.assertIn("OnBootSec=30min", timer)

    def test_restart_timer_failure_propagates_in_lxc(self):
        with (
            patch("lib.machine_state.get_machine_type", return_value="unprivileged"),
            patch.object(security_steps, "is_dry_run", return_value=False),
            patch("lib.maintenance_systemd.is_dry_run", return_value=False),
            patch("lib.maintenance_systemd.replace_units", side_effect=RuntimeError("restored previous timer")),
            patch.object(security_steps, "install_kernel_restart_hook") as hook,
        ):
            with self.assertRaisesRegex(RuntimeError, "Automatic restart timer failed verification"):
                security_steps.configure_auto_restart(SetupConfig("example.test", "agent", "server_web"))
        hook.assert_not_called()

    def test_restart_capability_and_dry_run_boundaries(self):
        for machine, dry_run in (("oci", False), ("wsl", False), ("vm", True), ("vm", False)):
            with (
                self.subTest(machine=machine, dry_run=dry_run),
                patch("lib.machine_state.get_machine_type", return_value=machine),
                patch.object(security_steps, "is_dry_run", return_value=dry_run),
                patch.object(security_steps, "configure_maintenance_timer", return_value=True) as timer,
                patch.object(security_steps, "install_kernel_restart_hook") as hook,
            ):
                security_steps.configure_auto_restart(SetupConfig("example.test", "agent", "server_web"))
                self.assertEqual(timer.call_count, int(machine == "vm"))
                self.assertEqual(hook.call_count, int(machine == "vm" and not dry_run))


if __name__ == "__main__":
    unittest.main()
