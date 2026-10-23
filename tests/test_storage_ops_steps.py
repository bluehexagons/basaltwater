"""Tests for sync/storage_ops_steps.py."""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from lib.config import SetupConfig
from sync.storage_ops_steps import (
    create_storage_ops_service,
    schedule_storage_ops_update,
)


class TestCreateStorageOpsService(unittest.TestCase):
    @patch("sync.storage_ops_steps.os.makedirs")
    @patch("sync.storage_ops_steps.replace_units")
    @patch("sync.storage_ops_steps.is_dry_run", return_value=False)
    def test_service_leaves_mount_validation_to_each_operation(self, _dry_run, replace, _makedirs):
        config = SetupConfig(
            host="test", username="test", system_type="server_lite",
            backup_specs=[["/srv/data", "/srv/backup", "daily"]],
            scrub_specs=[["/srv/data", "/mnt/offline-parity", "10%", "weekly"]],
            storage_mounts=[["backup", "/srv/backup", "ext4", "empty"]],
        )
        create_storage_ops_service(config)
        service = replace.call_args.args[0]["storage-ops.service"]
        self.assertNotIn("ExecCondition=", service)
        self.assertIn("ExecStart=/usr/bin/python3", service)

    @patch("sync.storage_ops_steps.run")
    @patch("sync.storage_ops_steps.open")
    @patch("sync.storage_ops_steps.os.makedirs")
    @patch("sync.storage_ops_steps.replace_units")
    @patch("sync.storage_ops_steps.is_dry_run", return_value=True)
    def test_skips_file_changes_in_dry_run(self, _dry_run, cleanup, makedirs, file_open, run_cmd):
        config = SetupConfig(
            host="test",
            username="test",
            system_type="server_lite",
            sync_specs=[["/mnt/data/source", "/mnt/backup/target", "daily"]],
            scrub_specs=[],
        )
        create_storage_ops_service(config)
        cleanup.assert_not_called()
        makedirs.assert_not_called()
        file_open.assert_not_called()
        run_cmd.assert_not_called()


class TestScheduleStorageOpsUpdate(unittest.TestCase):
    @patch(
        "sync.storage_ops_steps.run",
        return_value=subprocess.CompletedProcess(args=["cmd"], returncode=0),
    )
    def test_schedules_delayed_update_by_default(self, run_cmd):
        schedule_storage_ops_update()
        run_cmd.assert_called_once()
        command = run_cmd.call_args[0][0]
        self.assertEqual(command[:2], ["systemd-run", "--collect"])
        self.assertTrue(any(item.startswith("--unit=storage-ops-initial-update-") for item in command))
        self.assertEqual(command[command.index("--on-active") + 1], "2m")
        self.assertEqual(command[-3:], ["/usr/bin/systemctl", "start", "storage-ops.service"])
        self.assertEqual(run_cmd.call_args[1], {"check": False})

    @patch(
        "sync.storage_ops_steps.run",
        return_value=subprocess.CompletedProcess(args=["cmd"], returncode=0),
    )
    def test_schedules_delayed_update_with_custom_delay(self, run_cmd):
        schedule_storage_ops_update(delay_minutes=7)
        run_cmd.assert_called_once()
        command = run_cmd.call_args[0][0]
        self.assertEqual(command[command.index("--on-active") + 1], "7m")


if __name__ == "__main__":
    unittest.main()
