"""Tests for GitHub release and Actions maintenance commands."""

from __future__ import annotations

import contextlib
import io
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import basaltwater
from lib.github_maintenance import _release_tags_to_delete, discover_github_repos, run_maintenance_command


class TestMaintenanceCli(unittest.TestCase):
    def test_parser_accepts_github_audit(self) -> None:
        parser, _setup_parser, _patch_parser = basaltwater.create_basaltwater_parser()
        args = parser.parse_args(["maintenance", "github", "audit", "--json"])
        self.assertEqual(args.command, "maintenance")
        self.assertEqual(args.maintenance_command, "github")
        self.assertEqual(args.github_command, "audit")
        self.assertTrue(args.json)

    def test_parser_accepts_github_prune(self) -> None:
        parser, _setup_parser, _patch_parser = basaltwater.create_basaltwater_parser()
        args = parser.parse_args([
            "maintenance",
            "github",
            "prune",
            "--keep-releases",
            "1",
            "--delete-caches",
        ])
        self.assertEqual(args.keep_releases, 1)
        self.assertTrue(args.delete_caches)

    def test_release_prune_keeps_newest_tags(self) -> None:
        releases = [
            {"tag_name": "v1.0.0", "published_at": "2025-01-01T00:00:00Z"},
            {"tag_name": "v1.1.0", "published_at": "2025-02-01T00:00:00Z"},
            {"tag_name": "v1.2.0", "published_at": "2025-03-01T00:00:00Z"},
        ]
        self.assertEqual(_release_tags_to_delete(releases, 2), ["v1.0.0"])

    def test_run_maintenance_command_requires_subcommand(self) -> None:
        parser, _setup_parser, _patch_parser = basaltwater.create_basaltwater_parser()
        args = parser.parse_args(["maintenance"])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = run_maintenance_command(args)
        self.assertEqual(rc, 1)
        self.assertIn("maintenance command required", buf.getvalue())

    @patch("lib.github_maintenance._find_git_repo_roots")
    @patch("lib.github_maintenance._git_remote_url")
    def test_discover_github_repos_uses_git_roots(self, mock_remote_url, mock_find_roots) -> None:
        mock_find_roots.return_value = ["/tmp/repo1", "/tmp/repo2"]
        mock_remote_url.side_effect = [
            "https://github.com/bluehexagons/alpha.git",
            "https://github.com/bluehexagons/beta.git",
        ]
        repos = discover_github_repos(["/tmp/workspace"])
        self.assertEqual([repo.full_name for repo in repos], ["bluehexagons/alpha", "bluehexagons/beta"])


if __name__ == "__main__":
    unittest.main()
