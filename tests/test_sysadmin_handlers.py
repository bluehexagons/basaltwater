"""Focused tests for sysadmin handlers at their external-command boundaries."""

from __future__ import annotations

import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

from lib import sysadmin_fan, sysadmin_health, sysadmin_keys, sysadmin_mount
from lib import sysadmin_reachable, sysadmin_ssh, sysadmin_transfer, sysadmin_upgrade
from lib import sysadmin_process, sysadmin_svc
from lib.remote_utils import CommandTimeoutError


def completed(
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(["mock"], returncode, stdout, stderr)


class TestSysadminDeadlines(unittest.TestCase):
    def test_timeout_returns_distinct_failure_with_diagnostic(self):
        with patch.object(sysadmin_process, "run", side_effect=CommandTimeoutError("ssh", 7)) as run:
            result = sysadmin_process.run_command(["ssh"], capture_output=True, timeout=7)
        self.assertEqual(result.returncode, 124)
        self.assertIn("timed out", result.stderr)
        run.assert_called_once_with(["ssh"], check=False, capture_output=True, text=True, timeout=7)

    def test_log_follow_is_explicitly_unbounded_but_snapshot_is_not(self):
        with patch.object(sysadmin_svc, "_resolve_credentials", return_value=("agent", None)), patch.object(sysadmin_svc, "build_ssh_command", return_value=["ssh"]), patch.object(sysadmin_svc, "run_command", return_value=completed(124)) as run, patch.object(sysadmin_svc.os, "execvp") as execute:
            self.assertEqual(sysadmin_svc.run_logs("server", "demo"), 124)
            run.assert_called_once_with(["ssh"], timeout=120)
            execute.assert_not_called()
            self.assertEqual(sysadmin_svc.run_logs("server", "demo", follow=True), 0)
            execute.assert_called_once_with("ssh", ["ssh"])


class TestSysadminFan(unittest.TestCase):
    def test_run_remote_resolves_saved_credentials_and_invokes_ssh(self) -> None:
        config = SimpleNamespace(username="saved", ssh_key="/tmp/saved")
        with patch.object(sysadmin_fan, "load_setup_command", return_value=config), patch.object(sysadmin_fan, "ssh_batch_mode", return_value=True), patch.object(sysadmin_fan, "build_ssh_command", return_value=["ssh"]) as build, patch.object(sysadmin_fan, "run_command", return_value=completed(stdout="ok")) as run:
            result = sysadmin_fan._run_remote("server", "uname -a", None, None)

        self.assertEqual(result, ("server", 0, "ok", ""))
        build.assert_called_once_with("server", "saved", "/tmp/saved", batch_mode=True, connect_timeout=15, remote_command="uname -a")
        run.assert_called_once_with(["ssh"] , capture_output=True, text=True)

    def test_run_fan_sorts_hosts_and_reports_failures(self) -> None:
        def run_remote(host: str, command: str, username: str | None, ssh_key: str | None):
            del username, ssh_key
            return (host, 0 if host == "alpha" else 2, f"output-{host}\n", "bad" if host == "beta" else "")

        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch.object(sysadmin_fan, "_run_remote", side_effect=run_remote):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                result = sysadmin_fan.run_fan(["beta", "alpha"], ["echo", "hello world"], max_workers=2)

        self.assertEqual(result, 1)
        output = stdout.getvalue()
        self.assertLess(output.index("alpha"), output.index("beta"))
        self.assertIn("Summary: 1/2 succeeded, failed: beta", output)
        self.assertIn("bad", stderr.getvalue())

    def test_run_df_filters_failures_and_marks_high_usage(self) -> None:
        def run_remote(host: str, command: str, username: str | None, ssh_key: str | None):
            del command, username, ssh_key
            if host == "bad":
                return (host, 1, "", "connection failed")
            return (host, 0, "Use%  Size Used Avail Mounted\n90% 10G 9G 1G /srv\n", "")

        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch.object(sysadmin_fan, "_run_remote", side_effect=run_remote):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                result = sysadmin_fan.run_df(["bad", "good"], max_workers=2)

        self.assertEqual(result, 0)
        self.assertIn("[!] good", stdout.getvalue())
        self.assertIn("Warning: bad: connection failed", stderr.getvalue())

    def test_df_parser_skips_headers_and_malformed_rows(self) -> None:
        rows = sysadmin_fan._parse_df_lines(
            "Use% Size Used Avail Target\n"
            "85% 10G 8G 2G /data\n"
            "not-a-percent 1G 1G 0G /bad\n"
            "70% 5G 3G 2G /other\n",
            "host",
        )
        self.assertEqual(rows, [("host", "85%", "10G", "8G", "2G", "/data"), ("host", "70%", "5G", "3G", "2G", "/other")])


class TestSysadminHealth(unittest.TestCase):
    def test_disk_highlighting_handles_threshold_and_invalid_values(self) -> None:
        self.assertEqual(sysadmin_health._highlight_disk_line("/dev/sda 85%"), "  [!] /dev/sda 85%")
        self.assertEqual(sysadmin_health._highlight_disk_line("/dev/sda 84%"), "      /dev/sda 84%")
        self.assertEqual(sysadmin_health._highlight_disk_line("/dev/sda unknown"), "      /dev/sda unknown")

    def test_run_health_uses_saved_credentials_and_formats_disk_warning(self) -> None:
        result = completed(stdout="=== DISK ===\nFilesystem Use%\n/dev/sda 90%\n=== END ===\n")
        stdout = io.StringIO()
        with patch.object(sysadmin_health, "load_setup_command", return_value=SimpleNamespace(username="saved", ssh_key="/tmp/saved")), patch.object(sysadmin_health, "build_ssh_command", return_value=["ssh"] ) as build, patch.object(sysadmin_health, "ssh_batch_mode", return_value=True), patch.object(sysadmin_health, "run_command", return_value=result):
            with redirect_stdout(stdout):
                self.assertEqual(sysadmin_health.run_health("server"), 0)

        build.assert_called_once_with("server", "saved", "/tmp/saved", batch_mode=True, remote_command=sysadmin_health._HEALTH_SCRIPT)
        self.assertIn("Health: server", stdout.getvalue())
        self.assertIn("[!] /dev/sda 90%", stdout.getvalue())

    def test_run_health_returns_remote_error(self) -> None:
        stderr = io.StringIO()
        with patch.object(sysadmin_health, "build_ssh_command", return_value=["ssh"]), patch.object(sysadmin_health, "run_command", return_value=completed(255, stderr="permission denied")):
            with redirect_stderr(stderr):
                result = sysadmin_health.run_health("server", username="admin")
        self.assertEqual(result, 255)
        self.assertIn("permission denied", stderr.getvalue())


class TestSysadminKeys(unittest.TestCase):
    def test_key_push_reads_key_and_escapes_shell_quotes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pubkey_path = os.path.join(directory, "id.pub")
            with open(pubkey_path, "w", encoding="utf-8") as key_file:
                key_file.write("ssh-ed25519 AAAA user's-key\n")

            with patch.object(sysadmin_keys, "load_setup_command", return_value=SimpleNamespace(username="saved", ssh_key="/tmp/auth")), patch.object(sysadmin_keys, "build_ssh_command", return_value=["ssh", "server"]) as build, patch.object(sysadmin_keys, "run_command", return_value=completed(0)) as run:
                result = sysadmin_keys.run_key_push("server", pubkey_path=pubkey_path)

        self.assertEqual(result, 0)
        build.assert_called_once()
        self.assertEqual(build.call_args.args[:3], ("server", "saved", "/tmp/auth"))
        self.assertIn("user'\\''s-key", build.call_args.kwargs["remote_command"])
        run.assert_called_once_with(["ssh", "server"])

    def test_key_push_rejects_missing_and_empty_files(self) -> None:
        self.assertEqual(sysadmin_keys.run_key_push("server", pubkey_path="/no/such/key"), 1)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8") as key_file:
            self.assertEqual(sysadmin_keys.run_key_push("server", pubkey_path=key_file.name), 1)


class TestSysadminMount(unittest.TestCase):
    def test_mount_credentials_fall_back_to_saved_config(self) -> None:
        config = SimpleNamespace(username="saved", ssh_key="/tmp/key", port=2200)
        with patch.object(sysadmin_mount, "load_setup_command", return_value=config):
            result = sysadmin_mount._resolve_host_credentials("server", None, None, None)
        self.assertEqual(result, ("saved", "/tmp/key", 2200))

    def test_mount_creates_mountpoint_and_builds_read_only_options(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            mountpoint = os.path.join(directory, "mount")
            with patch.object(sysadmin_mount.shutil, "which", return_value="/usr/bin/sshfs"), patch.object(sysadmin_mount, "get_workspace_known_hosts_path", return_value="/tmp/known_hosts"), patch.object(sysadmin_mount, "ssh_batch_mode", return_value=True), patch.object(sysadmin_mount, "run_command", return_value=completed()) as run:
                result = sysadmin_mount.run_mount("server:/srv/data", mountpoint, username="admin", ssh_key="/tmp/key", port=2222, read_only=True)

            self.assertTrue(os.path.isdir(mountpoint))
        self.assertEqual(result, 0)
        command = run.call_args.args[0]
        self.assertEqual(command[:3], ["sshfs", "admin@server:/srv/data", mountpoint])
        self.assertIn("port=2222", command[-1])
        self.assertIn("ro", command[-1])
        self.assertIn("StrictHostKeyChecking=yes", command[-1])

    def test_mount_rejects_missing_dependency_and_invalid_remote(self) -> None:
        with patch.object(sysadmin_mount.shutil, "which", return_value=None):
            self.assertEqual(sysadmin_mount.run_mount("server:/srv", "/tmp/mount"), 1)
        with patch.object(sysadmin_mount.shutil, "which", return_value="/usr/bin/sshfs"):
            self.assertEqual(sysadmin_mount.run_mount("server", "/tmp/mount"), 1)

    def test_umount_resolves_host_and_falls_back_to_umount(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            local_mount = os.path.join(directory, "mount")
            os.mkdir(local_mount)
            which = lambda command: None if command == "fusermount" else "/usr/bin/umount"
            with patch.object(sysadmin_mount.shutil, "which", side_effect=which), patch.object(sysadmin_mount, "run_command", side_effect=[completed(stdout=f"{local_mount}\n"), completed(0)]) as run:
                result = sysadmin_mount.run_umount("server")

        self.assertEqual(result, 0)
        self.assertEqual(run.call_args_list[0].args[0][0], "findmnt")
        self.assertEqual(run.call_args_list[1].args[0], ["umount", local_mount])

    def test_umount_rejects_multiple_mounts(self) -> None:
        with patch.object(sysadmin_mount.os.path, "exists", return_value=False), patch.object(sysadmin_mount, "run_command", return_value=completed(stdout="/mnt/a\n/mnt/b\n")):
            self.assertEqual(sysadmin_mount.run_umount("server"), 1)


class TestSysadminReachable(unittest.TestCase):
    def test_probe_host_uses_saved_credentials_and_reports_latency(self) -> None:
        config = SimpleNamespace(username="saved", ssh_key="/tmp/key")
        with patch.object(sysadmin_reachable, "load_setup_command", return_value=config), patch.object(sysadmin_reachable, "ssh_batch_mode", return_value=True), patch.object(sysadmin_reachable, "build_ssh_command", return_value=["ssh"]) as build, patch.object(sysadmin_reachable, "run_command", return_value=completed()), patch.object(sysadmin_reachable.time, "monotonic", side_effect=[10.0, 10.025]):
            result = sysadmin_reachable._probe_host("server", None, None)
        self.assertEqual(result[:2], ("server", True))
        self.assertAlmostEqual(result[2], 25.0)
        build.assert_called_once_with("server", "saved", "/tmp/key", batch_mode=True, connect_timeout=5, server_alive_interval=None, remote_command="true")

    def test_list_saved_hosts_ignores_invalid_and_duplicate_entries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with open(os.path.join(directory, "one.json"), "w", encoding="utf-8") as file_obj:
                json.dump({"host": "server"}, file_obj)
            with open(os.path.join(directory, "duplicate.json"), "w", encoding="utf-8") as file_obj:
                json.dump({"host": "server"}, file_obj)
            with open(os.path.join(directory, "invalid.json"), "w", encoding="utf-8") as file_obj:
                file_obj.write("not json")
            with open(os.path.join(directory, "ignored.txt"), "w", encoding="utf-8") as file_obj:
                file_obj.write('{"host": "ignored"}')

            with patch.object(sysadmin_reachable, "get_setup_cache_dir", return_value=directory):
                self.assertEqual(sysadmin_reachable._list_saved_hosts(), ["server"])

    def test_reachable_sorts_results_and_returns_failure_for_unreachable_hosts(self) -> None:
        def probe(host: str, username: str | None, ssh_key: str | None):
            del username, ssh_key
            return host, host == "alpha", 12.0

        stdout = io.StringIO()
        with patch.object(sysadmin_reachable, "_probe_host", side_effect=probe):
            with redirect_stdout(stdout):
                result = sysadmin_reachable.run_reachable(hosts=["beta", "alpha"], max_workers=2)

        self.assertEqual(result, 1)
        output = stdout.getvalue()
        self.assertLess(output.index("alpha"), output.index("beta"))
        self.assertIn("1/2 reachable", output)

    def test_reachable_handles_missing_saved_hosts_and_pattern_misses(self) -> None:
        with patch.object(sysadmin_reachable, "_list_saved_hosts", return_value=[]):
            self.assertEqual(sysadmin_reachable.run_reachable(), 0)
        with patch.object(sysadmin_reachable, "_list_saved_hosts", return_value=["server"]):
            self.assertEqual(sysadmin_reachable.run_reachable(pattern="*.example"), 0)


class TestSysadminSsh(unittest.TestCase):
    def test_ssh_adds_connection_reuse_and_quotes_remote_command(self) -> None:
        with patch.object(sysadmin_ssh, "load_setup_command", return_value=SimpleNamespace(username="saved", ssh_key="/tmp/key", port=2200)), patch.object(sysadmin_ssh, "build_ssh_command", return_value=["ssh", "-p", "2200", "saved@server"]) as build, patch.object(sysadmin_ssh.os, "execvp") as execvp:
            result = sysadmin_ssh.run_ssh("server", remote_command=["journalctl", "-f"])

        self.assertEqual(result, 0)
        build.assert_called_once_with("server", "saved", "/tmp/key", port=2200, batch_mode=False, connect_timeout=30, server_alive_interval=30)
        command = execvp.call_args.args[1]
        self.assertIn("ControlMaster=auto", command)
        self.assertEqual(command[-1], "journalctl -f")


class TestSysadminTransfer(unittest.TestCase):
    def test_push_dry_run_builds_delete_command_without_confirmation(self) -> None:
        with patch.object(sysadmin_transfer.shutil, "which", return_value="/usr/bin/rsync"), patch.object(sysadmin_transfer, "build_rsync_ssh_transport", return_value="ssh -i /tmp/key -p 2222"), patch.object(sysadmin_transfer, "ssh_batch_mode", return_value=True), patch.object(sysadmin_transfer, "run_command", return_value=completed(3)) as run, patch("builtins.input") as input_mock:
            result = sysadmin_transfer.run_push("./dist", "server:/srv/app", username="admin", ssh_key="/tmp/key", port=2222, delete=True, dry_run=True)

        self.assertEqual(result, 3)
        input_mock.assert_not_called()
        command = run.call_args.args[0]
        self.assertIn("admin@server:/srv/app", command)
        self.assertIn("--delete", command)
        self.assertIn("--dry-run", command)

    def test_push_requires_confirmation_for_destructive_delete(self) -> None:
        with patch("builtins.input", return_value="n"), patch.object(sysadmin_transfer, "run_command") as run:
            result = sysadmin_transfer.run_push("./dist", "server:/srv", delete=True)
        self.assertEqual(result, 1)
        run.assert_not_called()

    def test_pull_defaults_to_remote_basename_and_rejects_invalid_remote(self) -> None:
        with patch.object(sysadmin_transfer.shutil, "which", return_value="/usr/bin/rsync"), patch.object(sysadmin_transfer, "build_rsync_ssh_transport", return_value="ssh"), patch.object(sysadmin_transfer, "run_command", return_value=completed()) as run:
            self.assertEqual(sysadmin_transfer.run_pull("server:/srv/data"), 0)
        self.assertIn("data", run.call_args.args[0])
        self.assertEqual(sysadmin_transfer.run_pull("server"), 1)

    def test_transfer_reports_missing_rsync(self) -> None:
        with patch.object(sysadmin_transfer.shutil, "which", return_value=None):
            self.assertEqual(sysadmin_transfer.run_pull("server:/srv/data"), 1)


class TestSysadminUpgrade(unittest.TestCase):
    def test_upgrade_reports_reboot_and_failure(self) -> None:
        def run_remote(host: str, command: str, username: str | None, ssh_key: str | None):
            del username, ssh_key
            if host == "bad":
                return host, 1, "", "apt failed"
            return host, 0, "REBOOT_REQUIRED", ""

        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch.object(sysadmin_upgrade, "_run_remote", side_effect=run_remote):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                result = sysadmin_upgrade.run_upgrade(["bad", "good"], max_workers=2)

        self.assertEqual(result, 1)
        self.assertIn("[OK, REBOOT] good", stdout.getvalue())
        self.assertIn("Reboot required: good", stdout.getvalue())
        self.assertIn("[FAIL] bad: apt failed", stderr.getvalue())

    def test_upgrade_check_parses_pending_count_and_unknown_output(self) -> None:
        def run_remote(host: str, command: str, username: str | None, ssh_key: str | None):
            del command, username, ssh_key
            return host, 0, "7\n" if host == "good" else "not-a-count\n", ""

        stdout = io.StringIO()
        with patch.object(sysadmin_upgrade, "_run_remote", side_effect=run_remote):
            with redirect_stdout(stdout):
                result = sysadmin_upgrade.run_upgrade(["good", "unknown"], check_only=True, max_workers=2)

        self.assertEqual(result, 0)
        self.assertIn("good: 7 package(s) pending", stdout.getvalue())
        self.assertIn("unknown: ? package(s) pending", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
