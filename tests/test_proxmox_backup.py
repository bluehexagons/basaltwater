"""Tests for lib/proxmox_backup.py."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lib.proxmox_hosts import ProxmoxHost
from lib.proxmox_backup import (
    BackupInfo,
    ProxmoxBackupError,
    create_backup,
    list_backups,
)


def _host(**kw) -> ProxmoxHost:
    return ProxmoxHost(name="pve1", address="10.0.0.10", **kw)


def _ok(stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], 0, stdout, stderr)


def _fail(stderr: str = "error") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], 1, "", stderr)


_STORAGE_JSON = json.dumps([
    {"storage": "local", "content": "backup,iso", "active": 1},
    {"storage": "local-lvm", "content": "images,rootdir", "active": 1},
])

_CONTENT_JSON = json.dumps([
    {
        "volid": "local:backup/vzdump-qemu-100-2024_01_01-00_00_00.vma.zst",
        "vmid": 100,
        "size": 1_073_741_824,
        "ctime": 1704067200,
        "format": "vma.zst",
        "notes": "",
    },
    {
        "volid": "local:backup/vzdump-qemu-200-2024_01_01-00_00_00.vma.zst",
        "vmid": 200,
        "size": 512_000_000,
        "ctime": 1704067300,
        "format": "vma.zst",
        "notes": "",
    },
])


class TestListBackups(unittest.TestCase):
    def test_shell_reports_backup_and_migration_errors_and_continues(self):
        from lib.proxmox_migrate import ProxmoxMigrateError
        from lib.proxmox_shell import ProxmoxShell

        for error_type in (ProxmoxBackupError, ProxmoxMigrateError):
            with self.subTest(error_type=error_type):
                inputs = iter(["backups 100", "help", "quit"])
                output = []
                shell = ProxmoxShell(
                    input_func=lambda _prompt: next(inputs), output_func=output.append
                )
                with patch.object(shell, "dispatch", side_effect=[error_type("lookup failed"), None]) as dispatch:
                    self.assertEqual(shell.run(), 0)
                self.assertEqual(dispatch.call_count, 2)
                self.assertIn("Error: lookup failed", output)

    @patch("lib.proxmox_backup._ssh_run")
    def test_returns_backups_for_vmid(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = [
            _ok(_STORAGE_JSON),
            _ok(_CONTENT_JSON),
        ]
        backups = list_backups(_host(), 100)
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].vmid, 100)
        self.assertEqual(backups[0].size, 1_073_741_824)

    @patch("lib.proxmox_backup._ssh_run")
    def test_returns_empty_when_no_backup_storage(self, mock_run: MagicMock) -> None:
        mock_run.return_value = _ok("[]")
        backups = list_backups(_host(), 100)
        self.assertEqual(backups, [])

    @patch("lib.proxmox_backup._ssh_run")
    def test_reports_pvesh_failure(self, mock_run: MagicMock) -> None:
        mock_run.return_value = _fail()
        with self.assertRaises(ProxmoxBackupError):
            list_backups(_host(), 100)

    @patch("lib.proxmox_backup._ssh_run")
    def test_failed_pool_listing_does_not_return_partial_inventory(self, mock_run):
        pools = json.dumps([
            {"storage": name, "content": "backup", "active": 1}
            for name in ("local", "nas")
        ])
        mock_run.side_effect = [_ok(pools), _ok(_CONTENT_JSON), _fail("NAS offline")]
        with self.assertRaisesRegex(ProxmoxBackupError, "NAS offline"):
            list_backups(_host(), 100)

    @patch("lib.proxmox_backup._ssh_run")
    def test_invalid_inventory_is_reported(self, mock_run):
        for payload in ("", "null", "{}", "[null]", '[{"vmid":"invalid"}]'):
            with self.subTest(payload=payload):
                mock_run.side_effect = [_ok(_STORAGE_JSON), _ok(payload)]
                with self.assertRaises(ProxmoxBackupError):
                    list_backups(_host(), 100)

    @patch("lib.proxmox_backup._ssh_run")
    def test_filters_other_vmids(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = [_ok(_STORAGE_JSON), _ok(_CONTENT_JSON)]
        backups = list_backups(_host(), 999)
        self.assertEqual(backups, [])

    def test_backup_info_storage_property(self) -> None:
        b = BackupInfo(volid="local:backup/foo.vma.zst", vmid=100, size=0)
        self.assertEqual(b.storage, "local")
        self.assertEqual(b.filename, "backup/foo.vma.zst")


class TestCreateBackup(unittest.TestCase):
    @patch("lib.proxmox_backup._ssh_run")
    def test_calls_vzdump(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = [_ok(_STORAGE_JSON), _ok()]
        create_backup(_host(), 100)
        vzdump_call = mock_run.call_args_list[-1]
        cmd = vzdump_call[0][3]
        self.assertIn("vzdump", cmd)
        self.assertIn("100", cmd)
        self.assertIn("snapshot", cmd)
        self.assertIn("zstd", cmd)

    @patch("lib.proxmox_backup._ssh_run")
    def test_uses_explicit_storage(self, mock_run: MagicMock) -> None:
        mock_run.return_value = _ok()
        create_backup(_host(), 100, storage="nfs-backup")
        cmd = mock_run.call_args[0][3]
        self.assertIn("nfs-backup", cmd)

    @patch("lib.proxmox_backup._ssh_run")
    def test_raises_when_no_backup_storage(self, mock_run: MagicMock) -> None:
        mock_run.return_value = _ok("[]")
        with self.assertRaises(ProxmoxBackupError):
            create_backup(_host(), 100)

    @patch("lib.proxmox_backup._ssh_run")
    def test_raises_on_vzdump_failure(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = [_ok(_STORAGE_JSON), _fail("lock failed")]
        with self.assertRaises(ProxmoxBackupError):
            create_backup(_host(), 100)

    def test_invalid_mode_raises(self) -> None:
        with self.assertRaises(ValueError):
            create_backup(_host(), 100, mode="live")

    def test_invalid_compress_raises(self) -> None:
        with self.assertRaises(ValueError):
            create_backup(_host(), 100, compress="bzip2")

    @patch("lib.proxmox_backup._ssh_run")
    def test_dry_run_passes_through(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = [_ok(_STORAGE_JSON), _ok()]
        create_backup(_host(), 100, dry_run=True)
        _, kwargs = mock_run.call_args
        self.assertTrue(kwargs.get("dry_run"))


if __name__ == "__main__":
    unittest.main()
