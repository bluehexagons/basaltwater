"""Runtime provenance cannot grant cleanup permission to arbitrary NVM files."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from lib.node_runtime_ownership import MARKER, mark_runtime, runtime_directory, runtime_lock, runtime_owner


class RuntimeOwnershipTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.nvm = Path(temporary.name)
        self.runtime = runtime_directory(self.nvm, "26.10.0")
        (self.runtime / "bin").mkdir(parents=True)
        (self.runtime / "bin/node").write_text("fixture")

    def test_project_claim_survives_automatic_claims(self):
        self.assertIsNone(runtime_owner(self.nvm, "26.10.0"))
        mark_runtime(self.nvm, "26.10.0", owner="automatic")
        self.assertEqual(runtime_owner(self.nvm, "v26.10.0"), "automatic")
        mark_runtime(self.nvm, "26.10.0", owner="project")
        mark_runtime(self.nvm, "26.10.0", owner="automatic")
        self.assertEqual(runtime_owner(self.nvm, "26.10.0"), "project")
        self.assertEqual((self.runtime / MARKER).stat().st_mode & 0o777, 0o600)

    def test_missing_malformed_or_unsafe_markers_never_authorize_cleanup(self):
        marker = self.runtime / MARKER
        for value in ("broken", "[]", '{"schema_version":2,"version":"v26.10.0","owner":"automatic"}'):
            marker.write_text(value)
            marker.chmod(0o600)
            self.assertIsNone(runtime_owner(self.nvm, "26.10.0"))
            with self.assertRaisesRegex(ValueError, "Unsafe or invalid"):
                mark_runtime(self.nvm, "26.10.0", owner="project")
        marker.unlink()
        mark_runtime(self.nvm, "26.10.0", owner="automatic")
        marker.chmod(0o666)
        self.assertIsNone(runtime_owner(self.nvm, "26.10.0"))
        marker.unlink()
        personal = self.nvm / "personal.json"
        personal.write_text(json.dumps({"schema_version": 1, "version": "v26.10.0", "owner": "automatic"}))
        marker.symlink_to(personal)
        self.assertIsNone(runtime_owner(self.nvm, "26.10.0"))
        with self.assertRaises(ValueError):
            mark_runtime(self.nvm, "26.10.0", owner="project")
        self.assertIn("automatic", personal.read_text())

    def test_runtime_paths_and_lock_reject_symlinks_and_non_versions(self):
        for version in ("26", "../../personal", "v26.10.0\n", "26.10.0-beta"):
            with self.assertRaises(ValueError):
                runtime_directory(self.nvm, version)
        directory = self.nvm / "versions/node/v22.23.2"
        directory.symlink_to(self.runtime)
        with self.assertRaises(ValueError):
            mark_runtime(self.nvm, "22.23.2", owner="project")
        personal = self.nvm / "personal"
        personal.write_text("keep")
        lock = self.nvm / ".basaltwater-runtime.lock"
        lock.unlink()
        lock.symlink_to(personal)
        with self.assertRaises(OSError):
            mark_runtime(self.nvm, "26.10.0", owner="project")
        self.assertEqual(personal.read_text(), "keep")

    def test_lock_contention_fails_without_overwriting_project_protection(self):
        mark_runtime(self.nvm, "26.10.0", owner="project")
        with runtime_lock(self.nvm), self.assertRaisesRegex(ValueError, "operation is active"):
            mark_runtime(self.nvm, "26.10.0", owner="automatic")
        self.assertEqual(runtime_owner(self.nvm, "26.10.0"), "project")


if __name__ == "__main__":
    unittest.main()
