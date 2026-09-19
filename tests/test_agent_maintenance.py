"""Tests for bounded agent maintenance holds and CLI routing."""

from __future__ import annotations

import argparse
import os
import stat
import tempfile
import unittest
from unittest.mock import patch

from lib.agent_cli import add_agent_subparser, run_agent_command
from lib.agent_maintenance import (
    agent_maintenance_path,
    hold_agent_maintenance,
    inspect_agent_maintenance,
    release_agent_maintenance,
)


class TestAgentMaintenance(unittest.TestCase):
    def test_hold_is_private_bounded_and_expires(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            held = hold_agent_maintenance(2, home=home, now=1_000)
            path = agent_maintenance_path(home)

            self.assertEqual(held["status"], "active")
            self.assertEqual(held["remaining_seconds"], 2 * 60 * 60)
            self.assertEqual(stat.S_IMODE(os.lstat(path).st_mode), 0o600)
            self.assertEqual(
                inspect_agent_maintenance(home, now=8_201)["status"],
                "expired",
            )

    def test_hold_rejects_unbounded_duration(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            for hours in (0, 73):
                with self.subTest(hours=hours):
                    with self.assertRaisesRegex(ValueError, "between 1 and 72"):
                        hold_agent_maintenance(hours, home=home, now=1_000)

    def test_invalid_marker_is_reported_without_following_a_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            outside = os.path.join(home, "outside")
            with open(outside, "w", encoding="utf-8") as file_obj:
                file_obj.write("do not read or remove")
            path = agent_maintenance_path(home)
            os.makedirs(os.path.dirname(path), mode=0o700)
            os.symlink(outside, path)

            result = inspect_agent_maintenance(home, now=1_000)
            released = release_agent_maintenance(home=home)

            self.assertEqual(result["status"], "invalid")
            self.assertTrue(released["released"])
            self.assertTrue(os.path.isfile(outside))

    def test_marker_must_be_private_and_small(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            path = agent_maintenance_path(home)
            os.makedirs(os.path.dirname(path), mode=0o700)
            with open(path, "w", encoding="utf-8") as file_obj:
                file_obj.write("{}")
            os.chmod(path, 0o644)
            self.assertEqual(
                inspect_agent_maintenance(home, now=1_000)["status"],
                "invalid",
            )

            os.chmod(path, 0o600)
            with open(path, "w", encoding="utf-8") as file_obj:
                file_obj.write("x" * 4_097)
            self.assertEqual(
                inspect_agent_maintenance(home, now=1_000)["status"],
                "invalid",
            )

    def test_state_directory_must_not_be_a_symbolic_link(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            outside = os.path.join(home, "outside")
            os.mkdir(outside)
            state_parent = os.path.join(home, ".local", "state")
            os.makedirs(state_parent)
            os.symlink(outside, os.path.join(state_parent, "basaltwater"))

            with self.assertRaisesRegex(RuntimeError, "symbolic link"):
                hold_agent_maintenance(1, home=home, now=1_000)

    def test_release_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            self.assertFalse(release_agent_maintenance(home=home)["released"])
            hold_agent_maintenance(1, home=home, now=1_000)
            self.assertTrue(release_agent_maintenance(home=home)["released"])
            self.assertFalse(release_agent_maintenance(home=home)["released"])

    def test_parser_routes_local_and_remote_operations(self) -> None:
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        add_agent_subparser(subparsers)
        local_args = parser.parse_args(
            ["agent", "maintenance", "hold", "--hours", "4", "--json"]
        )
        remote_args = parser.parse_args(
            [
                "agent",
                "maintenance",
                "release",
                "agent.example.test",
                "worker",
                "--json",
            ]
        )

        with patch(
            "lib.agent_maintenance.run_agent_maintenance_command",
            return_value=0,
        ) as local:
            self.assertEqual(run_agent_command(local_args), 0)
        local.assert_called_once_with(local_args)

        with patch(
            "lib.agent_cli._run_remote_agent_lifecycle",
            return_value=0,
        ) as remote:
            self.assertEqual(run_agent_command(remote_args), 0)
        remote.assert_called_once_with(
            ("agent.example.test", "worker", None),
            "maintenance",
            ["release", "--json"],
            timeout=60,
        )


if __name__ == "__main__":
    unittest.main()
