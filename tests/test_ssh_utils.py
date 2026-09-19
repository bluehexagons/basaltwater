"""Tests for shared SSH/SCP/rsync command builders."""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from lib.ssh_utils import (
    build_rsync_ssh_transport,
    build_scp_command,
    build_ssh_command,
    chain_remote_commands,
    ensure_remote_sudo,
    get_ssh_control_path,
    shell_join,
    ssh_batch_mode,
    ssh_process_timeout,
)


class TestSshUtils(unittest.TestCase):
    @patch("lib.ssh_utils.sys.stdin.isatty", return_value=True)
    def test_ssh_batch_mode_allows_terminal_prompts(self, _mock_isatty):
        self.assertFalse(ssh_batch_mode())

    @patch("lib.ssh_utils.sys.stdin.isatty", return_value=False)
    def test_ssh_batch_mode_requires_agent_without_terminal(self, _mock_isatty):
        self.assertTrue(ssh_batch_mode())

    @patch("lib.ssh_utils.sys.stdin.isatty", return_value=True)
    def test_interactive_ssh_waits_for_passphrase_without_wall_clock_timeout(
        self, _mock_isatty
    ):
        self.assertIsNone(ssh_process_timeout(60))

    @patch("lib.ssh_utils.sys.stdin.isatty", return_value=False)
    def test_noninteractive_ssh_keeps_bounded_timeout(self, _mock_isatty):
        self.assertEqual(ssh_process_timeout(60), 60)

    def test_control_path_is_private_and_identity_specific(self):
        first = get_ssh_control_path("10.0.0.10", "root", "/tmp/key")
        second = get_ssh_control_path("10.0.0.11", "root", "/tmp/key")

        self.assertNotEqual(first, second)
        self.assertTrue(first.endswith(".sock"))
        self.assertIn("basaltwater-ssh-", first)

    def test_shell_join_quotes_spaces(self):
        self.assertEqual(shell_join(["cat", "/tmp/file name.txt"]), "cat '/tmp/file name.txt'")

    def test_chain_remote_commands_quotes_each_command(self):
        command = chain_remote_commands(
            [
                ["mkdir", "-p", "/tmp/infra tools"],
                ["python3", "/opt/basaltwater/remote_setup.py", "--name", "web node"],
            ]
        )
        self.assertEqual(
            command,
            "mkdir -p '/tmp/infra tools' && python3 /opt/basaltwater/remote_setup.py --name 'web node'",
        )

    @patch("lib.ssh_utils.ensure_workspace_dir")
    @patch("lib.ssh_utils.get_known_hosts_path", return_value="/tmp/workspace/known_hosts")
    def test_build_ssh_command_with_remote_command(self, _mock_known_hosts, _mock_ensure_workspace):
        command = build_ssh_command(
            "example.com",
            "deploy",
            "/tmp/key",
            port=2222,
            remote_command="echo ok",
            batch_mode=True,
            server_alive_interval=None,
        )
        self.assertEqual(
            command,
            [
                "ssh",
                "-i",
                "/tmp/key",
                "-p",
                "2222",
                "-o",
                "UserKnownHostsFile=/tmp/workspace/known_hosts",
                "-o",
                "StrictHostKeyChecking=yes",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=30",
                "deploy@example.com",
                "echo ok",
            ],
        )

    @patch("lib.ssh_utils.ensure_workspace_dir")
    @patch("lib.ssh_utils.get_known_hosts_path", return_value="/tmp/workspace/known_hosts")
    def test_build_ssh_command_can_reuse_authenticated_connection(
        self, _mock_known_hosts, _mock_ensure_workspace
    ):
        command = build_ssh_command(
            "example.com",
            "root",
            "/tmp/key",
            control_path="/tmp/basaltwater.sock",
        )

        self.assertIn("ControlMaster=auto", command)
        self.assertIn("ControlPersist=60s", command)
        self.assertIn("ControlPath=/tmp/basaltwater.sock", command)

    def test_build_ssh_command_accepts_explicit_known_hosts_path(self):
        command = build_ssh_command(
            "example.com",
            "deploy",
            known_hosts_path="/secure/workspace/known_hosts",
        )

        self.assertIn(
            "UserKnownHostsFile=/secure/workspace/known_hosts",
            command,
        )

    @patch("lib.ssh_utils.ssh_batch_mode", return_value=True)
    @patch("lib.ssh_utils.subprocess.run")
    def test_remote_sudo_succeeds_without_prompt_when_nopasswd_is_available(
        self,
        mock_run,
        _mock_batch_mode,
    ):
        mock_run.return_value = subprocess.CompletedProcess([], 0, "", "")

        with patch("lib.ssh_utils.build_ssh_command", return_value=["ssh"]):
            self.assertTrue(
                ensure_remote_sudo(
                    "192.0.2.40",
                    "agent",
                    "/tmp/key",
                    control_path="/tmp/basaltwater.sock",
                )
            )

        mock_run.assert_called_once_with(
            ["ssh"],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )

    @patch("lib.ssh_utils.subprocess.run")
    def test_remote_sudo_requires_noninteractive_policy(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            [], 1, "", "sudo: a password is required\n"
        )

        with patch("lib.ssh_utils.build_ssh_command", return_value=["ssh"]):
            self.assertFalse(ensure_remote_sudo("192.0.2.40", "agent"))

        mock_run.assert_called_once()

    @patch("lib.ssh_utils.ssh_batch_mode", return_value=False)
    @patch("lib.ssh_utils.subprocess.run")
    def test_remote_sudo_passphrase_prompt_is_not_timed_out(
        self, mock_run, _mock_batch_mode
    ):
        mock_run.return_value = subprocess.CompletedProcess([], 0, "", "")

        with patch("lib.ssh_utils.build_ssh_command", return_value=["ssh"]):
            self.assertTrue(ensure_remote_sudo("192.0.2.40", "agent"))

        self.assertIsNone(mock_run.call_args.kwargs["timeout"])

    @patch("lib.ssh_utils.ssh_batch_mode", return_value=False)
    @patch("lib.ssh_utils.subprocess.run")
    def test_remote_sudo_retries_interactive_ssh_connection_failure(
        self, mock_run, _mock_batch_mode
    ):
        mock_run.side_effect = [
            subprocess.CompletedProcess(
                [], 255, "", "Connection closed by 192.0.2.40 port 22\n"
            ),
            subprocess.CompletedProcess([], 0, "", ""),
        ]

        with patch("lib.ssh_utils.build_ssh_command", return_value=["ssh"]):
            self.assertTrue(ensure_remote_sudo("192.0.2.40", "agent"))

        self.assertEqual(mock_run.call_count, 2)

    @patch("lib.ssh_utils.ssh_batch_mode", return_value=True)
    @patch("lib.ssh_utils.subprocess.run")
    def test_remote_sudo_does_not_retry_noninteractive_ssh_failure(
        self, mock_run, _mock_batch_mode
    ):
        mock_run.return_value = subprocess.CompletedProcess(
            [], 255, "", "Connection closed by 192.0.2.40 port 22\n"
        )

        with patch("lib.ssh_utils.build_ssh_command", return_value=["ssh"]):
            self.assertFalse(ensure_remote_sudo("192.0.2.40", "agent"))

        mock_run.assert_called_once()

    @patch("lib.ssh_utils.ensure_workspace_dir")
    @patch("lib.ssh_utils.get_known_hosts_path", return_value="/tmp/workspace/known_hosts")
    def test_build_scp_command(self, _mock_known_hosts, _mock_ensure_workspace):
        command = build_scp_command(
            "example.com",
            "deploy",
            "/tmp/local file",
            "/tmp/remote file",
            "/tmp/key",
            port=2222,
            batch_mode=True,
        )
        self.assertEqual(
            command,
            [
                "scp",
                "-i",
                "/tmp/key",
                "-P",
                "2222",
                "-o",
                "UserKnownHostsFile=/tmp/workspace/known_hosts",
                "-o",
                "StrictHostKeyChecking=yes",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=30",
                "/tmp/local file",
                "deploy@example.com:/tmp/remote file",
            ],
        )

    @patch("lib.ssh_utils.ssh_batch_mode", return_value=False)
    @patch("lib.ssh_utils.ensure_workspace_dir")
    @patch("lib.ssh_utils.get_known_hosts_path", return_value="/tmp/workspace/known_hosts")
    def test_build_scp_command_allows_passphrase_prompt_in_terminal(
        self, _mock_known_hosts, _mock_ensure_workspace, _mock_batch_mode
    ):
        command = build_scp_command(
            "example.com", "deploy", "/tmp/local", "/tmp/remote", "/tmp/key"
        )
        self.assertIn("BatchMode=no", command)

    @patch("lib.ssh_utils.ensure_workspace_dir")
    @patch("lib.ssh_utils.get_known_hosts_path", return_value="/tmp/workspace/known_hosts")
    def test_build_rsync_ssh_transport_quotes_key_path(self, _mock_known_hosts, _mock_ensure_workspace):
        transport = build_rsync_ssh_transport(
            ssh_key="/tmp/key file", port=2222, batch_mode=True
        )
        self.assertIn("ssh", transport)
        self.assertIn("'/tmp/key file'", transport)
        self.assertIn("-p 2222", transport)
        self.assertIn("UserKnownHostsFile=/tmp/workspace/known_hosts", transport)
        self.assertIn("StrictHostKeyChecking=yes", transport)
        self.assertIn("BatchMode=yes", transport)

    @patch("lib.ssh_utils.ssh_batch_mode", return_value=False)
    @patch("lib.ssh_utils.ensure_workspace_dir")
    @patch("lib.ssh_utils.get_known_hosts_path", return_value="/tmp/workspace/known_hosts")
    def test_build_rsync_ssh_transport_allows_passphrase_prompt_in_terminal(
        self, _mock_known_hosts, _mock_ensure_workspace, _mock_batch_mode
    ):
        transport = build_rsync_ssh_transport()
        self.assertIn("BatchMode=no", transport)


if __name__ == "__main__":
    unittest.main()
