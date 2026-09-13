"""Tests for lib/proxmox_storage.py."""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lib.proxmox_hosts import ProxmoxHost
from lib.proxmox_storage import (
    OrphanedVolume,
    ProxmoxStorageError,
    _parse_pvesm_list,
    delete_volume,
    list_orphaned_volumes,
)


def _host() -> ProxmoxHost:
    return ProxmoxHost(name="pve1", address="10.0.0.10")


def _ok(stdout: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], 0, stdout, "")


def _fail(stderr: str = "error") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], 1, "", stderr)


_PVESM_STATUS = (
    "Name             Type       Status           Total            Used       Available        %\n"
    "local            dir        active       103796736        47669248        56127488   45.93%\n"
    "local-lvm        lvmthin    active      1073741824       214748364       858993459   20.00%\n"
)

_PVESM_LIST_LOCAL_LVM = (
    "Volid                          Format  Type      Size VMID\n"
    "local-lvm:vm-100-disk-0        raw     images    10G  100\n"
    "local-lvm:vm-999-disk-0        raw     images    20G  999\n"  # orphan — no VMID 999
    "local-lvm:vm-100-disk-1        raw     rootdir    5G  100\n"
)

_QM_LIST = "VMID NAME STATUS\n100 myvm running\n"
_PCT_LIST = "VMID Status Name\n"   # no containers


class TestParsePvesmList(unittest.TestCase):
    def test_parses_guest_disk_entries(self) -> None:
        entries = _parse_pvesm_list(_PVESM_LIST_LOCAL_LVM)
        self.assertEqual(len(entries), 3)
        volids = [e[0] for e in entries]
        self.assertIn("local-lvm:vm-100-disk-0", volids)
        self.assertIn("local-lvm:vm-999-disk-0", volids)

    def test_skips_non_guest_content(self) -> None:
        stdout = (
            "Volid                  Format  Type     Size VMID\n"
            "local:iso/debian.iso   iso     iso       1G  \n"
            "local-lvm:vm-100-disk-0 raw    images   10G  100\n"
        )
        entries = _parse_pvesm_list(stdout)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0][0], "local-lvm:vm-100-disk-0")

    def test_skips_lines_without_vmid(self) -> None:
        stdout = (
            "Volid  Format Type Size VMID\n"
            "local-lvm:base-9000-disk-0 raw images 10G\n"
        )
        entries = _parse_pvesm_list(stdout)
        self.assertEqual(entries, [])


class TestListOrphanedVolumes(unittest.TestCase):
    @patch("lib.proxmox_storage._ssh_run")
    def test_identifies_orphaned_volume(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = [
            _ok(_QM_LIST),     # qm list
            _ok(_PCT_LIST),    # pct list
            _ok('[{"vmid": 100}]'),
            _ok(_PVESM_STATUS),
            _ok(_PVESM_LIST_LOCAL_LVM),  # local
            _ok(_PVESM_LIST_LOCAL_LVM),  # local-lvm
        ]
        orphans = list_orphaned_volumes(_host())
        vmids = [o.vmid for o in orphans]
        self.assertIn(999, vmids)
        self.assertNotIn(100, vmids)

    @patch("lib.proxmox_storage._ssh_run")
    def test_returns_empty_when_no_orphans(self, mock_run: MagicMock) -> None:
        no_orphan_list = (
            "Volid                          Format  Type    Size VMID\n"
            "local-lvm:vm-100-disk-0        raw     images  10G  100\n"
        )
        mock_run.side_effect = [
            _ok(_QM_LIST),
            _ok(_PCT_LIST),
            _ok('[{"vmid": 100}]'),
            _ok(_PVESM_STATUS),
            _ok(no_orphan_list),
            _ok(no_orphan_list),
        ]
        orphans = list_orphaned_volumes(_host())
        self.assertEqual(orphans, [])

    @patch("lib.proxmox_storage._ssh_run")
    def test_raises_on_pvesm_status_failure(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = [_ok(_QM_LIST), _ok(_PCT_LIST), _ok('[]'), _fail()]
        with self.assertRaises(ProxmoxStorageError):
            list_orphaned_volumes(_host())

    def test_incomplete_inventory_never_returns_orphans(self) -> None:
        success = [_ok(_QM_LIST), _ok(_PCT_LIST), _ok('[]'), _ok(_PVESM_STATUS), _ok(_PVESM_LIST_LOCAL_LVM), _ok(_PVESM_LIST_LOCAL_LVM)]
        for index in range(len(success)):
            results = success[:index] + [_fail()]
            with self.subTest(index=index), patch('lib.proxmox_storage._ssh_run', side_effect=results):
                with self.assertRaises(ProxmoxStorageError):
                    list_orphaned_volumes(_host())

    @patch('lib.proxmox_storage._ssh_run')
    def test_shared_volume_owned_by_other_node_is_not_orphan(self, mock_run):
        mock_run.side_effect = [
            _ok(_QM_LIST), _ok(_PCT_LIST), _ok('[{"vmid": 999}]'),
            _ok(_PVESM_STATUS), _ok(_PVESM_LIST_LOCAL_LVM), _ok(_PVESM_LIST_LOCAL_LVM),
        ]
        self.assertEqual(list_orphaned_volumes(_host()), [])


class TestDeleteVolume(unittest.TestCase):
    def setUp(self):
        scan = patch('lib.proxmox_storage.list_orphaned_volumes', return_value=[
            OrphanedVolume('local-lvm:vm-999-disk-0', 'local-lvm', 999, '20G'),
        ])
        self.scan = scan.start()
        self.addCleanup(scan.stop)

    @patch('lib.proxmox_storage._ssh_run')
    def test_rechecks_before_deleting(self, mock_run):
        self.scan.return_value = []
        with self.assertRaisesRegex(ProxmoxStorageError, 'fresh orphan inventory'):
            delete_volume(_host(), 'local-lvm:vm-999-disk-0')
        mock_run.assert_not_called()

    @patch("lib.proxmox_storage._ssh_run")
    def test_calls_pvesm_free(self, mock_run: MagicMock) -> None:
        mock_run.return_value = _ok()
        delete_volume(_host(), "local-lvm:vm-999-disk-0")
        cmd = mock_run.call_args[0][3]
        self.assertIn("pvesm", cmd)
        self.assertIn("free", cmd)
        self.assertIn("local-lvm:vm-999-disk-0", cmd)

    @patch("lib.proxmox_storage._ssh_run")
    def test_raises_on_failure(self, mock_run: MagicMock) -> None:
        mock_run.return_value = _fail("volume in use")
        with self.assertRaises(ProxmoxStorageError):
            delete_volume(_host(), "local-lvm:vm-999-disk-0")

    @patch("lib.proxmox_storage._ssh_run")
    def test_dry_run_passes_through(self, mock_run: MagicMock) -> None:
        mock_run.return_value = _ok()
        delete_volume(_host(), "local-lvm:vm-999-disk-0", dry_run=True)
        _, kwargs = mock_run.call_args
        self.assertTrue(kwargs.get("dry_run"))


if __name__ == "__main__":
    unittest.main()
