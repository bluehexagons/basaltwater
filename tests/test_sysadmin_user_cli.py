"""Tests for remote target-user rename command registration."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from basaltwater import create_basaltwater_parser
from lib import sysadmin_user


class TestSysadminUserCli(unittest.TestCase):
    def test_rename_parser(self):
        parser, _, _ = create_basaltwater_parser()
        args = parser.parse_args(
            [
                "user",
                "rename",
                "server.example",
                "newadmin",
                "--admin-user",
                "root",
                "--new-home",
                "/home/newadmin",
                "--yes",
            ]
        )

        self.assertEqual(args.command, "user")
        self.assertEqual(args._sysadmin_cmd, "user_rename")
        self.assertEqual(args.host, "server.example")
        self.assertEqual(args.new_username, "newadmin")
        self.assertEqual(args.admin_user, "root")
        self.assertEqual(args.new_home, "/home/newadmin")
        self.assertTrue(args.yes)

    def test_rename_home_options_are_mutually_exclusive(self):
        parser, _, _ = create_basaltwater_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(
                [
                    "user",
                    "rename",
                    "server.example",
                    "newadmin",
                    "--new-home",
                    "/home/newadmin",
                    "--keep-home",
                ]
            )

    def test_resume_options_cannot_change_durable_home_selection_or_dry_run(self):
        parser, _, _ = create_basaltwater_parser()
        for options in (["--keep-home"], ["--new-home", "/srv/newadmin"], ["--dry-run"]):
            with self.subTest(options=options):
                args = parser.parse_args([
                    "user", "rename", "server.example", "newadmin", "--resume", "0123456789abcdef", *options,
                ])
                with patch.object(sysadmin_user, "_resolve_credentials") as resolve:
                    result = sysadmin_user.run_user_rename(
                        args.host, args.new_username, resume=args.resume, keep_home=args.keep_home,
                        new_home=args.new_home, dry_run=args.dry_run,
                    )
                self.assertEqual(result, 1)
                resolve.assert_not_called()


if __name__ == "__main__":
    unittest.main()
