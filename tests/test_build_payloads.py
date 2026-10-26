"""Independent descriptor leases protect payloads without adding lock files."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

from lib.build_jobs import BuildJobStore
from lib.build_payloads import payload_lease


class BuildPayloadTests(unittest.TestCase):
    def test_shared_readers_exclusive_nonblocking_and_worker_flock_are_independent(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            jobs = BuildJobStore(root / "jobs.json")
            request_id = "a" * 32
            with payload_lease(jobs, request_id, exclusive=False):
                original = (root / jobs.worker_lock_name).stat()
                with payload_lease(jobs, request_id, exclusive=False):
                    with self.assertRaises(BlockingIOError):
                        with payload_lease(jobs, request_id, exclusive=True):
                            self.fail("exclusive lease entered")
                with jobs.worker():
                    with self.assertRaises(BlockingIOError):
                        with payload_lease(jobs, request_id, exclusive=True):
                            self.fail("worker flock bypassed payload reader")
                with payload_lease(jobs, "b" * 32, exclusive=True):
                    pass
            with self.assertRaises(KeyboardInterrupt):
                with payload_lease(jobs, request_id, exclusive=True):
                    raise KeyboardInterrupt
            with payload_lease(jobs, request_id, exclusive=True):
                with self.assertRaises(BlockingIOError):
                    with payload_lease(jobs, request_id, exclusive=False):
                        self.fail("shared lease entered")
            current = (root / jobs.worker_lock_name).stat()
            self.assertEqual((original.st_dev, original.st_ino), (current.st_dev, current.st_ino))
            self.assertEqual(current.st_size, 0)
            self.assertEqual(set(os.listdir(root)), {jobs.worker_lock_name})

    def test_lock_symlinks_hardlinks_wrong_modes_and_unsafe_roots_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            jobs = BuildJobStore(root / "jobs.json")
            path = root / jobs.worker_lock_name
            path.symlink_to(root / "missing")
            with self.assertRaises((OSError, ValueError)):
                with payload_lease(jobs, "a" * 32, exclusive=False):
                    self.fail("symlink lock entered")
            path.unlink()
            path.write_bytes(b"")
            path.chmod(0o644)
            with self.assertRaises(ValueError):
                with payload_lease(jobs, "a" * 32, exclusive=False):
                    self.fail("wrong mode lock entered")
            path.chmod(0o600)
            os.link(path, root / "alias")
            with self.assertRaises(ValueError):
                with payload_lease(jobs, "a" * 32, exclusive=False):
                    self.fail("hardlinked lock entered")
            (root / "alias").unlink()
            root.chmod(0o777)
            with self.assertRaises(ValueError):
                with payload_lease(jobs, "a" * 32, exclusive=False):
                    self.fail("unsafe root entered")
