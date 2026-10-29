"""Debian refresh uses successful local state and a fresh upgraded runner."""

from __future__ import annotations

import argparse
from contextlib import ExitStack, nullcontext
from dataclasses import replace
import io
import errno
import json
import os
from pathlib import Path
import signal
import shutil
import stat
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from lib import refresh
from lib.admin_status import JOB_ID_ENV
from lib.config import AGENT_TOOLS, SetupConfig
from lib.channel_manager import ChannelError
from lib.operation_state import OperationStateStore
from lib.plugin_registry import get_system_type_definition


class DebianRefreshTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        stack.enter_context(patch.dict(os.environ, {"BASALTWATER_REFRESH_WORKER": "1"}))
        self.state = self.root / "setup.json"
        self.setup_marker = self.root / "setup-operation.json"
        self.state.write_text("{}")
        self.state.chmod(0o600)
        stack.enter_context(patch.object(refresh, "SETUP_CONFIG_FILE", str(self.state)))
        stack.enter_context(patch.object(refresh, "STATE_DIR", str(self.root)))
        stack.enter_context(patch("lib.privilege_policy.protected_path"))
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
        self.receipt = stack.enter_context(patch.object(refresh, "write_refresh_source_metadata"))

    def invoke(self, *, dry_run=False):
        return refresh.run_refresh_command(argparse.Namespace(dry_run=dry_run))

    def test_upgrade_precedes_fresh_target_setup_and_private_arguments_are_removed(self):
        events = []
        self.upgrade.side_effect = lambda repo: events.append("upgrade") or {
            "commit": "abcdef", "channel": "dev", "updated": False,
        }
        self.receipt.side_effect = lambda repo: events.append("receipt")
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
        self.assertEqual(events, ["upgrade", "receipt", "setup"])
        self.assertFalse(temporary_paths[0].exists())

    def test_source_failure_never_runs_setup(self):
        self.upgrade.side_effect = ChannelError("dirty worktree")
        self.assertEqual(self.invoke(), 1)
        self.run.assert_not_called()

    def test_terminal_refresh_dispatches_before_source_activation(self):
        for environment in ({}, {"SSH_CONNECTION": "fixture"}, {"T3CODE_PORT": "3773"}):
            with self.subTest(environment=environment), patch.dict(os.environ, environment, clear=True), \
                    patch("lib.refresh_job.queue_refresh", return_value=0) as queue:
                self.assertEqual(self.invoke(), 0)
                queue.assert_called_once_with(str(self.root))
        self.upgrade.assert_not_called()
        self.run.assert_not_called()

    def test_existing_independent_admin_job_does_not_dispatch_a_nested_refresh(self):
        with patch.dict(os.environ, {JOB_ID_ENV: "a" * 32}, clear=True), \
                patch("lib.refresh_job.queue_refresh") as queue:
            self.assertEqual(self.invoke(), 0)
        queue.assert_not_called()
        self.upgrade.assert_called_once()

    def test_failed_dispatch_does_not_change_source(self):
        with patch.dict(os.environ, {}, clear=True), \
                patch("lib.refresh_job.queue_refresh", side_effect=RuntimeError("Cannot queue")):
            self.assertEqual(self.invoke(), 1)
        self.upgrade.assert_not_called()
        self.run.assert_not_called()

    def test_container_without_service_management_keeps_foreground_refresh(self):
        self.config.machine_type = "oci"
        with patch.dict(os.environ, {}, clear=True), patch("lib.refresh_job.queue_refresh") as queue:
            self.assertEqual(self.invoke(), 0)
        queue.assert_not_called()
        self.upgrade.assert_called_once()

    def test_managed_upgrade_reconciles_code_modes_before_setup_without_changing_data(self):
        def upgrade(repo):
            (self.root / "lib").mkdir(mode=0o700)
            (self.root / "lib/helper.py").write_text("source")
            (self.root / "lib/helper.py").chmod(0o600)
            (self.root / "pyproject.toml").write_text('[project]\nversion = "2.0.0rc1"\n')
            (self.root / "pyproject.toml").chmod(0o600)
            (self.root / ".git").mkdir(mode=0o700)
            return {"commit": "new", "channel": "dev"}
        self.upgrade.side_effect = upgrade
        def execute(command, **kwargs):
            self.assertEqual(stat.S_IMODE((self.root / "lib").stat().st_mode), 0o755)
            self.assertEqual(stat.S_IMODE((self.root / "lib/helper.py").stat().st_mode), 0o644)
            self.assertEqual(stat.S_IMODE((self.root / "pyproject.toml").stat().st_mode), 0o644)
            self.assertEqual(stat.S_IMODE((self.root / ".git").stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(self.state.stat().st_mode), 0o600)
            return subprocess.CompletedProcess(command, 0)
        self.run.side_effect = execute
        self.assertEqual(self.invoke(), 0)

    def test_dry_run_never_changes_source_or_invokes_target(self):
        with patch.object(refresh, "get_channel_info", return_value={"channel": "stable"}), \
                patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(self.invoke(dry_run=True), 0)
        self.assertIn("server_lite", output.getvalue())
        self.assertIn("stable", output.getvalue())
        self.upgrade.assert_not_called()
        self.run.assert_not_called()
        self.receipt.assert_not_called()

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

    def test_source_upgrade_excludes_target_setup_until_fresh_runner_claim(self):
        def upgrade(repository):
            competitor = OperationStateStore(str(self.setup_marker))
            self.addCleanup(competitor.close)
            with self.assertRaisesRegex(ValueError, "another process"):
                competitor.begin("target_setup", "server_lite", "applying")
            return {"commit": "new", "channel": "dev"}

        def execute(command, **kwargs):
            runner = OperationStateStore(str(self.setup_marker))
            record = runner.begin("target_setup", "server_lite", "applying")
            runner.complete(record.operation_id)
            return subprocess.CompletedProcess(command, 0)

        self.upgrade.side_effect = upgrade
        self.run.side_effect = execute
        self.assertEqual(self.invoke(), 0)
        self.receipt.assert_called_once()

    def test_live_setup_owner_without_published_marker_blocks_refresh(self):
        with OperationStateStore(str(self.setup_marker)).ownership():
            self.assertEqual(self.invoke(), 1)
        self.upgrade.assert_not_called()
        self.receipt.assert_not_called()
        self.run.assert_not_called()

    def test_in_progress_setup_marker_blocks_source_upgrade_and_preserves_job_evidence(self):
        store = OperationStateStore(str(self.setup_marker))
        record = store.begin("target_setup", "server_lite", "applying", context={
            "machine_type": "hardware", "system_type": "server_lite", "username": "human",
            "maintenance_job_id": "a" * 32, "setup_stage": "steps", "step_index": 2,
        })
        store.close()
        before = self.setup_marker.read_bytes()

        self.assertEqual(self.invoke(), 1)

        self.upgrade.assert_not_called()
        self.receipt.assert_not_called()
        self.run.assert_not_called()
        self.assertEqual(self.setup_marker.read_bytes(), before)
        self.assertEqual(OperationStateStore(str(self.setup_marker)).load(), record)

    def test_matching_recovery_required_setup_marker_can_still_replay_idempotent_setup(self):
        store = OperationStateStore(str(self.setup_marker))
        record = store.begin("target_setup", "server_lite", "applying", context={
            "machine_type": "hardware", "system_type": "server_lite", "username": "human",
            "maintenance_job_id": "a" * 32, "setup_stage": "steps", "step_index": 2,
        })
        recovered = store.transition(
            record.operation_id, "recovery", status="recovery_required", expected_record=record,
        )
        store.close()
        before = self.setup_marker.read_bytes()

        self.assertEqual(self.invoke(), 0)

        self.upgrade.assert_called_once()
        self.receipt.assert_called_once()
        self.run.assert_called_once()
        self.assertEqual(OperationStateStore(str(self.setup_marker)).load(), recovered)
        self.assertEqual(self.setup_marker.read_bytes(), before)

    def test_recovery_lookup_errors_stop_before_source_or_receipt_changes(self):
        activation_marker = self.root.parent / refresh.SOURCE_ACTIVATION_MARKER_FILENAME
        real_lstat = os.lstat
        for marker in (activation_marker, self.setup_marker):
            for target in (str(marker), str(marker) + ".lock"):
                def lookup(path, *args, **kwargs):
                    if path == target:
                        raise OSError(errno.EIO, "injected lookup failure")
                    return real_lstat(path, *args, **kwargs)
                with self.subTest(target=target), patch.object(refresh.os, "lstat", side_effect=lookup):
                    self.assertEqual(self.invoke(), 1)
                self.upgrade.assert_not_called()
                self.receipt.assert_not_called()
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
        state_inode = os.lstat(self.source / "state").st_ino
        deployments_inode = os.stat(self.source / "deployments").st_ino
        deployment_inode = os.stat(self.source / "deployments/app").st_ino
        refresh._upgrade_snapshot(str(self.source), "dev")
        self.assertEqual((self.source / "basaltwater.py").read_text(), "new source")
        self.assertTrue((self.source / "state").is_symlink())
        self.assertEqual(os.lstat(self.source / "state").st_ino, state_inode)
        self.assertEqual((self.source / "state/setup.json").read_text(), "saved options")
        self.assertEqual((self.source / "deployments/app").read_text(), "application data")
        self.assertEqual(os.stat(self.source / "deployments").st_ino, deployments_inode)
        self.assertEqual(os.stat(self.source / "deployments/app").st_ino, deployment_inode)
        backup, = self.root.glob(".basaltwater-before-refresh-*")
        self.assertEqual((backup / "basaltwater.py").read_text(), "old source")
        self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o700)
        self.assertIn("/deployments/", (self.source / ".git/info/exclude").read_text())
        self.assertFalse((self.root / refresh.SOURCE_ACTIVATION_MARKER_FILENAME).exists())
        result = json.loads((self.root / (refresh.SOURCE_ACTIVATION_MARKER_FILENAME + ".last.json")).read_text())
        self.assertEqual(result["outcome"], "succeeded")

    def test_activated_state_symlink_leaves_a_real_git_checkout_clean(self):
        environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        environment.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        def git(root, *arguments):
            return subprocess.run(["git", "-C", str(root), *arguments], check=True,
                                  capture_output=True, text=True, env=environment)
        def clone(command, **kwargs):
            staged = Path(command[-1])
            staged.mkdir()
            git(staged, "init", "--initial-branch=main")
            (staged / "basaltwater.py").write_text("new source")
            (staged / ".gitignore").write_bytes((Path(__file__).parents[1] / ".gitignore").read_bytes())
            git(staged, "add", ".gitignore", "basaltwater.py")
            git(staged, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                "commit", "-m", "fixture")
            return subprocess.CompletedProcess(command, 0)
        self.run.side_effect = clone
        refresh._upgrade_snapshot(str(self.source), "dev")
        self.assertTrue((self.source / "state").is_symlink())
        self.assertEqual(git(self.source, "status", "--porcelain", "--untracked-files=all").stdout, "")

    def test_download_failure_preserves_installed_snapshot(self):
        self.run.side_effect = None
        self.run.return_value = subprocess.CompletedProcess([], 1)
        with self.assertRaisesRegex(RuntimeError, "unchanged"):
            refresh._upgrade_snapshot(str(self.source), "dev")
        self.assertEqual((self.source / "basaltwater.py").read_text(), "old source")
        self.assertEqual((self.source / "deployments/app").read_text(), "application data")

    def test_private_job_umask_keeps_code_readable_and_retained_state_private(self):
        self.state.chmod(0o700)
        (self.state / "setup.json").chmod(0o600)
        def private_clone(command, **kwargs):
            result = self.clone(command, **kwargs)
            staged = Path(command[-1])
            (staged / "lib").mkdir()
            (staged / "lib/helper.py").write_text("source")
            (staged / "basaltwater.py").chmod(0o700)
            (staged / ".basaltwater").mkdir()
            (staged / ".basaltwater/channel.json").write_text("{}")
            return result
        self.run.side_effect = private_clone
        previous_umask = os.umask(0o077)
        try:
            refresh._upgrade_snapshot(str(self.source), "dev")
        finally:
            os.umask(previous_umask)
        for relative, mode in (("lib", 0o755), ("lib/helper.py", 0o644),
                               ("basaltwater.py", 0o755), (".git", 0o700),
                               (".basaltwater", 0o700), (".basaltwater/channel.json", 0o600)):
            self.assertEqual(stat.S_IMODE((self.source / relative).stat().st_mode), mode, relative)
        self.assertEqual(stat.S_IMODE(self.state.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.state / "setup.json").stat().st_mode), 0o600)

    def test_linked_downloaded_source_is_rejected_before_activation(self):
        for directory in (False, True):
            with self.subTest(directory=directory):
                def linked_clone(command, **kwargs):
                    result = self.clone(command, **kwargs)
                    (Path(command[-1]) / "linked").symlink_to(self.state if directory else self.state / "setup.json")
                    return result
                self.run.side_effect = linked_clone
                with self.assertRaisesRegex(ValueError, "regular files and directories"):
                    refresh._upgrade_snapshot(str(self.source), "dev")
                self.assertEqual((self.source / "basaltwater.py").read_text(), "old source")
                self.assertEqual((self.source / "state/setup.json").read_text(), "saved options")

    def test_activation_failure_restores_snapshot_and_runtime_data(self):
        state_inode = os.lstat(self.source / "state").st_ino
        deployments_inode = os.stat(self.source / "deployments").st_ino
        deployment_inode = os.stat(self.source / "deployments/app").st_ino
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
        self.assertEqual(os.lstat(self.source / "state").st_ino, state_inode)
        self.assertEqual(os.stat(self.source / "deployments").st_ino, deployments_inode)
        self.assertEqual(os.stat(self.source / "deployments/app").st_ino, deployment_inode)
        result = json.loads((self.root / (refresh.SOURCE_ACTIVATION_MARKER_FILENAME + ".last.json")).read_text())
        self.assertEqual(result["outcome"], "rolled_back")

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
        marker = self.root / refresh.SOURCE_ACTIVATION_MARKER_FILENAME
        record = OperationStateStore(str(marker)).load()
        self.assertIsNotNone(record)
        self.assertEqual(record.status, "recovery_required")
        self.assertEqual(record.context["repository_path"], str(self.source))
        self.assertEqual(record.context["backup_path"], str(backup))
        self.assertEqual(record.context["staged_path"], str(staged))

    def test_interrupt_after_a_completed_rename_retains_marker_and_runtime_data(self):
        real_rename = os.rename
        for target in ("source", "deployments", "state"):
            for interruption in (KeyboardInterrupt, SystemExit):
                with self.subTest(target=target, interruption=interruption):
                    def interrupt_after_move(source, destination):
                        real_rename(source, destination)
                        if target == "source" and source == str(self.source):
                            raise interruption("interrupted after rename")
                        if target != "source" and str(destination).endswith("/source/" + target):
                            raise interruption("interrupted after rename")
                    with patch.object(refresh.os, "rename", side_effect=interrupt_after_move):
                        with self.assertRaises(interruption):
                            refresh._upgrade_snapshot(str(self.source), "dev")
                    marker = self.root / refresh.SOURCE_ACTIVATION_MARKER_FILENAME
                    store = OperationStateStore(str(marker))
                    record = store.load()
                    self.assertIsNotNone(record)
                    backup = Path(record.context["backup_path"])
                    staged = Path(record.context["staged_path"])
                    self.assertEqual((backup / "basaltwater.py").read_text(), "old source")
                    self.assertEqual((staged / "basaltwater.py").read_text(), "new source")
                    for name, content in (("deployments/app", "application data"),
                                          ("state/setup.json", "saved options")):
                        copies = [root / name for root in (backup, staged) if (root / name).exists()]
                        self.assertEqual(len(copies), 1)
                        self.assertEqual(copies[0].read_text(), content)
                    # Reconcile this inspected fixture before the next subtest.
                    for name in record.context["runtime_data_names"]:
                        if os.path.lexists(staged / name):
                            real_rename(staged / name, backup / name)
                    real_rename(backup, self.source)
                    store.complete(record.operation_id, outcome="rolled_back")
                    shutil.rmtree(record.context["temporary_path"])

    def test_sigkill_after_first_rename_leaves_exact_activation_paths_for_recovery(self):
        script = """
import os
import signal
import sys
from pathlib import Path
from types import SimpleNamespace
from lib import refresh
from lib.operation_state import OperationStateStore
import lib.privilege_policy

root = Path(sys.argv[1])
repository = root / "basaltwater"
lib.privilege_policy.protected_path = lambda *args, **kwargs: None

def clone(command, **kwargs):
    staged = Path(command[-1])
    (staged / ".git/info").mkdir(parents=True)
    (staged / "basaltwater.py").write_text("new source")
    return SimpleNamespace(returncode=0)

refresh.run = clone
refresh.switch_channel = lambda staged, channel: {"commit": "new", "channel": channel}
real_rename = os.rename

def rename_then_kill(source, destination):
    real_rename(source, destination)
    if source == str(repository) and str(destination).startswith(str(root / ".basaltwater-before-refresh-")):
        marker = root / refresh.SOURCE_ACTIVATION_MARKER_FILENAME
        if OperationStateStore(str(marker)).load() is None:
            os._exit(91)
        print("first rename completed with durable marker", flush=True)
        os.kill(os.getpid(), signal.SIGKILL)

os.rename = rename_then_kill
refresh._upgrade_snapshot(str(repository), "dev")
"""
        completed = subprocess.run(
            [refresh.sys.executable, "-c", script, str(self.root)],
            cwd=Path(__file__).parents[1], capture_output=True, text=True, check=False,
        )

        self.assertEqual(completed.returncode, -signal.SIGKILL, completed.stderr)
        self.assertIn("first rename completed with durable marker", completed.stdout)
        self.assertFalse(self.source.exists())
        marker = self.root / refresh.SOURCE_ACTIVATION_MARKER_FILENAME
        record = OperationStateStore(str(marker)).load()
        self.assertIsNotNone(record)
        self.assertEqual(record.status, "in_progress")
        self.assertEqual(stat.S_IMODE(marker.stat().st_mode), 0o600)
        backup = Path(record.context["backup_path"])
        staged = Path(record.context["staged_path"])
        self.assertEqual(Path(record.context["repository_path"]), self.source)
        self.assertTrue(Path(record.context["temporary_path"]).is_dir())
        self.assertEqual(record.context["runtime_data_names"], ["deployments", "state"])
        self.assertEqual((backup / "basaltwater.py").read_text(), "old source")
        self.assertEqual((backup / "deployments/app").read_text(), "application data")
        self.assertEqual((staged / "basaltwater.py").read_text(), "new source")

        with patch.object(refresh, "run") as clone, self.assertRaisesRegex(
            RuntimeError, "Unresolved Basaltwater source activation",
        ):
            refresh._upgrade_snapshot(str(self.source), "dev")
        clone.assert_not_called()

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

    def test_approval_origin_survives_target_serialization_and_fresh_setup_parsing(self):
        from lib.arg_parser import create_setup_argument_parser
        from remote_setup import config_from_remote_args

        config = SetupConfig(host="localhost", username="human", system_type="agent_vm",
                             machine_type="vm", privilege_broker_port=9444)
        data = config.to_dict()
        data["system_type"] = config.system_type
        self.path.write_text(json.dumps(data))
        self.path.chmod(0o600)
        before = self.path.read_bytes()
        for origin, expected in (("https://192.0.2.12:9444", "192.0.2.12"),
                                 ("https://vm.example:9444", "vm.example")):
            with self.subTest(origin=origin), \
                    patch("lib.privilege_policy.load_policy", return_value={"origin": origin, "requester_uid": 1000}), \
                    patch.object(refresh.pwd, "getpwnam", return_value=SimpleNamespace(pw_uid=1000)):
                restored = refresh._debian_setup()
            parser = create_setup_argument_parser("Refresh", for_remote=True, allow_steps=True)
            arguments = refresh.shlex.split(" ".join(restored.to_remote_args()))
            parsed = config_from_remote_args(parser.parse_args(arguments))
            self.assertEqual(parsed.privilege_broker_host, expected)
        self.assertEqual(self.path.read_bytes(), before)
        for policy in ({"origin": "https://vm.example:9445", "requester_uid": 1000},
                       {"origin": "https://vm.example:9444", "requester_uid": 1001}):
            with self.subTest(policy=policy), patch("lib.privilege_policy.load_policy", return_value=policy), \
                    patch.object(refresh.pwd, "getpwnam", return_value=SimpleNamespace(pw_uid=1000)), \
                    self.assertRaisesRegex(ValueError, "different port or requester"):
                refresh._debian_setup()

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


class IndependentRefreshJobTests(unittest.TestCase):
    def test_dispatch_uses_a_private_log_and_service_owned_stdio(self):
        from lib import refresh_job

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "basaltwater.py").write_text("# fixture\n")
            logs = root / "logs"
            with patch.object(refresh_job, "LOG_DIRECTORY", str(logs)), \
                    patch.object(refresh_job, "run", return_value=SimpleNamespace(returncode=0)) as run, \
                    patch("sys.stdout", new_callable=io.StringIO) as output:
                self.assertEqual(refresh_job.queue_refresh(str(root)), 0)
            command = run.call_args.args[0]
            self.assertEqual(command[-4:], [refresh_job.sys.executable, "-I", str(root / "basaltwater.py"), "refresh"])
            self.assertIn("--property=StandardInput=null", command)
            self.assertIn("--property=StandardError=inherit", command)
            log = next(logs.iterdir())
            self.assertIn("--property=StandardOutput=append:" + str(log), command)
            self.assertEqual(stat.S_IMODE(logs.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(log.stat().st_mode), 0o600)
            self.assertIn("dispatch only", output.getvalue())

    def test_dispatch_failure_is_reported_and_unsafe_log_directory_is_preserved(self):
        from lib import refresh_job

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "basaltwater.py").touch()
            logs = root / "logs"
            with patch.object(refresh_job, "LOG_DIRECTORY", str(logs)), \
                    patch.object(refresh_job, "run", return_value=SimpleNamespace(returncode=1)) as run:
                with self.assertRaisesRegex(RuntimeError, "no source or setup changes"):
                    refresh_job.queue_refresh(str(root))
                logs.chmod(0o755)
                run.reset_mock()
                with self.assertRaisesRegex(ValueError, "private directory"):
                    refresh_job.queue_refresh(str(root))
                run.assert_not_called()
                self.assertEqual(stat.S_IMODE(logs.stat().st_mode), 0o755)


class DebianT3CompletionTests(unittest.TestCase):
    def setUp(self):
        from common import t3code_completion as completion

        self.completion = completion
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        self.request = self.root / "request.json"
        stack.enter_context(patch.object(completion, "REQUEST_FILE", str(self.request)))
        stack.enter_context(patch.object(completion, "STATE_DIR", str(self.root)))
        stack.enter_context(patch.object(completion, "is_dry_run", return_value=False))
        stack.enter_context(patch.object(completion, "can_manage_system_services", return_value=True))
        self.run = stack.enter_context(patch.object(completion, "run", return_value=SimpleNamespace(returncode=0)))
        stack.enter_context(patch.object(completion, "maintenance_lock", side_effect=lambda: nullcontext(True)))
        self.wait_for_setup = completion._wait_for_setup
        stack.enter_context(patch.object(completion, "_wait_for_setup"))
        self.config = SetupConfig(host="localhost", username="human", system_type="server_lite",
                                  web_interfaces=["t3code"], agent_tools=["codex"])
        self.service = stack.enter_context(patch("common.t3code_steps._user_systemctl",
                                               return_value=SimpleNamespace(returncode=0)))
        self.ready = stack.enter_context(patch("common.t3code_steps._wait_for_t3_service"))
        stack.enter_context(patch.object(completion.pwd, "getpwnam", return_value=SimpleNamespace(
            pw_uid=1000, pw_dir="/home/human")))

    def queue(self):
        with patch("sys.stdout", new_callable=io.StringIO):
            self.completion.queue_completion(self.config)
        return json.loads(self.request.read_text())["id"]

    def test_queue_is_independent_and_latest_completed_setup_wins(self):
        old = self.queue()
        current = self.queue()
        self.assertEqual(stat.S_IMODE(self.request.stat().st_mode), 0o600)
        command = self.run.call_args.args[0]
        self.assertIn("--property=StandardInput=null", command)
        self.assertIn("--on-active=5s", command)
        self.completion.complete(old, 123, "456")
        self.service.assert_not_called()
        self.completion.complete(current, 123, "456")
        self.service.assert_called_once_with("human", 1000, "restart", "t3code")
        self.ready.assert_called_once_with("human", 1000, "127.0.0.1", 3773, "/home/human")

    def test_unfinished_setup_prevents_restart_and_service_failure_propagates(self):
        request = self.queue()
        store = OperationStateStore(str(self.root / "setup-operation.json"))
        record = store.begin("target_setup", "server_lite", "applying")
        store.close()
        with self.assertRaisesRegex(RuntimeError, "Unfinished setup"):
            self.completion.complete(request, 123, "456")
        self.service.assert_not_called()
        store.complete(record.operation_id)
        self.service.return_value.returncode = 1
        with self.assertRaisesRegex(RuntimeError, "start/reload failed"):
            self.completion.complete(request, 123, "456")
        self.ready.assert_not_called()

    def test_completion_accepts_configured_ipv4_and_ipv6_wildcard_listeners(self):
        for host in ("0.0.0.0", "::", "::1", "localhost"):
            with self.subTest(host=host):
                self.config.web_interface_host = host
                self.config.web_interface_sources = ["192.168.1.0/24"] if host in {"0.0.0.0", "::"} else []
                request = self.queue()
                self.completion.complete(request, 123, "456")
                self.ready.assert_called_with("human", 1000, host, 3773, "/home/human")

    def test_no_t3_or_dry_run_does_not_queue(self):
        with patch.object(self.completion, "is_dry_run", return_value=True):
            self.completion.queue_completion(self.config)
        self.config.web_interfaces = []
        self.completion.queue_completion(self.config)
        self.run.assert_not_called()
        self.assertFalse(self.request.exists())

    def test_pid_reuse_does_not_wait_on_an_unrelated_process(self):
        with patch.object(self.completion, "_process_start", return_value="other"), \
                patch.object(self.completion.os, "pidfd_open") as handle:
            self.wait_for_setup(123, "456")
            handle.assert_not_called()

    def test_worker_waits_for_setup_exit_and_refuses_to_interrupt_it_on_timeout(self):
        with patch.object(self.completion, "_process_start", return_value="456"), \
                patch.object(self.completion.os, "pidfd_open", return_value=99), \
                patch.object(self.completion.select, "select", return_value=([], [], [])) as wait, \
                patch.object(self.completion.os, "close") as close:
            with self.assertRaisesRegex(RuntimeError, "has not exited"):
                self.wait_for_setup(123, "456")
            wait.assert_called_once_with([99], [], [], 600)
            close.assert_called_once_with(99)

    def test_runner_queues_only_after_all_steps_state_and_notifications(self):
        import remote_setup

        events = []
        self.config.notify_specs = [("discord", "https://example.invalid/webhook")]
        with ExitStack() as stack:
            stack.enter_context(patch.object(remote_setup, "enable_detected_build_runtimes"))
            stack.enter_context(patch.object(remote_setup, "print_setup_summary"))
            stack.enter_context(patch.object(remote_setup, "detect_os", return_value="Debian"))
            stack.enter_context(patch.object(remote_setup, "get_steps_for_system_type", return_value=[
                ("Providers and skills", lambda config: events.append("steps")),
            ]))
            stack.enter_context(patch("plugins.post_setup.build_post_setup_steps", return_value=[]))
            stack.enter_context(patch.object(remote_setup, "_save_setup_state", side_effect=lambda config: events.append("state")))
            stack.enter_context(patch.object(remote_setup, "_begin_setup_operation"))
            stack.enter_context(patch.object(remote_setup, "_transition_setup_operation"))
            stack.enter_context(patch.object(remote_setup, "_send_setup_notification", side_effect=lambda **kwargs: events.append("notify")))
            queue = stack.enter_context(patch.object(self.completion, "queue_completion", side_effect=lambda config: events.append("queue")))
            stack.enter_context(patch("sys.stdout", new_callable=io.StringIO))
            self.assertEqual(remote_setup._run_configured_setup(self.config, argparse.Namespace(dry_run=False)), 0)
            self.assertEqual(events, ["steps", "state", "notify", "queue"])
            queue.reset_mock()
            self.assertEqual(remote_setup._run_configured_setup(self.config, argparse.Namespace(dry_run=True)), 0)
            queue.assert_not_called()

    def test_successful_setup_retains_exclusion_through_final_report_and_cleanup(self):
        import remote_setup
        from lib.operation_state import OperationStateError

        marker = str(self.root / "setup-operation.json")
        competing = OperationStateStore(marker)
        self.addCleanup(competing.close)

        def assert_excluded():
            self.assertFalse(Path(marker).exists())
            with self.assertRaisesRegex(OperationStateError, "another process"):
                with competing.ownership():
                    pass

        def finish():
            remote_setup._begin_setup_operation(self.config)
            remote_setup._complete_setup_operation()
            assert_excluded()  # Notification and queueing boundary.
            return 0

        with patch.object(remote_setup, "SETUP_OPERATION_FILE", marker), \
                patch.object(remote_setup, "record_setup_activity"), \
                patch.object(remote_setup, "_run_main", side_effect=finish), \
                patch.object(remote_setup, "_remove_secret_payloads", side_effect=assert_excluded), \
                patch.object(remote_setup.SetupReport, "render", side_effect=assert_excluded):
            self.assertEqual(remote_setup.main(), 0)
        with competing.ownership():
            self.assertIsNone(competing.load())


if __name__ == "__main__":
    unittest.main()
