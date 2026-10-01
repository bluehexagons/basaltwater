"""Exercise node-local setup/maintenance exclusion using temporary lock files."""

from __future__ import annotations

import fcntl
import os
import tempfile
import unittest
from unittest.mock import patch

from lib.maintenance_lock import maintenance_lock


class TestMaintenanceLock(unittest.TestCase):
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
