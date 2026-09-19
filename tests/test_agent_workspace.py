"""Tests for isolated agent worktrees and redacted support snapshots."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lib.agent_cli import add_agent_subparser, run_agent_command
from lib.agent_support import build_agent_support_bundle, write_agent_support_bundle
from lib.agent_workspace import (
    create_agent_worktree,
    list_agent_worktrees,
    remove_agent_worktree,
)


class AgentWorkspaceTests(unittest.TestCase):
    def _git(self, repository: str, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", repository, *arguments],
            check=True,
            capture_output=True,
            text=True,
        )

    def _repository(self, home: str) -> str:
        repository = os.path.join(home, "repos", "project")
        os.makedirs(repository)
        subprocess.run(
            ["git", "init", "--initial-branch=main", repository],
            check=True,
            capture_output=True,
            text=True,
        )
        self._git(repository, "config", "user.name", "Test Agent")
        self._git(repository, "config", "user.email", "agent@example.test")
        self._git(repository, "commit", "--allow-empty", "-m", "initial")
        return repository

    def test_create_lists_an_isolated_agent_branch(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            repository = self._repository(home)

            record = create_agent_worktree(repository, "api-check", home=home)
            worktrees = list_agent_worktrees(repository)

            self.assertEqual(record["branch"], "agent/api-check")
            self.assertTrue(str(record["path"]).startswith(home + os.path.sep))
            self.assertFalse(record["dirty"])
            self.assertEqual(len(worktrees), 2)
            self.assertEqual(sum(bool(item["main"]) for item in worktrees), 1)

    def test_remove_refuses_dirty_and_unmerged_work_before_safe_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            repository = self._repository(home)
            record = create_agent_worktree(repository, "worker", home=home)
            worktree = str(record["path"])
            changed = os.path.join(worktree, "change.txt")
            Path(changed).write_text("work\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "uncommitted or untracked"):
                remove_agent_worktree(worktree, home=home)

            self._git(worktree, "add", "change.txt")
            self._git(worktree, "commit", "-m", "task change")
            with self.assertRaisesRegex(ValueError, "not merged"):
                remove_agent_worktree(worktree, home=home)

            self._git(repository, "merge", "--ff-only", "agent/worker")
            preview = remove_agent_worktree(worktree, home=home, dry_run=True)
            self.assertEqual(preview["status"], "planned")
            self.assertTrue(os.path.isdir(worktree))

            removed = remove_agent_worktree(worktree, home=home)

            self.assertEqual(removed["status"], "removed")
            self.assertFalse(os.path.exists(worktree))
            self.assertNotIn(
                "refs/heads/agent/worker",
                self._git(repository, "for-each-ref", "--format=%(refname)").stdout,
            )

    def test_create_rejects_unsafe_task_and_symlinked_root(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            repository = self._repository(home)
            with self.assertRaisesRegex(ValueError, "Task name"):
                create_agent_worktree(repository, "../escape", home=home)

            outside = os.path.join(home, "outside")
            os.mkdir(outside)
            linked_root = os.path.join(home, "linked")
            os.symlink(outside, linked_root)
            with self.assertRaisesRegex(ValueError, "Unsafe agent worktree"):
                create_agent_worktree(
                    repository,
                    "safe-task",
                    root=linked_root,
                    home=home,
                )

    def test_parser_routes_workspace_command(self) -> None:
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        add_agent_subparser(subparsers)
        args = parser.parse_args(
            ["agent", "workspace", "list", "/tmp/repository", "--json"]
        )
        with patch(
            "lib.agent_workspace.list_agent_worktrees",
            return_value=[],
        ):
            self.assertEqual(run_agent_command(args), 0)


class AgentSupportTests(unittest.TestCase):
    def _host_result(self) -> dict[str, object]:
        return {
            "healthy": True,
            "status": "warning",
            "memory": {"total_bytes": 1},
            "disk": {"path": "/secret/home", "free_bytes": 2},
            "agent_storage": {
                "paths": {"npm_cache": "/secret/home/.npm"},
                "size_bytes": {"npm_cache": 3},
                "codex_release_count": 2,
            },
            "t3_service": {"memory_current_bytes": 4},
            "maintenance": {},
            "maintenance_hold": {
                "status": "active",
                "active": True,
                "created_at": "2026-08-27T12:00:00Z",
                "expires_at": "2026-08-27T20:00:00Z",
                "remaining_seconds": 28_800,
            },
            "reboot_pending": False,
            "warnings": ["capacity warning"],
            "errors": [],
        }

    def test_support_bundle_omits_paths_identity_and_log_contents(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            with (
                patch(
                    "lib.agent_support.build_setup_snapshot_metadata",
                    return_value={
                        "version": "2.0.0",
                        "commit": "a" * 40,
                        "branch": "private-feature-name",
                        "dirty": False,
                    },
                ),
                patch(
                    "lib.agent_cli.inspect_agent_tools",
                    return_value=[
                        {
                            "tool": "codex",
                            "installed": True,
                            "path": "/secret/home/bin/codex",
                            "version": "codex 1.0",
                            "credential": True,
                        }
                    ],
                ),
                patch(
                    "lib.agent_cli.inspect_host_readiness",
                    return_value=self._host_result(),
                ),
                patch(
                    "lib.agent_cli.inspect_t3code",
                    return_value={
                        "healthy": True,
                        "checks": {"service_active": True},
                        "version": "t3 1.0",
                        "git_identity": {
                            "name": "Private Name",
                            "email": "private@example.test",
                        },
                        "service_log": "/secret/home/log",
                    },
                ),
                patch(
                    "lib.agent_cli.inspect_browser_automation",
                    return_value={
                        "installed": False,
                        "path": "/secret/browser",
                        "launchers_secure": True,
                        "launcher_features": {
                            "browser_selection": False,
                            "private_evidence": True,
                            "bounded_evidence": True,
                            "coordinate_input": True,
                            "webgl_settle_delay": False,
                            "private_path": "/secret/browser-output",
                        },
                        "managed_defaults": False,
                        "running_processes": {
                            "total": 1,
                            "stale": 1,
                            "inspected": True,
                            "private_pid": 123,
                        },
                        "registrations": {},
                        "workflow_skills": [
                            "basaltwater-t3-preview-testing",
                            "/secret/browser-skill",
                        ],
                        "workflow_skill_ready": False,
                        "configured": False,
                        "smoke_test": False,
                        "healthy": False,
                        "issues": [
                            "launchers_missing",
                            "mcp_browser_selection_missing",
                            "registration_missing",
                            "workflow_skill_missing_or_stale",
                            "/secret/browser",
                        ],
                        "remediation": "rerun_setup_with_browser_automation",
                    },
                ),
                patch(
                    "lib.agent_cli.inspect_development_readiness",
                    return_value={
                        "installed": True,
                        "healthy": False,
                        "issues": ["node_pnpm_missing", "/secret/toolchain"],
                        "toolchains": {
                            "node": {
                                "installed": True,
                                "healthy": False,
                                "version": "v24.20.0",
                                "npm": "11.19.0",
                                "pnpm": None,
                                "path": "/secret/node",
                            }
                        },
                    },
                ),
            ):
                bundle = build_agent_support_bundle(home)

            rendered = json.dumps(bundle)
            self.assertNotIn("/secret", rendered)
            self.assertNotIn("Private Name", rendered)
            self.assertNotIn("private@example.test", rendered)
            self.assertNotIn("private-feature-name", rendered)
            self.assertFalse(bundle["privacy"]["log_contents_included"])
            self.assertFalse(bundle["privacy"]["installation_branch_included"])
            self.assertEqual(bundle["host"]["maintenance_hold"]["status"], "active")
            self.assertEqual(
                bundle["basaltwater"],
                {
                    "version": "2.0.0",
                    "commit": "a" * 40,
                    "dirty": False,
                },
            )
            self.assertEqual(
                bundle["browser"]["launcher_features"],
                {
                    "browser_selection": False,
                    "private_evidence": True,
                    "bounded_evidence": True,
                    "coordinate_input": True,
                    "webgl_settle_delay": False,
                },
            )
            self.assertFalse(bundle["browser"]["managed_defaults"])
            self.assertTrue(bundle["browser"]["launchers_secure"])
            self.assertEqual(
                bundle["browser"]["running_processes"],
                {"total": 1, "stale": 1, "inspected": True},
            )
            self.assertEqual(
                bundle["browser"]["workflow_skills"],
                ["basaltwater-t3-preview-testing"],
            )
            self.assertFalse(bundle["browser"]["workflow_skill_ready"])
            self.assertEqual(
                bundle["browser"]["issues"],
                [
                    "launchers_missing",
                    "mcp_browser_selection_missing",
                    "registration_missing",
                    "workflow_skill_missing_or_stale",
                ],
            )
            self.assertEqual(
                bundle["browser"]["remediation"],
                "rerun_setup_with_browser_automation",
            )
            self.assertEqual(bundle["development"]["issues"], ["node_pnpm_missing"])
            self.assertNotIn("path", bundle["development"]["toolchains"]["node"])

    def test_support_bundle_writes_new_private_file_below_home(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            path = os.path.join(home, "support.json")
            written = write_agent_support_bundle({"schema_version": 1}, path, home)

            self.assertEqual(written, path)
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            with self.assertRaisesRegex(ValueError, "already exists"):
                write_agent_support_bundle({"schema_version": 1}, path, home)

    def test_support_bundle_rejects_symlinked_parent(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            actual = os.path.join(home, "actual")
            linked = os.path.join(home, "linked")
            os.mkdir(actual)
            os.symlink(actual, linked)

            with self.assertRaisesRegex(ValueError, "symbolic link"):
                write_agent_support_bundle(
                    {"schema_version": 1},
                    os.path.join(linked, "support.json"),
                    home,
                )


if __name__ == "__main__":
    unittest.main()
