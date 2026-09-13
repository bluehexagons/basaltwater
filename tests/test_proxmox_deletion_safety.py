"""Confirmation and dry-run guards for destructive Proxmox commands."""

from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from lib import proxmox_cli, proxmox_shell
from lib.proxmox_hosts import ProxmoxHost
from lib.proxmox_storage import OrphanedVolume


class TestDeletionSafety(unittest.TestCase):
    def test_cli_snapshot_requires_confirmation_or_yes(self):
        host = ProxmoxHost('pve', 'pve.example.com')
        for answer, yes, dry_run, expected in [('no', False, False, 1), ('yes', False, False, 0), ('', True, False, 0), ('', False, True, 0)]:
            args = SimpleNamespace(host='pve', vmid=100, name='backup', yes=yes, dry_run=dry_run)
            with self.subTest(answer=answer, yes=yes, dry_run=dry_run), patch.object(proxmox_cli, '_resolve_host', return_value=host), patch('builtins.input', return_value=answer), patch.object(proxmox_cli, 'delete_snapshot') as delete:
                self.assertEqual(proxmox_cli._cmd_delsnapshot(args, None), expected)
                self.assertEqual(delete.called, expected == 0)

    def test_shell_refusal_and_cleanup_dry_run_do_not_delete(self):
        shell = proxmox_shell.ProxmoxShell(input_func=lambda _: 'no', output_func=Mock())
        shell.state.active_host = ProxmoxHost('pve', 'pve.example.com')
        with patch.object(proxmox_shell, 'delete_snapshot') as snapshot:
            shell.dispatch('delsnapshot 100 backup')
            snapshot.assert_not_called()
        with patch.object(proxmox_shell, 'list_orphaned_volumes', return_value=[OrphanedVolume('pool:disk', 'pool', 999, '1G')]), patch.object(proxmox_shell, 'delete_volume') as volume:
            shell.dispatch('clean-disks --delete --yes --dry-run')
            volume.assert_not_called()
