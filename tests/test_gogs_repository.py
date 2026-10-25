"""Tests for GitHub/Gogs repository configuration."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from lib.gogs_repository import (
    configure_github_gogs_repository,
    normalize_repository_url,
)
from lib.types import StrList


def _completed(
    command: StrList,
    stdout: str = "",
    returncode: int = 0,
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(command, returncode, stdout=stdout, stderr="")


class TestRepositoryUrlValidation(unittest.TestCase):
    def test_normalizes_https_repository_urls(self):
        self.assertEqual(
            normalize_repository_url(
                "https://github.com/team/project",
                label="GitHub repository URL",
                github=True,
            ),
            "https://github.com/team/project.git",
        )

    def test_rejects_credentials_and_non_github_host(self):
        with self.assertRaisesRegex(ValueError, "embedded credentials"):
            normalize_repository_url(
                "https://user:secret@git.example.test/team/project.git",
                label="Gogs repository URL",
            )
        with self.assertRaisesRegex(ValueError, "github.com"):
            normalize_repository_url(
                "https://example.test/team/project.git",
                label="GitHub repository URL",
                github=True,
            )


class TestConfigureRepository(unittest.TestCase):
    def _runner(self, root: str, filesystem: str = "ext4"):
        calls: list[StrList] = []

        def run(command: StrList, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append(command)
            if command[:2] == ["findmnt", "-n"]:
                return _completed(command, f"{filesystem}\n")
            arguments = command[3:]
            if arguments == ["rev-parse", "--show-toplevel"]:
                return _completed(command, f"{root}\n")
            if arguments == ["remote"]:
                return _completed(command, "origin\n")
            return _completed(command)

        return calls, run

    def test_configures_dual_push_remotes_and_gogs_lfs(self):
        with tempfile.TemporaryDirectory() as root:
            calls, runner = self._runner(root)
            with patch("lib.gogs_repository.subprocess.run", side_effect=runner):
                result = configure_github_gogs_repository(
                    root,
                    "https://github.com/team/project.git",
                    "https://git.example.test/team/project.git",
                    track_patterns=["assets/**", "*.blend"],
                )

        git_arguments = [
            command[3:] for command in calls if command[:2] == ["git", "-C"]
        ]
        self.assertIn(
            [
                "config",
                "--replace-all",
                "remote.origin.pushurl",
                "https://github.com/team/project.git",
            ],
            git_arguments,
        )
        self.assertIn(
            [
                "config",
                "--add",
                "remote.origin.pushurl",
                "https://git.example.test/team/project.git",
            ],
            git_arguments,
        )
        self.assertIn(
            [
                "config",
                "--file",
                os.path.join(root, ".lfsconfig"),
                "lfs.url",
                "https://git.example.test/team/project.git/info/lfs",
            ],
            git_arguments,
        )
        self.assertIn(["lfs", "track", "assets/**"], git_arguments)
        self.assertIn(["lfs", "env"], git_arguments)
        self.assertIn(["lfs", "version"], git_arguments)
        self.assertIn(["lfs", "install", "--local"], git_arguments)
        self.assertEqual(result["filesystem"], "ext4")

    def test_dry_run_does_not_apply_mutating_commands(self):
        with tempfile.TemporaryDirectory() as root:
            calls, runner = self._runner(root)
            with patch("lib.gogs_repository.subprocess.run", side_effect=runner):
                result = configure_github_gogs_repository(
                    root,
                    "https://github.com/team/project.git",
                    "https://git.example.test/team/project.git",
                    dry_run=True,
                )

        git_arguments = [
            command[3:] for command in calls if command[:2] == ["git", "-C"]
        ]
        self.assertNotIn(["lfs", "install", "--local"], git_arguments)
        self.assertNotIn(["lfs", "status"], git_arguments)
        self.assertFalse(
            any(arguments[:1] == ["config"] for arguments in git_arguments)
        )
        self.assertFalse(
            any(arguments[:2] == ["lfs", "track"] for arguments in git_arguments)
        )
        self.assertIn(
            ["status", "--porcelain", "--untracked-files=all"],
            git_arguments,
        )
        self.assertIn(["lfs", "version"], git_arguments)
        self.assertIn(["lfs", "env"], git_arguments)
        self.assertTrue(any(command[:2] == ["findmnt", "-n"] for command in calls))
        self.assertTrue(result["dry_run"])
        self.assertTrue(result["actions"])

    def test_no_combined_push_resets_origin_to_github_only(self):
        with tempfile.TemporaryDirectory() as root:
            calls, runner = self._runner(root)
            with patch("lib.gogs_repository.subprocess.run", side_effect=runner):
                configure_github_gogs_repository(
                    root,
                    "https://github.com/team/project.git",
                    "https://git.example.test/team/project.git",
                    combined_push=False,
                )

        git_arguments = [
            command[3:] for command in calls if command[:2] == ["git", "-C"]
        ]
        self.assertIn(
            [
                "config",
                "--replace-all",
                "remote.origin.pushurl",
                "https://github.com/team/project.git",
            ],
            git_arguments,
        )
        self.assertNotIn(
            [
                "config",
                "--add",
                "remote.origin.pushurl",
                "https://git.example.test/team/project.git",
            ],
            git_arguments,
        )

    def test_rejects_dirty_or_network_mounted_worktree(self):
        with tempfile.TemporaryDirectory() as root:
            _calls, runner = self._runner(root)

            def dirty_run(
                command: StrList, **kwargs: object
            ) -> subprocess.CompletedProcess[str]:
                if command[3:] == ["status", "--porcelain", "--untracked-files=all"]:
                    return _completed(command, "?? asset.bin\n")
                return runner(command, **kwargs)

            with patch("lib.gogs_repository.subprocess.run", side_effect=dirty_run):
                with self.assertRaisesRegex(ValueError, "must be clean"):
                    configure_github_gogs_repository(
                        root,
                        "https://github.com/team/project.git",
                        "https://git.example.test/team/project.git",
                    )

            _calls, network_runner = self._runner(root, filesystem="cifs")
            with patch("lib.gogs_repository.subprocess.run", side_effect=network_runner):
                with self.assertRaisesRegex(ValueError, "local storage"):
                    configure_github_gogs_repository(
                        root,
                        "https://github.com/team/project.git",
                        "https://git.example.test/team/project.git",
                    )

    def test_rejects_symlinked_lfs_configuration(self):
        with tempfile.TemporaryDirectory() as root:
            os.symlink("outside", os.path.join(root, ".lfsconfig"))
            _calls, runner = self._runner(root)
            with patch("lib.gogs_repository.subprocess.run", side_effect=runner):
                with self.assertRaisesRegex(ValueError, "regular file"):
                    configure_github_gogs_repository(
                        root,
                        "https://github.com/team/project.git",
                        "https://git.example.test/team/project.git",
                    )


if __name__ == "__main__":
    unittest.main()
