"""Offline checks for unattended task policy qualification."""

from __future__ import annotations

import copy
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from lib import agent_task_policy as policy
from lib import agent_tasks as tasks


class TaskPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = self.temporary.name
        self.path = Path(self.home) / "requirements.toml"
        self.policy_path = patch.object(policy, "REQUIREMENTS_PATH", self.path)
        self.policy_path.start()
        self.addCleanup(self.policy_path.stop)

    def issues(self, content: str, **settings: object) -> list[str]:
        self.path.write_text(content, encoding="utf-8")
        return policy.task_policy_issues({"mode": "inspect", **settings})

    def test_missing_empty_and_unrestricted_policy(self) -> None:
        self.assertEqual(policy.task_policy_issues({"mode": "inspect"}), [])
        for content in ("", "# no constraints\n", 'allow_login_shell = false\n'):
            with self.subTest(content=content):
                self.assertEqual(self.issues(content), [])
                self.assertEqual(self.issues(content, mode="workspace", network=True), [])

    def test_malformed_policy_errors_do_not_expose_content(self) -> None:
        for content in ('secret = "PRIVATE', 'allowed_sandbox_modes = "PRIVATE"',
                        'allowed_approval_policies = [false]', 'allowed_web_search_modes = {}',
                        'allowed_permission_profiles = []',
                        '[allowed_permission_profiles]\nPRIVATE = "yes"'):
            with self.subTest(content=content):
                self.assertEqual(self.issues(content), [policy._INVALID])
                self.assertNotIn("PRIVATE", " ".join(policy.task_policy_issues({"mode": "inspect"})))
        self.path.write_bytes(b"\xff")
        self.assertEqual(policy.task_policy_issues({"mode": "inspect"}), [policy._INVALID])

    def test_oversized_policy(self) -> None:
        self.assertEqual(self.issues("#" * (policy.MAX_REQUIREMENTS_BYTES + 1)), [policy._INVALID])

    def test_read_is_bounded_even_if_file_grows(self) -> None:
        self.path.write_text("#" * 100)
        metadata = self.path.stat()
        with patch.object(policy, "MAX_REQUIREMENTS_BYTES", 10), patch.object(
            policy.os, "fstat", return_value=type("Metadata", (), {"st_mode": metadata.st_mode, "st_size": 0})()
        ):
            self.assertEqual(policy.task_policy_issues({"mode": "inspect"}), [policy._INVALID])

    def test_unreadable_policy(self) -> None:
        with patch.object(policy.os, "open", side_effect=PermissionError("PRIVATE")):
            self.assertEqual(policy.task_policy_issues({"mode": "inspect"}), [policy._UNREADABLE])
        self.path.write_text("")
        with patch.object(policy.os, "fstat", side_effect=OSError("PRIVATE")):
            self.assertEqual(policy.task_policy_issues({"mode": "inspect"}), [policy._UNREADABLE])

    def test_nonregular_and_symlink_policy_are_blocked(self) -> None:
        self.path.mkdir()
        self.assertEqual(policy.task_policy_issues({"mode": "inspect"}), [policy._UNREADABLE])
        self.path.rmdir()
        self.path.symlink_to(Path(self.home) / "missing")
        self.assertEqual(policy.task_policy_issues({"mode": "inspect"}), [policy._UNREADABLE])

    def test_sandbox_constraint(self) -> None:
        content = 'allowed_sandbox_modes = ["read-only"]'
        self.assertEqual(self.issues(content), [])
        self.assertIn("sandbox mode", self.issues(content, mode="workspace")[0])
        self.assertIn("sandbox mode", self.issues('allowed_sandbox_modes = []')[0])

    def test_approval_constraint_requires_never(self) -> None:
        self.assertEqual(self.issues('allowed_approval_policies = ["never"]'), [])
        for value in ('[]', '["on-request"]'):
            self.assertIn("approval policy never", self.issues(f'allowed_approval_policies = {value}')[0])

    def test_web_search_empty_allows_only_disabled(self) -> None:
        self.assertEqual(self.issues('allowed_web_search_modes = []'), [])
        for search in ("cached", "live"):
            self.assertIn("web search mode", self.issues('allowed_web_search_modes = []', web_search=search)[0])
        self.assertEqual(self.issues('allowed_web_search_modes = ["cached"]', web_search="cached"), [])
        self.assertIn("web search mode", self.issues('allowed_web_search_modes = ["live"]')[0])

    def test_profile_allowlist_requires_read_only_true(self) -> None:
        self.assertEqual(self.issues('[allowed_permission_profiles]\n":read-only" = true'), [])
        for content in ('[allowed_permission_profiles]', '[allowed_permission_profiles]\n":read-only" = false'):
            self.assertIn(":read-only permission profile", self.issues(content)[0])

    def test_workspace_never_substitutes_allowlisted_custom_profile(self) -> None:
        content = ('default_permissions = "custom"\n[allowed_permission_profiles]\n'
                   'custom = true\n":workspace" = true\n":read-only" = true\n'
                   '[permissions.custom]\nextends = ":workspace"\n'
                   '[permissions.custom.network]\nenabled = false\n')
        for network in (False, True):
            with self.subTest(network=network):
                issues = self.issues(content, mode="workspace", network=network)
                self.assertEqual(len(issues), 1)
                self.assertIn("generated workspace profile is not allowlisted", issues[0])
        self.assertEqual(self.path.read_text(), content)

    def test_all_known_conflicts_reported(self) -> None:
        content = ('allowed_sandbox_modes = ["read-only"]\nallowed_approval_policies = []\n'
                   'allowed_web_search_modes = []\n[allowed_permission_profiles]\n":read-only" = true')
        self.assertEqual(len(self.issues(content, mode="workspace", web_search="live")), 4)

    def test_execution_blocks_before_provider_without_settings_mutation(self) -> None:
        content = '[allowed_permission_profiles]\n":read-only" = true\n'
        self.path.write_text(content)
        task = {"title": "Check", "prompt": "Inspect", "directory": self.home,
                "mode": "workspace", "interval": "hourly", "token_budget": 100}
        original = copy.deepcopy(task)
        with patch.object(tasks.os, "geteuid", return_value=1000), patch.object(
            tasks, "_tool_path"
        ) as provider, patch.object(tasks.subprocess, "Popen") as popen, patch.object(
            tasks, "codex_command"
        ) as command:
            result = tasks.execute_prompt(task, self.home, threading.Event())
        provider.assert_not_called()
        popen.assert_not_called()
        command.assert_not_called()
        self.assertEqual(task, original)
        self.assertEqual(self.path.read_text(), content)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["outcome"], "blocked")
        self.assertEqual(result["usage"], {"input_tokens": 0, "output_tokens": 0})
        self.assertIn("generated workspace profile is not allowlisted", result["output"])
        self.assertNotIn("checkpoint", result)
        self.assertIsNone(result["follow_up"])

    def test_blocked_policy_counts_toward_failure_limit(self) -> None:
        task = {**tasks.validate_task({"title": "Check", "prompt": "Inspect", "directory": self.home,
                                     "mode": "inspect", "interval": "hourly"}, self.home),
                "id": "a" * 32, "enabled": True, "queued": True, "consecutive_failures": 2,
                "tokens_used": 0, "attempts": 1, "next_run": 3600}
        manager = tasks.AgentTasks(self.home)
        manager._record_outcome({"tasks": [task]}, {
            "id": "b" * 32, "task": {"id": task["id"]}, "status": "failed", "outcome": "blocked",
            "finished_at": 100, "usage": {"input_tokens": 0, "output_tokens": 0}})
        self.assertEqual(task["consecutive_failures"], 3)
        self.assertTrue(task["auto_paused"])
        self.assertFalse(task["enabled"])
        self.assertFalse(task["queued"])


if __name__ == "__main__":
    unittest.main()
