"""Tests for mount validation and SMB boundary utilities."""

from __future__ import annotations

import io
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import Mock, patch

from lib import mount_utils


def completed(returncode: int = 0, stdout: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(["mountpoint"], returncode, stdout, "")


class TestMountPathHelpers(unittest.TestCase):
    def test_is_path_under_mnt_uses_path_boundary(self) -> None:
        cases = {
            "/mnt": True,
            "/mnt/data": True,
            "/mnt-data": False,
            "/var/mnt/data": False,
        }
        for path, expected in cases.items():
            with self.subTest(path=path):
                self.assertEqual(mount_utils.is_path_under_mnt(path), expected)

    def test_get_mount_ancestor_returns_closest_mounted_parent(self) -> None:
        checked = []

        def run(command: list[str], capture_output: bool):
            del capture_output
            checked.append(command[-1])
            return completed(0 if command[-1] == "/mnt/data" else 1)

        with patch.object(mount_utils.subprocess, "run", side_effect=run):
            result = mount_utils.get_mount_ancestor("/mnt/data/logs/app.log")

        self.assertEqual(result, "/mnt/data")
        self.assertEqual(checked, ["/mnt/data/logs/app.log", "/mnt/data/logs", "/mnt/data"])

    def test_get_mount_ancestor_returns_none_without_mount(self) -> None:
        with patch.object(mount_utils.subprocess, "run", return_value=completed(1)) as run:
            result = mount_utils.get_mount_ancestor("/tmp/data")
        self.assertIsNone(result)
        self.assertEqual(run.call_count, 2)


class TestMountValidation(unittest.TestCase):
    def test_validate_mount_for_sync_accepts_mount_and_ancestor(self) -> None:
        with patch.object(mount_utils.subprocess, "run", return_value=completed(0)), patch.object(mount_utils, "get_mount_ancestor") as ancestor:
            self.assertTrue(mount_utils.validate_mount_for_sync("/mnt/data"))
        ancestor.assert_not_called()

        with patch.object(mount_utils.subprocess, "run", return_value=completed(1)), patch.object(mount_utils, "get_mount_ancestor", return_value="/mnt"):
            self.assertTrue(mount_utils.validate_mount_for_sync("/mnt/data/file"))

    def test_validate_mount_for_sync_rejects_unmounted_mnt_path(self) -> None:
        error = io.StringIO()
        with patch.object(mount_utils.subprocess, "run", return_value=completed(1)), patch.object(mount_utils, "get_mount_ancestor", return_value=None):
            with redirect_stderr(error):
                result = mount_utils.validate_mount_for_sync("/mnt/data", "source")
        self.assertFalse(result)
        self.assertIn("Source path /mnt/data is not on a mounted filesystem", error.getvalue())

    def test_validate_mount_for_sync_allows_unmounted_local_path(self) -> None:
        with patch.object(mount_utils.subprocess, "run", return_value=completed(1)), patch.object(mount_utils, "get_mount_ancestor", return_value=None):
            self.assertTrue(mount_utils.validate_mount_for_sync("/srv/data"))

    def test_validate_multiple_paths_preserves_path_result_mapping(self) -> None:
        with patch.object(mount_utils, "validate_mount_for_sync", side_effect=[True, False]) as validate:
            result = mount_utils.validate_multiple_paths(["/mnt/a", "/mnt/b"], ["source", "destination"])
        self.assertEqual(result, {"/mnt/a": True, "/mnt/b": False})
        self.assertEqual(validate.call_args_list[0].args, ("/mnt/a", "source"))
        self.assertEqual(validate.call_args_list[1].args, ("/mnt/b", "destination"))


class TestSmbConnectivity(unittest.TestCase):
    def test_readonly_source_uses_containing_mount_and_never_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            child = os.path.join(directory, 'child')
            os.mkdir(child)
            with patch.object(mount_utils, 'is_path_mounted', return_value=True), patch.object(mount_utils.subprocess, 'run', return_value=completed(stdout='cifs')) as run, patch.object(mount_utils, '_probe_writable_directory', side_effect=PermissionError('read only')) as probe:
                self.assertTrue(mount_utils.validate_smb_connectivity(child, writable=False))
                probe.assert_not_called()
                self.assertEqual(run.call_args.args[0][-2:], ['--target', child])
                self.assertFalse(mount_utils.validate_smb_connectivity(child))

    def test_probe_preserves_existing_diagnostic_names(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = [os.path.join(directory, name) for name in
                     ('.smb_connectivity_test', '.accessibility_test')]
            for path in paths:
                with open(path, 'w') as stream:
                    stream.write('user data')
            mount_utils._probe_writable_directory(directory)
            self.assertEqual(len(os.listdir(directory)), 2)
            for path in paths:
                with open(path) as stream:
                    self.assertEqual(stream.read(), 'user data')

    def test_validate_smb_connectivity_checks_type_and_file_operations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            stdout = io.StringIO()
            with patch.object(mount_utils, "is_path_mounted", return_value=True), patch.object(mount_utils.subprocess, "run", return_value=completed(stdout="cifs")):
                with redirect_stdout(stdout):
                    result = mount_utils.validate_smb_connectivity(directory)
            self.assertFalse(os.path.exists(os.path.join(directory, ".smb_connectivity_test")))

        self.assertTrue(result)
        self.assertIn("SMB connectivity test passed", stdout.getvalue())

    def test_validate_smb_connectivity_rejects_non_smb_and_unmounted_paths(self) -> None:
        with patch.object(mount_utils, "is_path_mounted", return_value=True), patch.object(mount_utils.subprocess, "run", return_value=completed(stdout="ext4")):
            self.assertFalse(mount_utils.validate_smb_connectivity("/srv/data"))

        stdout = io.StringIO()
        with patch.object(mount_utils, "is_path_mounted", return_value=False), redirect_stdout(stdout):
            self.assertFalse(mount_utils.validate_smb_connectivity("/mnt/missing"))
        self.assertIn("Path is not mounted", stdout.getvalue())

    def test_validate_smb_connectivity_handles_findmnt_failure(self) -> None:
        with patch.object(mount_utils, "is_path_mounted", return_value=True), patch.object(mount_utils.subprocess, "run", side_effect=subprocess.CalledProcessError(1, ["findmnt"])):
            self.assertFalse(mount_utils.validate_smb_connectivity("/mnt/share"))


class TestMountStatus(unittest.TestCase):
    def test_status_details_returns_minimal_data_for_unmounted_path(self) -> None:
        with patch.object(mount_utils, "is_path_mounted", return_value=False), patch.object(mount_utils, "get_mount_ancestor", return_value=None):
            details = mount_utils.get_mount_status_details("/srv/data")
        self.assertEqual(details["path"], "/srv/data")
        self.assertFalse(details["is_mounted"])
        self.assertFalse(details["accessible"])
        self.assertIsNone(details["fstype"])

    def test_status_details_reports_remote_filesystem_and_accessibility(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            findmnt_results = [completed(stdout="cifs rw,relatime"), completed(stdout="//nas/share")]
            with patch.object(mount_utils, "is_path_mounted", return_value=True), patch.object(mount_utils, "get_mount_ancestor", return_value=directory), patch.object(mount_utils.subprocess, "run", side_effect=findmnt_results):
                details = mount_utils.get_mount_status_details(directory)

        self.assertEqual(details["mount_ancestor"], directory)
        self.assertEqual(details["fstype"], "cifs")
        self.assertEqual(details["mount_options"], "rw,relatime")
        self.assertEqual(details["remote_server"], "//nas/share")
        self.assertTrue(details["accessible"])

    def test_status_details_survives_findmnt_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.object(mount_utils, "is_path_mounted", return_value=True), patch.object(mount_utils, "get_mount_ancestor", return_value=directory), patch.object(mount_utils.subprocess, "run", side_effect=subprocess.CalledProcessError(1, ["findmnt"])):
            details = mount_utils.get_mount_status_details(directory)
        self.assertIsNone(details["fstype"])
        self.assertTrue(details["accessible"])


class TestMountMonitor(unittest.TestCase):
    def test_monitor_calls_callback_when_validation_fails(self) -> None:
        class StopMonitoring(Exception):
            pass

        class ImmediateThread:
            def __init__(self, target, daemon):
                self.target = target
                self.daemon = daemon

            def start(self):
                try:
                    self.target()
                except StopMonitoring:
                    pass

        callback = Mock()
        with patch.object(mount_utils.threading, "Thread", ImmediateThread), patch.object(mount_utils, "validate_mount_for_sync", return_value=False), patch.object(mount_utils.time, "sleep", side_effect=StopMonitoring):
            thread = mount_utils.monitor_mount_with_callback("/mnt/share", callback, check_interval=1)

        self.assertIsInstance(thread, ImmediateThread)
        self.assertTrue(thread.daemon)
        callback.assert_called_once_with("Mount issue detected for /mnt/share")


if __name__ == "__main__":
    unittest.main()
