"""Recovery metadata is subject to the same private-file contract as keys."""

from __future__ import annotations

import os
from unittest.mock import patch
import unittest

from lib import headscale_backup as backup
from tests import test_headscale_backup as fixtures


class MetadataSafetyTests(unittest.TestCase):
    setUp = fixtures.HeadscaleBackupTests.setUp
    capture = fixtures.HeadscaleBackupTests.capture

    def test_private_metadata_modes_links_and_size_before_native_validation(self):
        self.capture()
        path = self.snapshot / "backup.json"
        original = path.read_bytes()
        with patch.object(backup, "configtest") as native:
            path.chmod(0o644)
            with self.assertRaises(ValueError):
                backup.validate_backup(self.snapshot, expected=self.manifest, paths=self.paths)
            path.chmod(0o600)
            link = self.root / "extra-link"
            os.link(path, link)
            with self.assertRaises(ValueError):
                backup.validate_backup(self.snapshot, expected=self.manifest, paths=self.paths)
            link.unlink()
            path.write_bytes(b" " * (64 * 1024 + 1))
            with self.assertRaises(ValueError):
                backup.validate_backup(self.snapshot, expected=self.manifest, paths=self.paths)
            path.write_bytes(original)
            path.unlink()
            path.symlink_to(self.paths.manifest)
            with self.assertRaises(OSError):
                backup.validate_backup(self.snapshot, expected=self.manifest, paths=self.paths)
            native.assert_not_called()
