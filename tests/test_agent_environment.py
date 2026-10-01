"""Tests for read-only project environment discovery."""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from lib import agent_cli, agent_environment, agent_workspace


class TestAgentEnvironment(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.file = Path(self.directory, agent_environment.PROJECT_FILE)
        self.git = self.enterContext(patch.object(
            agent_workspace, "_git", return_value=subprocess.CompletedProcess([], 0, "", ""),
        ))

    def declaration(self, **fields) -> None:
        self.file.write_text(json.dumps({"version": 1, **fields}), encoding="utf-8")

    def test_missing_declarations_do_not_invent_deployments(self) -> None:
        with (
            patch.object(agent_workspace, "_repository_root", return_value=self.directory),
            patch.object(agent_workspace, "_effective_home", return_value=self.directory),
            patch.object(agent_workspace, "_worktree_record", return_value={"branch": "dev", "head": "a" * 40, "dirty": False}),
            patch.object(agent_environment.shutil, "which", return_value=None),
            patch.object(agent_environment.subprocess, "run") as execute,
        ):
            result = agent_environment.inspect_environment(self.directory)
        self.assertIsNone(result["current_deployment"])
        self.assertEqual(result["undeclared_branches"], ["dev", "staging"])
        self.assertEqual(result["deployments"], {})
        self.assertIsNone(result["tools"]["yarn"])
        execute.assert_not_called()

    def test_explicit_mappings_artifacts_and_current_branch(self) -> None:
        mapping = {"environment": "preproduction", "provider": "aws", "region": "us-east-1", "url": "https://staging.example.com/"}
        self.declaration(deployments={"staging": mapping}, artifact_directories=[".artifacts", ".artifacts"])
        with (
            patch.object(agent_workspace, "_repository_root", return_value=self.directory),
            patch.object(agent_workspace, "_effective_home", return_value=self.directory),
            patch.object(agent_workspace, "_worktree_record", return_value={"branch": "staging", "head": "b" * 40, "dirty": True}),
            patch.object(agent_environment.shutil, "which", side_effect=lambda name: "/bin/git" if name == "git" else None),
        ):
            result = agent_environment.inspect_environment(self.directory)
        self.assertEqual(result["current_deployment"], mapping)
        self.assertEqual(result["undeclared_branches"], ["dev"])
        self.assertEqual(result["project_source"], str(self.file))
        self.assertTrue(result["workspace"]["dirty"])
        self.assertEqual(result["workspace"]["artifact_directories"], [{"path": ".artifacts", "ignored": True}])
        self.assertEqual(result["tools"]["git"], "/bin/git")

    def test_invalid_schema_and_mapping_are_rejected(self) -> None:
        for data in (
            {"version": True}, {"version": 2}, {"version": 1, "secrets": {}},
            {"version": 1, "deployments": []},
            {"version": 1, "deployments": {"dev": {"provider": "aws"}}},
            {"version": 1, "deployments": {"dev": {"environment": "dev", "token": "secret"}}},
            {"version": 1, "deployments": {"--unsafe": {"environment": "dev"}}},
        ):
            with self.subTest(data=data):
                self.file.write_text(json.dumps(data), encoding="utf-8")
                with self.assertRaises(ValueError):
                    agent_environment.load_project_environment(self.directory)

    def test_git_rejects_invalid_branch(self) -> None:
        self.declaration(deployments={"dev..other": {"environment": "dev"}})
        self.git.return_value.returncode = 1
        with self.assertRaisesRegex(ValueError, "branch"):
            agent_environment.load_project_environment(self.directory)

    def test_urls_cannot_include_authentication_or_untrusted_schemes(self) -> None:
        for url in ("http://dev.example.com", "https://u:p@dev.example.com", "https://dev.example.com/?token=secret", "https://dev.example.com/#token", "https://dev.example.com:bad"):
            with self.subTest(url=url):
                self.declaration(deployments={"dev": {"environment": "dev", "url": url}})
                with self.assertRaises(ValueError):
                    agent_environment.load_project_environment(self.directory)

    def test_artifacts_must_be_relative_bounded_paths(self) -> None:
        for path in ("/tmp", "../other", "a/../../other", ".", "a\nother"):
            with self.subTest(path=path):
                self.declaration(artifact_directories=[path])
                with self.assertRaises(ValueError):
                    agent_environment.load_project_environment(self.directory)

    def test_unsafe_or_oversized_declaration_is_not_read(self) -> None:
        self.file.write_bytes(b" " * (64 * 1024 + 1))
        with self.assertRaises(ValueError):
            agent_environment.load_project_environment(self.directory)
        self.file.unlink()
        target = Path(self.directory, "other.json")
        target.write_text('{"version":1}')
        self.file.symlink_to(target)
        with self.assertRaises(OSError):
            agent_environment.load_project_environment(self.directory)
        self.file.unlink()
        os.mkfifo(self.file)
        with self.assertRaises(ValueError):
            agent_environment.load_project_environment(self.directory)

    def test_command_dispatches_and_emits_machine_readable_errors(self) -> None:
        parser = argparse.ArgumentParser()
        agent_cli.add_agent_subparser(parser.add_subparsers(dest="command"))
        args = parser.parse_args(["agent", "manifest", self.directory, "--json"])
        output = StringIO()
        with patch.object(agent_environment, "inspect_environment", side_effect=ValueError("invalid declaration")), redirect_stdout(output):
            self.assertEqual(agent_cli.run_agent_command(args), 1)
        self.assertEqual(json.loads(output.getvalue()), {"ok": False, "error": "invalid declaration"})


if __name__ == "__main__":
    unittest.main()
