"""Tests for managed installation channel selection and upgrades."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from lib import channel_manager
from lib.remote_utils import CommandTimeoutError
from lib.channel_manager import ChannelError, get_channel_info, switch_channel, upgrade_channel
from lib.validation import validate_channel


def _git(cwd: str, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


class TestChannelValidation(unittest.TestCase):
    def test_git_timeout_is_bounded_and_reported(self) -> None:
        with patch.object(channel_manager, 'run', side_effect=CommandTimeoutError('git fetch', 300)) as run:
            with self.assertRaisesRegex(ChannelError, 'saved channel state is unchanged'):
                channel_manager._run_git('/repository', ['fetch', 'origin'])
        self.assertEqual(run.call_args.kwargs['timeout'], 300)
        self.assertEqual(run.call_args.args[0][:3], ['env', 'GIT_TERMINAL_PROMPT=0', 'git'])

    def test_accepts_supported_channels(self) -> None:
        for channel in [
            "stable",
            "dev",
            "v1.2.3",
            "branch-main",
            "branch-feature/example",
            "commit-0123456",
        ]:
            self.assertEqual(validate_channel(channel), channel)

    def test_rejects_unsafe_or_unknown_channels(self) -> None:
        for channel in [
            "",
            "latest",
            "branch-../main",
            "branch-feature//example",
            "v1.2",
            "commit-not-a-hash",
        ]:
            with self.assertRaises(ValueError, msg=channel):
                validate_channel(channel)


class TestChannelManager(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        root = self.directory.name
        self.seed = os.path.join(root, "seed")
        self.remote = os.path.join(root, "remote.git")
        self.repo = os.path.join(root, "repo")

        os.makedirs(self.seed)
        _git(self.seed, "init", "--initial-branch=main")
        _git(self.seed, "config", "user.email", "tests@example.invalid")
        _git(self.seed, "config", "user.name", "infra_tools tests")
        with open(os.path.join(self.seed, ".gitignore"), "w", encoding="utf-8") as file_obj:
            file_obj.write(".infra_tools/\n")
        with open(os.path.join(self.seed, "version.txt"), "w", encoding="utf-8") as file_obj:
            file_obj.write("1.0\n")
        _git(self.seed, "add", ".")
        _git(self.seed, "commit", "-m", "initial")
        _git(self.seed, "tag", "v1.0.0")

        _git(self.seed, "checkout", "-b", "feature/example")
        with open(os.path.join(self.seed, "feature.txt"), "w", encoding="utf-8") as file_obj:
            file_obj.write("feature\n")
        _git(self.seed, "add", "feature.txt")
        _git(self.seed, "commit", "-m", "feature")
        self.feature_commit = _git(self.seed, "rev-parse", "HEAD")
        _git(self.seed, "checkout", "main")
        with open(os.path.join(self.seed, "version.txt"), "w", encoding="utf-8") as file_obj:
            file_obj.write("1.1\n")
        _git(self.seed, "add", "version.txt")
        _git(self.seed, "commit", "-m", "release")
        _git(self.seed, "tag", "v1.1.0")

        _git(root, "init", "--bare", self.remote)
        _git(self.seed, "remote", "add", "origin", self.remote)
        _git(self.seed, "push", "origin", "--all")
        _git(self.seed, "push", "origin", "--tags")
        _git(root, "clone", self.remote, self.repo)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_stable_selects_highest_release_and_branch_channel(self) -> None:
        stable = switch_channel(self.repo, "stable")
        self.assertEqual(stable["channel"], "stable")
        self.assertEqual(stable["commit"], _git(self.repo, "rev-parse", "v1.1.0"))

        branch = switch_channel(self.repo, "branch-feature/example")
        self.assertEqual(branch["commit"], self.feature_commit)

    def test_dev_upgrade_follows_main(self) -> None:
        switch_channel(self.repo, "dev")
        previous = _git(self.repo, "rev-parse", "HEAD")
        with open(os.path.join(self.seed, "version.txt"), "w", encoding="utf-8") as file_obj:
            file_obj.write("1.2\n")
        _git(self.seed, "add", "version.txt")
        _git(self.seed, "commit", "-m", "development update")
        _git(self.seed, "push", "origin", "main")

        result = upgrade_channel(self.repo)
        self.assertTrue(result["updated"])
        self.assertNotEqual(result["commit"], previous)
        self.assertEqual(result["commit"], _git(self.repo, "rev-parse", "origin/main"))

    def test_commit_channel_and_dirty_worktree_are_handled(self) -> None:
        result = switch_channel(self.repo, f"commit-{self.feature_commit}")
        self.assertEqual(result["commit"], self.feature_commit)
        self.assertEqual(get_channel_info(self.repo)["channel"], f"commit-{self.feature_commit}")

        with open(os.path.join(self.repo, "local.txt"), "w", encoding="utf-8") as file_obj:
            file_obj.write("do not overwrite\n")
        with self.assertRaises(ChannelError):
            switch_channel(self.repo, "dev")


if __name__ == "__main__":
    unittest.main()
