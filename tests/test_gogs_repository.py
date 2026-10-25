"""Tests for GitHub/Gogs repository configuration."""

from __future__ import annotations

import os
import re
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
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        command,
        returncode,
        stdout=stdout,
        stderr=stderr,
    )


def _is_mutating(arguments: StrList) -> bool:
    if arguments[:1] == ["config"]:
        query_options = {"--get", "--get-all", "--get-regexp"}
        return not any(argument in query_options for argument in arguments)
    if arguments[:1] == ["remote"]:
        return len(arguments) > 1 and arguments[1] in {"add", "set-url"}
    return (
        arguments[:1] == ["lfs"]
        and len(arguments) > 1
        and arguments[1] in {"install", "track"}
    )


def _origin_records(*entries: tuple[str, str]) -> str:
    return "".join(f"{origin}\0{value}\0" for origin, value in entries)


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
    def _runner(
        self,
        root: str,
        filesystem: str = "ext4",
        *,
        effective_remotes: str = "",
        config_results: dict[
            tuple[str | None, str], tuple[int, str, str]
        ] | None = None,
        config_origin_results: dict[str, tuple[int, str, str]] | None = None,
        local_remote_names: dict[str, tuple[str, ...]] | None = None,
        local_remote_name_query_result: tuple[int, str, str] | None = None,
        worktree_config_result: tuple[int, str, str] = (1, "", ""),
        git_config_path: str | None = None,
    ):
        calls: list[StrList] = []
        configured_results = config_results or {}
        configured_origin_results = config_origin_results or {}
        configured_local_remote_names = local_remote_names or {}
        local_config_path = git_config_path or os.path.join(root, ".git", "config")

        def run(command: StrList, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append(command)
            if command[:2] == ["findmnt", "-n"]:
                return _completed(command, f"{filesystem}\n")
            arguments = command[3:]
            if arguments == ["rev-parse", "--show-toplevel"]:
                return _completed(command, f"{root}\n")
            if arguments == ["rev-parse", "--git-path", "config"]:
                return _completed(command, f"{local_config_path}\n")
            if arguments == ["remote"]:
                return _completed(command, effective_remotes)
            if arguments[:1] == ["config"] and "extensions.worktreeConfig" in arguments:
                returncode, stdout, stderr = worktree_config_result
                return _completed(
                    command,
                    stdout=stdout,
                    returncode=returncode,
                    stderr=stderr,
                )
            if arguments[:1] == ["config"] and "--get-regexp" in arguments:
                if local_remote_name_query_result is not None:
                    returncode, stdout, stderr = local_remote_name_query_result
                    return _completed(
                        command,
                        stdout=stdout,
                        returncode=returncode,
                        stderr=stderr,
                    )
                scope = next(
                    (
                        argument
                        for argument in arguments
                        if argument in {"--local", "--worktree"}
                    ),
                    "",
                )
                expression = re.compile(arguments[-1], re.IGNORECASE)
                names = [
                    name
                    for name in configured_local_remote_names.get(scope, ())
                    if expression.match(name)
                ]
                if not names:
                    return _completed(command, returncode=1)
                return _completed(command, "".join(f"{name}\0" for name in names))
            if arguments[:1] == ["config"] and "--show-origin" in arguments:
                returncode, stdout, stderr = configured_origin_results.get(
                    arguments[-1],
                    (1, "", ""),
                )
                return _completed(
                    command,
                    stdout=stdout,
                    returncode=returncode,
                    stderr=stderr,
                )
            if arguments[:1] == ["config"] and (
                "--get" in arguments or "--get-all" in arguments
            ):
                config_file = None
                if "--file" in arguments:
                    config_file = arguments[arguments.index("--file") + 1]
                returncode, stdout, stderr = configured_results.get(
                    (config_file, arguments[-1]),
                    (1, "", ""),
                )
                return _completed(
                    command,
                    stdout=stdout,
                    returncode=returncode,
                    stderr=stderr,
                )
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
                "--replace-all",
                "lfs.url",
                "https://git.example.test/team/project.git/info/lfs",
            ],
            git_arguments,
        )
        self.assertIn(["lfs", "track", "assets/**"], git_arguments)
        self.assertIn(["lfs", "env"], git_arguments)
        self.assertIn(["lfs", "version"], git_arguments)
        self.assertIn(["lfs", "install", "--local"], git_arguments)
        self.assertIn(
            [
                "config",
                "--replace-all",
                "remote.gogs.pushurl",
                "https://git.example.test/team/project.git",
            ],
            git_arguments,
        )
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
        self.assertFalse(any(_is_mutating(arguments) for arguments in git_arguments))
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

    def test_replaces_multivalue_fetch_urls_for_existing_custom_mirror(self):
        with tempfile.TemporaryDirectory() as root:
            local_config = f"file:{root}/.git/config"
            stale_one = "https://old-one.invalid/team/project.git"
            stale_two = "https://old-two.invalid/team/project.git"
            calls, runner = self._runner(
                root,
                local_remote_names={
                    "--local": (
                        "remote.origin.url",
                        "remote.origin.fetch",
                        "remote.backup-mirror.url",
                        "remote.backup-mirror.fetch",
                    )
                },
                config_origin_results={
                    "remote.origin.url": (
                        0,
                        _origin_records(
                            (local_config, stale_one),
                            (local_config, stale_two),
                        ),
                        "",
                    ),
                    "remote.backup-mirror.url": (
                        0,
                        _origin_records(
                            (local_config, stale_one),
                            (local_config, stale_two),
                        ),
                        "",
                    ),
                },
            )
            with patch(
                "lib.gogs_repository.subprocess.run",
                side_effect=runner,
            ):
                configure_github_gogs_repository(
                    root,
                    "https://github.com/team/project.git",
                    "https://git.example.test/team/project.git",
                    mirror_remote="backup-mirror",
                    combined_push=False,
                )

        git_arguments = [
            command[3:] for command in calls if command[:2] == ["git", "-C"]
        ]
        self.assertIn(
            [
                "config",
                "--replace-all",
                "remote.origin.url",
                "https://github.com/team/project.git",
            ],
            git_arguments,
        )
        self.assertIn(
            [
                "config",
                "--replace-all",
                "remote.backup-mirror.url",
                "https://git.example.test/team/project.git",
            ],
            git_arguments,
        )
        self.assertNotIn(
            ["remote", "set-url", "origin", "https://github.com/team/project.git"],
            git_arguments,
        )
        self.assertNotIn(
            ["remote", "add", "origin", "https://github.com/team/project.git"],
            git_arguments,
        )
        self.assertNotIn(
            [
                "remote",
                "set-url",
                "backup-mirror",
                "https://git.example.test/team/project.git",
            ],
            git_arguments,
        )
        self.assertNotIn(
            [
                "remote",
                "add",
                "backup-mirror",
                "https://git.example.test/team/project.git",
            ],
            git_arguments,
        )
        fetch_keys = {"remote.origin.fetch", "remote.backup-mirror.fetch"}
        self.assertFalse(
            any(
                arguments[:1] == ["config"] and fetch_keys.intersection(arguments)
                for arguments in git_arguments
            )
        )
        self.assertIn(
            [
                "config",
                "--replace-all",
                "remote.backup-mirror.pushurl",
                "https://git.example.test/team/project.git",
            ],
            git_arguments,
        )

    def test_rejects_unexpected_inherited_push_urls_before_actions(self):
        cases = (
            (
                "remote.origin.pushurl",
                "https://user:origin-secret@stale.invalid/private.git",
                "file:/etc/gitconfig",
                "gogs",
                True,
            ),
            (
                "remote.gogs.pushurl",
                "https://user:mirror-secret@stale.invalid/private.git",
                "file:/etc/gitconfig",
                "gogs",
                True,
            ),
            (
                "remote.backup-mirror.pushurl",
                "https://user:custom-secret@stale.invalid/private.git",
                "file:/tmp/config include with spaces/extra.gitconfig",
                "backup-mirror",
                False,
            ),
        )
        for key, stale, origin, mirror_remote, combined_push in cases:
            with self.subTest(key=key), tempfile.TemporaryDirectory() as root:
                calls, runner = self._runner(
                    root,
                    config_origin_results={
                        key: (0, _origin_records((origin, stale)), "")
                    },
                )

                with patch(
                    "lib.gogs_repository.subprocess.run",
                    side_effect=runner,
                ):
                    with self.assertRaisesRegex(
                        ValueError,
                        "inherited destination",
                    ) as error:
                        configure_github_gogs_repository(
                            root,
                            "https://github.com/team/project.git",
                            "https://git.example.test/team/project.git",
                            mirror_remote=mirror_remote,
                            combined_push=combined_push,
                        )

                self.assertNotIn("stale.invalid", str(error.exception))
                self.assertNotIn("secret", str(error.exception))
                git_arguments = [
                    command[3:]
                    for command in calls
                    if command[:2] == ["git", "-C"]
                ]
                self.assertFalse(
                    any(_is_mutating(arguments) for arguments in git_arguments)
                )
                self.assertNotIn(["lfs", "env"], git_arguments)

    def test_rejects_unexpected_inherited_fetch_urls_before_actions(self):
        cases = (
            (
                "remote.origin.url",
                "https://user:origin-fetch-secret@stale.invalid/private.git",
                "file:/etc/gitconfig",
                "gogs",
            ),
            (
                "remote.gogs.url",
                "https://user:mirror-fetch-secret@stale.invalid/private.git",
                "file:/etc/gitconfig",
                "gogs",
            ),
            (
                "remote.backup-mirror.url",
                "https://user:custom-fetch-secret@stale.invalid/private.git",
                "file:/tmp/config include with spaces/extra.gitconfig",
                "backup-mirror",
            ),
        )
        for key, stale, origin, mirror_remote in cases:
            with self.subTest(key=key), tempfile.TemporaryDirectory() as root:
                calls, runner = self._runner(
                    root,
                    config_origin_results={
                        key: (0, _origin_records((origin, stale)), "")
                    },
                )

                with patch(
                    "lib.gogs_repository.subprocess.run",
                    side_effect=runner,
                ):
                    with self.assertRaisesRegex(
                        ValueError,
                        "fetch URL.*inherited destination",
                    ) as error:
                        configure_github_gogs_repository(
                            root,
                            "https://github.com/team/project.git",
                            "https://git.example.test/team/project.git",
                            mirror_remote=mirror_remote,
                        )

                self.assertNotIn("stale.invalid", str(error.exception))
                self.assertNotIn("secret", str(error.exception))
                git_arguments = [
                    command[3:]
                    for command in calls
                    if command[:2] == ["git", "-C"]
                ]
                self.assertFalse(
                    any(_is_mutating(arguments) for arguments in git_arguments)
                )
                self.assertNotIn(["lfs", "env"], git_arguments)

    def test_repairs_stale_multivalue_push_urls_from_writable_config(self):
        with tempfile.TemporaryDirectory() as root:
            stale_one = "https://stale-one.invalid/private.git"
            stale_two = "https://stale-two.invalid/private.git"
            local_config = f"file:{root}/.git/config"
            calls, runner = self._runner(
                root,
                local_remote_names={
                    "--local": (
                        "remote.origin.pushurl",
                        "remote.gogs.pushurl",
                    )
                },
                config_origin_results={
                    "remote.origin.pushurl": (
                        0,
                        _origin_records(
                            ("file:.git/config", stale_one),
                            ("file:.git/config", stale_two),
                        ),
                        "",
                    ),
                    "remote.gogs.pushurl": (
                        0,
                        _origin_records(
                            ("file:.git/config", stale_one),
                            ("file:.git/config", stale_two),
                        ),
                        "",
                    ),
                },
            )

            with patch("lib.gogs_repository.subprocess.run", side_effect=runner):
                configure_github_gogs_repository(
                    root,
                    "https://github.com/team/project.git",
                    "https://git.example.test/team/project.git",
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
                "--replace-all",
                "remote.origin.url",
                "https://github.com/team/project.git",
            ],
            git_arguments,
        )
        self.assertIn(
            [
                "config",
                "--replace-all",
                "remote.gogs.url",
                "https://git.example.test/team/project.git",
            ],
            git_arguments,
        )
        self.assertIn(
            [
                "config",
                "--replace-all",
                "remote.gogs.pushurl",
                "https://git.example.test/team/project.git",
            ],
            git_arguments,
        )

    def test_accepts_intended_inherited_push_urls_for_selected_destinations(self):
        cases = (
            (
                "gogs",
                True,
                (
                    ("file:/etc/gitconfig", "https://github.com/team/project.git"),
                    (
                        "file:/etc/gitconfig",
                        "https://git.example.test/team/project.git",
                    ),
                ),
            ),
            (
                "backup-mirror",
                False,
                (("command line:", "https://github.com/team/project.git"),),
            ),
        )
        for mirror_remote, combined_push, origin_entries in cases:
            with (
                self.subTest(mirror_remote=mirror_remote),
                tempfile.TemporaryDirectory() as root,
            ):
                mirror = "file:/etc/gitconfig"
                entries = {
                    "remote.origin.url": (
                        0,
                        _origin_records(
                            (mirror, "https://github.com/team/project.git")
                        ),
                        "",
                    ),
                    f"remote.{mirror_remote}.url": (
                        0,
                        _origin_records(
                            (mirror, "https://git.example.test/team/project.git")
                        ),
                        "",
                    ),
                    "remote.origin.pushurl": (
                        0,
                        _origin_records(*origin_entries),
                        "",
                    ),
                    f"remote.{mirror_remote}.pushurl": (
                        0,
                        _origin_records(
                            (mirror, "https://git.example.test/team/project.git")
                        ),
                        "",
                    ),
                }
                calls, runner = self._runner(
                    root,
                    effective_remotes=f"origin\n{mirror_remote}\n",
                    config_origin_results=entries,
                )

                with patch(
                    "lib.gogs_repository.subprocess.run",
                    side_effect=runner,
                ):
                    result = configure_github_gogs_repository(
                        root,
                        "https://github.com/team/project.git",
                        "https://git.example.test/team/project.git",
                        mirror_remote=mirror_remote,
                        combined_push=combined_push,
                    )

                self.assertEqual(result["combined_push"], combined_push)
                git_arguments = [
                    command[3:]
                    for command in calls
                    if command[:2] == ["git", "-C"]
                ]
                self.assertIn(
                    [
                        "remote",
                        "add",
                        "origin",
                        "https://github.com/team/project.git",
                    ],
                    git_arguments,
                )
                self.assertNotIn(["git", "-C", root, "remote"], calls)
                self.assertIn(
                    [
                        "remote",
                        "add",
                        mirror_remote,
                        "https://git.example.test/team/project.git",
                    ],
                    git_arguments,
                )
                self.assertIn(
                    [
                        "git",
                        "-C",
                        root,
                        "config",
                        "--includes",
                        "--null",
                        "--show-origin",
                        "--get-all",
                        "remote.origin.pushurl",
                    ],
                    calls,
                )

    def test_local_include_remote_definition_replaces_only_url(self):
        with tempfile.TemporaryDirectory() as root:
            origin = "file:/tmp/repository config include/remote.gitconfig"
            github = "https://github.com/team/project.git"
            calls, runner = self._runner(
                root,
                config_origin_results={
                    "remote.origin.url": (0, _origin_records((origin, github)), "")
                },
                local_remote_names={"--local": ("remote.origin.url",)},
            )

            with patch("lib.gogs_repository.subprocess.run", side_effect=runner):
                configure_github_gogs_repository(
                    root,
                    github,
                    "https://git.example.test/team/project.git",
                )

        git_arguments = [
            command[3:] for command in calls if command[:2] == ["git", "-C"]
        ]
        self.assertIn(
            ["config", "--replace-all", "remote.origin.url", github],
            git_arguments,
        )
        self.assertIn(
            [
                "remote",
                "add",
                "gogs",
                "https://git.example.test/team/project.git",
            ],
            git_arguments,
        )
        self.assertIn(
            [
                "git",
                "-C",
                root,
                "config",
                "--local",
                "--includes",
                "--null",
                "--name-only",
                "--get-regexp",
                r"^remote\.origin\.",
            ],
            calls,
        )

    def test_local_fetch_only_remote_definition_replaces_url(self):
        with tempfile.TemporaryDirectory() as root:
            github = "https://github.com/team/project.git"
            calls, runner = self._runner(
                root,
                local_remote_names={"--local": ("remote.origin.fetch",)},
            )

            with patch("lib.gogs_repository.subprocess.run", side_effect=runner):
                configure_github_gogs_repository(
                    root,
                    github,
                    "https://git.example.test/team/project.git",
                )

        git_arguments = [
            command[3:] for command in calls if command[:2] == ["git", "-C"]
        ]
        self.assertIn(
            ["config", "--replace-all", "remote.origin.url", github],
            git_arguments,
        )

    def test_worktree_remote_definition_replaces_url(self):
        with tempfile.TemporaryDirectory() as root:
            github = "https://github.com/team/project.git"
            worktree_origin = f"file:{root}/.git/worktrees/feature/config.worktree"
            calls, runner = self._runner(
                root,
                config_origin_results={
                    "remote.origin.pushurl": (
                        0,
                        _origin_records((worktree_origin, github)),
                        "",
                    )
                },
                local_remote_names={
                    "--worktree": ("remote.origin.pushurl",)
                },
                worktree_config_result=(0, "true\0", ""),
            )

            with patch("lib.gogs_repository.subprocess.run", side_effect=runner):
                configure_github_gogs_repository(
                    root,
                    github,
                    "https://git.example.test/team/project.git",
                )

        git_arguments = [
            command[3:] for command in calls if command[:2] == ["git", "-C"]
        ]
        self.assertIn(
            ["config", "--replace-all", "remote.origin.url", github],
            git_arguments,
        )
        self.assertIn(
            [
                "git",
                "-C",
                root,
                "config",
                "--worktree",
                "--includes",
                "--null",
                "--name-only",
                "--get-regexp",
                r"^remote\.origin\.",
            ],
            calls,
        )

    def test_local_remote_name_query_error_is_sanitized_and_fails_closed(self):
        with tempfile.TemporaryDirectory() as root:
            calls, runner = self._runner(
                root,
                local_remote_name_query_result=(
                    128,
                    "",
                    "bad remote URL https://user:private-token@hidden.invalid/repo.git",
                ),
            )

            with patch("lib.gogs_repository.subprocess.run", side_effect=runner):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "remote-name query failed",
                ) as error:
                    configure_github_gogs_repository(
                        root,
                        "https://github.com/team/project.git",
                        "https://git.example.test/team/project.git",
                    )

        self.assertNotIn("private-token", str(error.exception))
        self.assertNotIn("hidden.invalid", str(error.exception))
        self.assertFalse(
            any(
                _is_mutating(command[3:])
                for command in calls
                if command[:2] == ["git", "-C"]
            )
        )

    def test_no_combined_push_rejects_inherited_gogs_origin_destination(self):
        with tempfile.TemporaryDirectory() as root:
            calls, runner = self._runner(
                root,
                config_origin_results={
                    "remote.origin.pushurl": (
                        0,
                        _origin_records(
                            (
                                "file:/etc/gitconfig",
                                "https://git.example.test/team/project.git",
                            )
                        ),
                        "",
                    )
                },
            )

            with patch("lib.gogs_repository.subprocess.run", side_effect=runner):
                with self.assertRaisesRegex(ValueError, "inherited destination"):
                    configure_github_gogs_repository(
                        root,
                        "https://github.com/team/project.git",
                        "https://git.example.test/team/project.git",
                        combined_push=False,
                    )

        self.assertFalse(
            any(
                _is_mutating(command[3:])
                for command in calls
                if command[:2] == ["git", "-C"]
            )
        )

    def test_rejects_stale_linked_worktree_push_url(self):
        with tempfile.TemporaryDirectory() as root:
            calls, runner = self._runner(
                root,
                config_origin_results={
                    "remote.origin.pushurl": (
                        0,
                        _origin_records(
                            (
                                f"file:{root}/.git/worktrees/feature/config.worktree",
                                "https://stale.invalid/private.git",
                            )
                        ),
                        "",
                    )
                },
            )

            with patch("lib.gogs_repository.subprocess.run", side_effect=runner):
                with self.assertRaisesRegex(ValueError, "inherited destination"):
                    configure_github_gogs_repository(
                        root,
                        "https://github.com/team/project.git",
                        "https://git.example.test/team/project.git",
                    )

        self.assertFalse(
            any(
                _is_mutating(command[3:])
                for command in calls
                if command[:2] == ["git", "-C"]
            )
        )

    def test_push_url_config_query_error_is_sanitized_and_fails_closed(self):
        with tempfile.TemporaryDirectory() as root:
            calls, runner = self._runner(
                root,
                config_origin_results={
                    "remote.origin.pushurl": (
                        128,
                        "",
                        "bad push URL "
                        "https://user:private-token@hidden.invalid/secret.git",
                    )
                },
            )

            with patch("lib.gogs_repository.subprocess.run", side_effect=runner):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "config query failed",
                ) as error:
                    configure_github_gogs_repository(
                        root,
                        "https://github.com/team/project.git",
                        "https://git.example.test/team/project.git",
                    )

        self.assertNotIn("private-token", str(error.exception))
        self.assertNotIn("hidden.invalid", str(error.exception))
        self.assertFalse(
            any(
                _is_mutating(command[3:])
                for command in calls
                if command[:2] == ["git", "-C"]
            )
        )

    def test_malformed_push_url_config_query_fails_closed(self):
        with tempfile.TemporaryDirectory() as root:
            calls, runner = self._runner(
                root,
                config_origin_results={
                    "remote.origin.pushurl": (0, "file:/etc/gitconfig\0", "")
                },
            )

            with patch("lib.gogs_repository.subprocess.run", side_effect=runner):
                with self.assertRaisesRegex(RuntimeError, "returned invalid data"):
                    configure_github_gogs_repository(
                        root,
                        "https://github.com/team/project.git",
                        "https://git.example.test/team/project.git",
                    )

        self.assertFalse(
            any(
                _is_mutating(command[3:])
                for command in calls
                if command[:2] == ["git", "-C"]
            )
        )

    def test_validates_git_config_target_before_querying_config(self):
        with tempfile.TemporaryDirectory() as root:
            git_directory = os.path.join(root, ".git")
            os.mkdir(git_directory)
            config_path = os.path.join(git_directory, "config")
            outside = os.path.join(root, "external-config")
            with open(outside, "w", encoding="utf-8"):
                pass
            os.symlink(outside, config_path)
            calls, runner = self._runner(root, git_config_path=config_path)

            with patch("lib.gogs_repository.subprocess.run", side_effect=runner):
                with self.assertRaisesRegex(ValueError, "regular file"):
                    configure_github_gogs_repository(
                        root,
                        "https://github.com/team/project.git",
                        "https://git.example.test/team/project.git",
                    )

        git_arguments = [
            command[3:] for command in calls if command[:2] == ["git", "-C"]
        ]
        self.assertFalse(
            any(arguments[:1] == ["config"] for arguments in git_arguments)
        )

    def test_nonempty_git_config_fails_before_actions_without_changing_override(self):
        with tempfile.TemporaryDirectory() as root:
            alternate = os.path.join(root, "alternate config")
            original_contents = "[user]\n\tname = preserve-me\n"
            with open(alternate, "w", encoding="utf-8") as stream:
                stream.write(original_contents)
            calls, runner = self._runner(root)

            with patch.dict(os.environ, {"GIT_CONFIG": alternate}):
                with patch("lib.gogs_repository.subprocess.run", side_effect=runner):
                    with self.assertRaisesRegex(ValueError, "GIT_CONFIG") as error:
                        configure_github_gogs_repository(
                            root,
                            "https://github.com/team/project.git",
                            "https://git.example.test/team/project.git",
                        )
                self.assertEqual(os.environ.get("GIT_CONFIG"), alternate)

            with open(alternate, encoding="utf-8") as stream:
                self.assertEqual(stream.read(), original_contents)
            self.assertNotIn(alternate, str(error.exception))
            self.assertEqual(calls, [])

    def test_rejects_effective_lfs_url_conflict_before_actions(self):
        with tempfile.TemporaryDirectory() as root:
            stale = "https://stale.invalid/owner/private-old.git/info/lfs"
            calls, runner = self._runner(
                root,
                config_results={(None, "lfs.url"): (0, f"{stale}\0", "")},
            )

            with patch(
                "lib.gogs_repository.subprocess.run",
                side_effect=runner,
            ):
                with self.assertRaisesRegex(
                    ValueError,
                    "lfs.url.*conflicts",
                ) as error:
                    configure_github_gogs_repository(
                        root,
                        "https://github.com/team/project.git",
                        "https://git.example.test/team/project.git",
                    )

        self.assertNotIn("private-old", str(error.exception))
        git_arguments = [
            command[3:] for command in calls if command[:2] == ["git", "-C"]
        ]
        self.assertFalse(any(_is_mutating(arguments) for arguments in git_arguments))
        self.assertNotIn(["lfs", "env"], git_arguments)

    def test_does_not_echo_credentials_from_invalid_lfs_endpoint(self):
        with tempfile.TemporaryDirectory() as root:
            stale = "https://user:private-value@stale.invalid/owner/old.git/info/lfs"
            calls, runner = self._runner(
                root,
                config_results={(None, "lfs.url"): (0, f"{stale}\0", "")},
            )

            with patch(
                "lib.gogs_repository.subprocess.run",
                side_effect=runner,
            ):
                with self.assertRaisesRegex(ValueError, "lfs.url.*invalid") as error:
                    configure_github_gogs_repository(
                        root,
                        "https://github.com/team/project.git",
                        "https://git.example.test/team/project.git",
                    )

        self.assertNotIn("private-value", str(error.exception))
        self.assertFalse(
            any(
                _is_mutating(command[3:])
                for command in calls
                if command[:2] == ["git", "-C"]
            )
        )
        self.assertNotIn(
            ["lfs", "env"],
            [command[3:] for command in calls if command[:2] == ["git", "-C"]],
        )

    def test_accepts_matching_lfs_endpoint_overrides(self):
        with tempfile.TemporaryDirectory() as root:
            lfsconfig = os.path.join(root, ".lfsconfig")
            with open(lfsconfig, "w", encoding="utf-8"):
                pass
            expected = "https://git.example.test/team/project.git/info/lfs"
            config_results = {
                (None, "lfs.url"): (0, f"{expected}\0", ""),
                (None, "lfs.pushurl"): (0, f"{expected}/\0", ""),
                (None, "remote.origin.lfsurl"): (0, f"{expected}\0", ""),
                (None, "remote.origin.lfspushurl"): (0, f"{expected}\0", ""),
                (None, "remote.gogs.lfsurl"): (0, f"{expected}\0", ""),
                (None, "remote.gogs.lfspushurl"): (0, f"{expected}\0", ""),
                (lfsconfig, "lfs.url"): (0, f"{expected}\0", ""),
                (lfsconfig, "lfs.pushurl"): (0, f"{expected}\0", ""),
            }
            calls, runner = self._runner(root, config_results=config_results)

            with patch(
                "lib.gogs_repository.subprocess.run",
                side_effect=runner,
            ):
                result = configure_github_gogs_repository(
                    root,
                    "https://github.com/team/project.git",
                    "https://git.example.test/team/project.git",
                )

        self.assertEqual(result["lfs_url"], expected)
        git_arguments = [
            command[3:] for command in calls if command[:2] == ["git", "-C"]
        ]
        self.assertIn(
            ["config", "--file", lfsconfig, "--replace-all", "lfs.url", expected],
            git_arguments,
        )

    def test_rejects_lfsconfig_upload_override_before_actions(self):
        with tempfile.TemporaryDirectory() as root:
            lfsconfig = os.path.join(root, ".lfsconfig")
            with open(lfsconfig, "w", encoding="utf-8"):
                pass
            stale = "https://stale.invalid/owner/old.git/info/lfs"
            calls, runner = self._runner(
                root,
                config_results={(lfsconfig, "lfs.pushurl"): (0, f"{stale}\0", "")},
            )

            with patch(
                "lib.gogs_repository.subprocess.run",
                side_effect=runner,
            ):
                with self.assertRaisesRegex(ValueError, "lfs.pushurl.*conflicts"):
                    configure_github_gogs_repository(
                        root,
                        "https://github.com/team/project.git",
                        "https://git.example.test/team/project.git",
                    )

        git_arguments = [
            command[3:] for command in calls if command[:2] == ["git", "-C"]
        ]
        self.assertFalse(any(_is_mutating(arguments) for arguments in git_arguments))
        self.assertNotIn(["lfs", "env"], git_arguments)

    def test_rejects_push_endpoint_overrides_before_actions(self):
        for key in ("lfs.pushurl", "remote.backup-lfs.lfspushurl"):
            with self.subTest(key=key), tempfile.TemporaryDirectory() as root:
                stale = "https://stale.invalid/owner/private-old-repo.git/info/lfs"
                calls, runner = self._runner(
                    root,
                    config_results={(None, key): (0, f"{stale}\0", "")},
                )

                with patch(
                    "lib.gogs_repository.subprocess.run",
                    side_effect=runner,
                ):
                    with self.assertRaisesRegex(ValueError, "conflicts"):
                        configure_github_gogs_repository(
                            root,
                            "https://github.com/team/project.git",
                            "https://git.example.test/team/project.git",
                            mirror_remote="backup-lfs",
                        )

                git_arguments = [
                    command[3:]
                    for command in calls
                    if command[:2] == ["git", "-C"]
                ]
                self.assertFalse(
                    any(_is_mutating(arguments) for arguments in git_arguments)
                )

    def test_config_query_failure_is_not_treated_as_absence(self):
        with tempfile.TemporaryDirectory() as root:
            calls, runner = self._runner(
                root,
                config_results={
                    (None, "lfs.url"): (
                        128,
                        "",
                        "malformed endpoint https://private.invalid/token",
                    )
                },
            )

            with patch(
                "lib.gogs_repository.subprocess.run",
                side_effect=runner,
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "config query failed",
                ) as error:
                    configure_github_gogs_repository(
                        root,
                        "https://github.com/team/project.git",
                        "https://git.example.test/team/project.git",
                    )

        self.assertNotIn("private.invalid", str(error.exception))
        git_arguments = [
            command[3:] for command in calls if command[:2] == ["git", "-C"]
        ]
        self.assertFalse(any(_is_mutating(arguments) for arguments in git_arguments))
        self.assertNotIn(["lfs", "env"], git_arguments)

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
