"""Tests for lib/proxmox_migrate.py."""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lib.proxmox_hosts import ProxmoxHost, ProxmoxHostFacts
from lib.proxmox_migrate import ProxmoxMigrateError, migrate_guest


def _host(name: str = "pve1", address: str = "10.0.0.10", node_name: str | None = None) -> ProxmoxHost:
    facts = ProxmoxHostFacts(node_name=node_name) if node_name else None
    return ProxmoxHost(name=name, address=address, facts=facts)


def _ok(stdout: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], 0, stdout, "")


def _fail(stderr: str = "error") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], 1, "", stderr)


class TestMigrateGuest(unittest.TestCase):
    @patch("lib.proxmox_migrate._ssh_run")
    def test_vm_uses_qm_migrate(self, mock_run: MagicMock) -> None:
        # qm status succeeds → it's a VM; qm migrate also succeeds
        mock_run.side_effect = [_ok("status: running"), _ok()]
        src = _host("pve1", "10.0.0.10")
        target = _host("pve2", "10.0.0.11", node_name="pve2")
        migrate_guest(src, 100, target)
        migrate_call = mock_run.call_args_list[-1]
        cmd = migrate_call[0][3]
        self.assertIn("qm", cmd)
        self.assertIn("migrate", cmd)
        self.assertIn("100", cmd)
        self.assertIn("pve2", cmd)

    @patch("lib.proxmox_migrate._ssh_run")
    def test_lxc_uses_pct_migrate(self, mock_run: MagicMock) -> None:
        # LXC must be positively identified before pct migrate.
        mock_run.side_effect = [_fail("not found"), _ok("status: running"), _ok()]
        src = _host("pve1", "10.0.0.10")
        target = _host("pve2", "10.0.0.11", node_name="pve2")
        migrate_guest(src, 100, target)
        migrate_call = mock_run.call_args_list[-1]
        cmd = migrate_call[0][3]
        self.assertIn("pct", cmd)
        self.assertIn("migrate", cmd)
        self.assertIn("--restart", cmd)

    @patch("lib.proxmox_migrate._ssh_run")
    def test_online_flag_added_for_vm(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = [_ok("status: running"), _ok()]
        src = _host("pve1", "10.0.0.10")
        target = _host("pve2", "10.0.0.11", node_name="pve2")
        migrate_guest(src, 100, target, online=True)
        cmd = mock_run.call_args_list[-1][0][3]
        self.assertIn("--online", cmd)

    @patch("lib.proxmox_migrate._ssh_run")
    def test_online_lxc_raises(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = [_fail("not found"), _ok("status: running")]
        src = _host()
        target = _host("pve2", "10.0.0.11", node_name="pve2")
        with self.assertRaises(ProxmoxMigrateError):
            migrate_guest(src, 100, target, online=True)

    @patch("lib.proxmox_migrate._ssh_run")
    def test_failed_guest_lookup_does_not_attempt_migration(self, mock_run):
        mock_run.return_value = _fail("SSH unavailable")
        with self.assertRaisesRegex(ProxmoxMigrateError, "Could not identify guest"):
            migrate_guest(_host(), 100, _host("pve2", "10.0.0.11", node_name="pve2"))
        self.assertEqual(mock_run.call_count, 2)
        self.assertFalse(any("migrate" in call.args[3] for call in mock_run.call_args_list))

    @patch("lib.proxmox_migrate._get_node_name", return_value="")
    @patch("lib.proxmox_migrate._ssh_run")
    def test_unresolved_destination_is_not_replaced_with_registry_alias(self, mock_run, _name):
        with self.assertRaisesRegex(ProxmoxMigrateError, "Could not resolve"):
            migrate_guest(_host(), 100, _host("friendly-alias", "10.0.0.11"))
        mock_run.assert_not_called()

    @patch("lib.proxmox_migrate._ssh_run")
    def test_raises_on_migrate_failure(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = [_ok(), _fail("insufficient memory")]
        src = _host()
        target = _host("pve2", "10.0.0.11", node_name="pve2")
        with self.assertRaises(ProxmoxMigrateError):
            migrate_guest(src, 100, target)

    @patch("lib.proxmox_migrate._ssh_run")
    def test_resolves_node_name_from_facts(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = [_ok(), _ok()]
        src = _host()
        target = _host("pve2", "10.0.0.11", node_name="proxmox-node-2")
        migrate_guest(src, 100, target)
        cmd = mock_run.call_args_list[-1][0][3]
        self.assertIn("proxmox-node-2", cmd)

    @patch("lib.proxmox_migrate._get_node_name", return_value="pve2-live")
    @patch("lib.proxmox_migrate._ssh_run")
    def test_falls_back_to_live_node_name(
        self, mock_run: MagicMock, mock_name: MagicMock
    ) -> None:
        mock_run.side_effect = [_ok(), _ok()]
        src = _host()
        target = _host("pve2", "10.0.0.11")   # no facts → live lookup
        migrate_guest(src, 100, target)
        cmd = mock_run.call_args_list[-1][0][3]
        self.assertIn("pve2-live", cmd)

    @patch("lib.proxmox_migrate._ssh_run")
    def test_dry_run_passes_through(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = [_ok(), _ok()]
        src = _host()
        target = _host("pve2", "10.0.0.11", node_name="pve2")
        migrate_guest(src, 100, target, dry_run=True)
        _, kwargs = mock_run.call_args
        self.assertTrue(kwargs.get("dry_run"))


if __name__ == "__main__":
    unittest.main()
