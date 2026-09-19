"""Tests for the top-level basaltwater interactive shell."""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lib.config import SetupConfig
from lib.proxmox_hosts import ProxmoxHost
from lib.interactive_shell import InteractiveShell, run_interactive_shell


def _make_shell(commands: list[str]) -> tuple[InteractiveShell, list[str]]:
    inputs = iter(commands)
    output: list[str] = []

    def _input(_prompt: str) -> str:
        try:
            return next(inputs)
        except StopIteration:
            raise EOFError()

    shell = InteractiveShell(input_func=_input, output_func=output.append)
    return shell, output


class TestInteractiveShellDispatch(unittest.TestCase):
    def test_help_prints_command_list(self):
        shell, output = _make_shell(["help", "exit"])
        self.assertEqual(shell.run(), 0)
        joined = "\n".join(output)
        self.assertIn("Available commands", joined)
        self.assertIn("new/setup", joined)
        self.assertIn("proxmox", joined)
        self.assertIn("recall", joined)

    def test_unknown_command_reports_error(self):
        shell, output = _make_shell(["bogus", "exit"])
        self.assertEqual(shell.run(), 0)
        self.assertTrue(any("Unknown command 'bogus'" in line for line in output))

    def test_exit_returns_zero(self):
        shell, _ = _make_shell(["exit"])
        self.assertEqual(shell.run(), 0)

    def test_eof_returns_zero(self):
        shell, _ = _make_shell([])
        self.assertEqual(shell.run(), 0)

    def test_blank_lines_skip(self):
        shell, output = _make_shell(["", "   ", "help", "exit"])
        self.assertEqual(shell.run(), 0)
        self.assertTrue(any("Available commands" in line for line in output))

    @patch("basaltwater.list_configurations")
    def test_list_invokes_list_configurations(self, mock_list):
        shell, _ = _make_shell(["list prod", "exit"])
        shell.run()
        mock_list.assert_called_once_with("prod", json_output=False)

    @patch("basaltwater.list_configurations")
    def test_list_without_pattern_passes_none(self, mock_list):
        shell, _ = _make_shell(["ls", "exit"])
        shell.run()
        mock_list.assert_called_once_with(None, json_output=False)

    @patch("basaltwater.list_configurations")
    def test_list_json_flag_passes_true(self, mock_list):
        shell, _ = _make_shell(["list --json", "exit"])
        shell.run()
        mock_list.assert_called_once_with(None, json_output=True)

    @patch("basaltwater.show_info")
    def test_info_invokes_show_info(self, mock_show):
        shell, _ = _make_shell(["info web", "exit"])
        shell.run()
        mock_show.assert_called_once_with("web", compact=False)

    @patch("basaltwater.show_info")
    def test_info_compact_flag(self, mock_show):
        shell, _ = _make_shell(["info --compact", "exit"])
        shell.run()
        mock_show.assert_called_once_with(None, compact=True)

    @patch("basaltwater.show_command")
    def test_cmd_invokes_show_command(self, mock_show):
        shell, _ = _make_shell(["cmd web", "exit"])
        shell.run()
        mock_show.assert_called_once_with("web")

    @patch("basaltwater.deploy_configurations")
    def test_deploy_requires_pattern(self, mock_deploy):
        shell, output = _make_shell(["deploy", "exit"])
        shell.run()
        mock_deploy.assert_not_called()
        self.assertTrue(any("Usage: deploy" in line for line in output))

    @patch("basaltwater.deploy_configurations")
    def test_deploy_passes_yes_flag(self, mock_deploy):
        shell, _ = _make_shell(["deploy prod --yes", "exit"])
        shell.run()
        mock_deploy.assert_called_once_with("prod", True, False)

    @patch("basaltwater.deploy_configurations")
    def test_deploy_passes_latest_flag(self, mock_deploy):
        shell, _ = _make_shell(["deploy prod --yes --deploy-latest", "exit"])
        shell.run()
        mock_deploy.assert_called_once_with("prod", True, True)

    @patch("basaltwater.remove_configurations")
    def test_remove_passes_short_yes_flag(self, mock_remove):
        shell, _ = _make_shell(["rm web -y", "exit"])
        shell.run()
        mock_remove.assert_called_once_with("web", True)

    @patch("lib.proxmox_shell.run_proxmox_shell")
    def test_proxmox_drops_into_proxmox_shell(self, mock_run_shell):
        mock_run_shell.return_value = 0
        shell, _ = _make_shell(["proxmox", "exit"])
        shell.run()
        mock_run_shell.assert_called_once_with(None)

    @patch("lib.proxmox_network.suggest_free_ips", return_value=[])
    @patch("lib.cache.save_setup_command")
    @patch("lib.proxmox_hosts.load_proxmox_hosts")
    @patch("lib.cache.load_all_setup_commands")
    def test_new_guides_hosted_workstation_setup(
        self,
        mock_load_templates,
        mock_load_proxmox_hosts,
        mock_save_setup,
        _mock_suggest_free_ips,
    ):
        mock_load_templates.return_value = [
            SetupConfig(
                host="10.0.0.20",
                username="devuser",
                system_type="workstation_dev",
                machine_type="vm",
                friendly_name="old-dev",
                tags=["dev"],
                enable_rdp=True,
                desktop="i3",
                install_node=True,
                install_go=True,
                install_python=True,
                install_data_analysis_tools=True,
                hosted_node="pve1",
                container_memory="8G",
                container_cores=4,
                container_storage=[["root", "40G"]],
                container_base="debian",
            )
        ]
        mock_load_proxmox_hosts.return_value = [
            ProxmoxHost(name="pve1", address="10.0.0.10", user="root")
        ]
        shell, _ = _make_shell(
            [
                "new",
                "1",          # template
                "1",          # proxmox host
                "dev-02",     # name
                "10.0.0.51",  # target host
                "",           # machine type default (vm)
                "",           # system type default (workstation_dev)
                "",           # username default
                "",           # tags default
                "",           # desktop default
                "",           # rdp default
                "n",          # skip the combined dev-runtime bundle
                "",           # data-analysis tools default
                "",           # memory default
                "",           # cores default
                "",           # disk default
                "",           # base os default
                "exit",
            ]
        )

        shell.run()

        saved_config = mock_save_setup.call_args.args[0]
        self.assertEqual(saved_config.friendly_name, "dev-02")
        self.assertEqual(saved_config.host, "10.0.0.51")
        self.assertEqual(saved_config.machine_type, "vm")
        self.assertEqual(saved_config.system_type, "workstation_dev")
        self.assertEqual(saved_config.hosted_node, "pve1")
        self.assertEqual(saved_config.container_storage, [["root", "40G"]])
        self.assertFalse(saved_config.install_node)
        self.assertFalse(saved_config.install_go)
        self.assertTrue(saved_config.install_python)
        self.assertTrue(saved_config.install_data_analysis_tools)

    @patch("lib.cache.save_setup_command")
    @patch("lib.proxmox_hosts.load_proxmox_hosts")
    @patch("lib.cache.load_all_setup_commands")
    def test_new_can_skip_proxmox_host_for_server_setup(
        self,
        mock_load_templates,
        mock_load_proxmox_hosts,
        mock_save_setup,
    ):
        mock_load_templates.return_value = []
        mock_load_proxmox_hosts.return_value = [
            ProxmoxHost(name="pve1", address="10.0.0.10", user="root")
        ]
        shell, _ = _make_shell(
            [
                "new",
                "",                  # no proxmox host
                "web-01",            # name
                "10.0.0.80",         # target host
                "1",                 # hardware
                "3",                 # server_web
                "admin",             # username
                "web,prod",          # tags
                "",                  # node default yes
                "y",                 # enable ssl
                "admin@example.com", # ssl email
                "exit",
            ]
        )

        shell.run()

        saved_config = mock_save_setup.call_args.args[0]
        self.assertEqual(saved_config.friendly_name, "web-01")
        self.assertEqual(saved_config.machine_type, "hardware")
        self.assertEqual(saved_config.system_type, "server_web")
        self.assertIsNone(saved_config.hosted_node)
        self.assertTrue(saved_config.install_node)
        self.assertTrue(saved_config.enable_ssl)
        self.assertEqual(saved_config.ssl_email, "admin@example.com")
        self.assertEqual(saved_config.tags, ["web", "prod"])

    def test_recall_rejects_invalid_host(self):
        shell, output = _make_shell(["recall '%bad host'", "exit"])
        shell.run()
        self.assertTrue(any("Invalid IP address or hostname" in line for line in output))

    @patch("lib.reconstruct.run_reconstruct_command", return_value=0)
    def test_reconstruct_passes_compact_flag(self, mock_run):
        shell, _ = _make_shell(["reconstruct --compact", "exit"])
        shell.run()
        mock_run.assert_called_once_with(True)

    def test_workspace_without_arg_prints_default(self):
        shell, output = _make_shell(["workspace", "exit"])
        shell.run()
        self.assertTrue(any("Workspace:" in line for line in output))


class TestRunInteractiveShellEntry(unittest.TestCase):
    @patch("lib.interactive_shell.InteractiveShell.run", return_value=0)
    def test_run_interactive_shell_invokes_shell(self, mock_run):
        self.assertEqual(run_interactive_shell(workspace="/tmp/ws"), 0)
        mock_run.assert_called_once()


class TestInteractiveShellInitFile(unittest.TestCase):
    def test_init_file_commands_run_at_startup(self):
        import tempfile
        from pathlib import Path
        outputs: list[str] = []
        shell = InteractiveShell(
            input_func=lambda _p: (_ for _ in ()).throw(EOFError()),
            output_func=outputs.append,
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".rc", delete=False) as f:
            f.write("help\n")
            rc_path = Path(f.name)
        try:
            shell._run_init_file(rc_path)
        finally:
            rc_path.unlink(missing_ok=True)
        self.assertTrue(any("Available commands" in line for line in outputs))

    def test_init_file_skips_comments_and_blank_lines(self):
        import tempfile
        from pathlib import Path
        outputs: list[str] = []
        shell = InteractiveShell(
            input_func=lambda _p: (_ for _ in ()).throw(EOFError()),
            output_func=outputs.append,
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".rc", delete=False) as f:
            f.write("# this is a comment\n\n   \n")
            rc_path = Path(f.name)
        try:
            shell._run_init_file(rc_path)
        finally:
            rc_path.unlink(missing_ok=True)
        self.assertEqual(outputs, [])

    def test_init_file_missing_is_silent(self):
        from pathlib import Path
        outputs: list[str] = []
        shell = InteractiveShell(
            input_func=lambda _p: (_ for _ in ()).throw(EOFError()),
            output_func=outputs.append,
        )
        shell._run_init_file(Path("/nonexistent/no/such/file.rc"))
        self.assertEqual(outputs, [])

    def test_init_file_errors_reported_gracefully(self):
        import tempfile
        from pathlib import Path
        outputs: list[str] = []
        shell = InteractiveShell(
            input_func=lambda _p: (_ for _ in ()).throw(EOFError()),
            output_func=outputs.append,
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".rc", delete=False) as f:
            f.write("unknown_command_xyz\n")
            rc_path = Path(f.name)
        try:
            shell._run_init_file(rc_path)
        finally:
            rc_path.unlink(missing_ok=True)
        self.assertTrue(any("Init file error" in line for line in outputs))


class TestWorkspacePrompt(unittest.TestCase):
    def test_default_prompt(self) -> None:
        shell = InteractiveShell(
            input_func=lambda _p: (_ for _ in ()).throw(EOFError()),
            output_func=lambda _: None,
        )
        self.assertEqual(shell._make_prompt(), "basaltwater> ")

    def test_workspace_prompt_shows_basename(self) -> None:
        shell = InteractiveShell(
            input_func=lambda _p: (_ for _ in ()).throw(EOFError()),
            output_func=lambda _: None,
        )
        shell.state.workspace = "/home/user/projects/myproject"
        self.assertIn("myproject", shell._make_prompt())
        self.assertIn("[", shell._make_prompt())

    def test_workspace_prompt_after_set(self) -> None:
        shell, _ = _make_shell([])
        shell.state.workspace = "/workspaces/prod"
        self.assertEqual(shell._make_prompt(), "basaltwater[prod]> ")


class TestRenameCommand(unittest.TestCase):
    def test_rename_updates_friendly_name(self) -> None:
        config = SetupConfig(
            host="10.0.0.5",
            username="admin",
            system_type="server_lite",
            friendly_name="old-name",
        )
        shell, output = _make_shell([])
        with (
            patch("lib.cache.load_setup_command", return_value=config),
            patch("lib.cache.save_setup_command") as mock_save,
        ):
            shell._cmd_rename(["old-name", "new-name"])
        mock_save.assert_called_once()
        self.assertEqual(config.friendly_name, "new-name")
        self.assertTrue(any("new-name" in line for line in output))

    def test_rename_requires_two_args(self) -> None:
        shell, _ = _make_shell([])
        with self.assertRaises(ValueError):
            shell._cmd_rename(["only-one"])

    def test_rename_missing_config_raises(self) -> None:
        shell, _ = _make_shell([])
        with patch("lib.cache.load_setup_command", return_value=None):
            with self.assertRaises(ValueError):
                shell._cmd_rename(["no-such", "newname"])


class TestCloneCommand(unittest.TestCase):
    def test_clone_creates_new_host_entry(self) -> None:
        config = SetupConfig(
            host="10.0.0.5",
            username="admin",
            system_type="server_lite",
            friendly_name="original",
        )
        shell, output = _make_shell([])
        with (
            patch("lib.cache.load_setup_command", return_value=config),
            patch("lib.cache.save_setup_command") as mock_save,
        ):
            shell._cmd_clone(["original", "10.0.0.6"])
        mock_save.assert_called_once()
        self.assertEqual(config.host, "10.0.0.6")

    def test_clone_with_new_name(self) -> None:
        config = SetupConfig(
            host="10.0.0.5",
            username="admin",
            system_type="server_lite",
        )
        shell, _ = _make_shell([])
        with (
            patch("lib.cache.load_setup_command", return_value=config),
            patch("lib.cache.save_setup_command"),
        ):
            shell._cmd_clone(["original", "10.0.0.7", "replica"])
        self.assertEqual(config.friendly_name, "replica")

    def test_clone_invalid_host_raises(self) -> None:
        config = SetupConfig(host="10.0.0.5", username="admin", system_type="server_lite")
        shell, _ = _make_shell([])
        with patch("lib.cache.load_setup_command", return_value=config):
            with self.assertRaises(ValueError):
                shell._cmd_clone(["src", "not a valid host!!"])


class TestTagUntag(unittest.TestCase):
    def _config(self, tags=None) -> SetupConfig:
        return SetupConfig(
            host="10.0.0.5",
            username="admin",
            system_type="server_lite",
            tags=list(tags) if tags else None,
        )

    def test_tag_adds_tags(self) -> None:
        config = self._config()
        shell, output = _make_shell([])
        with (
            patch("lib.cache.load_setup_command", return_value=config),
            patch("lib.cache.save_setup_command"),
        ):
            shell._cmd_tag(["myhost", "prod", "web"])
        self.assertIn("prod", config.tags or [])
        self.assertIn("web", config.tags or [])

    def test_tag_deduplicates(self) -> None:
        config = self._config(tags=["prod"])
        shell, _ = _make_shell([])
        with (
            patch("lib.cache.load_setup_command", return_value=config),
            patch("lib.cache.save_setup_command"),
        ):
            shell._cmd_tag(["myhost", "prod", "staging"])
        self.assertEqual(config.tags, ["prod", "staging"])

    def test_untag_removes_tags(self) -> None:
        config = self._config(tags=["prod", "web", "legacy"])
        shell, output = _make_shell([])
        with (
            patch("lib.cache.load_setup_command", return_value=config),
            patch("lib.cache.save_setup_command"),
        ):
            shell._cmd_untag(["myhost", "legacy", "web"])
        self.assertEqual(config.tags, ["prod"])

    def test_untag_missing_config_raises(self) -> None:
        shell, _ = _make_shell([])
        with patch("lib.cache.load_setup_command", return_value=None):
            with self.assertRaises(ValueError):
                shell._cmd_untag(["no-such", "tag"])


if __name__ == "__main__":
    unittest.main()
