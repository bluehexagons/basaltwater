"""Inventoried cleanup preserves replacements, active paths and failed disposal."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from common.service_tools import cleanup_maintenance as root_cleanup
from common.service_tools import user_cache_maintenance as user_cleanup
from lib import cleanup_paths


class CleanupIdentityTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.path = self.root / "cache"
        self.path.mkdir()
        (self.path / "payload").write_text("old rebuildable data")
        self.expected = self.path.lstat()

    def remove(self, **kwargs):
        return cleanup_paths.remove_checked_path(str(self.path), self.expected, uid=os.getuid(),
                                                  in_use=kwargs.pop("in_use", lambda: False), **kwargs)

    def test_replaced_directory_and_changed_ownership_are_rejected_before_disposal(self):
        self.path.rename(self.root / "retained-old-cache")
        self.path.mkdir()
        (self.path / "report").write_text("keep")
        with self.assertRaisesRegex(OSError, "changed since inventory"):
            self.remove()
        self.assertTrue((self.path / "report").exists())
        self.assertFalse(list(self.root.glob(".basaltwater-dispose-*")))
        fields = list(self.expected)
        fields[4] = os.getuid() + 1
        self.expected = os.stat_result(fields)
        with self.assertRaisesRegex(OSError, "ownership"):
            self.remove()

    def test_recreated_original_path_is_preserved_during_disposal(self):
        rmtree = cleanup_paths.shutil.rmtree
        def recreate(path, *, dir_fd):
            self.path.mkdir()
            (self.path / "report").write_text("new active work")
            rmtree(path, dir_fd=dir_fd)
        with patch.object(cleanup_paths.shutil, "rmtree", side_effect=recreate):
            self.assertTrue(self.remove())
        self.assertEqual((self.path / "report").read_text(), "new active work")
        self.assertFalse(list(self.root.glob(".basaltwater-dispose-*")))

    def test_recreated_private_disposal_name_is_not_removed(self):
        rmtree = cleanup_paths.shutil.rmtree
        recreated = []
        def recreate(path, *, dir_fd):
            private = Path(os.readlink(f"/proc/self/fd/{dir_fd}"))
            private.rename(self.root / "retained-private-directory")
            private.mkdir()
            (private / "report").write_text("keep")
            recreated.append(private)
            rmtree(path, dir_fd=dir_fd)
        with patch.object(cleanup_paths.shutil, "rmtree", side_effect=recreate):
            self.assertTrue(self.remove())
        self.assertTrue((recreated[0] / "report").exists())

    def test_replacement_during_rename_is_retained_privately_for_explicit_recovery(self):
        rename = cleanup_paths.os.rename
        def replace(source, destination, **kwargs):
            rename(self.path, self.root / "retained-original")
            self.path.mkdir()
            (self.path / "report").write_text("replacement")
            rename(source, destination, **kwargs)
        with patch.object(cleanup_paths.os, "rename", side_effect=replace), \
                patch.object(cleanup_paths.shutil, "rmtree") as delete:
            with self.assertRaises(cleanup_paths.CleanupPathError) as error:
                self.remove()
        delete.assert_not_called()
        self.assertTrue((Path(error.exception.recovery_path) / "report").exists())
        self.assertTrue((self.root / "retained-original/payload").exists())

    def test_failed_disposal_preserves_remaining_data_without_replay(self):
        with patch.object(cleanup_paths.shutil, "rmtree", side_effect=OSError("busy")) as delete:
            with self.assertRaises(cleanup_paths.CleanupPathError) as error:
                self.remove()
        delete.assert_called_once()
        self.assertEqual(str(error.exception), "busy")
        self.assertTrue((Path(error.exception.recovery_path) / "payload").exists())

    def test_interrupted_disposal_has_a_recovery_location_before_deletion(self):
        locations = []
        with patch.object(cleanup_paths.shutil, "rmtree", side_effect=KeyboardInterrupt) as delete:
            with self.assertRaises(KeyboardInterrupt):
                self.remove(on_isolated=locations.append)
        delete.assert_called_once()
        self.assertEqual(len(locations), 1)
        self.assertTrue((Path(locations[0]) / "payload").exists())

    def test_dry_run_and_new_active_use_preserve_the_original(self):
        self.assertTrue(self.remove(dry_run=True))
        self.assertFalse(self.remove(in_use=lambda: True))
        self.assertTrue((self.path / "payload").exists())
        self.assertFalse(list(self.root.glob(".basaltwater-dispose-*")))

    def test_symlinked_parent_is_rejected_without_reading_or_disposing_its_target(self):
        parent = self.root / "linked"
        parent.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(OSError):
            cleanup_paths.remove_checked_path(str(parent / "cache"), self.expected, uid=os.getuid(), in_use=lambda: False)
        self.assertTrue((self.path / "payload").exists())

    def test_generic_user_cache_rechecks_liveness_immediately_before_removal(self):
        context = user_cleanup.UserContext("fixture", str(self.root), os.getuid())
        with patch.object(user_cleanup, "defer_active_cache", side_effect=[False, True]) as active:
            failures = user_cleanup.cleanup_managed_directory(context, label="Fixture", path=str(self.path),
                        max_bytes=1, max_age_days=7, process_names=("fixture",), dry_run=False)
        self.assertEqual(failures, [])
        self.assertEqual(active.call_count, 2)
        self.assertTrue((self.path / "payload").exists())

    def test_stale_children_preserve_a_replacement_after_inventory(self):
        child = self.path / "stale"
        child.mkdir()
        old = time.time() - 8 * 86400
        os.utime(child, (old, old))
        context = user_cleanup.UserContext("fixture", str(self.root), os.getuid())
        usage = user_cleanup.cache_usage
        def replace(path):
            value = usage(path)
            if path == str(child):
                child.rename(self.root / "retained-child")
                child.mkdir()
                (child / "report").write_text("keep")
            return value
        with patch.object(user_cleanup, "tool_is_active", return_value=False), \
                patch.object(user_cleanup, "process_uses_path", return_value=False), \
                patch.object(user_cleanup, "cache_usage", side_effect=replace):
            failures = user_cleanup.cleanup_stale_children(context, label="Fixture", path=str(self.path),
                        max_age_days=7, process_names=("fixture",), dry_run=False)
        self.assertTrue(failures)
        self.assertIn("changed since inventory", failures[0])
        self.assertTrue((child / "report").exists())

    def test_privileged_temp_cleanup_preserves_owner_active_paths_and_unrelated_evidence(self):
        old = time.time() - 8 * 86400
        candidate = self.root / "basaltwater_setup_build_fixture"
        candidate.mkdir()
        evidence = self.root / "project-capture.png"
        evidence.write_text("keep")
        os.utime(candidate, (old, old))
        os.utime(evidence, (old, old))
        with patch.object(root_cleanup, "process_uses_path", return_value=True) as active:
            self.assertEqual(root_cleanup.cleanup_stale_basaltwater_tmp_artifacts(str(self.root)), [])
        active.assert_called_once_with(str(candidate), uid=os.getuid())
        self.assertTrue(candidate.exists())
        self.assertTrue(evidence.exists())

    def test_owner_path_scan_bounds_arguments_and_recognizes_option_values(self):
        proc = self.root / "proc"
        process = proc / "123"
        process.mkdir(parents=True)
        arguments = process / "cmdline"
        arguments.write_bytes(b"worker\0--output=" + os.fsencode(self.path) + b"\0")
        self.assertTrue(cleanup_paths.process_uses_path(str(self.path), str(proc), uid=os.getuid()))
        self.assertFalse(cleanup_paths.process_uses_path(str(self.path), str(proc), uid=os.getuid() + 1))
        arguments.write_bytes(b"x" * 65537)
        self.assertTrue(cleanup_paths.process_uses_path(str(self.path), str(proc)))

    def test_open_descriptor_checks_detect_use_and_fail_closed_on_denial_or_limit(self):
        proc = self.root / "proc"
        process = proc / "123"
        descriptors = process / "fd"
        descriptors.mkdir(parents=True)
        (process / "cmdline").write_bytes(b"worker\0")
        (descriptors / "4").symlink_to(self.path / "payload")
        self.assertFalse(cleanup_paths.process_uses_path(str(self.path), str(proc)))
        self.assertTrue(cleanup_paths.process_uses_path(str(self.path), str(proc), include_open_files=True))
        (descriptors / "4").unlink()
        scandir = cleanup_paths.os.scandir

        def denied(path):
            if str(path) == str(descriptors):
                raise PermissionError
            return scandir(path)

        with patch.object(cleanup_paths.os, "scandir", side_effect=denied):
            self.assertTrue(cleanup_paths.process_uses_path(str(self.path), str(proc), include_open_files=True))
            incomplete = []
            self.assertFalse(cleanup_paths.process_uses_path(str(self.path), str(proc), include_open_files=True,
                on_incomplete=lambda: incomplete.append(True)))
            self.assertEqual(incomplete, [True])
        with patch.object(cleanup_paths.os, "readlink", return_value="/unrelated"):
            entries = MagicMock()
            entries.__enter__.return_value = [SimpleNamespace(path="fd")] * 4097

            def crowded(path):
                return entries if str(path) == str(descriptors) else scandir(path)

            with patch.object(cleanup_paths.os, "scandir", side_effect=crowded):
                self.assertTrue(cleanup_paths.process_uses_path(str(self.path), str(proc), include_open_files=True))
