"""Tests for isolated agent worktrees and redacted support snapshots."""

from __future__ import annotations

import argparse
import contextlib
import io
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
    adopt_agent_worktree,
    inspect_workspace_group,
    inspect_agent_worktree,
    list_agent_worktrees,
    remove_agent_worktree,
    run_agent_workspace_command,
)


class AgentWorkspaceTests(unittest.TestCase):
    def test_status_reports_ancestry_and_fails_on_git_errors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            primary = os.path.join(directory, "primary")
            worktree = os.path.join(directory, "worker")
            task = {"path": worktree, "branch": "agent/worker", "head": "a" * 40,
                    "dirty": False, "main": False}
            destination = {"path": primary, "branch": "main", "head": "b" * 40,
                           "dirty": False, "main": True}
            for code in (0, 1, 128):
                with self.subTest(code=code), patch(
                    "lib.agent_workspace.primary_agent_repository", return_value=primary,
                ), patch(
                    "lib.agent_workspace._worktree_record", side_effect=[task.copy(), destination],
                ), patch(
                    "lib.agent_workspace._git",
                    return_value=subprocess.CompletedProcess([], code, "", ""),
                ) as git:
                    if code == 128:
                        with self.assertRaisesRegex(RuntimeError, "ancestry"):
                            inspect_agent_worktree(worktree)
                    else:
                        record = inspect_agent_worktree(worktree)
                        self.assertEqual(record["merged_into_primary"], code == 0)
                        self.assertEqual(record["primary_branch"], "main")
                        self.assertEqual(record["repository"], primary)
                    git.assert_called_once_with(
                        primary, ["merge-base", "--is-ancestor", "a" * 40, "b" * 40], check=False,
                    )

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

    def test_owned_feature_branch_keeps_dirty_and_merge_checks(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            repository = self._repository(home)
            record = create_agent_worktree(repository, "typescript", branch="feature/typescript-migration", home=home)
            worktree = record["path"]
            self.assertTrue(record["managed"])
            self.assertEqual(record["branch"], "feature/typescript-migration")
            self.assertEqual(inspect_agent_worktree(worktree)["task"], "typescript")
            Path(worktree, "feature.txt").write_text("feature", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "uncommitted"):
                remove_agent_worktree(worktree, home=home, dry_run=True)
            self._git(worktree, "add", "feature.txt")
            self._git(worktree, "commit", "-m", "feature")
            with self.assertRaisesRegex(ValueError, "not merged"):
                remove_agent_worktree(worktree, home=home, dry_run=True)
            self._git(repository, "merge", "--ff-only", record["branch"])
            self.assertEqual(remove_agent_worktree(worktree, home=home, dry_run=True)["status"], "planned")
            remove_agent_worktree(worktree, home=home)
            self.assertFalse(os.path.exists(worktree))

    def test_adoption_requires_explicit_ownership_and_preserves_dirty_files(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            repository = self._repository(home)
            root = Path(home) / "managed"
            root.mkdir(mode=0o700)
            worktree = root / "feature"
            self._git(repository, "worktree", "add", "-b", "feature/existing", str(worktree))
            with self.assertRaisesRegex(ValueError, "ownership record"):
                remove_agent_worktree(str(worktree), root=str(root), home=home, dry_run=True)
            Path(worktree, "keep.txt").write_text("uncommitted work", encoding="utf-8")
            record = adopt_agent_worktree(str(worktree), task="related", root=str(root), home=home)
            self.assertTrue(record["dirty"])
            self.assertEqual(Path(worktree, "keep.txt").read_text(), "uncommitted work")
            with self.assertRaisesRegex(ValueError, "uncommitted"):
                remove_agent_worktree(str(worktree), root=str(root), home=home, dry_run=True)
            self._git(str(worktree), "branch", "-m", "feature/renamed")
            with self.assertRaisesRegex(ValueError, "ownership record"):
                remove_agent_worktree(str(worktree), root=str(root), home=home, dry_run=True)

    def test_group_reports_owned_task_paths_across_related_repositories(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            repository = self._repository(home)
            related_home = Path(home) / "related"
            related_home.mkdir()
            related = self._repository(str(related_home))
            first = create_agent_worktree(repository, "migration", branch="feature/migration", home=home)
            second = create_agent_worktree(related, "migration", branch="feature/migration", home=home)
            result = inspect_workspace_group("migration", [repository, related, repository])
            self.assertEqual({item["path"] for item in result["workspaces"]}, {first["path"], second["path"]})
            self.assertEqual(result["missing_repositories"], [])
            self.assertEqual({item["branch"] for item in result["workspaces"]}, {"feature/migration"})

    def test_public_or_corrupt_ownership_never_allows_cleanup(self) -> None:
        from lib.agent_workspace import _ownership_path
        with tempfile.TemporaryDirectory() as home:
            repository = self._repository(home)
            record = create_agent_worktree(repository, "private", home=home)
            ownership = Path(_ownership_path(record["path"]))
            ownership.chmod(0o644)
            with self.assertRaisesRegex(ValueError, "Unsafe workspace ownership"):
                remove_agent_worktree(record["path"], home=home)
            self.assertTrue(Path(record["path"]).is_dir())
            ownership.chmod(0o600)
            ownership.write_text("{malformed", encoding="utf-8")
            with self.assertRaises(ValueError):
                remove_agent_worktree(record["path"], home=home)
            self.assertTrue(Path(record["path"]).is_dir())

    def test_json_failures_have_stable_issue_and_remediation(self) -> None:
        args = argparse.Namespace(agent_workspace_command="remove", path="/missing", workspace_root=None, dry_run=True, json=True)
        stdout = io.StringIO()
        from lib.agent_workspace import WorkspaceIssue
        with (
            patch("lib.agent_workspace.remove_agent_worktree", side_effect=WorkspaceIssue("workspace_unmerged", "not merged", "Merge the reviewed branch before cleanup.")),
            contextlib.redirect_stdout(stdout),
        ):
            self.assertEqual(run_agent_workspace_command(args), 1)
        result = json.loads(stdout.getvalue())
        self.assertFalse(result["ok"])
        self.assertEqual(result["issue"], "workspace_unmerged")
        self.assertIn("Merge", result["remediation"])
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(run_agent_workspace_command(argparse.Namespace(agent_workspace_command=None)), 1)
        self.assertIn("Error:", stdout.getvalue())


class AgentSupportTests(unittest.TestCase):
    def _host_result(self) -> dict[str, object]:
        return {
            "healthy": True,
            "status": "warning",
            "memory": {"total_bytes": 1},
            "disk": {"path": "/secret/home", "free_bytes": 2,
                     "root_filesystem": {"free_bytes": 1, "usage_percent": 95}},
            "agent_storage": {
                "paths": {"npm_cache": "/secret/home/.npm"},
                "size_bytes": {"npm_cache": 3},
                "codex_release_count": 2,
            },
            "t3_service": {"memory_current_bytes": 4},
            "container_storage": {"requested": True, "engines": [{
                "engine": "podman", "available": True, "rootless": True,
                "graph_root": "/secret/home/containers", "image_count": 10,
                "registry": "https://private-registry", "images": ["private-image"],
                "labels": {"owner": "Private Name"},
                "filesystem": {"available": True, "free_bytes": 6, "mountpoint": "/secret/home"},
            }]},
            "workload_limits": {"scope": "current-process", "affinity_available": True,
                                "affinity_cpu_count": 6, "cgroup_path": "/secret/workload",
                                "cpu_quota": {"available": True, "value": 1.5, "path": "/secret/quota"}},
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
                    "lib.agent_support.inspect_installation_provenance",
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
                        "installed": True,
                        "path": "/secret/browser",
                        "launchers_secure": None,
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
                        "smoke_test": None,
                        "healthy": False,
                        "issues": [
                            "launchers_unverified",
                            "mcp_browser_selection_missing",
                            "registration_missing",
                            "registration_unverified",
                            "workflow_skill_missing_or_stale",
                            "/secret/browser",
                        ],
                        "remediation": "verify_launchers_from_host",
                    },
                ),
                patch(
                    "lib.agent_cli.inspect_development_readiness",
                    return_value={
                        "installed": True,
                        "healthy": False,
                        "issues": ["node_pnpm_missing", "native_command_missing", "native_module_missing", "native_selection_unsafe", "/secret/toolchain"],
                        "toolchains": {
                            "node": {
                                "installed": True,
                                "healthy": False,
                                "version": "v24.20.0",
                                "npm": "11.19.0",
                                "pnpm": None,
                                "path": "/secret/node",
                            },
                            "native": {
                                "installed": True,
                                "selected": None,
                                "healthy": False,
                                "commands": {"cmake": False, "cc": True, "gdb": "/secret/gdb", "/secret/compiler": False},
                                "modules": {"glew": None, "openal": "1.24.2", "sdl3": "/secret/sdl", "/secret/library": None},
                                "project_bootstrap_required": ["sdl3", "sdl3-image", "/secret/bootstrap"],
                                "path": "/secret/native",
                            },
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
            self.assertNotIn("private-registry", rendered)
            self.assertNotIn("private-image", rendered)
            self.assertFalse(bundle["privacy"]["log_contents_included"])
            self.assertFalse(bundle["privacy"]["installation_branch_included"])
            self.assertEqual(bundle["host"]["container_storage"]["engines"][0]["image_count"], 10)
            self.assertEqual(bundle["host"]["workload_limits"]["cpu_quota"]["value"], 1.5)
            self.assertEqual(bundle["host"]["maintenance_hold"]["status"], "active")
            self.assertEqual(bundle["host"]["disk"]["root_filesystem"], {"free_bytes": 1, "usage_percent": 95})
            self.assertEqual(
                bundle["basaltwater"],
                {
                    "version": "2.0.0",
                    "commit": "a" * 40,
                    "dirty": False,
                    "installation_type": None,
                    "evidence_source": None,
                    "setup_snapshot": None,
                    "refresh_receipt": None,
                    "running_source": None,
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
            self.assertIsNone(bundle["browser"]["launchers_secure"])
            self.assertIsNone(bundle["browser"]["smoke_test"])
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
                    "launchers_unverified",
                    "mcp_browser_selection_missing",
                    "registration_missing",
                    "registration_unverified",
                    "workflow_skill_missing_or_stale",
                ],
            )
            self.assertEqual(
                bundle["browser"]["remediation"],
                "verify_launchers_from_host",
            )
            self.assertEqual(bundle["development"]["issues"], [
                "node_pnpm_missing", "native_command_missing", "native_module_missing", "native_selection_unsafe",
            ])
            self.assertNotIn("path", bundle["development"]["toolchains"]["node"])
            self.assertEqual(bundle["development"]["toolchains"]["native"], {
                "installed": True, "selected": None, "healthy": False,
                "commands": {"cmake": False, "cc": True},
                "modules": {"glew": None, "openal": "1.24.2"},
                "project_bootstrap_required": ["sdl3", "sdl3-image"],
            })

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
