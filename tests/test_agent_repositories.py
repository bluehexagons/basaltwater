"""Preserve sibling checkouts across equivalent HTTPS clone URL spellings."""

from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from common import agent_steps, cachyos_steps
from lib.config import SetupConfig


class AgentRepositoryTests(unittest.TestCase):
    def check_existing(self, profile: str, requested: str, actual: str) -> list[list[str]]:
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            home = Path(temporary)
            destination = home / "workspace/project"
            (destination / ".git").mkdir(parents=True)
            marker = destination / "personal.txt"
            marker.write_text("unfinished work\n")
            config = SetupConfig(host="localhost", username="human", system_type=profile,
                                 agent_workspace=str(destination.parent), agent_repos=[requested])
            commands = []

            def run(argv, **kwargs):
                commands.append(argv)
                self.assertEqual(argv[:3], ["git", "-C", str(destination)])
                if argv[3:] == ["remote", "get-url", "origin"]:
                    output = actual
                elif argv[3:] == ["rev-parse", "--show-toplevel"]:
                    output = str(destination)
                elif argv[3:] == ["rev-parse", "--absolute-git-dir"]:
                    output = str(destination / ".git")
                else:
                    self.fail(f"Unexpected repository mutation: {argv}")
                return subprocess.CompletedProcess(argv, 0, output + "\n", "")

            if profile == "agent_cachyos":
                stack.enter_context(patch.object(cachyos_steps, "_home", return_value=home))
                stack.enter_context(patch.object(cachyos_steps, "_user_run",
                                          side_effect=lambda argv, account_home, **kwargs: run(argv, **kwargs)))
                operation = cachyos_steps.prepare_cachyos_workspace
            else:
                stack.enter_context(patch.object(agent_steps, "_user_home", return_value=str(home)))
                stack.enter_context(patch.object(agent_steps, "_chown_path"))
                stack.enter_context(patch.object(agent_steps, "is_dry_run", return_value=False))
                stack.enter_context(patch("common.storage_steps.assert_declared_storage_mount"))
                stack.enter_context(patch.object(agent_steps, "_run_as_login_user",
                                          side_effect=lambda user, account_home, command, **kwargs:
                                          run(shlex.split(command), **kwargs)))
                operation = agent_steps.clone_agent_repositories
            try:
                operation(config)
            finally:
                self.assertEqual(marker.read_text(), "unfinished work\n")
            return commands

    def test_equivalent_https_origins_retain_existing_checkouts(self):
        base = "https://github.com/example/project"
        for profile in ("agent_cachyos", "agent_code_vm"):
            for requested, actual in ((base + ".git", base), (base, base + ".git/")):
                with self.subTest(profile=profile, requested=requested, actual=actual):
                    commands = self.check_existing(profile, requested, actual)
                    self.assertTrue(commands)

    def test_different_origins_still_fail_without_repository_changes(self):
        requested = "https://github.com/example/project.git"
        for profile in ("agent_cachyos", "agent_code_vm"):
            for actual in ("https://github.com/other/project", "https://git.example.net/example/project.git",
                           "git@github.com:example/project.git"):
                with self.subTest(profile=profile, actual=actual):
                    with self.assertRaisesRegex((ValueError, RuntimeError), "different origin"):
                        self.check_existing(profile, requested, actual)
