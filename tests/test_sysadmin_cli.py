"""Tests for sysadmin command registration and dispatch."""

from __future__ import annotations

import argparse
import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from basaltwater import create_basaltwater_parser, main
from lib.sysadmin_cli import run_sysadmin_command


class SysadminCliTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.parser, _, _ = create_basaltwater_parser()

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
            (("rolling-update", "app", "pve1", "--resume", "--reboot-timeout", "180"), {"_sysadmin_cmd": "rolling-update", "targets": ["app", "pve1"], "resume": True, "reboot_timeout": 180}),
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
        enroll.assert_called_once_with(
            "server", port=22, assume_yes=True, expected_fingerprint=None,
        )

    def test_pinned_host_key_dispatch_does_not_assume_trust(self) -> None:
        fingerprint = "SHA256:" + "a" * 43
        args = self._parse("ssh-key", "enroll", "server", "--fingerprint", fingerprint)
        with patch("lib.ssh_enrollment.enroll_host_key", return_value=0) as enroll:
            self.assertEqual(run_sysadmin_command(args), 0)
        enroll.assert_called_once_with(
            "server", port=22, assume_yes=False, expected_fingerprint=fingerprint,
        )

    def test_invalid_fingerprint_fails_before_network_access(self) -> None:
        args = self._parse("ssh-key", "enroll", "server", "--fingerprint", "SHA256:short")
        with (
            patch("lib.ssh_enrollment.subprocess.run") as run,
            patch("builtins.print") as output,
        ):
            self.assertEqual(run_sysadmin_command(args), 1)
        run.assert_not_called()
        output.assert_called_once_with(
            "Error: Expected fingerprint must use the OpenSSH SHA256:... format",
        )

    def test_fan_and_df_dispatch(self) -> None:
        fan_args = self._parse("fan", "server", "--", "uname", "-a")
        with patch("lib.sysadmin_fan.run_fan", return_value=10) as run_fan:
            self.assertEqual(run_sysadmin_command(fan_args), 10)
        run_fan.assert_called_once_with(["server"], ["uname", "-a"], username=None, ssh_key=None)

        df_args = self._parse("df", "server", "other")
        with patch("lib.sysadmin_fan.run_df", return_value=11) as run_df:
            self.assertEqual(run_sysadmin_command(df_args), 11)
        run_df.assert_called_once_with(["server", "other"], username=None, ssh_key=None)

    def test_fan_options_and_remote_tokens_reach_dispatch(self) -> None:
        remote = ["printf", "--", "%s", "two words", "", "--username", "remote"]
        for connection in (
            ["-u", "root", "-i", "/tmp/key", "h1", "h2"],
            ["h1", "h2", "--username=root", "--key=/tmp/key"],
            ["h1", "-u", "root", "h2", "-i", "/tmp/key"],
        ):
            with self.subTest(connection=connection):
                args = self._parse("fan", *connection, "--", *remote)
                with patch("lib.sysadmin_fan.run_fan", return_value=7) as run:
                    self.assertEqual(run_sysadmin_command(args), 7)
                run.assert_called_once_with(
                    ["h1", "h2"], remote, username="root", ssh_key="/tmp/key",
                )

    def test_fan_minimal_multi_host_command_reaches_dispatch(self) -> None:
        for remote in (["uptime"], ["--", "id", "--help"]):
            with self.subTest(remote=remote):
                args = self._parse("fan", "h1", "h2", "--", *remote)
                with patch("lib.sysadmin_fan.run_fan", return_value=0) as run:
                    self.assertEqual(run_sysadmin_command(args), 0)
                run.assert_called_once_with(
                    ["h1", "h2"], remote, username=None, ssh_key=None,
                )

    def test_ssh_root_override_and_remote_tokens_reach_dispatch(self) -> None:
        for connection in (
            ["-u", "root", "-i", "/tmp/key", "-p", "2222", "h1"],
            ["h1", "--username=root", "--key=/tmp/key", "--port=2222"],
        ):
            for remote in (["id"], ["--", "printf", "--", "", "two words", "-u", "remote"]):
                with self.subTest(connection=connection, remote=remote):
                    args = self._parse("ssh", *connection, "--", *remote)
                    with patch("lib.sysadmin_ssh.run_ssh", return_value=8) as run:
                        self.assertEqual(run_sysadmin_command(args), 8)
                    run.assert_called_once_with(
                        "h1", username="root", ssh_key="/tmp/key", port=2222,
                        remote_command=remote,
                    )

    def test_ssh_direct_command_and_interactive_forms(self) -> None:
        for suffix, remote in (
            ([], None),
            (["journalctl", "-u", "nginx", "--no-pager"], ["journalctl", "-u", "nginx", "--no-pager"]),
        ):
            for connection in (["-u", "root", "h1"], ["h1", "-uroot"], ["h1", "--user", "root"]):
                with self.subTest(connection=connection, suffix=suffix):
                    args = self._parse("ssh", *connection, *suffix)
                    with patch("lib.sysadmin_ssh.run_ssh", return_value=0) as run:
                        self.assertEqual(run_sysadmin_command(args), 0)
                    run.assert_called_once_with(
                        "h1", username="root", ssh_key=None, port=None, remote_command=remote,
                    )

    def test_unknown_connection_options_and_missing_hosts_fail(self) -> None:
        for argv in (
            ["fan", "h1", "--typo", "root", "--", "uptime"],
            ["ssh", "h1", "--typo", "root", "--", "id"],
            ["ssh", "h1", "--typo"],
            ["ssh", "h1", "-u", "--", "id"],
            ["ssh", "h1", "-p", "oops", "--", "id"],
            ["fan", "--", "uptime"],
            ["ssh", "--", "id"],
        ):
            with self.subTest(argv=argv), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    self._parse(*argv)
                self.assertEqual(error.exception.code, 2)

    def test_ssh_direct_command_preserves_remote_delimiters(self) -> None:
        remote = ["printf", "--", "%s", "two words", "", "--username", "remote"]
        for connection in (["-u", "root", "h1"], ["h1", "--username=root"]):
            for delimiter in ([], ["--"]):
                argv = ["ssh", *connection, *delimiter, *remote]
                with self.subTest(argv=argv):
                    args = self._parse(*argv)
                    with patch("lib.sysadmin_ssh.run_ssh", return_value=0) as run:
                        self.assertEqual(run_sysadmin_command(args), 0)
                    run.assert_called_once_with(
                        "h1", username="root", ssh_key=None, port=None,
                        remote_command=remote,
                    )
                    with (
                        patch("sys.argv", ["basaltw", *argv]),
                        patch("basaltwater.argcomplete", None),
                        patch("lib.sysadmin_ssh.run_ssh", return_value=0) as run,
                    ):
                        self.assertEqual(main(), 0)
                    run.assert_called_once_with(
                        "h1", username="root", ssh_key=None, port=None,
                        remote_command=remote,
                    )

    def test_explicit_empty_commands_never_connect(self) -> None:
        for command, module in (("fan", "lib.sysadmin_fan.run_fan"), ("ssh", "lib.sysadmin_ssh.run_ssh")):
            with self.subTest(command=command):
                args = self._parse(command, "h1", "-u", "root", "--")
                error = io.StringIO()
                with patch(module) as run, redirect_stderr(error):
                    self.assertEqual(run_sysadmin_command(args), 1)
                run.assert_not_called()
                self.assertIn("remote command is required after --", error.getvalue())

    def test_lazy_main_preserves_workspace_and_remote_argv(self) -> None:
        for command, module, hosts in (
            ("fan", "lib.sysadmin_fan.run_fan", ["h1", "h2"]),
            ("ssh", "lib.sysadmin_ssh.run_ssh", ["h1"]),
        ):
            argv = ["basaltw", "--workspace", "/tmp/workspace", command, *hosts, "-u", "root", "--", "id", "--", "-u"]
            with (
                self.subTest(command=command),
                patch("sys.argv", argv),
                patch("basaltwater.argcomplete", None),
                patch("basaltwater.validate_workspace_dir") as validate,
                patch("basaltwater.set_workspace_dir") as workspace,
                patch(module, return_value=6) as run,
            ):
                self.assertEqual(main(), 6)
                validate.assert_called_once_with("/tmp/workspace")
                workspace.assert_called_once_with("/tmp/workspace")
                if command == "fan":
                    run.assert_called_once_with(hosts, ["id", "--", "-u"], username="root", ssh_key=None)
                else:
                    run.assert_called_once_with("h1", username="root", ssh_key=None, port=None, remote_command=["id", "--", "-u"])

    def test_help_before_delimiter_stays_local(self) -> None:
        for command in ("ssh", "fan"):
            with self.subTest(command=command), redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    self._parse(command, "--help")
                self.assertEqual(error.exception.code, 0)

    def test_service_log_upgrade_and_reachable_dispatch(self) -> None:
        svc_args = self._parse("svc", "server", "nginx", "restart")
        with patch("lib.sysadmin_svc.run_svc", return_value=12) as run_svc:
            self.assertEqual(run_sysadmin_command(svc_args), 12)
        run_svc.assert_called_once_with("server", "nginx", action="restart", username=None, ssh_key=None)

        logs_args = self._parse("logs", "server", "nginx", "-n", "25", "-f")
        with patch("lib.sysadmin_svc.run_logs", return_value=13) as run_logs:
            self.assertEqual(run_sysadmin_command(logs_args), 13)
        run_logs.assert_called_once_with("server", "nginx", lines=25, follow=True, username=None, ssh_key=None)

        upgrade_args = self._parse("upgrade", "server", "--with-new-pkgs", "--check")
        with patch("lib.sysadmin_upgrade.run_upgrade", return_value=14) as run_upgrade:
            self.assertEqual(run_sysadmin_command(upgrade_args), 14)
        run_upgrade.assert_called_once_with(
            ["server"], username=None, ssh_key=None, check_only=True, with_new_pkgs=True,
        )

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

    def test_guest_upgrade_option_requires_explicit_target(self) -> None:
        output = io.StringIO()
        with (
            patch("sys.argv", ["basaltw", "upgrade", "--with-new-pkgs"]),
            patch("basaltwater.argcomplete", None),
            patch("basaltwater.run_tool_upgrade_command") as upgrade_tool,
            patch("lib.sysadmin_upgrade.run_upgrade") as upgrade_hosts,
            redirect_stdout(output),
        ):
            self.assertEqual(main(), 1)
        self.assertIn("--with-new-pkgs requires at least one remote host", output.getvalue())
        upgrade_tool.assert_not_called()
        upgrade_hosts.assert_not_called()

    def test_unified_rolling_update_dispatches_through_main(self) -> None:
        with (
            patch("sys.argv", ["basaltw", "rolling-update", "app", "pve1", "--dry-run"]),
            patch("basaltwater.argcomplete", None),
            patch("lib.cluster_update.run_cluster_update", return_value=1) as run,
        ):
            self.assertEqual(main(), 1)
        run.assert_called_once_with(["app", "pve1"], workspace=None, dry_run=True,
                                    reboot_timeout=300, resume=False, include_debian=True)

    def test_unknown_dispatch_command_returns_error(self) -> None:
        error = io.StringIO()
        with redirect_stderr(error):
            result = run_sysadmin_command(argparse.Namespace(_sysadmin_cmd="unknown"))
        self.assertEqual(result, 1)
        self.assertIn("unknown sysadmin command", error.getvalue())


if __name__ == "__main__":
    unittest.main()
