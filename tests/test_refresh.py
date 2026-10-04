"""Debian refresh uses successful local state and a fresh upgraded runner."""

from __future__ import annotations

import argparse
from contextlib import ExitStack, nullcontext
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from lib import refresh
from lib.config import AGENT_TOOLS, SetupConfig
from lib.channel_manager import ChannelError
from lib.plugin_registry import get_system_type_definition


class DebianRefreshTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        self.state = self.root / "setup.json"
        self.state.write_text("{}")
        self.state.chmod(0o600)
        stack.enter_context(patch.object(refresh, "SETUP_CONFIG_FILE", str(self.state)))
        stack.enter_context(patch.object(refresh, "is_cachyos", return_value=False))
        stack.enter_context(patch.object(refresh, "read_os_release", return_value={"ID": "debian"}))
        self.uid = stack.enter_context(patch.object(refresh.os, "geteuid", return_value=0))
        self.config = SetupConfig(host="localhost", username="human", system_type="server_lite",
                                  machine_type="hardware", install_node=True)
        self.saved = stack.enter_context(patch.object(refresh, "_debian_setup", return_value=self.config))
        stack.enter_context(patch.object(refresh, "managed_repository_path", return_value=str(self.root)))
        stack.enter_context(patch.object(refresh, "get_channel_info", return_value={"channel": "dev"}))
        stack.enter_context(patch.object(refresh, "maintenance_lock", side_effect=lambda: nullcontext(True)))
        self.upgrade = stack.enter_context(patch.object(refresh, "upgrade_channel", return_value={
            "commit": "abcdef0123456789", "channel": "dev", "updated": True,
        }))
        self.run = stack.enter_context(patch.object(refresh, "run", return_value=subprocess.CompletedProcess([], 0)))

    def invoke(self, *, dry_run=False):
        return refresh.run_refresh_command(argparse.Namespace(dry_run=dry_run))

    def test_upgrade_precedes_fresh_target_setup_and_private_arguments_are_removed(self):
        events = []
        self.upgrade.side_effect = lambda repo: events.append("upgrade") or {
            "commit": "abcdef", "channel": "dev", "updated": False,
        }
        temporary_paths = []
        def execute(command, **kwargs):
            events.append("setup")
            self.assertEqual(command[:3], [refresh.sys.executable, str(self.root / "remote_setup.py"), "--args-file"])
            path = Path(command[3])
            temporary_paths.append(path)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            arguments = json.loads(path.read_text())
            self.assertIn("--node", arguments)
            self.assertEqual(arguments[arguments.index("--system-type") + 1], "server_lite")
            self.assertTrue(kwargs["interactive"])
            self.assertFalse(kwargs["check"])
            self.assertIsNone(kwargs["timeout"])
            return subprocess.CompletedProcess(command, 19)
        self.run.side_effect = execute
        self.assertEqual(self.invoke(), 19)
        self.assertEqual(events, ["upgrade", "setup"])
        self.assertFalse(temporary_paths[0].exists())

    def test_source_failure_never_runs_setup(self):
        self.upgrade.side_effect = ChannelError("dirty worktree")
        self.assertEqual(self.invoke(), 1)
        self.run.assert_not_called()

    def test_dry_run_never_changes_source_or_invokes_target(self):
        with patch.object(refresh, "get_channel_info", return_value={"channel": "stable"}), \
                patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(self.invoke(dry_run=True), 0)
        self.assertIn("server_lite", output.getvalue())
        self.assertIn("stable", output.getvalue())
        self.upgrade.assert_not_called()
        self.run.assert_not_called()

    def test_standard_debian_profiles_replay_through_target_parser(self):
        for profile in ("control_plane", "workstation_dev", "agent_vm", "agent_workstation",
                        "agent_code_vm", "server_web", "server_proxmox"):
            with self.subTest(profile=profile):
                config = SetupConfig(host="localhost", username="human", system_type=profile,
                                     machine_type="hardware",
                                     git_access="read-write" if profile == "agent_code_vm" else "none")
                if config.enable_rdp:
                    config.rdp_existing_password = True
                self.saved.return_value = config
                with patch.object(refresh, "get_channel_info", return_value={"channel": "dev"}):
                    self.assertEqual(self.invoke(dry_run=True), 0)
        self.upgrade.assert_not_called()
        self.run.assert_not_called()

    def test_setup_snapshot_dry_run_requires_no_channel_setup_or_download(self):
        with patch.object(refresh, "get_channel_info", return_value={
            "channel": "setup-snapshot", "installation_type": "setup-snapshot",
        }):
            self.assertEqual(self.invoke(dry_run=True), 0)
        self.run.assert_not_called()
        self.upgrade.assert_not_called()

    def test_snapshot_is_upgraded_before_replaying_setup(self):
        with patch.object(refresh, "get_channel_info", return_value={
            "installation_type": "setup-snapshot", "branch": "main",
        }), patch.object(refresh, "_upgrade_snapshot", return_value={
            "commit": "abcdef", "channel": "dev",
        }) as snapshot:
            self.assertEqual(self.invoke(), 0)
        snapshot.assert_called_once_with(str(self.root), "dev")
        self.upgrade.assert_not_called()
        self.run.assert_called_once()

    def test_busy_setup_blocks_source_mutation_and_target_runner(self):
        with patch.object(refresh, "maintenance_lock", side_effect=lambda: nullcontext(False)):
            self.assertEqual(self.invoke(), 1)
        self.upgrade.assert_not_called()
        self.run.assert_not_called()

    def test_snapshot_branch_selection_is_validated(self):
        for branch, expected in ((None, "dev"), ("main", "dev"), ("feature/test", "branch-feature/test")):
            self.assertEqual(refresh._refresh_channel({"installation_type": "setup-snapshot", "branch": branch}), expected)
        with self.assertRaises(ValueError):
            refresh._refresh_channel({"installation_type": "setup-snapshot", "branch": "--bad;command"})

    def test_non_root_and_missing_state_stop_before_upgrade(self):
        self.uid.return_value = 1000
        self.assertEqual(self.invoke(), 1)
        self.uid.return_value = 0
        self.state.unlink()
        self.assertEqual(self.invoke(), 1)
        self.saved.assert_not_called()
        self.upgrade.assert_not_called()
        self.run.assert_not_called()

    def test_unsupported_distribution_is_not_treated_as_debian(self):
        with patch.object(refresh, "read_os_release", return_value={"ID": "fedora"}):
            self.assertEqual(self.invoke(), 1)
        self.saved.assert_not_called()
        self.upgrade.assert_not_called()


class SnapshotActivationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.source = self.root / "basaltwater"
        self.source.mkdir()
        (self.source / "basaltwater.py").write_text("old source")
        (self.source / refresh.INSTALLATION_METADATA_FILENAME).write_text("{}")
        self.state = self.root / "durable-state"
        self.state.mkdir()
        (self.state / "setup.json").write_text("saved options")
        (self.source / "state").symlink_to(self.state, target_is_directory=True)
        (self.source / "deployments").mkdir()
        (self.source / "deployments/app").write_text("application data")
        self.enterContext(patch("lib.privilege_policy.protected_path"))
        self.run = self.enterContext(patch.object(refresh, "run", side_effect=self.clone))
        self.enterContext(patch.object(refresh, "switch_channel", return_value={"commit": "new", "channel": "dev"}))

    def clone(self, command, **kwargs):
        staged = Path(command[-1])
        (staged / ".git/info").mkdir(parents=True)
        (staged / "basaltwater.py").write_text("new source")
        return subprocess.CompletedProcess(command, 0)

    def test_activation_preserves_state_link_deployments_and_previous_source(self):
        refresh._upgrade_snapshot(str(self.source), "dev")
        self.assertEqual((self.source / "basaltwater.py").read_text(), "new source")
        self.assertTrue((self.source / "state").is_symlink())
        self.assertEqual((self.source / "state/setup.json").read_text(), "saved options")
        self.assertEqual((self.source / "deployments/app").read_text(), "application data")
        backup, = self.root.glob(".basaltwater-before-refresh-*")
        self.assertEqual((backup / "basaltwater.py").read_text(), "old source")
        self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o700)
        self.assertIn("/deployments/", (self.source / ".git/info/exclude").read_text())

    def test_download_failure_preserves_installed_snapshot(self):
        self.run.side_effect = None
        self.run.return_value = subprocess.CompletedProcess([], 1)
        with self.assertRaisesRegex(RuntimeError, "unchanged"):
            refresh._upgrade_snapshot(str(self.source), "dev")
        self.assertEqual((self.source / "basaltwater.py").read_text(), "old source")
        self.assertEqual((self.source / "deployments/app").read_text(), "application data")

    def test_activation_failure_restores_snapshot_and_runtime_data(self):
        rename = os.rename
        def fail_activation(source, destination):
            if str(source).endswith("/source") and destination == str(self.source):
                raise OSError("activation failed")
            rename(source, destination)
        with patch.object(refresh.os, "rename", side_effect=fail_activation):
            with self.assertRaisesRegex(OSError, "activation failed"):
                refresh._upgrade_snapshot(str(self.source), "dev")
        self.assertEqual((self.source / "basaltwater.py").read_text(), "old source")
        self.assertEqual((self.source / "state/setup.json").read_text(), "saved options")
        self.assertEqual((self.source / "deployments/app").read_text(), "application data")

    def test_failed_recovery_retains_every_copy_for_operator_repair(self):
        rename = os.rename
        def fail_activation_and_recovery(source, destination):
            if str(source).endswith("/source") and destination == str(self.source):
                raise OSError("activation failed")
            if "/source/" in str(source):
                raise OSError("recovery failed")
            rename(source, destination)
        with patch.object(refresh.os, "rename", side_effect=fail_activation_and_recovery):
            with self.assertRaisesRegex(RuntimeError, "recovery needs review"):
                refresh._upgrade_snapshot(str(self.source), "dev")
        staged, = self.root.glob(".basaltwater-refresh-*/source")
        self.assertEqual((staged / "deployments/app").read_text(), "application data")
        self.assertEqual((staged / "state/setup.json").read_text(), "saved options")
        backup, = self.root.glob(".basaltwater-before-refresh-*")
        self.assertEqual((backup / "basaltwater.py").read_text(), "old source")

    def test_source_data_conflict_stops_before_renaming_installation(self):
        def conflicting_clone(command, **kwargs):
            result = self.clone(command, **kwargs)
            (Path(command[-1]) / "deployments").mkdir()
            return result
        self.run.side_effect = conflicting_clone
        with self.assertRaisesRegex(ValueError, "conflicts"):
            refresh._upgrade_snapshot(str(self.source), "dev")
        self.assertEqual((self.source / "basaltwater.py").read_text(), "old source")
        self.assertEqual((self.source / "deployments/app").read_text(), "application data")


class DebianSavedStateTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        self.path = self.root / "setup.json"
        stack.enter_context(patch.object(refresh, "SETUP_CONFIG_FILE", str(self.path)))
        stack.enter_context(patch.object(refresh.os, "geteuid", return_value=os.getuid()))

    def save(self, **values):
        config = SetupConfig(host="localhost", username="human", system_type="server_lite",
                             machine_type="hardware", **values)
        data = config.to_dict()
        data["system_type"] = config.system_type
        self.path.write_text(json.dumps(data))
        self.path.chmod(0o600)

    def test_existing_target_state_is_reused_without_rewriting_it(self):
        self.save(install_node=True, install_python=True, agent_repos=["https://github.com/example/project.git"])
        before = self.path.read_bytes()
        config = refresh._debian_setup()
        self.assertTrue(config.install_node)
        self.assertTrue(config.install_python)
        self.assertEqual(config.agent_repos, ["https://github.com/example/project.git"])
        self.assertEqual(self.path.read_bytes(), before)

    def test_saved_agents_survive_new_profile_defaults_and_target_reparse(self):
        from lib.arg_parser import create_setup_argument_parser
        from remote_setup import config_from_remote_args

        definition = get_system_type_definition("server_lite")
        for selected in ([], ["gh"], ["gh", "codex"]):
            with self.subTest(selected=selected):
                self.save(agent_tools=selected)
                before = self.path.read_bytes()
                for defaults in (tuple(AGENT_TOOLS), ()):
                    with patch("lib.config.get_system_type_definition", return_value=replace(
                        definition, default_agent_tools=defaults,
                    )):
                        config = refresh._debian_setup()
                        self.assertEqual(config.selected_agent_tools(), selected)
                        parser = create_setup_argument_parser("Refresh", for_remote=True, allow_steps=True)
                        arguments = refresh.shlex.split(" ".join(config.to_remote_args()))
                        restored = config_from_remote_args(parser.parse_args(arguments))
                        self.assertEqual(restored.selected_agent_tools(), selected)
                self.assertEqual(self.path.read_bytes(), before)

    def test_invalid_saved_agent_exclusions_are_not_discarded(self):
        self.save()
        data = json.loads(self.path.read_text())
        data["agent_tools_removed"] = ["unknown-provider"]
        self.path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "Unsupported removed agent tool"):
            refresh._debian_setup()

    def test_legacy_state_without_agent_selection_retains_profile_defaults(self):
        self.save()
        data = json.loads(self.path.read_text())
        del data["agent_tools"]
        self.path.write_text(json.dumps(data))
        definition = get_system_type_definition("server_lite")
        with patch("lib.config.get_system_type_definition", return_value=replace(
            definition, default_agent_tools=("gh", "codex"),
        )):
            self.assertEqual(refresh._debian_setup().selected_agent_tools(), ["gh", "codex"])

    def test_transient_credentials_and_destructive_one_shot_flags_are_not_replayed(self):
        self.save()
        data = json.loads(self.path.read_text())
        data.update(password="fixture-secret", share_credentials=[["human", "fixture-secret"]],
                    swap_initialize=True, activate_network=True, copy_agent_keys=True,
                    agent_payload=True, git_auth_token="fixture-secret")
        self.path.write_text(json.dumps(data))
        before = self.path.read_bytes()
        config = refresh._debian_setup()
        self.assertIsNone(config.password)
        self.assertIsNone(config.share_credentials)
        self.assertFalse(config.swap_initialize)
        self.assertFalse(config.activate_network)
        self.assertFalse(config.copy_agent_keys)
        self.assertFalse(config.agent_payload)
        self.assertNotIn("fixture-secret", " ".join(config.to_remote_args()))
        self.assertEqual(self.path.read_bytes(), before)

    def test_public_symlinked_and_corrupt_records_are_rejected(self):
        self.save()
        self.path.chmod(0o644)
        with self.assertRaisesRegex(ValueError, "private root-owned"):
            refresh._debian_setup()
        self.path.unlink()
        target = self.root / "personal.json"
        target.write_text("keep")
        self.path.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "private root-owned"):
            refresh._debian_setup()
        self.assertEqual(target.read_text(), "keep")
        self.path.unlink()
        self.path.write_text("{")
        self.path.chmod(0o600)
        with self.assertRaises(ValueError):
            refresh._debian_setup()


if __name__ == "__main__":
    unittest.main()
