"""Tests for unambiguous agent credential source declarations."""

from __future__ import annotations

import unittest
from contextlib import redirect_stdout
import io
import json
import os
import tempfile
from unittest.mock import patch

from lib.agent_auth import _remote_status_script
from lib.config import SetupConfig
from lib.validation import validate_agent_git_settings


class AgentAuthValidationTests(unittest.TestCase):
    def test_claude_status_reports_override_names_without_values_or_execution(self) -> None:
        for overrides in ({}, {"ANTHROPIC_API_KEY": "private-fixture", "CLAUDE_CONFIG_DIR": "/private/fixture"}):
            with self.subTest(overrides=bool(overrides)), tempfile.TemporaryDirectory() as home:
                with patch.dict(os.environ, {"HOME": home, **overrides}, clear=True), \
                     patch("shutil.which", return_value="/fixture/claude"), \
                     patch("subprocess.run") as run, redirect_stdout(io.StringIO()) as output:
                    exec(_remote_status_script("claude", "github.com"), {})
                value = json.loads(output.getvalue())
                self.assertEqual(value["environment_overrides"], list(overrides))
                self.assertIsNone(value["authentication"])
                self.assertFalse(value["credential"]["present"])
                self.assertNotIn("private-fixture", output.getvalue())
                self.assertNotIn("/private/fixture", output.getvalue())
                run.assert_not_called()

    def test_rejects_duplicate_agent_auth_file_tools(self) -> None:
        config = SetupConfig(
            host="target",
            username="agent",
            system_type="server_dev",
            agent_tools=["codex"],
            agent_auth_files=[
                ["codex", "/run/secrets/first.json"],
                ["codex", "/run/secrets/second.json"],
            ],
        )

        with self.assertRaisesRegex(ValueError, "Duplicate --agent-auth-file"):
            validate_agent_git_settings(config)

    def test_rejects_duplicate_github_sources(self) -> None:
        config = SetupConfig(
            host="target",
            username="agent",
            system_type="server_dev",
            agent_tools=["gh"],
            git_access="read",
            git_auth_source="active",
            agent_auth_files=[["gh", "/run/secrets/hosts.yml"]],
        )

        with self.assertRaisesRegex(ValueError, "either --git-auth"):
            validate_agent_git_settings(config)


if __name__ == "__main__":
    unittest.main()
