"""The repository-owned generated-output cleanup stays narrowly scoped."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from io import StringIO
from unittest.mock import patch

from scripts import clean_generated


class GeneratedOutputCleanupScriptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        (self.root / "scripts").mkdir(parents=True)
        self.targets = [self.root / name for name in ("build", "dist", "htmlcov")]
        for target in self.targets:
            target.mkdir()
            (target / "output.bin").write_bytes(b"generated")
        (self.root / "artifacts").mkdir()
        (self.root / "artifacts" / "screenshot.png").write_bytes(b"keep")
        (self.root / ".venv").mkdir()
        (self.root / ".venv" / "state").write_text("keep")
        (self.root / ".git").mkdir()
        (self.root / ".git" / "HEAD").write_text("keep")
        (self.root / "src").mkdir()
        (self.root / "src" / "module.py").write_text("keep")
        (self.root / "docs" / "brand").mkdir(parents=True)
        (self.root / "docs" / "brand" / "logo.svg").write_text("keep")
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(clean_generated, "repository_root", return_value=self.root))
        self.git = self.stack.enter_context(patch.object(
            clean_generated.subprocess, "run",
            return_value=subprocess.CompletedProcess([], 0, stdout=b"", stderr=b""),
        ))
        self.inventory = self.stack.enter_context(patch.object(
            clean_generated.agent_storage_inventory, "inspect_storage", side_effect=self.scan,
        ))
        self.process_use = self.stack.enter_context(patch.object(
            clean_generated.cleanup_paths, "process_uses_path", return_value=False,
        ))

    def scan(self, *, home, paths, max_entries, timeout):
        self.assertEqual(home, str(self.root))
        self.assertEqual(paths, [str(path) for path in self.targets])
        self.assertLessEqual(max_entries, 50_000)
        self.assertLessEqual(timeout, 10)
        rows = []
        for raw_path in paths:
            path = Path(raw_path)
            if not path.exists():
                rows.append({"path": raw_path, "status": "absent", "allocated_bytes": 0,
                             "owner_uid": None, "other_owner_entries": 0, "issues": []})
                continue
            allocated = sum(entry.stat().st_size for entry in path.rglob("*"))
            rows.append({"path": raw_path, "status": "measured", "allocated_bytes": allocated,
                         "owner_uid": os.getuid(), "other_owner_entries": 0, "issues": []})
        return {"issues": [], "candidates": rows}

    def test_default_and_explicit_dry_run_list_targets_without_deleting(self):
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(clean_generated.main([]), 0)
            self.assertEqual(clean_generated.main(["--dry-run"]), 0)
        for target in self.targets:
            self.assertTrue((target / "output.bin").exists())
            self.assertIn(str(target), output.getvalue())
        self.assertIn("measured; dry-run", output.getvalue())
        self.git.assert_called()
        command = self.git.call_args.args[0]
        self.assertIn("-z", command)
        self.assertIn("build/", command)
        self.assertIn("dist/", command)
        self.assertIn("htmlcov/", command)
        self.assertEqual(self.git.call_args.kwargs["timeout"], 10)
        self.assertEqual(self.git.call_args.kwargs["env"]["GIT_OPTIONAL_LOCKS"], "0")
        self.assertEqual(self.process_use.call_count, 6)

    def test_apply_removes_only_allowlisted_directories_and_retains_evidence(self):
        self.assertEqual(clean_generated.main(["--apply"]), 0)
        for target in self.targets:
            self.assertFalse(target.exists())
        self.assertTrue((self.root / "artifacts" / "screenshot.png").exists())
        self.assertTrue((self.root / ".venv" / "state").exists())
        self.assertTrue((self.root / ".git" / "HEAD").exists())
        self.assertTrue((self.root / "src" / "module.py").exists())
        self.assertTrue((self.root / "docs" / "brand" / "logo.svg").exists())

    def test_active_target_blocks_the_whole_apply_before_first_removal(self):
        self.process_use.side_effect = lambda path, uid=None: path.endswith("/dist")
        errors = StringIO()
        with redirect_stderr(errors):
            self.assertEqual(clean_generated.main(["--apply"]), 1)
        self.assertIn("in use", errors.getvalue())
        self.assertTrue(all(target.exists() for target in self.targets))

    def test_tracked_target_is_refused(self):
        self.git.return_value = subprocess.CompletedProcess([], 0, stdout=b"build/tracked.py\0", stderr=b"")
        errors = StringIO()
        with redirect_stderr(errors):
            self.assertEqual(clean_generated.main(["--apply"]), 1)
        self.assertIn("tracked files", errors.getvalue())
        self.assertTrue(all(target.exists() for target in self.targets))
        self.inventory.assert_not_called()

    def test_symlink_target_and_incomplete_inventory_are_refused(self):
        self.targets[0].rename(self.root / "saved-build")
        self.targets[0].symlink_to(self.root / "saved-build", target_is_directory=True)
        errors = StringIO()
        with redirect_stderr(errors):
            self.assertEqual(clean_generated.main(["--apply"]), 1)
        self.assertIn("symlink", errors.getvalue())
        self.assertTrue((self.root / "saved-build" / "output.bin").exists())
        self.targets[0].unlink()
        (self.root / "build").mkdir()
        (self.root / "build" / "output.bin").write_bytes(b"generated")
        original_scan = self.scan

        def partial(**kwargs):
            result = original_scan(**kwargs)
            result["candidates"][1]["status"] = "partial"
            result["candidates"][1]["issues"] = ["nested_mounts_skipped"]
            return result

        self.inventory.side_effect = partial
        with redirect_stderr(errors):
            self.assertEqual(clean_generated.main(["--apply"]), 1)
        self.assertIn("nested_mounts_skipped", errors.getvalue())
        self.assertTrue(all(target.exists() for target in self.targets))

    def test_symlink_repository_ancestor_is_refused(self):
        real_root = self.root.with_name("repo-real")
        self.root.rename(real_root)
        self.root.symlink_to(real_root, target_is_directory=True)
        errors = StringIO()
        with redirect_stderr(errors):
            self.assertEqual(clean_generated.main(["--apply"]), 1)
        self.assertIn("symlink", errors.getvalue())
        self.assertTrue(all((real_root / target.name).exists() for target in self.targets))

    def test_git_and_disposal_errors_return_nonzero(self):
        self.git.side_effect = FileNotFoundError("git missing")
        errors = StringIO()
        with redirect_stderr(errors):
            self.assertEqual(clean_generated.main(["--apply"]), 1)
        self.assertIn("Git preflight failed", errors.getvalue())
        self.git.side_effect = None
        self.git.return_value = subprocess.CompletedProcess([], 0, stdout=b"", stderr=b"")
        with patch.object(clean_generated.cleanup_paths, "remove_checked_path", side_effect=OSError("disposal failed")):
            with redirect_stderr(errors):
                self.assertEqual(clean_generated.main(["--apply"]), 1)
        self.assertIn("disposal failed", errors.getvalue())
        self.assertTrue(all(target.exists() for target in self.targets))

    def test_apply_and_dry_run_are_mutually_exclusive(self):
        with self.assertRaises(SystemExit):
            clean_generated.main(["--apply", "--dry-run"])


if __name__ == "__main__":
    unittest.main()
