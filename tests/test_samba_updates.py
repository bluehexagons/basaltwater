"""Tests for the share-only Samba update CLI."""

from __future__ import annotations

import os
import sys
import unittest
from argparse import Namespace
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import basaltwater
from lib.config import SetupConfig


class TestApplyShareUpdates(unittest.TestCase):
    def _config(self) -> SetupConfig:
        return SetupConfig(
            host="fileserver",
            username="admin",
            system_type="server_lite",
            enable_samba=True,
            samba_shares=[
                ["read", "docs", "/srv/docs", "alice"],
                ["read", "archive", "/srv/archive", "alice"],
            ],
        )

    def test_replaces_by_name_adds_and_removes(self) -> None:
        config = self._config()
        args = Namespace(
            remove_share=["archive"],
            samba_shares=[
                ["write", "docs", "/srv/docs", "alice,bob"],
                ["read", "media", "/srv/media", "bob"],
            ],
            share_credentials=[["bob", "secret"]],
            dry_run=False,
            username=None,
            ssh_key=None,
        )

        basaltwater._apply_share_updates(config, args)

        self.assertEqual(
            config.samba_shares,
            [
                ["write", "docs", "/srv/docs", "alice,bob"],
                ["read", "media", "/srv/media", "bob"],
            ],
        )
        self.assertEqual(config.share_credentials, [["bob", "secret"]])

    def test_no_mutations_keeps_saved_desired_state(self) -> None:
        config = self._config()
        args = Namespace(
            remove_share=[],
            samba_shares=None,
            share_credentials=None,
            dry_run=True,
            username="root",
            ssh_key="/tmp/key",
        )

        basaltwater._apply_share_updates(config, args)

        self.assertEqual(len(config.samba_shares or []), 2)
        self.assertTrue(config.dry_run)
        self.assertEqual(config.username, "root")
        self.assertEqual(config.ssh_key, "/tmp/key")


class TestSharesParser(unittest.TestCase):
    def test_parses_fast_path_options(self) -> None:
        parser, _setup, _patch = basaltwater.create_basaltwater_parser()
        args = parser.parse_args(
            [
                "shares",
                "fileserver",
                "--share",
                "write",
                "docs",
                "/srv/docs",
                "alice,bob",
                "--remove-share",
                "archive",
            ]
        )

        self.assertEqual(args.command, "shares")
        self.assertEqual(args.samba_shares[0][1], "docs")
        self.assertEqual(args.remove_share, ["archive"])


class TestRunSharesCommand(unittest.TestCase):
    def test_sends_only_share_related_configuration(self) -> None:
        cached = SetupConfig(
            host="fileserver",
            username="admin",
            system_type="server_lite",
            samba_shares=[["read", "docs", "/srv/docs", "alice:secret"]],
            deploy_specs=[["example.com", "https://example.com/repo.git"]],
            smb_mounts=[["/mnt/docs", "10.0.0.2", "missing", "docs", "/"]],
        )
        args = Namespace(
            host="fileserver",
            remove_share=[],
            samba_shares=None,
            share_credentials=None,
            dry_run=True,
            username=None,
            ssh_key=None,
        )
        sent: list[SetupConfig] = []

        def fake_run(config: SetupConfig) -> int:
            sent.append(config)
            return 0

        with patch.object(basaltwater, "load_setup_command", return_value=cached), \
             patch.object(basaltwater, "run_remote_setup", side_effect=fake_run):
            result = basaltwater.run_shares_command(args)

        self.assertEqual(result, 0)
        self.assertEqual(sent[0].custom_steps, "reconcile_samba_shares")
        self.assertIsNone(sent[0].deploy_specs)
        self.assertIsNone(sent[0].smb_mounts)
        self.assertEqual(sent[0].samba_shares, cached.samba_shares)


if __name__ == "__main__":
    unittest.main()
