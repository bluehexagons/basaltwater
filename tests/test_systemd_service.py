"""Tests for lib/systemd_service.py: service config generation."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import call, mock_open, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from lib.systemd_service import (
    cleanup_service,
)


class TestCleanupFunctions(unittest.TestCase):
    @patch("lib.systemd_service.remove_file_durable")
    @patch("lib.systemd_service.run")
    @patch("lib.systemd_service.os.path.exists", return_value=True)
    @patch(
        "lib.systemd_service.open",
        new_callable=mock_open,
        read_data="[Unit]\nDescription=Demo\n[Service]\nExecStart=/bin/true\n[Install]\nWantedBy=multi-user.target\n",
    )
    def test_cleanup_service_disables_service_with_install(self, _open, _exists, mock_run, mock_remove):
        cleanup_service("demo")

        mock_run.assert_has_calls(
            [
                call("systemctl stop demo.timer"),
                call("systemctl disable demo.timer"),
                call("systemctl stop demo.path"),
                call("systemctl disable demo.path"),
                call("systemctl stop demo.service"),
                call("systemctl disable demo.service"),
                call("systemctl daemon-reload"),
            ]
        )
        mock_remove.assert_has_calls(
            [
                call("/etc/systemd/system/demo.timer"),
                call("/etc/systemd/system/demo.path"),
                call("/etc/systemd/system/demo.service"),
            ]
        )

    @patch("lib.systemd_service.remove_file_durable")
    @patch("lib.systemd_service.run")
    @patch("lib.systemd_service.os.path.exists", return_value=True)
    @patch("lib.systemd_service.open", new_callable=mock_open, read_data="[Unit]\n[Service]\n")
    def test_cleanup_service_skips_disable_without_install(self, _open, _exists, mock_run, _remove):
        cleanup_service("demo")
        run_commands = [args[0] for args, _ in mock_run.call_args_list]
        self.assertNotIn("systemctl disable demo.service", run_commands)

    @patch("lib.systemd_service.remove_file_durable")
    @patch("lib.systemd_service.run")
    @patch("lib.systemd_service.os.path.exists", return_value=False)
    def test_cleanup_service_handles_missing_files(self, _exists, mock_run, mock_remove):
        cleanup_service("demo")
        mock_run.assert_not_called()
        mock_remove.assert_not_called()

    def test_failed_stop_preserves_unit_and_prevents_disable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "demo.service")
            with open(path, "w") as stream:
                stream.write("[Service]\nExecStart=/bin/true\n[Install]\n")
            with patch("lib.systemd_service.SYSTEMD_DIR", directory), patch(
                "lib.systemd_service.run", side_effect=RuntimeError("stop failed"),
            ) as command:
                with self.assertRaisesRegex(RuntimeError, "stop failed"):
                    cleanup_service("demo")
            self.assertTrue(os.path.isfile(path))
            command.assert_called_once_with("systemctl stop demo.service")


if __name__ == '__main__':
    unittest.main()
