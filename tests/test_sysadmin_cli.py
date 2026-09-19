"""Tests for sysadmin command registration and dispatch."""

from __future__ import annotations

import argparse
import io
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

from basaltwater import create_basaltwater_parser
from lib.sysadmin_cli import run_sysadmin_command


class SysadminCliTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.parser, _, _ = create_basaltwater_parser()

    def _parse(self, *argv: str) -> argparse.Namespace:
        return self.parser.parse_args(list(argv))


class TestSysadminCliParsing(SysadminCliTestCase):
    def test_registers_all_public_command_shapes(self) -> None:
        cases = (
            (
                ("mount", "server", "/mnt/server", "-u", "admin", "-i", "/tmp/key", "-p", "2222", "--ro"),
                {"_sysadmin_cmd": "mount", "remote": "server", "local_path": "/mnt/server", "username": "admin", "ssh_key": "/tmp/key", "port": 2222, "ro": True},
            ),
            (("umount", "/mnt/server"), {"_sysadmin_cmd": "umount", "target": "/mnt/server"}),
            (("health", "server", "-u", "admin", "-i", "/tmp/key"), {"_sysadmin_cmd": "health", "host": "server", "username": "admin", "ssh_key": "/tmp/key"}),
            (("ssh", "-p", "2222", "server", "--", "journalctl", "-f"), {"_sysadmin_cmd": "ssh", "host": "server", "port": 2222, "remote_command": ["journalctl", "-f"]}),
            (("push", "./dist", "server:/srv/app", "--delete", "--dry-run"), {"_sysadmin_cmd": "push", "local_path": "./dist", "remote": "server:/srv/app", "delete": True, "dry_run": True}),
            (("pull", "server:/var/log"), {"_sysadmin_cmd": "pull", "remote": "server:/var/log", "local_path": None, "dry_run": False}),
            (("key", "push", "server", "--pubkey", "/tmp/id.pub"), {"_sysadmin_cmd": "key_push", "host": "server", "pubkey": "/tmp/id.pub"}),
            (("ssh-key", "enroll", "server", "--port", "2200", "--yes"), {"_sysadmin_cmd": "ssh_key_enroll", "host": "server", "port": 2200, "yes": True}),
            (("df", "server", "other", "-u", "admin"), {"_sysadmin_cmd": "df", "hosts": ["server", "other"], "username": "admin"}),
            (("fan", "server", "other"), {"_sysadmin_cmd": "fan", "hosts": ["server", "other"], "remote_command": []}),
            (("svc", "server", "nginx", "restart"), {"_sysadmin_cmd": "svc", "host": "server", "unit": "nginx", "action": "restart"}),
            (("logs", "server", "nginx", "-n", "100", "-f"), {"_sysadmin_cmd": "logs", "host": "server", "unit": "nginx", "lines": 100, "follow": True}),
            (("upgrade", "server", "other", "--check"), {"_sysadmin_cmd": "upgrade", "hosts": ["server", "other"], "check": True}),
            (("reachable", "server", "other", "--pattern", "*.example"), {"_sysadmin_cmd": "reachable", "hosts": ["server", "other"], "pattern": "*.example"}),
        )

        for argv, expected in cases:
            with self.subTest(argv=argv):
                args = self._parse(*argv)
                for name, value in expected.items():
                    self.assertEqual(getattr(args, name), value)

    def test_nested_commands_require_a_subcommand(self) -> None:
        with self.assertRaises(SystemExit):
            self._parse("key", "unknown")

        args = self._parse("key")
        self.assertEqual(args._sysadmin_cmd, "key")


class TestSysadminCliDispatch(SysadminCliTestCase):
    def test_mount_dispatches_all_options(self) -> None:
        args = self._parse("mount", "server:/srv", "/mnt/server", "-u", "admin", "-i", "/tmp/key", "-p", "2222", "--ro")
        with patch("lib.sysadmin_mount.run_mount", return_value=4) as run_mount:
            self.assertEqual(run_sysadmin_command(args), 4)
        run_mount.assert_called_once_with("server:/srv", "/mnt/server", username="admin", ssh_key="/tmp/key", port=2222, read_only=True)

    def test_umount_and_health_dispatch(self) -> None:
        umount_args = self._parse("umount", "server")
        with patch("lib.sysadmin_mount.run_umount", return_value=3) as run_umount:
            self.assertEqual(run_sysadmin_command(umount_args), 3)
        run_umount.assert_called_once_with("server")

        health_args = self._parse("health", "server", "-u", "admin")
        with patch("lib.sysadmin_health.run_health", return_value=2) as run_health:
            self.assertEqual(run_sysadmin_command(health_args), 2)
        run_health.assert_called_once_with("server", username="admin", ssh_key=None)

    def test_ssh_dispatch_strips_separator(self) -> None:
        args = self._parse("ssh", "server", "--", "journalctl", "-f")
        with patch("lib.sysadmin_ssh.run_ssh", return_value=5) as run_ssh:
            self.assertEqual(run_sysadmin_command(args), 5)
        run_ssh.assert_called_once_with("server", username=None, ssh_key=None, port=None, remote_command=["journalctl", "-f"])

    def test_transfer_dispatch(self) -> None:
        push_args = self._parse("push", "./dist", "server:/srv", "--delete", "--dry-run")
        with patch("lib.sysadmin_transfer.run_push", return_value=6) as run_push:
            self.assertEqual(run_sysadmin_command(push_args), 6)
        run_push.assert_called_once_with("./dist", "server:/srv", username=None, ssh_key=None, port=None, delete=True, dry_run=True)

        pull_args = self._parse("pull", "server:/srv", "./copy", "--dry-run")
        with patch("lib.sysadmin_transfer.run_pull", return_value=7) as run_pull:
            self.assertEqual(run_sysadmin_command(pull_args), 7)
        run_pull.assert_called_once_with("server:/srv", "./copy", username=None, ssh_key=None, port=None, dry_run=True)

    def test_key_and_host_key_dispatch(self) -> None:
        key_args = self._parse("key", "push", "server", "--pubkey", "/tmp/id.pub")
        with patch("lib.sysadmin_keys.run_key_push", return_value=8) as run_key_push:
            self.assertEqual(run_sysadmin_command(key_args), 8)
        run_key_push.assert_called_once_with("server", username=None, ssh_key=None, pubkey_path="/tmp/id.pub")

        enroll_args = self._parse("ssh-key", "enroll", "server", "--yes")
        with patch("lib.ssh_enrollment.enroll_host_key", return_value=9) as enroll:
            self.assertEqual(run_sysadmin_command(enroll_args), 9)
        enroll.assert_called_once_with("server", port=22, assume_yes=True)

    def test_fan_and_df_dispatch(self) -> None:
        fan_args = argparse.Namespace(
            _sysadmin_cmd="fan",
            hosts=["server"],
            remote_command=["--", "uname", "-a"],
            username=None,
            ssh_key=None,
        )
        with patch("lib.sysadmin_fan.run_fan", return_value=10) as run_fan:
            self.assertEqual(run_sysadmin_command(fan_args), 10)
        run_fan.assert_called_once_with(["server"], ["uname", "-a"], username=None, ssh_key=None)

        df_args = self._parse("df", "server", "other")
        with patch("lib.sysadmin_fan.run_df", return_value=11) as run_df:
            self.assertEqual(run_sysadmin_command(df_args), 11)
        run_df.assert_called_once_with(["server", "other"], username=None, ssh_key=None)

    def test_service_log_upgrade_and_reachable_dispatch(self) -> None:
        svc_args = self._parse("svc", "server", "nginx", "restart")
        with patch("lib.sysadmin_svc.run_svc", return_value=12) as run_svc:
            self.assertEqual(run_sysadmin_command(svc_args), 12)
        run_svc.assert_called_once_with("server", "nginx", action="restart", username=None, ssh_key=None)

        logs_args = self._parse("logs", "server", "nginx", "-n", "25", "-f")
        with patch("lib.sysadmin_svc.run_logs", return_value=13) as run_logs:
            self.assertEqual(run_sysadmin_command(logs_args), 13)
        run_logs.assert_called_once_with("server", "nginx", lines=25, follow=True, username=None, ssh_key=None)

        upgrade_args = self._parse("upgrade", "server", "--check")
        with patch("lib.sysadmin_upgrade.run_upgrade", return_value=14) as run_upgrade:
            self.assertEqual(run_sysadmin_command(upgrade_args), 14)
        run_upgrade.assert_called_once_with(["server"], username=None, ssh_key=None, check_only=True)

        reachable_args = self._parse("reachable", "server", "--pattern", "*.example")
        with patch("lib.sysadmin_reachable.run_reachable", return_value=15) as run_reachable:
            self.assertEqual(run_sysadmin_command(reachable_args), 15)
        run_reachable.assert_called_once_with(pattern="*.example", hosts=["server"], username=None, ssh_key=None)

        user_args = self._parse("user", "rename", "server", "newadmin", "--yes")
        with patch("lib.sysadmin_user.run_user_rename", return_value=16) as run_user_rename:
            self.assertEqual(run_sysadmin_command(user_args), 16)
        run_user_rename.assert_called_once_with("server", "newadmin", admin_user=None, ssh_key=None, new_home=None, keep_home=False, dry_run=False, assume_yes=True, resume=None)

    def test_missing_required_runtime_commands_return_errors(self) -> None:
        key_args = self._parse("key")
        key_error = io.StringIO()
        with redirect_stderr(key_error):
            self.assertEqual(run_sysadmin_command(key_args), 1)
        self.assertIn("key subcommand required", key_error.getvalue())

        fan_args = self._parse("fan", "server")
        fan_error = io.StringIO()
        with redirect_stderr(fan_error):
            self.assertEqual(run_sysadmin_command(fan_args), 1)
        self.assertIn("remote command is required", fan_error.getvalue())

    def test_unknown_dispatch_command_returns_error(self) -> None:
        error = io.StringIO()
        with redirect_stderr(error):
            result = run_sysadmin_command(argparse.Namespace(_sysadmin_cmd="unknown"))
        self.assertEqual(result, 1)
        self.assertIn("unknown sysadmin command", error.getvalue())


if __name__ == "__main__":
    unittest.main()
