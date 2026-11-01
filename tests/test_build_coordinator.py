"""Actual flock and durable markers, with host identities confined to a fixture."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from lib import build_coordinator as coordinator
from lib.setup_directories import directory_fd


class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.root.chmod(0o755)
        self.lock = self.root / "slot.lock"
        self.lock.touch()
        self.lock.chmod(0o660)
        self.enterContext(patch.object(coordinator, "_directory", side_effect=lambda: directory_fd(str(self.root))))
        original = os.fstat

        def root_stat(descriptor):
            info = original(descriptor)
            return SimpleNamespace(st_uid=0, st_gid=info.st_gid, st_mode=info.st_mode,
                                   st_nlink=info.st_nlink, st_size=info.st_size)

        self.enterContext(patch.object(coordinator.os, "fstat", side_effect=root_stat))
        self.enterContext(patch.object(coordinator.os, "geteuid", return_value=0))
        self.enterContext(patch.object(coordinator.grp, "getgrnam", return_value=SimpleNamespace(gr_gid=os.getgid())))

    def test_busy_producers_cannot_overlap_and_success_releases_durable_marker(self):
        with coordinator.host_slot():
            self.assertTrue(self.lock.read_bytes())
            with self.assertRaises(coordinator.CoordinatorBusy):
                with coordinator.host_slot():
                    self.fail("capacity overlapped")
        self.assertEqual(self.lock.read_bytes(), b"")
        with coordinator.host_slot():
            pass

    def test_install_preserves_capacity_inode_and_enables_shared_boot_recovery(self):
        inode = self.lock.stat().st_ino
        with patch.object(coordinator.os, "fchown"), \
             patch.object(coordinator, "DIRECTORY", str(self.root)), \
             patch("lib.unit_transaction.replace_units") as units:
            coordinator.ensure_coordinator()
        self.assertEqual(self.lock.stat().st_ino, inode)
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o755)
        self.assertEqual(units.call_args.kwargs["activate"], (coordinator.RECOVERY_SERVICE,))
        self.assertIn("Type=oneshot", units.call_args.args[0][coordinator.RECOVERY_SERVICE])
        self.assertIn("build_capacity_recovery.py", units.call_args.args[0][coordinator.RECOVERY_SERVICE])

    def test_crash_marker_requires_recovery_instead_of_releasing_capacity(self):
        with self.assertRaisesRegex(RuntimeError, "interrupted"):
            with coordinator.host_slot():
                raise RuntimeError("interrupted")
        with self.assertRaisesRegex(ValueError, "recovery"):
            with coordinator.host_slot():
                self.fail("unconfirmed work was ignored")

    def test_busy_maintenance_leaves_drain_and_can_be_retried_after_work_stops(self):
        with coordinator.host_slot():
            with self.assertRaises(coordinator.CoordinatorBusy):
                with coordinator.maintenance():
                    self.fail("active work was interrupted")
        self.assertTrue(coordinator.drained())
        with self.assertRaises(coordinator.CoordinatorBusy):
            with coordinator.host_slot():
                pass
        with coordinator.maintenance():
            self.assertTrue(coordinator.drained())
        self.assertFalse(coordinator.drained())

    def test_unsafe_inode_and_permissions_are_refused(self):
        self.lock.chmod(0o666)
        with self.assertRaises(ValueError):
            coordinator.open_slot()
        self.lock.unlink()
        self.lock.symlink_to(self.root / "missing")
        with self.assertRaises((OSError, ValueError)):
            coordinator.open_slot()

    def test_nested_setup_maintenance_retains_outer_ownership(self):
        with coordinator.maintenance():
            with coordinator.maintenance():
                self.assertTrue(coordinator.drained())
            self.assertTrue(coordinator.drained())
        self.assertFalse(coordinator.drained())

    def test_runtime_activation_leaves_drain_until_setup_completes(self):
        with coordinator.maintenance(release_drain=False):
            self.assertTrue(coordinator.drained())
        self.assertTrue(coordinator.drained())
        with self.assertRaises(coordinator.CoordinatorBusy):
            with coordinator.host_slot():
                self.fail("work started between activation and setup")
        with coordinator.maintenance():
            self.assertTrue(coordinator.drained())
        self.assertFalse(coordinator.drained())

    def test_setup_cannot_clear_pending_reboot_or_unknown_drain_intent(self):
        drain = self.root / "drain"
        drain.write_bytes(b"basaltwater-reboot:00000000-0000-0000-0000-000000000001\n")
        drain.chmod(0o600)
        for content in (drain.read_bytes(), b"unknown retained intent"):
            drain.write_bytes(content)
            with self.assertRaisesRegex(ValueError, "recovery"):
                with coordinator.maintenance():
                    self.fail("setup overlapped pending shutdown")
            self.assertEqual(drain.read_bytes(), content)

    def test_scheduled_maintenance_defers_to_builds_and_excludes_new_work(self):
        from lib import maintenance_lock as scheduled

        with patch.object(coordinator, "LOCK", str(self.lock)), patch.object(
                scheduled, "SETUP_LOCK_FILE", str(self.root / "setup.lock")):
            with coordinator.host_slot():
                with scheduled.maintenance_lock() as acquired:
                    self.assertFalse(acquired)
            with scheduled.maintenance_lock() as acquired:
                self.assertTrue(acquired)
                with self.assertRaises(coordinator.CoordinatorBusy):
                    with coordinator.host_slot():
                        self.fail("scheduled maintenance overlapped a build")

    def test_explicit_host_restart_refuses_busy_build_then_retains_reboot_drain(self):
        from lib import maintenance_lock as scheduled, setup_reboot

        with patch.object(coordinator, "LOCK", str(self.lock)), \
             patch.object(scheduled, "SETUP_LOCK_FILE", str(self.root / "setup.lock")), \
             patch.object(scheduled, "_local_boot_id", return_value="00000000-0000-0000-0000-000000000001"), \
             patch.object(setup_reboot, "can_restart_system", return_value=True), \
             patch.object(setup_reboot, "run") as reboot:
            with coordinator.host_slot():
                self.assertEqual(setup_reboot._restart_local_host(), 1)
            reboot.assert_not_called()

            def request(*_args, **_kwargs):
                self.assertTrue((self.root / "drain").read_bytes().startswith(b"basaltwater-reboot:"))
                with self.assertRaises(coordinator.CoordinatorBusy), coordinator.host_slot():
                    self.fail("build overlapped reboot")
                with scheduled.maintenance_lock() as acquired:
                    self.assertFalse(acquired)
                return subprocess.CompletedProcess([], 0, "", "")

            reboot.side_effect = request
            self.assertEqual(setup_reboot._restart_local_host(), 0)
            reboot.assert_called_once()
            self.assertTrue((self.root / "drain").exists())
            with self.assertRaises(coordinator.CoordinatorBusy), coordinator.host_slot():
                self.fail("build admitted after reboot request")

    def test_failed_explicit_reboot_retains_intent_and_returns_failure(self):
        from lib import maintenance_lock as scheduled, setup_reboot

        with patch.object(coordinator, "LOCK", str(self.lock)), \
             patch.object(scheduled, "SETUP_LOCK_FILE", str(self.root / "setup.lock")), \
             patch.object(scheduled, "_local_boot_id", return_value="00000000-0000-0000-0000-000000000001"), \
             patch.object(setup_reboot, "can_restart_system", return_value=True), \
             patch.object(setup_reboot, "run", return_value=subprocess.CompletedProcess([], 1, "", "refused")):
            self.assertEqual(setup_reboot._restart_local_host(), 1)
            self.assertTrue((self.root / "drain").exists())
            with self.assertRaises(coordinator.CoordinatorBusy), coordinator.host_slot():
                self.fail("failed reboot silently reopened capacity")


class LegacyWorkerGuardTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.unit = self.root / "etc/systemd/system/basaltwater-build-host.service"
        self.unit.parent.mkdir(parents=True)
        self.unit.write_text("[Service]\nExecStart=installed-worker\n")
        self.query = self.enterContext(patch.object(coordinator, "run"))

    def test_active_transitional_and_uncertain_queries_refuse_without_stopping(self):
        for state in ("active", "activating", "deactivating", "reloading", "unknown", ""):
            for status in (0, 1):
                with self.subTest(state=state, status=status):
                    self.query.return_value = subprocess.CompletedProcess(
                        [], status, f"LoadState=loaded\nActiveState={state}\n", "",
                    )
                    with self.assertRaisesRegex(RuntimeError, "legacy managed build-host"):
                        coordinator.reject_active_legacy_worker(self.root)
        self.assertTrue(all(call.args[0][:2] == ["systemctl", "show"] for call in self.query.call_args_list))
        self.query.side_effect = subprocess.TimeoutExpired("systemctl", 30)
        with self.assertRaisesRegex(RuntimeError, "Cannot confirm"):
            coordinator.reject_active_legacy_worker(self.root)

    def test_confirmed_stopped_worker_permits_first_upgrade(self):
        for state in ("inactive", "failed"):
            self.query.return_value = subprocess.CompletedProcess([], 0, f"LoadState=loaded\nActiveState={state}\n", "")
            coordinator.reject_active_legacy_worker(self.root)

    def test_only_installed_worker_with_leased_execution_can_bypass_query(self):
        installed = self.root / "opt/basaltwater/common/service_tools/build_host.py"
        installed.parent.mkdir(parents=True)
        lock = self.root / coordinator.LOCK.lstrip("/")
        lock.parent.mkdir(parents=True)
        lock.touch()
        self.query.return_value = subprocess.CompletedProcess([], 0, "LoadState=loaded\nActiveState=active\n", "")
        for source in ("# host_slot run_next\n", "def run_worker():\n    run_next()\n", "invalid python!"):
            installed.write_text(source)
            with self.assertRaises(RuntimeError):
                coordinator.reject_active_legacy_worker(self.root)
        installed.write_text((Path(__file__).parents[1] / "common/service_tools/build_host.py").read_text())
        self.query.reset_mock()
        coordinator.reject_active_legacy_worker(self.root)
        self.query.assert_not_called()
