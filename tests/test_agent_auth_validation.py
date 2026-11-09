"""Tests for unambiguous agent credential source declarations."""

from __future__ import annotations

import unittest
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from lib.agent_auth import _remote_set_script, _remote_status_script
from lib.config import SetupConfig
from lib.validation import validate_agent_git_settings


class AgentAuthValidationTests(unittest.TestCase):
    def test_claude_status_reports_override_names_without_values_or_execution(self) -> None:
        for overrides in (
            {},
            {"ANTHROPIC_API_KEY": "private-fixture", "CLAUDE_CONFIG_DIR": "/private/fixture"},
            {"CLAUDE_CODE_OAUTH_TOKEN": "private-fixture", "CLAUDE_CODE_OAUTH_REFRESH_TOKEN": "private-fixture",
             "CLAUDE_CODE_OAUTH_SCOPES": "private-fixture", "ANTHROPIC_CUSTOM_HEADERS": "private-fixture"},
            {"CLAUDE_CODE_OAUTH_TOKEN": "", "UNRELATED_SECRET": "private-fixture"},
        ):
            with self.subTest(overrides=bool(overrides)), tempfile.TemporaryDirectory() as home:
                with patch.dict(os.environ, {"HOME": home, **overrides}, clear=True), \
                     patch("shutil.which", return_value="/fixture/claude"), \
                     patch("subprocess.run") as run, redirect_stdout(io.StringIO()) as output:
                    exec(_remote_status_script("claude", "github.com"), {})
                value = json.loads(output.getvalue())
                self.assertCountEqual(value["environment_overrides"], [
                    name for name, value in overrides.items() if value and name != "UNRELATED_SECRET"
                ])
                self.assertIsNone(value["authentication"])
                self.assertFalse(value["credential"]["present"])
                self.assertNotIn("private-fixture", output.getvalue())
                self.assertNotIn("/private/fixture", output.getvalue())
                run.assert_not_called()

    def test_claude_rotation_rejects_custom_config_directory_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            custom = Path(home) / "custom-claude"
            custom.mkdir()
            credential = custom / ".credentials.json"
            credential.write_bytes(b"existing-private-fixture")
            with patch.dict(os.environ, {"HOME": home, "CLAUDE_CONFIG_DIR": str(custom)}, clear=True), \
                 patch("subprocess.run") as run, redirect_stdout(io.StringIO()) as output:
                with self.assertRaisesRegex(RuntimeError, "custom CLAUDE_CONFIG_DIR"):
                    exec(_remote_set_script("claude", "github.com"), {})
            self.assertFalse((Path(home) / ".claude").exists())
            self.assertEqual(credential.read_bytes(), b"existing-private-fixture")
            self.assertEqual(output.getvalue(), "")
            run.assert_not_called()

    def test_claude_rotation_writes_standard_home_privately_without_execution(self) -> None:
        for explicit in (False, True):
            with self.subTest(explicit=explicit), tempfile.TemporaryDirectory() as home:
                destination = Path(home) / ".claude/.credentials.json"
                environment = {"HOME": home}
                if explicit:
                    environment["CLAUDE_CONFIG_DIR"] = str(destination.parent)
                payload = b'{"claudeAiOauth":{"accessToken":"private-fixture"}}'
                with patch.dict(os.environ, environment, clear=True), \
                     patch("sys.stdin", SimpleNamespace(buffer=io.BytesIO(payload))), \
                     patch("subprocess.run") as run, redirect_stdout(io.StringIO()) as output:
                    exec(_remote_set_script("claude", "github.com"), {})
                self.assertEqual(destination.read_bytes(), payload)
                self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
                self.assertEqual(json.loads(output.getvalue()), {
                    "path": str(destination), "present": True, "authentication": None,
                })
                self.assertNotIn("private-fixture", output.getvalue())
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
