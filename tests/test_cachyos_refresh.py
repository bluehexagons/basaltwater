"""Local refresh persistence and upgrade handoff; no real host changes."""

from __future__ import annotations

import argparse
from contextlib import ExitStack
from dataclasses import replace
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import basaltwater
import remote_setup
from common import cachyos_steps
from lib import cachyos_refresh as refresh
from lib.cachyos import cachyos_config_from_args
from lib.channel_manager import ChannelError
from lib.plugin_registry import get_system_type_definition
from lib.system_types import get_steps_for_system_type


class CachyOSRefreshTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.home = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        self.account = SimpleNamespace(pw_dir=str(self.home), pw_name="human")
        self.real_account = refresh._account
        stack.enter_context(patch.object(refresh, "_account", return_value=self.account))
        stack.enter_context(patch.object(refresh, "is_cachyos", return_value=True))
        stack.enter_context(patch("lib.refresh.is_cachyos", return_value=True))
        stack.enter_context(patch.object(refresh, "is_dry_run", return_value=False))
        self.preflight = stack.enter_context(patch.object(refresh, "preflight_cachyos"))
        self.repository = self.home / "installation with spaces"
        stack.enter_context(patch.object(refresh, "managed_repository_path", return_value=str(self.repository)))
        self.upgrade = stack.enter_context(patch.object(refresh, "upgrade_channel", return_value={
            "commit": "abcdef0123456789", "channel": "dev", "updated": True,
        }))
        self.execute = stack.enter_context(patch.object(refresh.os, "execv"))
        stack.enter_context(patch("lib.cachyos_doctor.collect_cachyos_doctor", return_value={"capabilities": []}))
        stack.enter_context(patch("lib.cachyos_health.source_metadata", return_value={"commit": "abc123", "channel": "dev"}))
        self.parser, _, _ = basaltwater.create_basaltwater_parser()
        self.record = self.home / ".local/state/basaltwater/cachyos/last-setup.json"

    def config(self, *options):
        return cachyos_config_from_args(self.parser.parse_args([
            "setup", "agent_cachyos", "localhost", "human", *options,
        ]))

    def run_refresh(self, *, dry_run=False):
        return refresh.run_refresh_command(argparse.Namespace(dry_run=dry_run))

    def test_desktop_selection_roundtrips_project_paths_tools_and_exclusions(self):
        original = self.config(
            "--t3code-desktop", "--no-agent-tool", "gh", "--node", "--python", "--git-lfs",
            "--agent-workspace", str(self.home / "projects 'quoted' space"),
            "--repo", "https://github.com/example/project.git", "--gaming", "--sysadmin-tools",
        )
        refresh.save_successful_setup(original)
        arguments, restored = refresh.load_saved_setup()
        self.assertEqual(restored.selected_agent_tools(), ["codex"])
        for field in ("t3code_desktop", "agent_workspace", "agent_repos", "install_node",
                      "install_python", "install_git_lfs", "install_gaming", "install_sysadmin_tools"):
            self.assertEqual(getattr(restored, field), getattr(original, field), field)
        self.assertEqual(self.record.stat().st_mode & 0o777, 0o600)
        self.assertNotIn("password", self.record.read_text())
        self.assertIn("--no-agent-tool", arguments)

    def test_web_selection_preserves_host_port_and_replaces_desktop_selection(self):
        refresh.save_successful_setup(self.config("--t3code-desktop"))
        refresh.save_successful_setup(self.config(
            "--web-interface", "t3code", "--web-interface-host", "192.168.1.50",
            "--web-interface-port", "4773", "--agent-tool", "opencode", "--no-agent-tool", "codex",
        ))
        _, restored = refresh.load_saved_setup()
        self.assertFalse(restored.t3code_desktop)
        self.assertEqual(restored.web_interfaces, ["t3code"])
        self.assertEqual(restored.web_interface_host, "192.168.1.50")
        self.assertEqual(restored.web_interface_port, 4773)
        self.assertEqual(restored.selected_agent_tools(), ["gh", "opencode"])

    def test_no_selected_agents_stays_empty(self):
        refresh.save_successful_setup(self.config("--no-agent-tool", "gh,codex"))
        self.assertEqual(refresh.load_saved_setup()[1].selected_agent_tools(), [])

    def test_receipt_is_private_and_firewall_selection_replays(self):
        refresh.save_successful_setup(self.config("--t3code-desktop", "--lan-access", "--access-source", "10.2.3.4"))
        _, restored = refresh.load_saved_setup()
        self.assertTrue(restored.lan_access)
        self.assertEqual(restored.access_sources, ["10.2.3.4"])
        receipt = self.record.with_name("last-report.json")
        data = json.loads(receipt.read_text())
        self.assertEqual(receipt.stat().st_mode & 0o777, 0o600)
        self.assertEqual(data["source"]["channel"], "dev")
        self.assertIn("completed_at", data)
        self.assertEqual(data["observations"], [])

    def test_saved_agents_survive_changed_profile_defaults(self):
        for options, expected in (
            ((), ["gh", "codex"]),
            (("--no-agent-tool", "gh,codex"), []),
            (("--agent-tool", "claude", "--no-agent-tool", "codex"), ["gh", "claude"]),
        ):
            with self.subTest(options=options):
                refresh.save_successful_setup(self.config(*options))
                definition = get_system_type_definition("agent_cachyos")
                for defaults in (("gh", "codex", "claude", "opencode"), ()):
                    with patch("lib.config.get_system_type_definition", return_value=replace(
                        definition, default_agent_tools=defaults,
                    )):
                        self.assertEqual(refresh.load_saved_setup()[1].selected_agent_tools(), expected)

    def test_all_optional_tool_flags_roundtrip(self):
        flags = ("--node", "--python", "--go", "--git-lfs", "--av-tools", "--gl-tools",
                 "--godot", "--sunshine", "--moonlight", "--gaming", "--obs", "--blender",
                 "--kdenlive", "--krita", "--inkscape", "--scribus", "--audacity", "--ardour",
                 "--lmms", "--freecad", "--kicad", "--shotcut", "--gimp", "--remmina", "--sysadmin-tools")
        original = self.config(*flags, "--machine", "hardware")
        refresh.save_successful_setup(original)
        _, restored = refresh.load_saved_setup()
        for field in vars(original):
            if field.startswith("install_") or field == "machine_type":
                self.assertEqual(getattr(original, field), getattr(restored, field), field)

    def test_refresh_upgrades_then_executes_updated_entry_with_exact_arguments(self):
        refresh.save_successful_setup(self.config("--t3code-desktop"))
        arguments, _ = refresh.load_saved_setup()
        events = []
        self.upgrade.side_effect = lambda repo: events.append("upgrade") or {
            "commit": "abcdef", "channel": "dev", "updated": False,
        }
        self.execute.side_effect = lambda *args: events.append("exec")
        self.assertEqual(self.run_refresh(), 0)
        self.assertEqual(events, ["upgrade", "exec"])
        self.execute.assert_called_once_with(refresh.sys.executable, [
            refresh.sys.executable, str(self.repository / "basaltwater.py"), *arguments,
        ])
        self.preflight.assert_called_once()

    def test_upgrade_failure_never_runs_setup_or_replaces_record(self):
        refresh.save_successful_setup(self.config("--t3code-desktop"))
        before = self.record.read_bytes()
        self.upgrade.side_effect = ChannelError("dirty checkout")
        self.assertEqual(self.run_refresh(), 1)
        self.execute.assert_not_called()
        self.assertEqual(self.record.read_bytes(), before)

    def test_preflight_failure_stops_before_source_upgrade(self):
        refresh.save_successful_setup(self.config("--t3code-desktop"))
        self.preflight.side_effect = ValueError("wrong session")
        self.assertEqual(self.run_refresh(), 1)
        self.upgrade.assert_not_called()
        self.execute.assert_not_called()

    def test_missing_record_fails_before_upgrade(self):
        with patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(self.run_refresh(), 1)
        self.assertIn("Run your usual", output.getvalue())
        self.upgrade.assert_not_called()
        self.execute.assert_not_called()
        self.assertFalse(self.record.parent.exists())

    def test_dry_run_does_not_probe_upgrade_exec_or_rewrite(self):
        refresh.save_successful_setup(self.config("--t3code-desktop", "--node"))
        before = self.record.read_bytes()
        with patch.object(refresh, "get_channel_info", return_value={"channel": "dev"}), \
                patch("common.cachyos_t3.run") as command, \
                patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(self.run_refresh(dry_run=True), 0)
        self.assertIn("--t3code-desktop", output.getvalue())
        self.assertIn("Installing missing workstation packages", output.getvalue())
        command.assert_not_called()
        self.upgrade.assert_not_called()
        self.execute.assert_not_called()
        self.preflight.assert_not_called()
        self.assertEqual(self.record.read_bytes(), before)

    def test_failed_setup_and_preview_preserve_previous_success(self):
        refresh.save_successful_setup(self.config("--t3code-desktop"))
        before = self.record.read_bytes()
        steps = get_steps_for_system_type(self.config("--node"))
        self.assertIs(steps[-1][1], cachyos_steps.save_cachyos_setup)
        def fail(_config):
            raise RuntimeError("fixture failure")
        with patch("lib.cachyos.preflight_cachyos"), \
                patch.object(remote_setup, "get_steps_for_system_type", return_value=[("fail", fail), steps[-1]]):
            with self.assertRaisesRegex(RuntimeError, "fixture failure"):
                remote_setup.run_cachyos_setup(self.config("--node"))
            remote_setup.run_cachyos_setup(self.config("--node", "--dry-run"))
        refresh.save_successful_setup(self.config("--node", "--dry-run"))
        self.assertEqual(self.record.read_bytes(), before)

    def test_successful_runner_records_selection(self):
        with patch("lib.cachyos.preflight_cachyos"), \
                patch.object(remote_setup, "get_steps_for_system_type", return_value=[
                    ("save", cachyos_steps.save_cachyos_setup),
                ]):
            self.assertEqual(remote_setup.run_cachyos_setup(self.config("--t3code-desktop")), 0)
        self.assertTrue(refresh.load_saved_setup()[1].t3code_desktop)

    def test_invalid_records_fail_before_upgrade(self):
        refresh.save_successful_setup(self.config())
        valid = json.loads(self.record.read_text())
        cases = [
            {**valid, "schema_version": True}, {**valid, "extra": "unexpected"},
            {**valid, "arguments": "setup agent_cachyos localhost"},
        ]
        for arguments in (
            ["setup", "server_lite", "localhost"],
            ["setup", "agent_cachyos", "remote.example", "human"],
            ["setup", "agent_cachyos", "localhost", "other"],
            ["setup", "agent_cachyos", "localhost", "human", "--dry-run"],
            ["setup", "agent_cachyos", "localhost", "human", "--steps", "arbitrary"],
            ["setup", "agent_cachyos", "localhost", "human", "--unknown"],
        ):
            cases.append({**valid, "arguments": arguments})
        for value in cases:
            with self.subTest(value=value):
                self.record.write_text(json.dumps(value))
                self.assertEqual(self.run_refresh(), 1)
        self.record.write_text("{")
        self.assertEqual(self.run_refresh(), 1)
        self.upgrade.assert_not_called()
        self.execute.assert_not_called()

    def test_symlinks_and_public_record_are_rejected(self):
        refresh.save_successful_setup(self.config())
        self.record.chmod(0o644)
        self.assertEqual(self.run_refresh(), 1)
        self.record.unlink()
        target = self.home / "personal.json"
        target.write_text("keep")
        self.record.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "private regular file"):
            refresh.save_successful_setup(self.config())
        self.assertEqual(target.read_text(), "keep")
        self.upgrade.assert_not_called()

    def test_other_distro_is_rejected_before_state_or_upgrade(self):
        with patch.object(refresh, "is_cachyos", return_value=False), \
                patch.object(refresh, "load_saved_setup") as load:
            self.assertEqual(self.run_refresh(), 1)
        load.assert_not_called()
        self.upgrade.assert_not_called()

    def test_account_guard_refuses_root(self):
        # Call the real function while the fixture's lookup remains mocked.
        with patch.object(refresh.os, "geteuid", return_value=0):
            with self.assertRaisesRegex(ValueError, "without sudo"):
                self.real_account()

    def test_cli_dispatch(self):
        with patch.object(refresh, "run_refresh_command", return_value=7) as run, \
                patch("sys.argv", ["basaltw", "refresh", "--dry-run"]):
            self.assertEqual(basaltwater.main(), 7)
        self.assertTrue(run.call_args.args[0].dry_run)


if __name__ == "__main__":
    unittest.main()
