"""Tests for shared recurring-maintenance systemd provisioning."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lib.maintenance_systemd import configure_maintenance_timer


class TestConfigureMaintenanceTimer(unittest.TestCase):
    def test_writes_hardened_units_and_verifies_timer(self):
        with tempfile.TemporaryDirectory() as unit_dir, patch(
            "lib.maintenance_systemd.SYSTEMD_DIR", unit_dir
        ), patch("lib.maintenance_systemd.replace_units") as mock_run:

            configured = configure_maintenance_timer(
                service_name="auto-update-test",
                service_desc="Auto-update test runtime",
                timer_desc="Auto-update test runtime weekly",
                script_path="/opt/infra_tools/test/update.py",
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
                script_path="/opt/infra_tools/test/update.py",
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
                script_path="/opt/infra_tools/test/update.py",
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
                script_path="/opt/infra_tools/test/update.py",
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
                script_path="/opt/infra_tools/check.py",
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
                script_path="/opt/infra_tools/check.py",
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
            "ReadWritePaths=-/home/agent/.config/tool",
            service_content,
        )


if __name__ == "__main__":
    unittest.main()
