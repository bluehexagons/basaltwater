"""Tests for workspace-aware CLI parsing and user setup behavior."""

from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import common.common_steps as common_steps
import infra_tools
from lib.arg_parser import create_setup_argument_parser
from lib.config import SetupConfig
from argparse import Namespace


class TestWorkspaceCli(unittest.TestCase):
    def test_setup_parser_accepts_workspace(self):
        parser = create_setup_argument_parser("test")
        args = parser.parse_args(["example.com", "--workspace", "/tmp/workspace"])
        self.assertEqual(args.workspace, "/tmp/workspace")

    def test_setup_parser_accepts_control_plane_flag(self):
        parser = create_setup_argument_parser("test")
        args = parser.parse_args(["localhost", "--control-plane"])
        self.assertTrue(args.control_plane)

    def test_infra_tools_parser_accepts_credentials_command(self):
        parser, _setup_parser, _patch_parser = infra_tools.create_infra_tools_parser()
        args = parser.parse_args(["credentials", "--workspace", "/tmp/workspace", "list"])
        self.assertEqual(args.command, "credentials")
        self.assertEqual(args.workspace, "/tmp/workspace")
        self.assertEqual(args.credentials_command, "list")

    def test_infra_tools_parser_accepts_list_command(self):
        parser, _setup_parser, _patch_parser = infra_tools.create_infra_tools_parser()
        args = parser.parse_args(["list", "--workspace", "/tmp/workspace", "prod"])
        self.assertEqual(args.command, "list")
        self.assertEqual(args.workspace, "/tmp/workspace")
        self.assertEqual(args.pattern, "prod")

    def test_setup_parser_accepts_deploy_latest_pairs(self):
        parser, _setup_parser, _patch_parser = infra_tools.create_infra_tools_parser()
        args = parser.parse_args([
            "setup",
            "server_web",
            "example.com",
            "testuser",
            "--node",
            "--deploy-latest",
            "hexagonalhomelab.com/,/",
            "https://github.com/bluehexagons/bluehexagons.git",
            "--deploy-latest",
            "clicker.hexagonalhomelab.com",
            "https://github.com/bluehexagons/clicker.git",
            "--deploy-latest",
            "foodguide.hexagonalhomelab.com,/foodguide,hexagonalhomelab.com/foodguide",
            "https://github.com/bluehexagons/foodguide.git",
        ])

        self.assertTrue(args.deploy_latest)
        self.assertEqual(args.deploy_specs, [
            ["hexagonalhomelab.com/,/", "https://github.com/bluehexagons/bluehexagons.git"],
            ["clicker.hexagonalhomelab.com", "https://github.com/bluehexagons/clicker.git"],
            ["foodguide.hexagonalhomelab.com,/foodguide,hexagonalhomelab.com/foodguide", "https://github.com/bluehexagons/foodguide.git"],
        ])

    def test_remote_setup_parser_accepts_deploy_latest_pairs(self):
        parser = create_setup_argument_parser("test", for_remote=True, allow_steps=True)
        args = parser.parse_args([
            "--system-type",
            "server_web",
            "--deploy-latest",
            "clicker.example.com",
            "https://github.com/user/clicker.git",
        ])

        self.assertTrue(args.deploy_latest)
        self.assertEqual(args.deploy_specs, [["clicker.example.com", "https://github.com/user/clicker.git"]])

    def test_setup_parser_still_accepts_repeated_deploy_flags(self):
        parser, _setup_parser, _patch_parser = infra_tools.create_infra_tools_parser()
        args = parser.parse_args([
            "setup",
            "server_web",
            "example.com",
            "testuser",
            "--deploy",
            "clicker.example.com",
            "https://github.com/user/clicker.git",
            "--deploy",
            "food.example.com",
            "https://github.com/user/food.git",
        ])

        self.assertEqual(args.deploy_specs, [
            ["clicker.example.com", "https://github.com/user/clicker.git"],
            ["food.example.com", "https://github.com/user/food.git"],
        ])

    def test_infra_tools_parser_accepts_recall_command(self):
        parser, _setup_parser, _patch_parser = infra_tools.create_infra_tools_parser()
        args = parser.parse_args(["recall", "example.com", "admin", "--key", "~/.ssh/id_ed25519"])
        self.assertEqual(args.command, "recall")
        self.assertEqual(args.host, "example.com")
        self.assertEqual(args.username, "admin")
        self.assertEqual(args.ssh_key, "~/.ssh/id_ed25519")

    def test_infra_tools_parser_accepts_reconstruct_command(self):
        parser, _setup_parser, _patch_parser = infra_tools.create_infra_tools_parser()
        args = parser.parse_args(["reconstruct", "--compact"])
        self.assertEqual(args.command, "reconstruct")
        self.assertTrue(args.compact)

    def test_infra_tools_parser_accepts_completions_command(self):
        parser, _setup_parser, _patch_parser = infra_tools.create_infra_tools_parser()
        args = parser.parse_args(["completions", "--shell", "zsh", "--global"])
        self.assertEqual(args.command, "completions")
        self.assertEqual(args.shell, "zsh")
        self.assertTrue(args.global_install)

    def test_infra_tools_parser_accepts_python_tools_command(self):
        parser, _setup_parser, _patch_parser = infra_tools.create_infra_tools_parser()
        args = parser.parse_args(["python-tools", "--shell", "fish"])
        self.assertEqual(args.command, "python-tools")
        self.assertEqual(args.shell, "fish")

    def test_infra_tools_parser_accepts_bootstrap_command(self):
        parser, _setup_parser, _patch_parser = infra_tools.create_infra_tools_parser()
        args = parser.parse_args(["bootstrap", "--user", "admin", "--shell", "zsh", "--skip-system-packages"])
        self.assertEqual(args.command, "bootstrap")
        self.assertEqual(args.bootstrap_user, "admin")
        self.assertEqual(args.shell, "zsh")
        self.assertTrue(args.skip_system_packages)

    def test_infra_tools_parser_accepts_channel_selectors(self):
        parser, _setup_parser, _patch_parser = infra_tools.create_infra_tools_parser()
        args = parser.parse_args(["channel", "branch-feature/example"])
        self.assertEqual(args.command, "channel")
        self.assertEqual(args.channel_name, "branch-feature/example")

    def test_infra_tools_upgrade_without_hosts_is_tool_upgrade(self):
        parser, _setup_parser, _patch_parser = infra_tools.create_infra_tools_parser()
        args = parser.parse_args(["upgrade"])
        self.assertEqual(args.command, "upgrade")
        self.assertEqual(args.hosts, [])

    def test_infra_tools_setup_parser_accepts_antistatic_flags(self):
        parser, _setup_parser, _patch_parser = infra_tools.create_infra_tools_parser()
        args = parser.parse_args([
            "setup",
            "server_web",
            "example.com",
            "--antistatic-server",
            "lobby.example.com:9090",
            "--antistatic-admin",
            "operator",
            "--antistatic-db",
            "db.example.com:9091",
        ])
        self.assertEqual(args.command, "setup")
        self.assertEqual(args.antistatic_server, "lobby.example.com:9090")
        self.assertEqual(args.antistatic_admin, "operator")
        self.assertEqual(args.antistatic_db, "db.example.com:9091")

    def test_infra_tools_setup_parser_accepts_gogs_flag(self):
        parser, _setup_parser, _patch_parser = infra_tools.create_infra_tools_parser()
        args = parser.parse_args([
            "setup",
            "server_web",
            "example.com",
            "--gogs",
            "git.example.com:3000",
            "/srv/gogs",
        ])
        self.assertEqual(args.command, "setup")
        self.assertEqual(args.gogs, ["git.example.com:3000", "/srv/gogs"])

    def test_reconstruct_command_uses_unified_setup_entrypoint(self):
        config = SetupConfig(
            host="example.com",
            username="testuser",
            system_type="server_lite",
        )

        command = infra_tools.reconstruct_command(config)

        self.assertIn("basaltw setup server_lite", command)
        self.assertNotIn("setup_server_lite.py", command)

    @patch("builtins.print")
    @patch("infra_tools.set_workspace_credential", side_effect=ValueError("Credential username must not contain ':'"))
    def test_infra_tools_credentials_set_rejects_invalid_username(
        self,
        _mock_set_credential,
        mock_print,
    ):
        parser = unittest.mock.MagicMock()
        setup_parser = unittest.mock.MagicMock()
        patch_parser = unittest.mock.MagicMock()
        args = unittest.mock.MagicMock()
        args.command = "credentials"
        args.credentials_command = "set"
        args.username = "bad:user"
        args.password = "secret"
        args.workspace = None
        parser.parse_args.return_value = args

        with patch(
            "infra_tools.create_infra_tools_parser",
            return_value=(parser, setup_parser, patch_parser),
        ):
            result = infra_tools.main()

        self.assertEqual(result, 1)
        mock_print.assert_called_with("Error: Credential username must not contain ':'")

    @patch("builtins.print")
    @patch("infra_tools.getpass.getpass", return_value="secret")
    @patch("infra_tools.set_workspace_credential")
    def test_infra_tools_credentials_set_prompts_when_password_is_omitted(
        self,
        mock_set_credential,
        mock_getpass,
        _mock_print,
    ):
        parser = unittest.mock.MagicMock()
        args = unittest.mock.MagicMock()
        args.command = "credentials"
        args.credentials_command = "set"
        args.username = "operator"
        args.password = None
        args.workspace = None
        parser.parse_args.return_value = args

        with patch(
            "infra_tools.create_infra_tools_parser",
            return_value=(parser, unittest.mock.MagicMock(), unittest.mock.MagicMock()),
        ):
            result = infra_tools.main()

        self.assertEqual(result, 0)
        mock_getpass.assert_called_once_with("Password for operator: ")
        mock_set_credential.assert_called_once_with("operator", "secret")

    def test_unified_runtime_validation_rejects_antistatic_admin_without_tls(self):
        config = SetupConfig(
            host="example.com",
            username="testuser",
            system_type="server_lite",
            antistatic_server="lobby.example.com",
            antistatic_admin="operator",
            share_credentials=[["operator", "secret"]],
        )

        with self.assertRaisesRegex(ValueError, "requires --ssl or --cloudflare"):
            infra_tools._prepare_runtime_config_for_cli(config)

    @patch("builtins.print")
    @patch("infra_tools.validate_workspace_dir", side_effect=ValueError("bad workspace"))
    @patch("infra_tools.set_workspace_dir")
    def test_infra_tools_main_rejects_invalid_workspace(
        self,
        mock_set_workspace,
        _mock_validate_workspace,
        mock_print,
    ):
        parser = unittest.mock.MagicMock()
        setup_parser = unittest.mock.MagicMock()
        patch_parser = unittest.mock.MagicMock()
        args = unittest.mock.MagicMock()
        args.workspace = "/bad/workspace"
        parser.parse_args.return_value = args

        with patch(
            "infra_tools.create_infra_tools_parser",
            return_value=(parser, setup_parser, patch_parser),
        ):
            result = infra_tools.main()

        self.assertEqual(result, 1)
        mock_set_workspace.assert_not_called()
        mock_print.assert_called_with("Error: bad workspace")

    @patch("builtins.print")
    @patch("infra_tools.validate_samba_share_credentials")
    @patch("infra_tools.prepare_runtime_config")
    @patch("infra_tools.validate_username", return_value=True)
    @patch("infra_tools.validate_host", return_value=True)
    def test_run_setup_command_rejects_invalid_notify_specs(
        self,
        _mock_validate_host,
        _mock_validate_username,
        mock_prepare_runtime_config,
        _mock_validate_samba,
        mock_print,
    ):
        config = SetupConfig(
            host="example.com",
            username="testuser",
            system_type="server_lite",
            notify_specs=[["webhook", "not-a-url"]],
        )
        mock_prepare_runtime_config.return_value = config
        args = Namespace(host="example.com", username="testuser", system_type="server_lite")

        with patch("infra_tools.SetupConfig.from_args", return_value=config), \
             patch("infra_tools.run_remote_setup") as mock_run_remote:
            result = infra_tools.run_setup_command(args)

        self.assertEqual(result, 1)
        mock_run_remote.assert_not_called()
        mock_print.assert_called_with("Error: Invalid webhook URL")

    @patch("builtins.print")
    @patch("infra_tools.validate_samba_share_credentials")
    @patch("infra_tools.prepare_runtime_config")
    @patch("infra_tools.validate_username", return_value=True)
    @patch("infra_tools.validate_host", return_value=True)
    def test_run_setup_command_rejects_invalid_deploy_target(
        self,
        _mock_validate_host,
        _mock_validate_username,
        mock_prepare_runtime_config,
        _mock_validate_samba,
        mock_print,
    ):
        config = SetupConfig(
            host="example.com",
            username="testuser",
            system_type="server_lite",
            deploy_targets=["bad target"],
        )
        mock_prepare_runtime_config.return_value = config
        args = Namespace(host="example.com", username="testuser", system_type="server_lite")

        with patch("infra_tools.SetupConfig.from_args", return_value=config), \
             patch("infra_tools.run_remote_setup") as mock_run_remote:
            result = infra_tools.run_setup_command(args)

        self.assertEqual(result, 1)
        mock_run_remote.assert_not_called()
        mock_print.assert_called_with("Error: Invalid deploy target host: bad target")

    @patch("builtins.print")
    @patch("infra_tools.validate_samba_share_credentials")
    @patch("infra_tools.prepare_runtime_config")
    @patch("infra_tools.validate_username", return_value=True)
    @patch("infra_tools.validate_host", return_value=True)
    def test_run_setup_command_rejects_invalid_deploy_spec(
        self,
        _mock_validate_host,
        _mock_validate_username,
        mock_prepare_runtime_config,
        _mock_validate_samba,
        mock_print,
    ):
        config = SetupConfig(
            host="example.com",
            username="testuser",
            system_type="server_lite",
            deploy_specs=[["bad domain", "https://github.com/user/repo.git"]],
        )
        mock_prepare_runtime_config.return_value = config
        args = Namespace(host="example.com", username="testuser", system_type="server_lite")

        with patch("infra_tools.SetupConfig.from_args", return_value=config), \
             patch("infra_tools.run_remote_setup") as mock_run_remote:
            result = infra_tools.run_setup_command(args)

        self.assertEqual(result, 1)
        mock_run_remote.assert_not_called()
        mock_print.assert_called_with("Error: Invalid deploy domain: bad domain")

    @patch("builtins.print")
    @patch("infra_tools.validate_samba_share_credentials")
    @patch("infra_tools.prepare_runtime_config")
    @patch("infra_tools.validate_username", return_value=True)
    @patch("infra_tools.validate_host", return_value=True)
    def test_run_setup_command_rejects_invalid_proxmox_node(
        self,
        _mock_validate_host,
        _mock_validate_username,
        mock_prepare_runtime_config,
        _mock_validate_samba,
        mock_print,
    ):
        config = SetupConfig(
            host="example.com",
            username="testuser",
            system_type="server_lite",
            hosted_node="bad host",
            container_memory="2G",
            container_storage=[["root", "auto", "10G"]],
        )
        mock_prepare_runtime_config.return_value = config
        args = Namespace(host="example.com", username="testuser", system_type="server_lite")

        with patch("infra_tools.SetupConfig.from_args", return_value=config), \
             patch("infra_tools.run_remote_setup") as mock_run_remote:
            result = infra_tools.run_setup_command(args)

        self.assertEqual(result, 1)
        mock_run_remote.assert_not_called()
        mock_print.assert_called_with("Error: Invalid Proxmox node host: bad host")

    @patch("builtins.print")
    @patch("infra_tools.validate_samba_share_credentials")
    @patch("infra_tools.prepare_runtime_config")
    @patch("infra_tools.validate_username", return_value=True)
    @patch("infra_tools.validate_host", return_value=True)
    def test_run_setup_command_rejects_invalid_samba_share_spec(
        self,
        _mock_validate_host,
        _mock_validate_username,
        mock_prepare_runtime_config,
        _mock_validate_samba,
        mock_print,
    ):
        config = SetupConfig(
            host="example.com",
            username="testuser",
            system_type="server_lite",
            samba_shares=[["read", "bad/share", "/mnt/docs", "shareuser:secret"]],
        )
        mock_prepare_runtime_config.return_value = config
        args = Namespace(host="example.com", username="testuser", system_type="server_lite")

        with patch("infra_tools.SetupConfig.from_args", return_value=config), \
             patch("infra_tools.run_remote_setup") as mock_run_remote:
            result = infra_tools.run_setup_command(args)

        self.assertEqual(result, 1)
        mock_run_remote.assert_not_called()
        mock_print.assert_called_with("Error: Invalid Samba share name (cannot contain /, \\, or spaces): bad/share")

    @patch("builtins.print")
    @patch("infra_tools.validate_samba_share_credentials")
    @patch("infra_tools.prepare_runtime_config")
    @patch("infra_tools.validate_username", return_value=True)
    @patch("infra_tools.validate_host", return_value=True)
    def test_run_setup_command_rejects_invalid_ssl_email(
        self,
        _mock_validate_host,
        _mock_validate_username,
        mock_prepare_runtime_config,
        _mock_validate_samba,
        mock_print,
    ):
        config = SetupConfig(
            host="example.com",
            username="testuser",
            system_type="server_lite",
            ssl_email="bad-email",
        )
        mock_prepare_runtime_config.return_value = config
        args = Namespace(host="example.com", username="testuser", system_type="server_lite")

        with patch("infra_tools.SetupConfig.from_args", return_value=config), \
             patch("infra_tools.run_remote_setup") as mock_run_remote:
            result = infra_tools.run_setup_command(args)

        self.assertEqual(result, 1)
        mock_run_remote.assert_not_called()
        mock_print.assert_called_with("Error: Invalid SSL email address: bad-email")

    @patch("builtins.print")
    @patch("infra_tools.validate_samba_share_credentials")
    @patch("infra_tools.prepare_runtime_config")
    @patch("infra_tools.validate_username", return_value=True)
    @patch("infra_tools.validate_host", return_value=True)
    def test_run_setup_command_rejects_invalid_apt_package(
        self,
        _mock_validate_host,
        _mock_validate_username,
        mock_prepare_runtime_config,
        _mock_validate_samba,
        mock_print,
    ):
        config = SetupConfig(
            host="example.com",
            username="testuser",
            system_type="server_lite",
            apt_packages=["python3; rm -rf /"],
        )
        mock_prepare_runtime_config.return_value = config
        args = Namespace(host="example.com", username="testuser", system_type="server_lite")

        with patch("infra_tools.SetupConfig.from_args", return_value=config), \
             patch("infra_tools.run_remote_setup") as mock_run_remote:
            result = infra_tools.run_setup_command(args)

        self.assertEqual(result, 1)
        mock_run_remote.assert_not_called()
        mock_print.assert_called_with("Error: Invalid --apt-install name: python3; rm -rf /")

    @patch("builtins.print")
    @patch("infra_tools.validate_samba_share_credentials")
    @patch("infra_tools.prepare_runtime_config")
    @patch("infra_tools.validate_username", return_value=True)
    @patch("infra_tools.validate_host", return_value=True)
    def test_run_setup_command_rejects_invalid_timezone(
        self,
        _mock_validate_host,
        _mock_validate_username,
        mock_prepare_runtime_config,
        _mock_validate_samba,
        mock_print,
    ):
        config = SetupConfig(
            host="example.com",
            username="testuser",
            system_type="server_lite",
            timezone="Mars/Olympus",
        )
        mock_prepare_runtime_config.return_value = config
        args = Namespace(host="example.com", username="testuser", system_type="server_lite")

        with patch("infra_tools.SetupConfig.from_args", return_value=config), \
             patch("infra_tools.run_remote_setup") as mock_run_remote:
            result = infra_tools.run_setup_command(args)

        self.assertEqual(result, 1)
        mock_run_remote.assert_not_called()
        mock_print.assert_called_with("Error: Invalid timezone: Mars/Olympus")

    def test_run_patch_command_preserves_cached_lxc_machine_when_omitted(self):
        parser, _setup_parser, _patch_parser = infra_tools.create_infra_tools_parser()
        args = parser.parse_args(["patch", "example.com", "testuser"])
        cached = SetupConfig(
            host="example.com",
            username="testuser",
            system_type="server_web",
            machine_type="unprivileged",
            hosted_bridge="sdn-public",
        )

        with patch("infra_tools.validate_host", return_value=True), \
             patch("infra_tools.validate_username", return_value=True), \
             patch("infra_tools.load_setup_command", return_value=cached), \
             patch("infra_tools._execute_patch_config", return_value=0) as mock_execute:
            result = infra_tools.run_patch_command(args)

        self.assertEqual(result, 0)
        patched_config = mock_execute.call_args.args[0]
        self.assertEqual(patched_config.machine_type, "unprivileged")
        self.assertEqual(patched_config.hosted_bridge, "sdn-public")
        self.assertIn("vm_balloon_min", infra_tools._patch_preserve_keys(args))

    def test_run_patch_command_allows_explicit_machine_override(self):
        parser, _setup_parser, _patch_parser = infra_tools.create_infra_tools_parser()
        args = parser.parse_args(["patch", "example.com", "testuser", "--machine", "vm"])
        cached = SetupConfig(
            host="example.com",
            username="testuser",
            system_type="server_web",
            machine_type="unprivileged",
        )

        with patch("infra_tools.validate_host", return_value=True), \
             patch("infra_tools.validate_username", return_value=True), \
             patch("infra_tools.load_setup_command", return_value=cached), \
             patch("infra_tools._execute_patch_config", return_value=0) as mock_execute:
            result = infra_tools.run_patch_command(args)

        self.assertEqual(result, 0)
        patched_config = mock_execute.call_args.args[0]
        self.assertEqual(patched_config.machine_type, "vm")

    @patch("builtins.print")
    @patch("infra_tools.validate_samba_share_credentials")
    @patch("infra_tools.prepare_runtime_config")
    @patch("infra_tools.load_setup_command")
    @patch("infra_tools.validate_username", return_value=True)
    @patch("infra_tools.validate_host", return_value=True)
    def test_run_patch_command_rejects_invalid_notify_specs(
        self,
        _mock_validate_host,
        _mock_validate_username,
        mock_load_setup_command,
        mock_prepare_runtime_config,
        _mock_validate_samba,
        mock_print,
    ):
        cached = SetupConfig(host="example.com", username="testuser", system_type="server_lite")
        merged = SetupConfig(
            host="example.com",
            username="testuser",
            system_type="server_lite",
            notify_specs=[["mailbox", "bad-email"]],
        )
        mock_load_setup_command.return_value = cached
        mock_prepare_runtime_config.return_value = merged
        args = Namespace(host="example.com", username="testuser")

        with patch("infra_tools.SetupConfig.from_args", return_value=merged), \
             patch("infra_tools.merge_setup_configs", return_value=merged), \
             patch("infra_tools.run_remote_setup") as mock_run_remote:
            result = infra_tools.run_patch_command(args)

        self.assertEqual(result, 1)
        mock_run_remote.assert_not_called()
        mock_print.assert_called_with("Error: Invalid mailbox address")

    @patch("builtins.print")
    @patch("infra_tools.validate_samba_share_credentials")
    @patch("infra_tools.prepare_runtime_config")
    @patch("infra_tools.validate_username", return_value=True)
    @patch("infra_tools.validate_host", return_value=True)
    def test_run_setup_command_rejects_invalid_sync_spec(
        self,
        _mock_validate_host,
        _mock_validate_username,
        mock_prepare_runtime_config,
        _mock_validate_samba,
        mock_print,
    ):
        config = SetupConfig(
            host="example.com",
            username="testuser",
            system_type="server_lite",
            sync_specs=[["relative", "/dst", "daily"]],
        )
        mock_prepare_runtime_config.return_value = config
        args = Namespace(host="example.com", username="testuser", system_type="server_lite")

        with patch("infra_tools.SetupConfig.from_args", return_value=config), \
             patch("infra_tools.run_remote_setup") as mock_run_remote:
            result = infra_tools.run_setup_command(args)

        self.assertEqual(result, 1)
        mock_run_remote.assert_not_called()
        mock_print.assert_called_with("Error: Source path must be absolute: relative")

    @patch("builtins.print")
    @patch("infra_tools.validate_samba_share_credentials")
    @patch("infra_tools.prepare_runtime_config")
    @patch("infra_tools.validate_username", return_value=True)
    @patch("infra_tools.validate_host", return_value=True)
    def test_run_setup_command_rejects_invalid_scrub_spec(
        self,
        _mock_validate_host,
        _mock_validate_username,
        mock_prepare_runtime_config,
        _mock_validate_samba,
        mock_print,
    ):
        config = SetupConfig(
            host="example.com",
            username="testuser",
            system_type="server_lite",
            scrub_specs=[["/data", ".pardatabase", "0%", "weekly"]],
        )
        mock_prepare_runtime_config.return_value = config
        args = Namespace(host="example.com", username="testuser", system_type="server_lite")

        with patch("infra_tools.SetupConfig.from_args", return_value=config), \
             patch("infra_tools.run_remote_setup") as mock_run_remote:
            result = infra_tools.run_setup_command(args)

        self.assertEqual(result, 1)
        mock_run_remote.assert_not_called()
        mock_print.assert_called_with("Error: Redundancy percentage must be between 1 and 100: 0%")

    @patch("builtins.print")
    @patch("infra_tools.validate_samba_share_credentials")
    @patch("infra_tools.prepare_runtime_config")
    @patch("infra_tools.validate_username", return_value=True)
    @patch("infra_tools.validate_host", return_value=True)
    def test_run_setup_command_rejects_invalid_smb_mount_spec(
        self,
        _mock_validate_host,
        _mock_validate_username,
        mock_prepare_runtime_config,
        _mock_validate_samba,
        mock_print,
    ):
        config = SetupConfig(
            host="example.com",
            username="testuser",
            system_type="server_lite",
            smb_mounts=[["/mnt/share", "bad host", "user:pass", "docs", "/sub"]],
        )
        mock_prepare_runtime_config.return_value = config
        args = Namespace(host="example.com", username="testuser", system_type="server_lite")

        with patch("infra_tools.SetupConfig.from_args", return_value=config), \
             patch("infra_tools.run_remote_setup") as mock_run_remote:
            result = infra_tools.run_setup_command(args)

        self.assertEqual(result, 1)
        mock_run_remote.assert_not_called()
        mock_print.assert_called_with("Error: Invalid SMB mount host: bad host")

    @patch("builtins.print")
    @patch("infra_tools.validate_samba_share_credentials")
    @patch("infra_tools.prepare_runtime_config")
    @patch("infra_tools.load_setup_command")
    @patch("infra_tools.validate_username", return_value=True)
    @patch("infra_tools.validate_host", return_value=True)
    def test_run_patch_command_rejects_invalid_deploy_target(
        self,
        _mock_validate_host,
        _mock_validate_username,
        mock_load_setup_command,
        mock_prepare_runtime_config,
        _mock_validate_samba,
        mock_print,
    ):
        cached = SetupConfig(host="example.com", username="testuser", system_type="server_lite")
        merged = SetupConfig(
            host="example.com",
            username="testuser",
            system_type="server_lite",
            deploy_targets=["bad target"],
        )
        mock_load_setup_command.return_value = cached
        mock_prepare_runtime_config.return_value = merged
        args = Namespace(host="example.com", username="testuser")

        with patch("infra_tools.SetupConfig.from_args", return_value=merged), \
             patch("infra_tools.merge_setup_configs", return_value=merged), \
             patch("infra_tools.run_remote_setup") as mock_run_remote:
            result = infra_tools.run_patch_command(args)

        self.assertEqual(result, 1)
        mock_run_remote.assert_not_called()
        mock_print.assert_called_with("Error: Invalid deploy target host: bad target")

    @patch("builtins.print")
    @patch("infra_tools.validate_samba_share_credentials")
    @patch("infra_tools.prepare_runtime_config")
    @patch("infra_tools.load_setup_command")
    @patch("infra_tools.validate_username", return_value=True)
    @patch("infra_tools.validate_host", return_value=True)
    def test_run_patch_command_rejects_invalid_deploy_spec(
        self,
        _mock_validate_host,
        _mock_validate_username,
        mock_load_setup_command,
        mock_prepare_runtime_config,
        _mock_validate_samba,
        mock_print,
    ):
        cached = SetupConfig(host="example.com", username="testuser", system_type="server_lite")
        merged = SetupConfig(
            host="example.com",
            username="testuser",
            system_type="server_lite",
            deploy_specs=[["bad domain", "https://github.com/user/repo.git"]],
        )
        mock_load_setup_command.return_value = cached
        mock_prepare_runtime_config.return_value = merged
        args = Namespace(host="example.com", username="testuser")

        with patch("infra_tools.SetupConfig.from_args", return_value=merged), \
             patch("infra_tools.merge_setup_configs", return_value=merged), \
             patch("infra_tools.run_remote_setup") as mock_run_remote:
            result = infra_tools.run_patch_command(args)

        self.assertEqual(result, 1)
        mock_run_remote.assert_not_called()
        mock_print.assert_called_with("Error: Invalid deploy domain: bad domain")

    @patch("builtins.print")
    @patch("infra_tools.validate_samba_share_credentials")
    @patch("infra_tools.prepare_runtime_config")
    @patch("infra_tools.load_setup_command")
    @patch("infra_tools.validate_username", return_value=True)
    @patch("infra_tools.validate_host", return_value=True)
    def test_run_patch_command_rejects_invalid_sync_spec(
        self,
        _mock_validate_host,
        _mock_validate_username,
        mock_load_setup_command,
        mock_prepare_runtime_config,
        _mock_validate_samba,
        mock_print,
    ):
        cached = SetupConfig(host="example.com", username="testuser", system_type="server_lite")
        merged = SetupConfig(
            host="example.com",
            username="testuser",
            system_type="server_lite",
            sync_specs=[["relative", "/dst", "daily"]],
        )
        mock_load_setup_command.return_value = cached
        mock_prepare_runtime_config.return_value = merged
        args = Namespace(host="example.com", username="testuser")

        with patch("infra_tools.SetupConfig.from_args", return_value=merged), \
             patch("infra_tools.merge_setup_configs", return_value=merged), \
             patch("infra_tools.run_remote_setup") as mock_run_remote:
            result = infra_tools.run_patch_command(args)

        self.assertEqual(result, 1)
        mock_run_remote.assert_not_called()
        mock_print.assert_called_with("Error: Source path must be absolute: relative")

    @patch("builtins.print")
    @patch("infra_tools.validate_samba_share_credentials")
    @patch("infra_tools.prepare_runtime_config")
    @patch("infra_tools.load_setup_command")
    @patch("infra_tools.validate_username", return_value=True)
    @patch("infra_tools.validate_host", return_value=True)
    def test_run_patch_command_rejects_invalid_scrub_spec(
        self,
        _mock_validate_host,
        _mock_validate_username,
        mock_load_setup_command,
        mock_prepare_runtime_config,
        _mock_validate_samba,
        mock_print,
    ):
        cached = SetupConfig(host="example.com", username="testuser", system_type="server_lite")
        merged = SetupConfig(
            host="example.com",
            username="testuser",
            system_type="server_lite",
            scrub_specs=[["/data", ".pardatabase", "0%", "weekly"]],
        )
        mock_load_setup_command.return_value = cached
        mock_prepare_runtime_config.return_value = merged
        args = Namespace(host="example.com", username="testuser")

        with patch("infra_tools.SetupConfig.from_args", return_value=merged), \
             patch("infra_tools.merge_setup_configs", return_value=merged), \
             patch("infra_tools.run_remote_setup") as mock_run_remote:
            result = infra_tools.run_patch_command(args)

        self.assertEqual(result, 1)
        mock_run_remote.assert_not_called()
        mock_print.assert_called_with("Error: Redundancy percentage must be between 1 and 100: 0%")

    @patch("builtins.print")
    @patch("infra_tools.validate_samba_share_credentials")
    @patch("infra_tools.prepare_runtime_config")
    @patch("infra_tools.load_setup_command")
    @patch("infra_tools.validate_username", return_value=True)
    @patch("infra_tools.validate_host", return_value=True)
    def test_run_patch_command_rejects_invalid_smb_mount_spec(
        self,
        _mock_validate_host,
        _mock_validate_username,
        mock_load_setup_command,
        mock_prepare_runtime_config,
        _mock_validate_samba,
        mock_print,
    ):
        cached = SetupConfig(host="example.com", username="testuser", system_type="server_lite")
        merged = SetupConfig(
            host="example.com",
            username="testuser",
            system_type="server_lite",
            smb_mounts=[["/mnt/share", "bad host", "user:pass", "docs", "/sub"]],
        )
        mock_load_setup_command.return_value = cached
        mock_prepare_runtime_config.return_value = merged
        args = Namespace(host="example.com", username="testuser")

        with patch("infra_tools.SetupConfig.from_args", return_value=merged), \
             patch("infra_tools.merge_setup_configs", return_value=merged), \
             patch("infra_tools.run_remote_setup") as mock_run_remote:
            result = infra_tools.run_patch_command(args)

        self.assertEqual(result, 1)
        mock_run_remote.assert_not_called()
        mock_print.assert_called_with("Error: Invalid SMB mount host: bad host")

    @patch("builtins.print")
    @patch("infra_tools.validate_samba_share_credentials")
    @patch("infra_tools.prepare_runtime_config")
    @patch("infra_tools.load_setup_command")
    @patch("infra_tools.validate_username", return_value=True)
    @patch("infra_tools.validate_host", return_value=True)
    def test_run_patch_command_rejects_invalid_proxmox_node(
        self,
        _mock_validate_host,
        _mock_validate_username,
        mock_load_setup_command,
        mock_prepare_runtime_config,
        _mock_validate_samba,
        mock_print,
    ):
        cached = SetupConfig(host="example.com", username="testuser", system_type="server_lite")
        merged = SetupConfig(
            host="example.com",
            username="testuser",
            system_type="server_lite",
            hosted_node="bad host",
            container_memory="2G",
            container_storage=[["root", "auto", "10G"]],
        )
        mock_load_setup_command.return_value = cached
        mock_prepare_runtime_config.return_value = merged
        args = Namespace(host="example.com", username="testuser")

        with patch("infra_tools.SetupConfig.from_args", return_value=merged), \
             patch("infra_tools.merge_setup_configs", return_value=merged), \
             patch("infra_tools.run_remote_setup") as mock_run_remote:
            result = infra_tools.run_patch_command(args)

        self.assertEqual(result, 1)
        mock_run_remote.assert_not_called()
        mock_print.assert_called_with("Error: Invalid Proxmox node host: bad host")


class TestSetupUserPasswordless(unittest.TestCase):
    @patch("builtins.print")
    @patch("common.common_steps.set_user_password")
    @patch("common.common_steps.run")
    def test_new_user_without_password_skips_password_generation(self, mock_run, mock_set_password, mock_print):
        mock_run.side_effect = lambda *args, **kwargs: SimpleNamespace(returncode=1)
        config = SetupConfig(host="host", username="user", system_type="server_lite")

        common_steps.setup_user(config)

        mock_set_password.assert_not_called()
        commands = [call_args.args[0] for call_args in mock_run.call_args_list]
        self.assertTrue(any(command.startswith("useradd -m -s /bin/bash") for command in commands))
        mock_print.assert_any_call("  No password configured; relying on SSH key authentication")


if __name__ == "__main__":
    unittest.main()
