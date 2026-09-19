"""Tests for common.service_tools.cleanup_maintenance."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))

from common.service_tools import cleanup_maintenance


class TestCleanupMaintenance(unittest.TestCase):
    @patch("common.service_tools.cleanup_maintenance.notify_if_storage_still_low")
    @patch("common.service_tools.cleanup_maintenance.cleanup_stale_crash_reports", return_value=[])
    @patch("common.service_tools.cleanup_maintenance.cleanup_stale_basaltwater_tmp_artifacts", return_value=[])
    @patch("common.service_tools.cleanup_maintenance.run_optional_cleanup", return_value=None)
    @patch("common.service_tools.cleanup_maintenance.cleanup_filesystem_free_space", return_value=None)
    @patch("common.service_tools.cleanup_maintenance.audit_package_database", return_value=None)
    @patch("common.service_tools.cleanup_maintenance.cleanup_unused_packages", return_value=[])
    @patch("common.service_tools.cleanup_maintenance.cleanup_apt_cache", return_value=[])
    @patch("common.service_tools.cleanup_maintenance.load_notification_configs_from_state", return_value=[])
    def test_successful_cleanup_returns_zero(
        self,
        _configs,
        mock_apt,
        mock_unused_packages,
        mock_package_audit,
        mock_trim,
        mock_optional,
        mock_tmp_cleanup,
        mock_crash_cleanup,
        mock_low_space,
    ):
        call_order = Mock()
        call_order.attach_mock(mock_crash_cleanup, "crash_cleanup")
        call_order.attach_mock(mock_trim, "trim")
        call_order.attach_mock(mock_low_space, "storage_check")
        with self.assertLogs(cleanup_maintenance.logger, level="INFO") as logs:
            result = cleanup_maintenance.main()
        self.assertEqual(result, 0)
        mock_apt.assert_called_once()
        mock_unused_packages.assert_called_once()
        mock_package_audit.assert_called_once()
        mock_trim.assert_called_once()
        self.assertEqual(mock_optional.call_count, 3)
        self.assertFalse(any(call.args[2] == "gem cleanup" for call in mock_optional.call_args_list))
        # Stale Basaltwater tmp cleanup runs once per known temp directory (/tmp, /var/tmp).
        self.assertEqual(mock_tmp_cleanup.call_count, len(cleanup_maintenance.BASALTWATER_TMP_DIRS))
        self.assertEqual(
            mock_crash_cleanup.call_count,
            len(cleanup_maintenance.CRASH_REPORT_DIRS),
        )
        mock_low_space.assert_called_once()
        ordered_names = [call[0] for call in call_order.mock_calls]
        self.assertLess(
            max(index for index, name in enumerate(ordered_names) if name == "crash_cleanup"),
            ordered_names.index("trim"),
        )
        self.assertLess(ordered_names.index("trim"), ordered_names.index("storage_check"))
        joined = "\n".join(logs.output)
        self.assertIn("Starting cleanup maintenance", joined)
        self.assertIn("Cleanup maintenance completed successfully", joined)

    @patch("common.service_tools.cleanup_maintenance.notify_if_storage_still_low")
    @patch("common.service_tools.cleanup_maintenance.cleanup_stale_crash_reports", return_value=[])
    @patch("common.service_tools.cleanup_maintenance.cleanup_stale_basaltwater_tmp_artifacts", return_value=[])
    @patch("common.service_tools.cleanup_maintenance.send_notification_safe")
    @patch(
        "common.service_tools.cleanup_maintenance.run_optional_cleanup",
        side_effect=[None, "journal vacuum: failed", None],
    )
    @patch("common.service_tools.cleanup_maintenance.cleanup_filesystem_free_space", return_value=None)
    @patch("common.service_tools.cleanup_maintenance.audit_package_database", return_value=None)
    @patch("common.service_tools.cleanup_maintenance.cleanup_unused_packages", return_value=[])
    @patch("common.service_tools.cleanup_maintenance.cleanup_apt_cache", return_value=[])
    @patch("common.service_tools.cleanup_maintenance.load_notification_configs_from_state", return_value=["cfg"])
    def test_failure_notifies(
        self,
        _configs,
        _apt,
        _unused_packages,
        _package_audit,
        _trim,
        _optional,
        mock_notify,
        _tmp_cleanup,
        _crash_cleanup,
        mock_low_space,
    ):
        result = cleanup_maintenance.main()
        self.assertEqual(result, 1)
        mock_notify.assert_called_once()
        self.assertIn("cleanup maintenance failed", mock_notify.call_args.kwargs["subject"])
        self.assertIn("journal vacuum: failed", mock_notify.call_args.kwargs["details"])
        mock_low_space.assert_called_once()


class TestCleanupHelpers(unittest.TestCase):
    @patch("common.service_tools.cleanup_maintenance.run_command")
    def test_run_cleanup_command_logs_structured_failure(self, mock_run_command):
        mock_run_command.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr="permission denied",
        )

        with self.assertLogs(cleanup_maintenance.logger, level="WARNING") as logs:
            failure = cleanup_maintenance.run_cleanup_command(["apt-get", "clean"], "APT clean")

        self.assertEqual(failure, "APT clean: permission denied")
        self.assertIn("APT clean failed | stderr='permission denied'", "\n".join(logs.output))

    @patch("common.service_tools.cleanup_maintenance.run_command")
    def test_run_cleanup_command_reports_timeout(self, mock_run_command):
        mock_run_command.side_effect = subprocess.TimeoutExpired(["journalctl"], timeout=600)

        with self.assertLogs(cleanup_maintenance.logger, level="WARNING") as logs:
            failure = cleanup_maintenance.run_cleanup_command(["journalctl"], "journal vacuum")

        self.assertEqual(failure, "journal vacuum: timed out after 600s")
        self.assertIn("journal vacuum timed out", "\n".join(logs.output))

    @patch("common.service_tools.cleanup_maintenance.run_command", side_effect=OSError("missing"))
    def test_run_cleanup_command_reports_os_error(self, _run_command):
        failure = cleanup_maintenance.run_cleanup_command(["missing"], "optional cleanup")

        self.assertEqual(failure, "optional cleanup: missing")

    @patch("common.service_tools.cleanup_maintenance.run_command")
    @patch(
        "common.service_tools.cleanup_maintenance.shutil.which",
        return_value="/usr/bin/apt-get",
    )
    def test_cleanup_apt_cache_uses_noninteractive_env(self, _which, mock_run_command):
        mock_run_command.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        cleanup_maintenance.cleanup_apt_cache()

        first_call = mock_run_command.call_args_list[0]
        self.assertEqual(
            first_call.args[0],
            ["/usr/bin/apt-get", "autoclean", "-qq", "-o", "DPkg::Lock::Timeout=300"],
        )
        self.assertEqual(first_call.kwargs["env"]["DEBIAN_FRONTEND"], "noninteractive")
        self.assertEqual(
            mock_run_command.call_args_list[1].args[0],
            ["/usr/bin/apt-get", "clean", "-o", "DPkg::Lock::Timeout=300"],
        )

    @patch("common.service_tools.cleanup_maintenance.run_cleanup_command", return_value=None)
    @patch("common.service_tools.cleanup_maintenance.shutil.which", return_value="/usr/bin/apt-get")
    def test_cleanup_unused_packages_autoremoves_with_purge(
        self,
        _which,
        mock_cleanup,
    ):
        with patch("common.service_tools.cleanup_maintenance.obsolete_manual_kernels", return_value=[]):
            result = cleanup_maintenance.cleanup_unused_packages()

        self.assertEqual(result, [])
        mock_cleanup.assert_called_once_with(
            [
                "/usr/bin/apt-get",
                "autoremove",
                "--purge",
                "-y",
                "-qq",
                "-o",
                "DPkg::Lock::Timeout=300",
            ],
            "APT unused package cleanup",
            env=mock_cleanup.call_args.kwargs["env"],
        )
        self.assertEqual(
            mock_cleanup.call_args.kwargs["env"]["DEBIAN_FRONTEND"],
            "noninteractive",
        )

    @patch("common.service_tools.cleanup_maintenance.run_cleanup_command", return_value=None)
    @patch("common.service_tools.cleanup_maintenance.shutil.which", return_value="/usr/bin/apt-get")
    def test_cleanup_unused_packages_retains_residual_configuration(
        self,
        _which,
        mock_cleanup,
    ):
        with patch("common.service_tools.cleanup_maintenance.obsolete_manual_kernels", return_value=[]):
            cleanup_maintenance.cleanup_unused_packages()

        command = mock_cleanup.call_args.args[0]
        self.assertNotIn("~c", command)
        self.assertNotEqual(command[1], "purge")

    @patch("common.service_tools.cleanup_maintenance.run_command")
    @patch(
        "common.service_tools.cleanup_maintenance.shutil.which",
        return_value="/usr/bin/dpkg",
    )
    def test_audit_package_database_accepts_clean_state(self, _which, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="",
            stderr="",
        )

        failure = cleanup_maintenance.audit_package_database()

        self.assertIsNone(failure)
        mock_run.assert_called_once_with(["/usr/bin/dpkg", "--audit"], timeout=60)

    @patch("common.service_tools.cleanup_maintenance.run_command")
    @patch(
        "common.service_tools.cleanup_maintenance.shutil.which",
        return_value="/usr/bin/dpkg",
    )
    def test_audit_package_database_reports_dpkg_findings(self, _which, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="package is only half configured",
            stderr="",
        )

        failure = cleanup_maintenance.audit_package_database()

        self.assertEqual(
            failure,
            "package database audit: package is only half configured",
        )

    @patch("common.service_tools.cleanup_maintenance.run_cleanup_command", return_value=None)
    @patch(
        "common.service_tools.cleanup_maintenance.shutil.which",
        return_value="/usr/bin/journalctl",
    )
    def test_journal_cleanup_rotates_and_enforces_age_and_size(self, _which, mock_cleanup):
        cleanup_maintenance.run_optional_cleanup(
            ["journalctl"],
            [
                "--rotate",
                f"--vacuum-size={cleanup_maintenance.JOURNAL_MAX_USE}",
                f"--vacuum-time={cleanup_maintenance.JOURNAL_MAX_AGE}",
            ],
            "journal rotation and vacuum",
        )

        mock_cleanup.assert_called_once_with(
            [
                "/usr/bin/journalctl",
                "--rotate",
                f"--vacuum-size={cleanup_maintenance.JOURNAL_MAX_USE}",
                f"--vacuum-time={cleanup_maintenance.JOURNAL_MAX_AGE}",
            ],
            "journal rotation and vacuum",
        )

    @patch("common.service_tools.cleanup_maintenance.run_cleanup_command", return_value=None)
    @patch(
        "common.service_tools.cleanup_maintenance.shutil.which",
        side_effect=[None, "/usr/bin/pip"],
    )
    def test_run_optional_cleanup_uses_first_available_executable(self, _which, mock_cleanup):
        result = cleanup_maintenance.run_optional_cleanup(
            ["pip3", "pip"],
            ["cache", "purge"],
            "pip cache cleanup",
        )

        self.assertIsNone(result)
        mock_cleanup.assert_called_once_with(
            ["/usr/bin/pip", "cache", "purge"],
            "pip cache cleanup",
        )

    @patch("common.service_tools.cleanup_maintenance.run_optional_cleanup", return_value=None)
    @patch("common.service_tools.cleanup_maintenance.shutil.which", return_value=None)
    @patch("common.service_tools.cleanup_maintenance.is_container", return_value=False)
    def test_cleanup_filesystem_free_space_trims_host(
        self,
        _is_container,
        _which,
        mock_optional,
    ):
        result = cleanup_maintenance.cleanup_filesystem_free_space()

        self.assertIsNone(result)
        mock_optional.assert_called_once_with(
            ["fstrim"],
            ["--all", "--verbose", "--quiet-unsupported"],
            "filesystem trim",
        )

    @patch("common.service_tools.cleanup_maintenance.run_optional_cleanup")
    @patch("common.service_tools.cleanup_maintenance.run_command")
    @patch(
        "common.service_tools.cleanup_maintenance.shutil.which",
        return_value="/usr/bin/systemctl",
    )
    @patch("common.service_tools.cleanup_maintenance.is_container", return_value=False)
    def test_cleanup_filesystem_free_space_defers_to_native_timer(
        self,
        _is_container,
        _which,
        mock_run,
        mock_optional,
    ):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="",
            stderr="",
        )

        result = cleanup_maintenance.cleanup_filesystem_free_space()

        self.assertIsNone(result)
        mock_run.assert_called_once_with(
            ["/usr/bin/systemctl", "is-active", "--quiet", "fstrim.timer"],
            timeout=10,
        )
        mock_optional.assert_not_called()

    @patch("common.service_tools.cleanup_maintenance.run_optional_cleanup")
    @patch("common.service_tools.cleanup_maintenance.is_container", return_value=True)
    def test_cleanup_filesystem_free_space_skips_container(
        self,
        _is_container,
        mock_optional,
    ):
        result = cleanup_maintenance.cleanup_filesystem_free_space()

        self.assertIsNone(result)
        mock_optional.assert_not_called()

    def test_cleanup_stale_crash_reports_removes_only_old_recognized_files(self):
        with tempfile.TemporaryDirectory() as crash_dir:
            old_core = os.path.join(crash_dir, "core.python.1000.boot.123.zst")
            old_apport = os.path.join(crash_dir, "_usr_bin_python3.1000.crash")
            fresh_core = os.path.join(crash_dir, "core.fresh.1000.boot.456.zst")
            unrelated = os.path.join(crash_dir, "operator-note.txt")
            old_directory = os.path.join(crash_dir, "core.directory")
            old_symlink = os.path.join(crash_dir, "core.symlink")
            for path in (old_core, old_apport, fresh_core, unrelated):
                with open(path, "w", encoding="utf-8") as file_obj:
                    file_obj.write("x")
            os.mkdir(old_directory)
            os.symlink(old_core, old_symlink)

            old_time = time.time() - (31 * 24 * 60 * 60)
            for path in (old_core, old_apport, unrelated, old_directory):
                os.utime(path, (old_time, old_time))

            failures = cleanup_maintenance.cleanup_stale_crash_reports(
                crash_dir=crash_dir,
                max_age_days=30,
            )

            self.assertEqual(failures, [])
            self.assertFalse(os.path.exists(old_core))
            self.assertFalse(os.path.exists(old_apport))
            self.assertTrue(os.path.exists(fresh_core))
            self.assertTrue(os.path.exists(unrelated))
            self.assertTrue(os.path.isdir(old_directory))
            self.assertTrue(os.path.islink(old_symlink))

    def test_cleanup_stale_crash_reports_reports_remove_failure(self):
        with tempfile.TemporaryDirectory() as crash_dir:
            old_core = os.path.join(crash_dir, "core.python.1000.boot.123.zst")
            with open(old_core, "w", encoding="utf-8") as file_obj:
                file_obj.write("x")
            old_time = time.time() - (31 * 24 * 60 * 60)
            os.utime(old_core, (old_time, old_time))

            with patch(
                "common.service_tools.cleanup_maintenance.os.unlink",
                side_effect=OSError("busy"),
            ):
                failures = cleanup_maintenance.cleanup_stale_crash_reports(
                    crash_dir=crash_dir,
                    max_age_days=30,
                )

            self.assertEqual(failures, [f"{old_core}: busy"])

    def test_cleanup_stale_basaltwater_tmp_artifacts_removes_only_old_owned_paths(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            old_dir = os.path.join(tmp_dir, "basaltwater_setup_build_abcd")
            old_file = os.path.join(tmp_dir, "antistatic-server-linux-amd64.v1")
            old_bundler_dir = os.path.join(tmp_dir, "bundler20240101-12345-abc123")
            fresh_file = os.path.join(tmp_dir, "basaltwater_deploy_fresh")
            unrelated_file = os.path.join(tmp_dir, "unrelated")
            misleading_file = os.path.join(tmp_dir, "bundler_project")
            os.mkdir(old_dir)
            os.mkdir(old_bundler_dir)
            for path in (old_file, fresh_file, unrelated_file, misleading_file):
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write("x")

            old_time = time.time() - (8 * 24 * 60 * 60)
            os.utime(old_dir, (old_time, old_time))
            os.utime(old_bundler_dir, (old_time, old_time))
            os.utime(old_file, (old_time, old_time))
            os.utime(misleading_file, (old_time, old_time))

            failures = cleanup_maintenance.cleanup_stale_basaltwater_tmp_artifacts(
                tmp_dir=tmp_dir,
                max_age_days=7,
            )

            self.assertEqual(failures, [])
            self.assertFalse(os.path.exists(old_dir))
            self.assertFalse(os.path.exists(old_bundler_dir))
            self.assertFalse(os.path.exists(old_file))
            self.assertTrue(os.path.exists(fresh_file))
            self.assertTrue(os.path.exists(unrelated_file))
            self.assertTrue(os.path.exists(misleading_file))

    def test_cleanup_stale_basaltwater_tmp_artifacts_reports_remove_failure(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            old_dir = os.path.join(tmp_dir, "basaltwater_recall_abcd")
            os.mkdir(old_dir)
            old_time = time.time() - (8 * 24 * 60 * 60)
            os.utime(old_dir, (old_time, old_time))

            with patch(
                "common.service_tools.cleanup_maintenance.shutil.rmtree",
                side_effect=OSError("busy"),
            ):
                failures = cleanup_maintenance.cleanup_stale_basaltwater_tmp_artifacts(
                    tmp_dir=tmp_dir,
                    max_age_days=7,
                )

            self.assertEqual(failures, [f"{old_dir}: busy"])

    @patch("common.service_tools.cleanup_maintenance.validate_filesystem_path")
    @patch("common.service_tools.cleanup_maintenance.run_command")
    @patch(
        "common.service_tools.cleanup_maintenance.shutil.which",
        return_value="/usr/bin/findmnt",
    )
    def test_discover_local_mount_points_flattens_tree_and_skips_remote(
        self,
        _which,
        mock_run,
        _validate_path,
    ):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=(
                '{"filesystems": [{"target": "/", "fstype": "ext4", '
                '"children": [{"target": "/srv/data", "fstype": "xfs"}, '
                '{"target": "/mnt/nfs", "fstype": "nfs4"}, '
                '{"target": "/mnt/ssh", "fstype": "fuse.sshfs"}]}]}'
            ),
            stderr="",
        )

        mounts = cleanup_maintenance.discover_local_mount_points()

        self.assertEqual(mounts, ["/", "/srv/data"])
        mock_run.assert_called_once_with(
            [
                "/usr/bin/findmnt",
                "--json",
                "--real",
                "--output",
                "TARGET,FSTYPE",
            ],
            timeout=30,
        )

    @patch("common.service_tools.cleanup_maintenance.run_command")
    @patch(
        "common.service_tools.cleanup_maintenance.shutil.which",
        return_value="/usr/bin/findmnt",
    )
    def test_discover_local_mount_points_falls_back_on_invalid_json(
        self,
        _which,
        mock_run,
    ):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="not json",
            stderr="",
        )

        mounts = cleanup_maintenance.discover_local_mount_points()

        self.assertEqual(mounts, ["/"])

    @patch("common.service_tools.cleanup_maintenance.os.statvfs")
    @patch("common.service_tools.cleanup_maintenance.os.stat")
    @patch("common.service_tools.cleanup_maintenance.get_disk_usage_details")
    @patch(
        "common.service_tools.cleanup_maintenance.discover_local_mount_points",
        return_value=["/", "/bind", "/srv/data"],
    )
    def test_collect_local_storage_usage_deduplicates_bind_mounts_and_counts_inodes(
        self,
        _discover,
        mock_disk_usage,
        mock_stat,
        mock_statvfs,
    ):
        root_usage = {
            "total_mb": 1000,
            "used_mb": 500,
            "free_mb": 500,
            "usage_percent": 50,
        }
        data_usage = {
            "total_mb": 2000,
            "used_mb": 1500,
            "free_mb": 500,
            "usage_percent": 75,
        }
        mock_disk_usage.side_effect = [root_usage.copy(), root_usage.copy(), data_usage]
        mock_stat.side_effect = [
            SimpleNamespace(st_dev=1),
            SimpleNamespace(st_dev=1),
            SimpleNamespace(st_dev=2),
        ]
        mock_statvfs.side_effect = [
            SimpleNamespace(f_files=100, f_ffree=5),
            SimpleNamespace(f_files=100, f_ffree=5),
            SimpleNamespace(f_files=200, f_ffree=100),
        ]

        usage = cleanup_maintenance.collect_local_storage_usage()

        self.assertEqual(set(usage), {"/", "/srv/data"})
        self.assertEqual(usage["/"]["inode_usage_percent"], 95)
        self.assertEqual(usage["/srv/data"]["inode_usage_percent"], 50)

    @patch("common.service_tools.cleanup_maintenance.send_notification_safe")
    @patch("common.service_tools.cleanup_maintenance.collect_local_storage_usage")
    def test_low_space_notification_reports_space_and_inode_pressure(
        self,
        mock_usage,
        mock_notify,
    ):
        mock_usage.return_value = {
            "/": {
                "total_mb": 1000,
                "used_mb": 920,
                "free_mb": 80,
                "usage_percent": 92,
                "inode_usage_percent": 40,
            },
            "/var/lib/vz": {
                "total_mb": 2000,
                "used_mb": 1400,
                "free_mb": 600,
                "usage_percent": 70,
                "inode_usage_percent": 85,
            },
        }

        with tempfile.TemporaryDirectory() as state_dir:
            with patch.object(
                cleanup_maintenance,
                "STATE_FILE",
                os.path.join(state_dir, "cleanup-state.json"),
            ):
                cleanup_maintenance.notify_if_storage_still_low(["cfg"])

        mock_notify.assert_called_once()
        self.assertIn("storage still low after cleanup", mock_notify.call_args.kwargs["subject"].lower())
        self.assertEqual(mock_notify.call_args.kwargs["status"], "error")
        self.assertIn("/var/lib/vz", mock_notify.call_args.kwargs["details"])
        self.assertIn("inodes=85%", mock_notify.call_args.kwargs["details"])

    @patch("common.service_tools.cleanup_maintenance.send_notification_safe", return_value=True)
    @patch(
        "common.service_tools.cleanup_maintenance.collect_local_storage_usage",
        return_value={
            "/": {
                "total_mb": 1000,
                "used_mb": 920,
                "free_mb": 80,
                "usage_percent": 92,
                "inode_usage_percent": 40,
            },
        },
    )
    def test_repeated_storage_pressure_is_suppressed(self, _usage, mock_notify):
        with tempfile.TemporaryDirectory() as state_dir:
            with patch.object(
                cleanup_maintenance,
                "STATE_FILE",
                os.path.join(state_dir, "cleanup-state.json"),
            ):
                cleanup_maintenance.notify_if_storage_still_low(["cfg"])
                cleanup_maintenance.notify_if_storage_still_low(["cfg"])

        mock_notify.assert_called_once()

    @patch("common.service_tools.cleanup_maintenance.send_notification_safe", return_value=True)
    @patch(
        "common.service_tools.cleanup_maintenance.collect_local_storage_usage",
        side_effect=[
            {
                "/": {
                    "total_mb": 1000,
                    "used_mb": 920,
                    "free_mb": 80,
                    "usage_percent": 92,
                    "inode_usage_percent": 40,
                },
            },
            {},
        ],
    )
    def test_storage_pressure_recovery_is_reported(self, _usage, mock_notify):
        with tempfile.TemporaryDirectory() as state_dir:
            with patch.object(
                cleanup_maintenance,
                "STATE_FILE",
                os.path.join(state_dir, "cleanup-state.json"),
            ):
                cleanup_maintenance.notify_if_storage_still_low(["cfg"])
                cleanup_maintenance.notify_if_storage_still_low(["cfg"])

        self.assertEqual(mock_notify.call_count, 2)
        self.assertEqual(mock_notify.call_args_list[0].kwargs["state"], "firing")
        self.assertEqual(mock_notify.call_args_list[1].kwargs["state"], "resolved")

    @patch("common.service_tools.cleanup_maintenance.send_notification_safe")
    @patch(
        "common.service_tools.cleanup_maintenance.collect_local_storage_usage",
        return_value={
            "/": {
                "total_mb": 1000,
                "used_mb": 700,
                "free_mb": 300,
                "usage_percent": 70,
                "inode_usage_percent": 20,
            },
        },
    )
    def test_low_space_notification_skips_when_usage_is_ok(self, _usage, mock_notify):
        cleanup_maintenance.notify_if_storage_still_low([])

        mock_notify.assert_not_called()

    @patch("common.service_tools.cleanup_maintenance.shutil.disk_usage")
    def test_log_tmp_usage_logs_stats(self, mock_usage):
        from collections import namedtuple
        DiskUsage = namedtuple("DiskUsage", ["total", "used", "free"])
        mock_usage.return_value = DiskUsage(
            total=10 * 1024 * 1024 * 1024,
            used=2 * 1024 * 1024 * 1024,
            free=8 * 1024 * 1024 * 1024,
        )
        with self.assertLogs(cleanup_maintenance.logger, level="DEBUG") as logs:
            cleanup_maintenance.log_tmp_usage("/tmp")
        joined = "\n".join(logs.output)
        self.assertIn("Temp directory usage", joined)
        self.assertIn("tmp_dir='/tmp'", joined)

    @patch("common.service_tools.cleanup_maintenance.shutil.disk_usage", side_effect=OSError("no such directory"))
    def test_log_tmp_usage_handles_oserror(self, _mock_usage):
        with self.assertLogs(cleanup_maintenance.logger, level="WARNING") as logs:
            cleanup_maintenance.log_tmp_usage("/nonexistent")
        self.assertIn("Could not read temp directory usage", "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
