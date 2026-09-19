"""Tests for remote target-user rename command registration."""

from __future__ import annotations

import unittest

from basaltwater import create_basaltwater_parser


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


if __name__ == "__main__":
    unittest.main()
