"""Completed controller payload collection preserves compact recovery history."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from lib import build_controller_storage as storage
from lib.build_setup_history import maintain_completed_setup
from tests import test_build_setup_history as history_tests


class ControllerCollectionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = history_tests.BuildSetupHistoryTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        f = self.fixture
        (f.attempt / "sources").mkdir(mode=0o700)
        (f.attempt / "sources" / "snapshot").write_bytes(b"disposable prepared source")
        self.collect = lambda revision=None: storage.collect_completed_payloads(
            str(f.journal), f.attempt.name, expected_revision=revision)

    def test_preview_removes_only_payloads_and_history_can_archive_afterwards(self):
        f = self.fixture
        before = f.snapshot(f.attempt)
        report = self.collect()
        self.assertEqual(before, f.snapshot(f.attempt))
        self.assertGreater(report["allocated_bytes"], 0)
        self.collect(report["revision"])
        self.assertFalse((f.attempt / "sources").exists())
        self.assertTrue((f.attempt / "prepared.json").exists())
        self.assertEqual(self.collect()["status"], "completed")
        self.collect(report["revision"])
        history = maintain_completed_setup(f.journal, f.attempt.name)
        maintain_completed_setup(f.journal, f.attempt.name, expected_revision=history["revision"])
        self.assertEqual(self.collect()["status"], "completed")

    def test_changed_plan_symlinks_and_nested_mounts_refuse(self):
        f = self.fixture
        report = self.collect()
        payload = f.attempt / "sources" / "snapshot"
        payload.write_bytes(b"changed contents")
        with self.assertRaisesRegex(ValueError, "revision"):
            self.collect(report["revision"])
        payload.unlink()
        payload.symlink_to(f.attempt / "prepared.json")
        with self.assertRaises(ValueError):
            self.collect()
        payload.unlink()
        with patch.object(storage, "_mounts", return_value=[str(f.attempt / "sources")]), self.assertRaises(ValueError):
            self.collect()

    def test_partial_deletion_exact_retry_and_legacy_proof_survive_rotation(self):
        f = self.fixture
        (f.attempt / "profile-success.json").unlink()
        report = self.collect()
        delete = storage._delete
        calls = 0

        def crash(*args):
            nonlocal calls
            delete(*args)
            calls += 1
            if calls == 1:
                raise OSError("interrupted deletion")

        with patch.object(storage, "_delete", side_effect=crash), self.assertRaises(OSError):
            self.collect(report["revision"])
        self.assertEqual(self.collect()["status"], "pending")
        f.write(f.host_root / "setup.json.last.json", {"later_result": True})
        self.collect(report["revision"])
        self.assertEqual(self.collect()["status"], "completed")
