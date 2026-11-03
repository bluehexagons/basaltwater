"""Exercise node-local setup/maintenance exclusion using temporary lock files."""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from lib.maintenance_lock import maintenance_lock
from lib import maintenance_lock as scheduled, build_coordinator as coordinator
from lib.setup_directories import directory_fd


class TestMaintenanceLock(unittest.TestCase):
    def test_bounded_wait_acquires_released_setup_lock_without_replacing_inode(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "setup.lock")
            with open(path, "a+") as setup, \
                    patch.object(scheduled, "SETUP_LOCK_FILE", path), \
                    patch.object(coordinator.os.path, "lexists", return_value=False), \
                    patch.object(scheduled.time, "monotonic", return_value=0), \
                    patch.object(scheduled.time, "sleep", side_effect=lambda _: fcntl.flock(setup, fcntl.LOCK_UN)) as wait:
                fcntl.flock(setup, fcntl.LOCK_EX | fcntl.LOCK_NB)
                inode = os.stat(path).st_ino
                with maintenance_lock(wait_seconds=60) as acquired:
                    self.assertTrue(acquired)
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(setup, fcntl.LOCK_EX | fcntl.LOCK_NB)
                wait.assert_called_once()
                self.assertEqual(os.stat(path).st_ino, inode)

    def test_bounded_wait_expires_without_disturbing_setup_owner(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "setup.lock")
            with open(path, "a+") as setup, \
                    patch.object(scheduled, "SETUP_LOCK_FILE", path), \
                    patch.object(scheduled.time, "monotonic", side_effect=(0, 0.9, 1)), \
                    patch.object(scheduled.time, "sleep") as wait:
                fcntl.flock(setup, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with maintenance_lock(wait_seconds=1) as acquired:
                    self.assertFalse(acquired)
                wait.assert_called_once()
                self.assertAlmostEqual(wait.call_args.args[0], 0.1)
                with maintenance_lock() as acquired:
                    self.assertFalse(acquired)

    def test_setup_excludes_maintenance_and_release_keeps_inode(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "setup.lock")
            with patch("lib.maintenance_lock.SETUP_LOCK_FILE", path):
                with open(path, "a+") as setup:
                    fcntl.flock(setup, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    with maintenance_lock() as acquired:
                        self.assertFalse(acquired)
                inode = os.stat(path).st_ino
                with maintenance_lock() as acquired:
                    self.assertTrue(acquired)
                    with maintenance_lock() as overlapping:
                        self.assertFalse(overlapping)
                self.assertEqual(os.stat(path).st_ino, inode)
                with maintenance_lock() as acquired:
                    self.assertTrue(acquired)

    def test_symlink_and_special_files_are_refused(self):
        with tempfile.TemporaryDirectory() as root:
            target = os.path.join(root, "target")
            with open(target, "w") as handle:
                handle.write("unchanged")
            link, fifo = os.path.join(root, "link"), os.path.join(root, "fifo")
            os.symlink(target, link)
            os.mkfifo(fifo)
            for path in (link, fifo):
                with self.subTest(path=path), patch("lib.maintenance_lock.SETUP_LOCK_FILE", path):
                    with self.assertRaises((OSError, ValueError)):
                        with maintenance_lock():
                            self.fail("Unsafe lock accepted")


class BuilderMaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.root.chmod(0o755)
        self.slot = self.root / "slot.lock"
        self.slot.touch(mode=0o660)
        self.slot.chmod(0o660)
        self.drain = self.root / "drain"
        original = os.fstat

        def root_stat(descriptor):
            info = original(descriptor)
            return SimpleNamespace(st_uid=0, st_gid=info.st_gid, st_mode=info.st_mode,
                                   st_nlink=info.st_nlink, st_size=info.st_size,
                                   st_dev=info.st_dev, st_ino=info.st_ino)

        self.enterContext(patch.object(os, "fstat", side_effect=root_stat))
        self.enterContext(patch.object(os, "geteuid", return_value=0))
        self.enterContext(patch.object(coordinator.grp, "getgrnam", return_value=SimpleNamespace(gr_gid=os.getgid())))
        self.enterContext(patch.object(coordinator, "_directory", side_effect=lambda: directory_fd(str(self.root))))
        self.enterContext(patch.object(coordinator, "LOCK", str(self.slot)))
        self.enterContext(patch.object(scheduled, "SETUP_LOCK_FILE", str(self.root / "setup.lock")))
        self.boot = self.enterContext(patch.object(scheduled, "_local_boot_id", return_value="00000000-0000-0000-0000-000000000001"))

    def schedule(self):
        with maintenance_lock() as acquired:
            self.assertTrue(acquired)
            scheduled.retain_reboot_drain()

    def test_grace_blocks_new_builds_and_next_boot_recovers_idle_capacity(self):
        self.schedule()
        self.assertEqual(self.slot.read_bytes(), b"")
        with self.assertRaises(coordinator.CoordinatorBusy), coordinator.host_slot():
            self.fail("build admitted during reboot grace")
        with maintenance_lock() as acquired:
            self.assertFalse(acquired)
        self.boot.return_value = "00000000-0000-0000-0000-000000000002"
        with maintenance_lock() as acquired:
            self.assertTrue(acquired)
        self.assertFalse(self.drain.exists())

    def test_boot_recovery_and_cancellation_preserve_uncertain_build_lease(self):
        self.schedule()
        self.slot.write_bytes(b"uncertain build")
        self.boot.return_value = "00000000-0000-0000-0000-000000000002"
        for cancel in (False, True):
            self.assertFalse(scheduled.recover_reboot_drain(cancel=cancel))
            self.assertTrue(self.drain.exists())
            self.assertEqual(self.slot.read_bytes(), b"uncertain build")

    def test_shutdown_failure_retains_drain_and_does_not_clear_unrelated_drains(self):
        from common.service_tools import auto_restart_if_needed as restart
        import subprocess

        with patch.object(restart.shutil, "which", return_value="/sbin/shutdown"), \
                patch.object(restart, "send_notification_safe"), \
                patch.object(restart.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "shutdown")):
            with maintenance_lock() as acquired:
                self.assertTrue(acquired)
                self.assertEqual(restart.perform_restart([], 5), 1)
        self.assertTrue(self.drain.exists())
        self.assertEqual(self.slot.read_bytes(), b"")
        self.drain.write_bytes(b"unrelated maintenance")
        self.boot.return_value = "00000000-0000-0000-0000-000000000002"
        self.assertFalse(scheduled.recover_reboot_drain(cancel=True))
        self.assertEqual(self.drain.read_bytes(), b"unrelated maintenance")

    def test_malformed_reboot_drain_is_retained(self):
        self.schedule()
        self.drain.write_bytes(self.drain.read_bytes() + b" " * 200)
        self.boot.return_value = "00000000-0000-0000-0000-000000000002"
        with self.assertRaises(ValueError):
            scheduled.recover_reboot_drain()
        self.assertTrue(self.drain.exists())

    def test_explicit_cancellation_clears_only_after_shutdown_cancel_succeeds(self):
        from common.service_tools import auto_restart_if_needed as restart
        self.schedule()
        with patch.object(restart.shutil, "which", return_value="/sbin/shutdown"), \
                patch.object(restart.subprocess, "run") as run:
            run.side_effect = OSError("uncertain cancellation")
            with self.assertRaises(OSError):
                restart.cancel_scheduled_restart()
            self.assertTrue(self.drain.exists())
            run.side_effect = None
            self.assertEqual(restart.cancel_scheduled_restart(), 0)
            run.assert_called_with(["/sbin/shutdown", "-c"], check=True)
        self.assertFalse(self.drain.exists())

    def test_local_shutdown_cancel_cannot_release_external_provider_power_intent(self):
        from common.service_tools import auto_restart_if_needed as restart
        with maintenance_lock() as acquired:
            self.assertTrue(acquired)
            scheduled.retain_reboot_drain(external=True)
        with patch.object(restart.shutil, "which", return_value="/sbin/shutdown"), \
                patch.object(restart.subprocess, "run"):
            self.assertEqual(restart.cancel_scheduled_restart(), 0)
        self.assertTrue(self.drain.exists())
        self.boot.return_value = "00000000-0000-0000-0000-000000000002"
        with maintenance_lock() as acquired:
            self.assertTrue(acquired)
        self.assertFalse(self.drain.exists())

    def test_scheduled_apt_runs_idle_and_defers_busy_without_leaving_a_drain(self):
        from common.service_tools import auto_update_apt as apt
        with patch.object(apt, "load_notification_configs_from_state", return_value=[]), \
                patch.object(apt, "_update_packages", return_value=0) as update:
            with coordinator.host_slot():
                self.assertEqual(apt.main(), 0)
                update.assert_not_called()
            self.assertEqual(apt.main(), 0)
            update.assert_called_once_with([])
        self.assertFalse(self.drain.exists())

    def test_refresh_releases_slot_under_drain_before_child_and_retains_on_failure(self):
        from lib import refresh
        from lib.config import SetupConfig
        import subprocess

        def upgrade(_repository):
            self.assertTrue(self.drain.exists())
            with self.assertRaises(coordinator.CoordinatorBusy), coordinator.host_slot():
                self.fail("build admitted during source activation")
            return {"commit": "fixture", "channel": "dev"}

        def child(*args, **kwargs):
            self.assertTrue(self.drain.exists())
            # A separate flock acquisition models the fresh interpreter.
            token = coordinator._MAINTENANCE.set(False)
            try:
                with coordinator.maintenance():
                    pass
            finally:
                coordinator._MAINTENANCE.reset(token)
            return subprocess.CompletedProcess([], 0)

        with patch.object(refresh, "_assert_no_source_activation"), \
                patch.object(refresh, "_protected_operation_record"), \
                patch.object(refresh, "_setup_operation_marker", return_value=str(self.root / "operation.json")), \
                patch.object(refresh, "saved_setup_revision", return_value="revision"), \
                patch.object(refresh, "_assert_setup_operation_ready"), \
                patch.object(refresh, "upgrade_channel", side_effect=upgrade) as activate, \
                patch.object(refresh, "_normalize_downloaded_source"), \
                patch.object(refresh, "write_refresh_source_metadata"), \
                patch.object(refresh, "run", side_effect=child) as run:
            config = SetupConfig(host="localhost", username="human", system_type="server_lite")
            self.assertEqual(refresh._apply_refresh(str(self.root), "dev", {}, config, [], expected_revision="revision"), 0)
            self.assertFalse(self.drain.exists())
            run.side_effect = lambda *args, **kwargs: subprocess.CompletedProcess([], 19)
            self.assertEqual(refresh._apply_refresh(str(self.root), "dev", {}, config, [], expected_revision="revision"), 19)
            self.assertTrue(self.drain.exists())
            activate.side_effect = RuntimeError("source failure")
            with self.assertRaisesRegex(RuntimeError, "source failure"):
                refresh._apply_refresh(str(self.root), "dev", {}, config, [], expected_revision="revision")
            self.assertTrue(self.drain.exists())
