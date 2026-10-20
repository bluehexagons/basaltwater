"""Project-declared generated-output cleanup stays narrowly scoped."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from io import StringIO
import json
from unittest.mock import patch

from lib import agent_generated_cleanup as clean_generated
from lib import agent_storage_inventory


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
        (self.root / ".basaltwater").mkdir()
        self.policy = {"version": 1, "instructions": "Preserve evidence.", "preserve": ["artifacts"],
                       "rules": [{"name": "build", "strategy": "generated", "instructions": "Rebuildable output.",
                                  "paths": ["build", "dist", "htmlcov"]}]}
        self.write_policy()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(agent_storage_inventory, "_mounts", return_value={"/": "ext4"}))
        self.git = self.stack.enter_context(patch.object(
            clean_generated.subprocess, "run",
            return_value=subprocess.CompletedProcess([], 1),
        ))
        self.inventory = self.stack.enter_context(patch.object(
            clean_generated.agent_storage_inventory, "inspect_storage", side_effect=self.scan,
        ))
        self.process_use = self.stack.enter_context(patch.object(
            clean_generated.cleanup_paths, "process_uses_path", return_value=False,
        ))

    def write_policy(self):
        (self.root / ".basaltwater/cleanup.json").write_text(json.dumps(self.policy))

    def run_cleanup(self, *, apply=False, rule="build"):
        return clean_generated.clean_generated(str(self.root), rule, apply=apply)

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

    def test_preview_lists_targets_without_deleting(self):
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(self.run_cleanup(), 0)
        for target in self.targets:
            self.assertTrue((target / "output.bin").exists())
            self.assertIn(str(target), output.getvalue())
        self.assertIn("measured; dry-run", output.getvalue())
        self.git.assert_called()
        command = self.git.call_args.args[0]
        self.assertIn("--error-unmatch", command)
        self.assertEqual({call.args[0][-1] for call in self.git.call_args_list}, {":(literal)build", ":(literal)dist", ":(literal)htmlcov"})
        self.assertEqual(self.git.call_args.kwargs["stdout"], subprocess.DEVNULL)
        self.assertEqual(self.git.call_args.kwargs["timeout"], 10)
        self.assertEqual(self.git.call_args.kwargs["env"]["GIT_OPTIONAL_LOCKS"], "0")
        self.assertEqual(self.process_use.call_count, 3)

    def test_apply_removes_only_allowlisted_directories_and_retains_evidence(self):
        self.assertEqual(self.run_cleanup(apply=True), 0)
        for target in self.targets:
            self.assertFalse(target.exists())
        self.assertTrue((self.root / "artifacts" / "screenshot.png").exists())
        self.assertTrue((self.root / ".venv" / "state").exists())
        self.assertTrue((self.root / ".git" / "HEAD").exists())
        self.assertTrue((self.root / "src" / "module.py").exists())
        self.assertTrue((self.root / "docs" / "brand" / "logo.svg").exists())

    def test_active_target_blocks_the_whole_apply_before_first_removal(self):
        self.process_use.side_effect = lambda path, **kwargs: path.endswith("/dist")
        errors = StringIO()
        with redirect_stderr(errors):
            self.assertEqual(self.run_cleanup(apply=True), 1)
        self.assertIn("in use", errors.getvalue())
        self.assertTrue(all(target.exists() for target in self.targets))

    def test_hidden_daemon_descriptors_report_limited_coverage_once(self):
        def hidden(path, **kwargs):
            kwargs["on_incomplete"]()
            return False

        self.process_use.side_effect = hidden
        with redirect_stderr(StringIO()) as errors, redirect_stdout(StringIO()):
            self.assertEqual(self.run_cleanup(), 0)
        self.assertEqual(errors.getvalue().count("Open-file inspection is incomplete"), 1)
        self.assertTrue(all(target.exists() for target in self.targets))

    def test_shared_repository_is_refused_before_disposal(self):
        info = self.root.lstat()
        fields = list(info)
        fields[0] = (info.st_mode & ~0o777) | 0o755
        with patch.object(Path, "lstat", return_value=os.stat_result(fields)), redirect_stderr(StringIO()):
            self.assertEqual(self.run_cleanup(apply=True), 1)
        self.inventory.assert_not_called()
        self.assertTrue(all(target.exists() for target in self.targets))

    def test_tracked_target_is_refused(self):
        self.git.return_value = subprocess.CompletedProcess([], 0)
        errors = StringIO()
        with redirect_stderr(errors):
            self.assertEqual(self.run_cleanup(apply=True), 1)
        self.assertIn("tracked files", errors.getvalue())
        self.assertTrue(all(target.exists() for target in self.targets))
        self.inventory.assert_not_called()

    def test_symlink_target_and_incomplete_inventory_are_refused(self):
        self.targets[0].rename(self.root / "saved-build")
        self.targets[0].symlink_to(self.root / "saved-build", target_is_directory=True)
        errors = StringIO()
        with redirect_stderr(errors):
            self.assertEqual(self.run_cleanup(apply=True), 1)
        self.assertIn("symlink", errors.getvalue())
        self.assertTrue((self.root / "saved-build" / "output.bin").exists())
        self.targets[0].unlink()
        (self.root / "build").mkdir()
        (self.root / "build" / "output.bin").write_bytes(b"generated")
        for changes, message in (({"status": "partial", "issues": ["nested_mounts_skipped"]}, "nested_mounts_skipped"),
                                 ({"is_mountpoint": True}, "mountpoint"),
                                 ({"other_owner_entries": 1}, "outside the current account")):
            with self.subTest(changes=changes):
                def incomplete(**kwargs):
                    result = self.scan(**kwargs)
                    result["candidates"][1].update(changes)
                    return result

                self.inventory.side_effect = incomplete
                errors = StringIO()
                with redirect_stderr(errors):
                    self.assertEqual(self.run_cleanup(apply=True), 1)
                self.assertIn(message, errors.getvalue())
                self.assertTrue(all(target.exists() for target in self.targets))

    def test_symlink_repository_ancestor_is_refused(self):
        real_root = self.root.with_name("repo-real")
        self.root.rename(real_root)
        self.root.symlink_to(real_root, target_is_directory=True)
        errors = StringIO()
        with redirect_stderr(errors):
            self.assertEqual(self.run_cleanup(apply=True), 1)
        self.assertIn("symlink", errors.getvalue())
        self.assertTrue(all((real_root / target.name).exists() for target in self.targets))

    def test_git_and_disposal_errors_return_nonzero(self):
        self.git.side_effect = FileNotFoundError("git missing")
        errors = StringIO()
        with redirect_stderr(errors):
            self.assertEqual(self.run_cleanup(apply=True), 1)
        self.assertIn("Git preflight failed", errors.getvalue())
        self.git.side_effect = None
        self.git.return_value = subprocess.CompletedProcess([], 1)
        recovery = str(self.root / ".basaltwater-dispose-fixture/entry")
        for failure in (OSError("disposal failed"), clean_generated.cleanup_paths.CleanupPathError("disposal failed", recovery)):
            with self.subTest(failure=type(failure).__name__), patch.object(clean_generated.cleanup_paths, "remove_checked_path", side_effect=failure):
                with redirect_stderr(errors):
                    self.assertEqual(self.run_cleanup(apply=True), 1)
        self.assertIn("disposal failed", errors.getvalue())
        self.assertIn(recovery, errors.getvalue())
        self.assertTrue(all(target.exists() for target in self.targets))

    def test_only_declared_generated_rules_can_be_applied(self):
        with redirect_stderr(StringIO()):
            self.assertEqual(self.run_cleanup(apply=True, rule="missing"), 1)
            self.policy["rules"][0]["strategy"] = "agent"
            self.write_policy()
            self.assertEqual(self.run_cleanup(apply=True), 1)
        self.inventory.assert_not_called()
        self.assertTrue(all(target.exists() for target in self.targets))

    def test_nested_targets_preserve_tracked_parent_marker(self):
        (self.targets[0] / "linux").mkdir()
        (self.targets[0] / "linux/game").write_text("generated")
        (self.targets[0] / ".gdignore").write_text("keep")
        self.policy["rules"][0]["paths"] = ["build/linux"]
        self.write_policy()
        self.targets = [self.root / "build/linux"]
        self.assertEqual(self.run_cleanup(apply=True), 0)
        self.assertTrue((self.root / "build/.gdignore").exists())
        self.assertTrue((self.root / "build/output.bin").exists())


if __name__ == "__main__":
    unittest.main()
