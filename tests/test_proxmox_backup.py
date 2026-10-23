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
    DEFAULT_BACKUP_TIMEOUT,
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
    def setUp(self) -> None:
        self.inspect = self.enterContext(patch("lib.proxmox_backup._ssh_run"))
        self.execute = self.enterContext(patch("lib.proxmox_backup.subprocess.run", return_value=_ok()))
        # Do not touch SSH trust/control-path state in these offline tests.
        self.ssh = self.enterContext(patch(
            "lib.proxmox_backup._proxmox_ssh_command",
            side_effect=lambda address, user, opts, cmd: ["ssh", f"{user}@{address}", cmd],
        ))
        self.enterContext(patch("lib.proxmox_backup._ssh_opts", return_value=[]))
        self.archive = {
            "volid": "local:backup/vzdump-qemu-100-2026_10_23-00_00_00.vma.zst",
            "vmid": 100, "size": 4096, "ctime": 1792713600,
        }
        self.preflight = [
            _ok('[{"vmid":100}]'), _ok("[]"), _ok(_STORAGE_JSON), _ok(_CONTENT_JSON),
        ]
        self.inspect.side_effect = [*self.preflight, _ok(json.dumps([self.archive]))]

    def test_success_requires_new_archive_and_uses_default_bounded_wait(self) -> None:
        # Even an interactive terminal must receive a finite timeout.
        with patch("sys.stdin.isatty", return_value=True):
            create_backup(_host(), 100)
        self.assertEqual(self.execute.call_args.kwargs["timeout"], DEFAULT_BACKUP_TIMEOUT)
        cmd = self.execute.call_args.args[0][-1]
        self.assertEqual(cmd, "vzdump 100 --storage local --mode snapshot --compress zstd --remove 0")
        commands = [call.args[3] for call in self.inspect.call_args_list]
        self.assertIn("/qemu --output-format json", commands[0])
        self.assertIn("/lxc --output-format json", commands[1])
        self.assertEqual(commands[3], commands[4])

    def test_lxc_guest_is_supported(self) -> None:
        self.inspect.side_effect = [_ok("[]"), _ok('[{"vmid":100}]'),
                                    *self.preflight[2:], _ok(json.dumps([self.archive]))]
        create_backup(_host(), 100)
        self.execute.assert_called_once()

    def test_missing_or_other_node_guest_never_starts_backup(self) -> None:
        for guests in ("[]", '[{"vmid":200}]'):
            with self.subTest(guests=guests):
                self.inspect.side_effect = [_ok(guests), _ok("[]")]
                with self.assertRaisesRegex(ProxmoxBackupError, "owning host"):
                    create_backup(_host(), 100)
                self.execute.assert_not_called()

    def test_invalid_or_failed_guest_inventory_never_starts_backup(self) -> None:
        for result in (_ok("null"), _ok('[{}]'), _ok('[{"vmid":0}]'), _fail("permission denied")):
            with self.subTest(result=result):
                self.inspect.side_effect = [result]
                with self.assertRaises(ProxmoxBackupError):
                    create_backup(_host(), 100)
                self.execute.assert_not_called()

    def test_zero_exit_without_new_nonempty_guest_archive_is_failure(self) -> None:
        other_guest = {**self.archive, "vmid": 200}
        empty_archive = {**self.archive, "size": 0}
        for contents in ("[]", _CONTENT_JSON, json.dumps([other_guest]), json.dumps([empty_archive])):
            with self.subTest(contents=contents):
                self.inspect.side_effect = [*self.preflight, _ok(contents)]
                with self.assertRaisesRegex(ProxmoxBackupError, "no new nonempty backup archive"):
                    create_backup(_host(), 100)
        # This models migration after the initial ownership query, including
        # native vzdump's zero-exit, no-task/no-archive behavior.
        self.assertEqual(self.execute.call_count, 4)

    def test_failed_or_invalid_completion_inventory_is_not_success(self) -> None:
        foreign_storage = {**self.archive, "volid": "other:backup/new.vma.zst"}
        invalid_vmid = {**self.archive, "vmid": 100.5}
        for result in (_fail("storage offline"), _ok("{}"), _ok(json.dumps([foreign_storage])),
                       _ok(json.dumps([invalid_vmid]))):
            with self.subTest(result=result):
                self.inspect.side_effect = [*self.preflight, result]
                with self.assertRaisesRegex(ProxmoxBackupError, "Could not verify.*before retrying"):
                    create_backup(_host(), 100)

    def test_failed_initial_archive_inventory_blocks_mutation(self) -> None:
        for result in (_fail("storage offline"), _ok("{}")):
            with self.subTest(result=result):
                self.inspect.side_effect = [*self.preflight[:3], result]
                with self.assertRaises(ProxmoxBackupError):
                    create_backup(_host(), 100)
                self.execute.assert_not_called()

    def test_completion_inventory_timeout_is_unverified(self) -> None:
        self.inspect.side_effect = [*self.preflight, subprocess.TimeoutExpired(["ssh"], 120)]
        with self.assertRaisesRegex(ProxmoxBackupError, "Could not verify.*inspection timed out.*before retrying"):
            create_backup(_host(), 100)
        self.execute.assert_called_once()

    def test_explicit_pool_and_valid_options(self) -> None:
        pool = _ok('[{"storage":"nas","content":"backup","active":1}]')
        archive = {**self.archive, "volid": "nas:backup/new.vma.zst"}
        for mode, compress, timeout in (("snapshot", "zstd", 1), ("suspend", "gzip", 14400),
                                        ("stop", "lzo", 604800), ("snapshot", "0", 86400)):
            with self.subTest(mode=mode, compress=compress, timeout=timeout):
                self.inspect.side_effect = [*self.preflight[:2], pool, _ok("[]"), _ok(json.dumps([archive]))]
                create_backup(_host(), 100, storage="nas", mode=mode, compress=compress, timeout=timeout)
                self.assertIn(f"--storage nas --mode {mode} --compress {compress}",
                              self.execute.call_args.args[0][-1])
                self.assertEqual(self.execute.call_args.kwargs["timeout"], timeout)

    def test_no_backup_storage_or_unavailable_explicit_pool(self) -> None:
        for pool, storage in ((_ok("[]"), None), (_ok(_STORAGE_JSON), "missing")):
            with self.subTest(storage=storage):
                self.inspect.side_effect = [*self.preflight[:2], pool]
                with self.assertRaises(ProxmoxBackupError):
                    create_backup(_host(), 100, storage=storage)
                self.execute.assert_not_called()

    def test_command_failure_is_reported_without_replay(self) -> None:
        self.execute.return_value = _fail("lock failed")
        with self.assertRaisesRegex(ProxmoxBackupError, "vzdump 100 failed.*lock failed"):
            create_backup(_host(), 100)
        self.execute.assert_called_once()
        self.assertEqual(self.inspect.call_count, 4)

    def test_timeout_reports_uncertainty_without_replay_or_cleanup(self) -> None:
        self.execute.side_effect = subprocess.TimeoutExpired(["ssh"], 14400)
        with patch("sys.stdin.isatty", return_value=False):
            with self.assertRaisesRegex(ProxmoxBackupError, "timed out after 14400s.*remote completion is uncertain"):
                create_backup(_host(), 100, timeout=14400)
        self.execute.assert_called_once()
        self.assertEqual(self.execute.call_args.kwargs["timeout"], 14400)
        self.assertEqual(self.inspect.call_count, 4)

    def test_inspection_timeout_is_a_domain_error(self) -> None:
        self.inspect.side_effect = subprocess.TimeoutExpired(["ssh"], 120)
        with self.assertRaisesRegex(ProxmoxBackupError, "inspection timed out"):
            create_backup(_host(), 100)
        self.execute.assert_not_called()

    def test_invalid_options_fail_before_inspection(self) -> None:
        for options in ({"mode": "live"}, {"compress": "bzip2"}, {"storage": "bad/pool"},
                        {"storage": ""}, {"timeout": 0}, {"timeout": -1},
                        {"timeout": 604801}, {"timeout": "nan"}, {"timeout": 1.5}):
            with self.subTest(options=options):
                with self.assertRaises(ValueError):
                    create_backup(_host(), 100, **options)
        for vmid in (0, -1, "bad"):
            with self.subTest(vmid=vmid):
                with self.assertRaises(ValueError):
                    create_backup(_host(), vmid)
        self.inspect.assert_not_called()
        self.execute.assert_not_called()

    def test_dry_run_only_reads_preflight_and_prints_command(self) -> None:
        self.inspect.side_effect = [*self.preflight, _ok()]
        create_backup(_host(), 100, dry_run=True)
        self.execute.assert_not_called()
        self.ssh.assert_not_called()
        self.assertEqual(self.inspect.call_count, 5)
        self.assertTrue(self.inspect.call_args.kwargs["dry_run"])
        self.assertTrue(self.inspect.call_args.args[3].startswith("vzdump 100 "))


if __name__ == "__main__":
    unittest.main()
