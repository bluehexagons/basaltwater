"""Tests for lib/proxmox_migrate.py."""

from __future__ import annotations

import os
import io
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import ANY, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lib.proxmox_hosts import ProxmoxHost, ProxmoxHostFacts
from lib.proxmox_migrate import ProxmoxMigrateError, migrate_guest


def _host(name: str = "pve1", address: str = "10.0.0.10", node_name: str | None = None) -> ProxmoxHost:
    facts = ProxmoxHostFacts(node_name=node_name) if node_name else None
    return ProxmoxHost(name=name, address=address, facts=facts)


def _ok(stdout: str = "status: stopped") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], 0, stdout, "")


def _fail(stderr: str = "error") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], 1, "", stderr)


class TestMigrateGuest(unittest.TestCase):
    def setUp(self) -> None:
        node_lookup = patch(
            "lib.proxmox_migrate._get_node_name",
            side_effect=lambda address, *_args: "pve1" if address == "10.0.0.10" else "pve2",
        )
        self.node_lookup = node_lookup.start()
        self.addCleanup(node_lookup.stop)

    @staticmethod
    def _migration_command(mock_run: MagicMock) -> str:
        commands = [call.args[3] for call in mock_run.call_args_list if " migrate " in call.args[3]]
        assert len(commands) == 1, commands
        return commands[0]

    @patch("lib.proxmox_migrate._ssh_run")
    def test_vm_uses_qm_migrate(self, mock_run: MagicMock) -> None:
        # qm status succeeds → it's a VM; qm migrate also succeeds
        mock_run.side_effect = [_ok("status: stopped"), _ok(), _ok()]
        src = _host("pve1", "10.0.0.10")
        target = _host("pve2", "10.0.0.11", node_name="pve2")
        migrate_guest(src, 100, target)
        cmd = self._migration_command(mock_run)
        self.assertIn("qm", cmd)
        self.assertIn("migrate", cmd)
        self.assertIn("100", cmd)
        self.assertIn("pve2", cmd)

    @patch("lib.proxmox_migrate._ssh_run")
    def test_lxc_uses_pct_migrate(self, mock_run: MagicMock) -> None:
        # LXC must be positively identified before pct migrate.
        mock_run.side_effect = [_fail("not found"), _ok("status: running"), _ok(), _ok("status: running")]
        src = _host("pve1", "10.0.0.10")
        target = _host("pve2", "10.0.0.11", node_name="pve2")
        migrate_guest(src, 100, target)
        cmd = self._migration_command(mock_run)
        self.assertIn("pct", cmd)
        self.assertIn("migrate", cmd)
        self.assertIn("--restart", cmd)

    @patch("lib.proxmox_migrate._ssh_run")
    def test_online_flag_added_for_vm(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = [_ok("status: running"), _ok(), _ok("status: running")]
        src = _host("pve1", "10.0.0.10")
        target = _host("pve2", "10.0.0.11", node_name="pve2")
        migrate_guest(src, 100, target, online=True)
        cmd = self._migration_command(mock_run)
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
    def test_stale_cached_node_name_is_not_used(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = [_ok(), _ok(), _ok()]
        src = _host()
        target = _host("pve2", "10.0.0.11", node_name="proxmox-node-2")
        migrate_guest(src, 100, target)
        cmd = self._migration_command(mock_run)
        self.assertIn("pve2", cmd)
        self.assertNotIn("proxmox-node-2", cmd)
        self.node_lookup.assert_any_call(target.address, target.user, ANY)

    @patch("lib.proxmox_migrate._get_node_name", side_effect=["pve1", "pve2-live"])
    @patch("lib.proxmox_migrate._ssh_run")
    def test_falls_back_to_live_node_name(
        self, mock_run: MagicMock, mock_name: MagicMock
    ) -> None:
        mock_run.side_effect = [_ok(), _ok(), _ok()]
        src = _host()
        target = _host("pve2", "10.0.0.11")   # no facts → live lookup
        migrate_guest(src, 100, target)
        cmd = self._migration_command(mock_run)
        self.assertIn("pve2-live", cmd)

    @patch("lib.proxmox_migrate._ssh_run")
    def test_dry_run_passes_through(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = [_ok(), _ok()]
        src = _host()
        target = _host("pve2", "10.0.0.11", node_name="pve2")
        migrate_guest(src, 100, target, dry_run=True)
        _, kwargs = mock_run.call_args
        self.assertTrue(kwargs.get("dry_run"))

    @patch("lib.proxmox_migrate._storage_pool_supports_content", return_value=True)
    @patch("lib.proxmox_migrate._ssh_run")
    def test_offline_vm_maps_local_disks_to_destination_pool(self, mock_run, mock_storage):
        mock_run.side_effect = [_ok(), _ok(), _ok()]
        target = _host("pve2", "10.0.0.11")
        migrate_guest(_host(), 100, target, target_storage="local", timeout=14400)
        self.assertEqual(self._migration_command(mock_run), "qm migrate 100 pve2 --targetstorage local")
        mock_storage.assert_called_once_with(
            "local", "images", target.address, target.user, ANY, strict=True,
        )
        self.assertEqual(mock_run.call_args_list[1].kwargs["timeout"], 14400)
        self.assertEqual(mock_run.call_args_list[-1].args[0], target.address)
        self.assertEqual(mock_run.call_args_list[-1].args[3], "qm status 100")

    @patch("lib.proxmox_migrate._storage_pool_supports_content", return_value=True)
    @patch("lib.proxmox_migrate._ssh_run")
    def test_online_vm_transfers_local_disks(self, mock_run, _storage):
        mock_run.side_effect = [_ok("status: running"), _ok(), _ok("status: running")]
        migrate_guest(_host(), 100, _host("pve2", "10.0.0.11"), online=True,
                      with_local_disks=True, target_storage="local")
        self.assertEqual(self._migration_command(mock_run),
                         "qm migrate 100 pve2 --online 1 --with-local-disks 1 --targetstorage local")

    @patch("lib.proxmox_migrate._storage_pool_supports_content", return_value=True)
    @patch("lib.proxmox_migrate._ssh_run")
    def test_lxc_uses_native_hyphenated_storage_option(self, mock_run, mock_storage):
        mock_run.side_effect = [_fail(), _ok(), _ok(), _ok()]
        migrate_guest(_host(), 100, _host("pve2", "10.0.0.11"), target_storage="local")
        self.assertEqual(self._migration_command(mock_run),
                         "pct migrate 100 pve2 --restart 1 --target-storage local")
        self.assertEqual(mock_storage.call_args.args[1], "rootdir")

    @patch("lib.proxmox_migrate._storage_pool_supports_content", return_value=False)
    @patch("lib.proxmox_migrate._ssh_run")
    def test_unavailable_or_wrong_content_pool_blocks_even_dry_run(self, mock_run, _storage):
        for dry_run in (False, True):
            with self.subTest(dry_run=dry_run):
                mock_run.reset_mock()
                mock_run.return_value = _ok()
                with self.assertRaisesRegex(ProxmoxMigrateError, "does not support VM images"):
                    migrate_guest(_host(), 100, _host("pve2", "10.0.0.11"),
                                  target_storage="local", dry_run=dry_run)
                self.assertEqual(mock_run.call_count, 1)

    @patch("lib.proxmox_migrate._ssh_run")
    def test_invalid_inputs_are_rejected_before_connecting(self, mock_run):
        for options in ({"vmid": 0}, {"vmid": -1}, {"vmid": True},
                        {"target_storage": "--online"}, {"target_storage": "pool\n"},
                        {"target_storage": "pool; touch /tmp/unwanted"},
                        {"target_storage": "1"},
                        {"timeout": 0}, {"timeout": -1}):
            with self.subTest(options=options):
                with self.assertRaises(ValueError):
                    migrate_guest(_host(), target_host=_host("pve2", "10.0.0.11"),
                                  **{"vmid": 100, **options})
        mock_run.assert_not_called()
        self.node_lookup.assert_not_called()

    @patch("lib.proxmox_migrate._ssh_run")
    def test_invalid_connection_identity_is_rejected_before_connecting(self, mock_run):
        for host in (_host(address="--help"), _host(address="node\n")):
            with self.subTest(host=host):
                with self.assertRaisesRegex(ProxmoxMigrateError, "Invalid Proxmox host"):
                    migrate_guest(_host(), 100, host)
        target = _host("pve2", "10.0.0.11")
        target.user = "root;bad"
        with self.assertRaisesRegex(ProxmoxMigrateError, "Invalid Proxmox host"):
            migrate_guest(_host(), 100, target)
        mock_run.assert_not_called()
        self.node_lookup.assert_not_called()

    @patch("lib.proxmox_migrate._storage_pool_supports_content", return_value=True)
    @patch("lib.proxmox_migrate._ssh_run")
    def test_dry_run_prints_full_command_and_skips_destination_verification(self, mock_run, _storage):
        mock_run.side_effect = [_ok("status: running"), _ok()]
        output = io.StringIO()
        storage = "destination-" + "a" * 80
        with redirect_stdout(output):
            migrate_guest(_host(), 100, _host("pve2", "10.0.0.11"),
                          online=True, with_local_disks=True,
                          target_storage=storage, dry_run=True)
        self.assertIn(f"--targetstorage {storage}", output.getvalue())
        self.assertEqual(mock_run.call_count, 2)
        self.assertFalse(mock_run.call_args_list[0].kwargs["dry_run"])
        self.assertTrue(mock_run.call_args_list[1].kwargs["dry_run"])

    @patch("lib.proxmox_migrate._ssh_run")
    def test_source_host_and_node_aliases_are_rejected(self, mock_run):
        with self.assertRaisesRegex(ProxmoxMigrateError, "source host"):
            migrate_guest(_host(), 100, _host("alias"))
        self.node_lookup.return_value = "pve1"
        self.node_lookup.side_effect = None
        with self.assertRaisesRegex(ProxmoxMigrateError, "source node"):
            migrate_guest(_host(), 100, _host("alias", "10.0.0.11"))
        mock_run.assert_not_called()

    @patch("lib.proxmox_migrate._ssh_run")
    def test_invalid_live_node_name_is_rejected(self, mock_run):
        self.node_lookup.side_effect = ["pve1", "--online"]
        with self.assertRaisesRegex(ProxmoxMigrateError, "Could not resolve"):
            migrate_guest(_host(), 100, _host("pve2", "10.0.0.11"))
        mock_run.assert_not_called()

    @patch("lib.proxmox_migrate._ssh_run")
    def test_lxc_does_not_silently_ignore_local_disk_flag(self, mock_run):
        mock_run.side_effect = [_fail(), _ok()]
        with self.assertRaisesRegex(ProxmoxMigrateError, "only for QEMU"):
            migrate_guest(_host(), 100, _host("pve2", "10.0.0.11"), with_local_disks=True)
        self.assertEqual(mock_run.call_count, 2)

    @patch("lib.proxmox_migrate._ssh_run")
    def test_default_wait_is_one_hour_and_lost_results_are_not_replayed(self, mock_run):
        for error in (subprocess.TimeoutExpired("ssh", 3600), OSError("connection lost"), KeyboardInterrupt()):
            with self.subTest(error=error):
                mock_run.reset_mock()
                mock_run.side_effect = [_ok(), error]
                with self.assertRaisesRegex(ProxmoxMigrateError, "task may still be running"):
                    migrate_guest(_host(), 100, _host("pve2", "10.0.0.11"))
                self.assertEqual(mock_run.call_count, 2)
                self.assertEqual(mock_run.call_args.kwargs["timeout"], 3600)

    @patch("lib.proxmox_migrate._ssh_run")
    def test_inspection_timeout_does_not_attempt_migration(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired("ssh", 120)
        with self.assertRaisesRegex(ProxmoxMigrateError, "migration was not attempted"):
            migrate_guest(_host(), 100, _host("pve2", "10.0.0.11"))
        self.assertEqual(mock_run.call_count, 1)

    @patch("lib.proxmox_migrate._ssh_run")
    def test_destination_verification_failure_is_reported_without_replay(self, mock_run):
        for status in (_fail(), _ok(""), _ok("unexpected"), subprocess.TimeoutExpired("ssh", 120)):
            with self.subTest(status=status):
                mock_run.reset_mock()
                mock_run.side_effect = [_ok(), _ok(), status]
                with self.assertRaisesRegex(ProxmoxMigrateError, "Inspect both nodes"):
                    migrate_guest(_host(), 100, _host("pve2", "10.0.0.11"))
                self.assertEqual(mock_run.call_count, 3)
                self._migration_command(mock_run)


if __name__ == "__main__":
    unittest.main()
