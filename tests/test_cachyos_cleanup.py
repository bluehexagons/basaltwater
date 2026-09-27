"""CachyOS cleanup uses temporary caches and mocked package commands."""

from __future__ import annotations

from contextlib import ExitStack
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import cachyos_cleanup as cleanup
from common import cachyos_steps
from lib.agent_storage import cleanup_t3_rotated_logs
from lib.validation import validate_arch_package_name


class PackageCleanupTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.home = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        self.cache = self.home / "pkg"
        self.cache.mkdir()
        self.lock = self.home / "db.lck"
        self.candidates = []
        self.installed = ""
        self.error = ""
        stack.enter_context(patch.object(cleanup, "PACKAGE_CACHE", self.cache))
        stack.enter_context(patch.object(cleanup, "PACMAN_LOCK", self.lock))
        self.available = stack.enter_context(patch.object(cleanup, "_cache_available", return_value=True))
        stack.enter_context(patch.object(cleanup.os, "geteuid", return_value=0))
        stack.enter_context(patch.object(cleanup, "is_dry_run", return_value=False))
        self.run = stack.enter_context(patch.object(cleanup, "run", side_effect=self.command))
        actual_lstat = Path.lstat

        def root_owned(path):
            info = actual_lstat(path)
            # Simulate pacman's root-owned archives without chown or sudo.
            return SimpleNamespace(**{key: 0 if key == "st_uid" else getattr(info, key)
                                      for key in ("st_uid", "st_mode", "st_dev", "st_ino", "st_atime", "st_mtime", "st_atime_ns", "st_mtime_ns")})

        stack.enter_context(patch.object(Path, "lstat", root_owned))

    def archive(self, name, *, age=40):
        path = self.cache / name
        path.write_text("archive fixture")
        timestamp = time.time() - age * 86400
        os.utime(path, (timestamp, timestamp))
        self.candidates.append(path)
        return path

    def command(self, argv, **kwargs):
        if argv[0] == "/usr/bin/pacman":
            return subprocess.CompletedProcess(argv, 0, self.installed, "")
        if not self.candidates:
            output = "==> no candidate packages found for pruning\n"
        else:
            output = "==> Candidate packages:\n" + "\0".join(str(path) for path in self.candidates)
            output += f"\0\n==> finished dry run: {len(self.candidates)} candidates (disk space saved: 1 MiB)\n"
        return subprocess.CompletedProcess(argv, 0, output, self.error)

    def test_removes_only_old_completed_surplus_archives_and_their_signatures(self):
        old = self.archive("example-1.0-1-x86_64.pkg.tar.zst")
        signature = self.archive(old.name + ".sig")
        recent = self.archive("recent-1.0-1-x86_64.pkg.tar.zst", age=1)
        partial = self.archive("partial-1.0-1-x86_64.pkg.tar.zst.part")
        installed = self.archive("installed-1.0-1-x86_64.pkg.tar.zst")
        self.installed = "installed 2:1.0-1\n"
        active = self.archive("accessed-1.0-1-x86_64.pkg.tar.zst")
        os.utime(active, (time.time(), active.stat().st_mtime))
        unrelated = self.cache / "personal.txt"
        unrelated.write_text("keep")
        self.assertEqual(cleanup.prune_package_cache(dry_run=False), 2)
        self.assertFalse(old.exists())
        self.assertFalse(signature.exists())
        for path in (recent, partial, installed, active, unrelated):
            self.assertTrue(path.exists())
        command = self.run.call_args_list[0].args[0]
        self.assertIn("--dryrun", command)
        self.assertNotIn("--remove", command)
        self.assertNotIn("--min-mtime", command)
        self.assertEqual(command[-2:], ["--keep", "3"])

    def test_preview_never_deletes_or_requests_sudo(self):
        old = self.archive("example-1.0-1-x86_64.pkg.tar.zst")
        self.assertEqual(cleanup.prune_package_cache(dry_run=True), 1)
        self.assertTrue(old.exists())
        self.assertTrue(all(call.args[0][0] != "sudo" for call in self.run.call_args_list))

    def test_installed_arch_names_and_epochs_are_retained_after_downgrades(self):
        self.installed = "lm_sensors 1:1.0-1\nexample@stable 1.0-1\n"
        protected = [self.archive(f"{name}-1.0-1-x86_64.pkg.tar.zst")
                     for name in ("lm_sensors", "example@stable")]
        obsolete = self.archive("lm_sensors-0.9-1-x86_64.pkg.tar.zst")
        self.assertEqual(cleanup.prune_package_cache(dry_run=False), 1)
        self.assertFalse(obsolete.exists())
        self.assertTrue(all(path.exists() for path in protected))

    def test_arch_package_validation_rejects_options_paths_and_expressions(self):
        for value in ("--clean", ".hidden", "repo/pkg", "pkg>=1", "pkg\n", " pkg", "", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_arch_package_name(value)
        for value in ("lm_sensors", "example@stable", "libstdc++", "_example"):
            self.assertEqual(validate_arch_package_name(value), value)

    def test_lock_and_inventory_errors_preserve_everything(self):
        old = self.archive("example-1.0-1-x86_64.pkg.tar.zst")
        self.lock.touch()
        self.assertEqual(cleanup.prune_package_cache(dry_run=False), 0)
        self.run.assert_not_called()
        self.lock.unlink()
        self.error = "find: permission denied"
        with self.assertRaisesRegex(RuntimeError, "inventory failed"):
            cleanup.prune_package_cache(dry_run=False)
        self.assertTrue(old.exists())

    def test_rechecks_files_and_retains_signature_if_archive_changed(self):
        old = self.archive("example-1.0-1-x86_64.pkg.tar.zst")
        signature = self.archive(old.name + ".sig")

        checks = 0

        def touch_before_removal():
            nonlocal checks
            checks += 1
            if checks == 2:
                os.utime(old, None)
            return True

        self.available.side_effect = touch_before_removal
        self.assertEqual(cleanup.prune_package_cache(dry_run=False), 0)
        self.assertTrue(old.exists())
        self.assertTrue(signature.exists())

    def test_nested_or_outside_candidate_stops_before_removing_any_files(self):
        old = self.archive("example-1.0-1-x86_64.pkg.tar.zst")
        self.candidates.append(self.cache / "download-private/example-0.1-1-x86_64.pkg.tar.zst")
        with self.assertRaisesRegex(RuntimeError, "outside"):
            cleanup.prune_package_cache(dry_run=False)
        self.assertTrue(old.exists())

    def test_symlink_archive_and_unfamiliar_output_are_not_deleted(self):
        target = self.home / "personal"
        target.write_text("keep")
        link = self.cache / "example-1.0-1-x86_64.pkg.tar.zst"
        link.symlink_to(target)
        self.candidates.append(link)
        self.assertEqual(cleanup.prune_package_cache(dry_run=False), 0)
        self.run.side_effect = None
        self.run.return_value = subprocess.CompletedProcess([], 0, "unknown format", "")
        with self.assertRaisesRegex(RuntimeError, "Unrecognized"):
            cleanup.prune_package_cache(dry_run=False)
        self.assertEqual(target.read_text(), "keep")

    def test_setup_dry_run_does_not_inventory_or_elevate(self):
        cleanup.cleanup_cachyos_packages(SimpleNamespace(dry_run=True))
        self.run.assert_not_called()
        self.available.assert_not_called()

    def test_apply_wrapper_uses_sudo_only_when_preview_finds_candidates(self):
        with patch.object(cleanup, "prune_package_cache", return_value=0):
            cleanup.cleanup_cachyos_packages(SimpleNamespace(dry_run=False))
        self.run.assert_not_called()
        with patch.object(cleanup, "prune_package_cache", return_value=1), \
                patch.object(cleanup.os, "geteuid", return_value=1000):
            cleanup.cleanup_cachyos_packages(SimpleNamespace(dry_run=False))
        argv = self.run.call_args.args[0]
        self.assertEqual(argv[:2], ["sudo", "/usr/bin/python3"])
        self.assertEqual(argv[-1], "--apply")
        self.assertTrue(self.run.call_args.kwargs["interactive"])

    def test_cache_directory_redirects_and_unsafe_permissions_are_refused(self):
        with patch.object(Path, "is_symlink", return_value=False), \
                patch.object(Path, "stat", return_value=SimpleNamespace(st_mode=stat.S_IFDIR | 0o777, st_uid=0)), \
                self.assertRaisesRegex(RuntimeError, "root-owned"):
            _real_cache_available()
        with patch.object(Path, "is_symlink", return_value=True), \
                self.assertRaisesRegex(RuntimeError, "symlinked"):
            _real_cache_available()


class T3CacheTests(unittest.TestCase):
    def test_managed_web_logs_preserve_current_logs_database_and_links(self):
        with tempfile.TemporaryDirectory() as home:
            base = Path(home) / ".local/share/basaltwater/cachyos-t3/data"
            logs = base / "userdata/logs"
            logs.mkdir(parents=True)
            old = logs / "provider.log.1"
            old.write_text("old")
            os.utime(old, (1, 1))
            current = logs / "provider.log"
            current.write_text("current")
            database = base / "userdata/state.sqlite"
            database.write_text("history")
            link = logs / "provider.log.2"
            link.symlink_to(database)
            result = cleanup_t3_rotated_logs(home, os.getuid(), dry_run=False,
                                            max_bytes=1024, max_age_days=14, base_dir=str(base))
            self.assertFalse(result.errors)
            self.assertEqual(result.removed, (str(old),))
            self.assertEqual(current.read_text(), "current")
            self.assertEqual(database.read_text(), "history")
            self.assertTrue(link.is_symlink())

    def test_user_cache_step_targets_isolated_web_log_directory(self):
        with tempfile.TemporaryDirectory() as home, \
                patch.object(cachyos_steps, "_home", return_value=Path(home)), \
                patch("common.setup_maintenance.run_user_cache_maintenance", return_value=True), \
                patch("lib.agent_storage.cleanup_t3_rotated_logs", return_value=SimpleNamespace(errors=(), removed=())) as cleanup_logs:
            cachyos_steps.reconcile_cachyos_user_cache(SimpleNamespace(dry_run=False))
        self.assertEqual(cleanup_logs.call_args.kwargs["base_dir"], str(Path(home) / ".local/share/basaltwater/cachyos-t3/data"))


_real_cache_available = cleanup._cache_available


if __name__ == "__main__":
    unittest.main()
