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
    generate_mount_check_condition,
    schedule_storage_ops_update,
)


class TestGenerateMountCheckCondition(unittest.TestCase):
    @patch("lib.task_utils.get_mount_ancestor", return_value=None)
    def test_includes_derived_mnt_mountpoint_when_unmounted(self, _ancestor):
        config = SetupConfig(
            host="test",
            username="test",
            system_type="server_lite",
            sync_specs=[["/mnt/data/source", "/home/test/backup", "daily"]],
            scrub_specs=[],
        )
        condition = generate_mount_check_condition(config)
        self.assertIn("ExecCondition=", condition)
        self.assertIn("/mnt/data", condition)


class TestCreateStorageOpsService(unittest.TestCase):
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
