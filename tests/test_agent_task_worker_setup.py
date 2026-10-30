"""Independent task worker setup respects account and service capabilities."""

from __future__ import annotations

import os
import fcntl
import json
import tempfile
import unittest
from contextlib import contextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from common import agent_task_steps as steps
from lib.config import SetupConfig
from lib.operation_state import OperationStateError, OperationStateStore
from lib.remote_utils import CommandExecutionError


class TaskRefreshGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = self.enterContext(tempfile.TemporaryDirectory())
        self.account = SimpleNamespace(pw_uid=os.geteuid(), pw_gid=os.getegid(), pw_dir=self.home)

    def test_guard_blocks_new_controller_claims_until_setup_finishes(self) -> None:
        with steps._refresh_guard(self.account) as parent:
            descriptor = os.open("state.lock", os.O_RDWR, dir_fd=parent)
            self.addCleanup(os.close, descriptor)
            with self.assertRaises(BlockingIOError):
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertFalse(steps._worker_busy(self.account, parent))
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_guard_refuses_busy_transactions_and_symlinked_state(self) -> None:
        with steps._refresh_guard(self.account), self.assertRaisesRegex(RuntimeError, "state is busy"):
            with steps._refresh_guard(self.account):
                self.fail("An active transaction must not be interrupted")
        (Path(self.home) / ".local/state/basaltwater/prompt-tasks/state.lock").unlink()
        outside = Path(self.home) / "outside"
        outside.write_text("preserve")
        (Path(self.home) / ".local/state/basaltwater/prompt-tasks/state.lock").symlink_to(outside)
        with self.assertRaises(OSError), steps._refresh_guard(self.account):
            self.fail("A symlinked lock must not be followed")
        self.assertEqual(outside.read_text(), "preserve")

    def test_live_legacy_workers_cannot_be_migrated_with_an_idle_check(self) -> None:
        parent_path = Path(self.home) / ".local/state/basaltwater/prompt-tasks"
        with steps._refresh_guard(self.account) as parent:
            descriptor = os.open("worker.lock", os.O_CREAT | os.O_RDWR, 0o600, dir_fd=parent)
            self.addCleanup(os.close, descriptor)
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            state = parent_path / "tasks.json"
            state.write_text(json.dumps({"version": 1, "tasks": [], "runs": []}))
            state.chmod(0o600)
            with self.assertRaisesRegex(RuntimeError, "Stop the legacy"):
                steps._worker_busy(self.account, parent)
            state.write_text(json.dumps({"version": 2, "tasks": [], "runs": [{"status": "running"}]}))
            self.assertTrue(steps._worker_busy(self.account, parent))
            state.write_text(json.dumps({"version": 2, "tasks": [], "runs": []}))
            self.assertFalse(steps._worker_busy(self.account, parent))

    def test_invalid_live_state_is_preserved(self) -> None:
        with steps._refresh_guard(self.account) as parent:
            descriptor = os.open("worker.lock", os.O_CREAT | os.O_RDWR, 0o600, dir_fd=parent)
            self.addCleanup(os.close, descriptor)
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            path = Path(self.home) / ".local/state/basaltwater/prompt-tasks/tasks.json"
            for payload in ('{"version":2,"version":1,"runs":[]}',
                            '{"version":2,"runs":[],"extra":NaN}',
                            '{"version":true,"runs":[]}',
                            '{"version":2,"runs":[{"status":"unknown"}]}'):
                with self.subTest(payload=payload):
                    path.write_text(payload)
                    path.chmod(0o600)
                    with self.assertRaises(RuntimeError):
                        steps._worker_busy(self.account, parent)
                    self.assertEqual(path.read_text(), payload)

    def test_unsafe_directory_reports_the_component_and_preserves_permissions(self) -> None:
        directory = Path(self.home) / ".local/state/basaltwater/prompt-tasks"
        directory.mkdir(parents=True, mode=0o700)
        for path in (directory.parent, directory.parent.parent, directory.parent.parent.parent):
            path.chmod(0o700)
        for path, mode in ((directory.parent.parent, 0o775), (directory, 0o755)):
            with self.subTest(path=path, mode=mode):
                previous_mode = path.stat().st_mode & 0o777
                path.chmod(mode)
                try:
                    with self.assertRaises(RuntimeError) as rejected, steps._refresh_guard(self.account):
                        self.fail("Unsafe permissions must not be repaired implicitly")
                    message = str(rejected.exception)
                    self.assertIn(str(path), message)
                    self.assertIn(f"owner UID {os.geteuid()}", message)
                    self.assertIn(f"mode {mode:04o}", message)
                    self.assertEqual(path.stat().st_mode & 0o777, mode)
                finally:
                    path.chmod(previous_mode)


class TaskWorkerSetupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = self.enterContext(tempfile.TemporaryDirectory())
        self.account = SimpleNamespace(pw_uid=1000, pw_gid=1000, pw_name="agent", pw_dir=self.home)
        real_stat = os.stat

        def account_stat(path: object, *args: object, **kwargs: object) -> os.stat_result:
            info = real_stat(path, *args, **kwargs)
            if isinstance(path, (str, os.PathLike)) and os.fspath(path) == self.home:
                fields = list(info)
                fields[4] = self.account.pw_uid
                return os.stat_result(fields)
            return info

        self.enterContext(patch.object(steps.os, "stat", side_effect=account_stat))
        self.config = SetupConfig(system_type="agent_vm", host="example.test", username="agent", machine_type="vm", agent_tools=["codex"])
        self.enterContext(patch.object(steps, "can_manage_system_services", return_value=True))
        self.enterContext(patch.object(steps, "is_dry_run", return_value=False))
        self.enterContext(patch.object(steps.pwd, "getpwnam", return_value=self.account))
        self.enterContext(patch.object(steps, "UNIT_DIR", self.home))
        self.enterContext(patch.object(steps, "_refresh_guard", side_effect=lambda _account: nullcontext()))
        self.panel_path = Path(self.home) / steps.PANEL_UNIT
        self.panel_path.write_text(steps.PANEL_MARKER + "\n[Service]\nUser=agent\n")

    def _current_definition(self, unit: str) -> SimpleNamespace:
        return SimpleNamespace(returncode=0, stdout=(
            "LoadState=loaded\n"
            f"FragmentPath={Path(self.home) / unit}\n"
            "NeedDaemonReload=no\n"
        ))

    def _write_worker_definition(self, unit: str, content: str) -> None:
        path = Path(self.home) / unit
        path.write_text(content)
        path.chmod(0o644)

    def test_render_keeps_provider_and_workspace_identity_non_root(self) -> None:
        content = steps.render_task_worker(self.account, revision="reviewed")
        self.assertIn("User=agent", content)
        self.assertIn("SupplementaryGroups=systemd-journal\n", content)
        self.assertIn('Environment="HOME=' + self.home + '"', content)
        self.assertIn("KillMode=control-group", content)
        self.assertIn("agent tasks worker", content)
        self.assertNotIn("User=root", content)
        self.assertIn("%", steps._quote("value%"))
        self.assertEqual(steps._quote("value%"), '"value%%"')

    def test_working_directory_preserves_literal_systemd_paths(self) -> None:
        self.account.pw_dir = '/home/agent/work "quoted" %n\\ '
        content = steps.render_task_worker(self.account, revision="reviewed")
        directory = next(line for line in content.splitlines() if line.startswith("WorkingDirectory="))
        self.assertEqual(directory, 'WorkingDirectory=/home/agent/work "quoted" %%n\\ /')
        for path in ("relative/home", "/home/agent\nUser=root", "/home/agent\t"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.account.pw_dir = path
                steps.render_task_worker(self.account, revision="reviewed")

    def test_install_uses_transactional_unit_activation_without_starting_prompts(self) -> None:
        with patch.object(steps, "_worker_busy", return_value=False), patch.object(steps, "_active", return_value=False), patch.object(
            steps, "replace_units",
        ) as replace:
            steps.configure_agent_task_worker(self.config)
        units = replace.call_args.args[0]
        unit = next(iter(units))
        self.assertEqual(unit, f"basaltwater-agent-tasks-{self.account.pw_uid}.service")
        self.assertEqual(replace.call_args.kwargs["activate"], (unit,))
        self.assertEqual(replace.call_args.kwargs["unit_dir"], self.home)

    def test_container_root_and_dry_run_never_change_services(self) -> None:
        with patch.object(steps, "replace_units") as replace, patch.object(steps, "run") as run:
            with patch.object(steps, "can_manage_system_services", return_value=False):
                steps.configure_agent_task_worker(self.config)
            steps.configure_agent_task_worker(SetupConfig(system_type="server_web", host="example.test", username="root", agent_tools=["codex"]))
            with patch.object(steps, "is_dry_run", return_value=True):
                steps.configure_agent_task_worker(self.config)
        replace.assert_not_called()
        run.assert_not_called()

    def test_monitoring_only_panel_never_inspects_or_creates_task_storage(self) -> None:
        config = SetupConfig(system_type="server_lite", host="example.test", username="gitadmin", web_panel_port=443)
        with patch.object(steps, "_refresh_guard") as guard, patch.object(steps.pwd, "getpwnam") as account, patch.object(
            steps, "run",
        ) as run, patch.object(steps, "replace_units") as replace:
            steps.configure_agent_task_worker(config)
        guard.assert_not_called()
        account.assert_not_called()
        run.assert_not_called()
        replace.assert_not_called()
        self.assertFalse((Path(self.home) / ".local").exists())

    def test_busy_worker_refuses_refresh_before_any_service_is_stopped(self) -> None:
        with patch.object(steps, "_worker_busy", return_value=True), patch.object(steps, "run") as run, patch.object(
            steps, "replace_units",
        ) as replace, self.assertRaises(RuntimeError):
            steps.configure_agent_task_worker(self.config)
        run.assert_not_called()
        replace.assert_not_called()

    def test_unmanaged_unit_is_preserved(self) -> None:
        path = Path(self.home) / f"basaltwater-agent-tasks-{self.account.pw_uid}.service"
        path.write_text("[Service]\nExecStart=/another/service\n")
        with patch.object(steps, "replace_units") as replace, self.assertRaises(RuntimeError):
            steps.configure_agent_task_worker(self.config)
        replace.assert_not_called()
        self.assertIn("/another/service", path.read_text())

    def test_panel_handoff_only_touches_same_account_and_restores_on_failure(self) -> None:
        completed = SimpleNamespace(returncode=0, stdout="agent\n")
        with patch.object(steps, "_worker_busy", return_value=False), patch.object(steps, "_active", return_value=True), patch.object(
            steps, "run", return_value=completed,
        ) as run, patch.object(steps, "replace_units", side_effect=RuntimeError("activation failed")), self.assertRaises(RuntimeError):
            steps.configure_agent_task_worker(self.config)
        run.assert_any_call(["systemctl", "stop", steps.PANEL_UNIT], timeout=120)
        run.assert_any_call(["systemctl", "start", steps.PANEL_UNIT], timeout=120)
        with patch.object(steps, "_worker_busy", return_value=False), patch.object(steps, "_active", return_value=True), patch.object(
            steps, "run", return_value=SimpleNamespace(returncode=0, stdout="another-account\n"),
        ) as run, patch.object(steps, "replace_units"):
            steps.configure_agent_task_worker(self.config)
        self.assertEqual(run.call_count, 1)

    def test_failed_panel_stop_still_attempts_restoration_without_activation(self) -> None:
        events = []

        def command(args: list[str], **kwargs: object) -> SimpleNamespace:
            events.append(args[1])
            if args[1] == "stop":
                raise RuntimeError("stop timed out")
            return SimpleNamespace(returncode=0, stdout="agent\n")

        with patch.object(steps, "_worker_busy", return_value=False), patch.object(steps, "_active", return_value=True), patch.object(
            steps, "run", side_effect=command,
        ), patch.object(steps, "replace_units") as replace, self.assertRaisesRegex(RuntimeError, "stop timed out"):
            steps.configure_agent_task_worker(self.config)
        self.assertEqual(events, ["show", "stop", "start"])
        replace.assert_not_called()

    def test_unchanged_active_worker_does_not_interrupt_work(self) -> None:
        content = steps.WORKER_MARKER + "\nunchanged"
        unit = f"basaltwater-agent-tasks-{self.account.pw_uid}.service"
        self._write_worker_definition(unit, content)

        def command(arguments, **_kwargs):
            if arguments[1] == "show":
                return self._current_definition(unit)
            return SimpleNamespace(returncode=0, stdout="enabled\n")

        with patch.object(steps, "render_task_worker", return_value=content), patch.object(steps, "_active", return_value=True), patch.object(
            steps, "_worker_busy",
        ) as busy, patch.object(steps, "replace_units") as replace, patch.object(
            steps, "_refresh_guard",
        ) as guard, patch.object(steps, "run", side_effect=command) as run:
            steps.configure_agent_task_worker(self.config)
        busy.assert_not_called()
        guard.assert_not_called()
        replace.assert_not_called()
        self.assertEqual([call.args[0][1] for call in run.call_args_list], ["show", "is-enabled"])

    def test_unchanged_busy_worker_repairs_persistent_enablement_without_refresh(self) -> None:
        content = steps.WORKER_MARKER + "\nunchanged"
        unit = f"basaltwater-agent-tasks-{self.account.pw_uid}.service"
        self._write_worker_definition(unit, content)
        for initial_state in ("disabled", "enabled-runtime"):
            with self.subTest(initial_state=initial_state):
                state = initial_state

                def command(arguments, **_kwargs):
                    nonlocal state
                    if arguments[1] == "show":
                        return self._current_definition(unit)
                    if arguments == ["systemctl", "enable", unit]:
                        state = "enabled"
                    return SimpleNamespace(returncode=int(state == "disabled"), stdout=state + "\n")

                with patch.object(steps, "render_task_worker", return_value=content), patch.object(
                    steps, "_active", return_value=True,
                ), patch.object(steps, "_worker_busy", return_value=True) as busy, patch.object(
                    steps, "_refresh_guard",
                ) as guard, patch.object(steps, "replace_units") as replace, patch.object(
                    steps, "run", side_effect=command,
                ) as run:
                    steps.configure_agent_task_worker(self.config)
                self.assertEqual([call.args[0] for call in run.call_args_list[1:]], [
                    ["systemctl", "is-enabled", unit], ["systemctl", "enable", unit],
                    ["systemctl", "is-enabled", unit],
                ])
                busy.assert_not_called()
                guard.assert_not_called()
                replace.assert_not_called()

    def test_unchanged_worker_enablement_failure_is_not_reported_ready(self) -> None:
        content = steps.WORKER_MARKER + "\nunchanged"
        unit = f"basaltwater-agent-tasks-{self.account.pw_uid}.service"
        self._write_worker_definition(unit, content)
        for enable_fails in (False, True):
            with self.subTest(enable_fails=enable_fails):
                def command(arguments, **_kwargs):
                    if arguments[1] == "show":
                        return self._current_definition(unit)
                    if arguments == ["systemctl", "enable", unit] and enable_fails:
                        raise CommandExecutionError(" ".join(arguments), 1, "enable refused")
                    return SimpleNamespace(returncode=0, stdout="enabled-runtime\n")

                with patch.object(steps, "render_task_worker", return_value=content), patch.object(
                    steps, "_active", return_value=True,
                ), patch.object(steps, "_refresh_guard") as guard, patch.object(
                    steps, "replace_units",
                ) as replace, patch.object(steps, "run", side_effect=command) as run, patch(
                    "builtins.print",
                ) as messages, self.assertRaisesRegex(
                    (RuntimeError, CommandExecutionError), "enable refused|not persistently enabled",
                ):
                    steps.configure_agent_task_worker(self.config)
                self.assertEqual(run.call_count, 3 if enable_fails else 4)
                guard.assert_not_called()
                replace.assert_not_called()
                self.assertFalse(any("worker ready" in call.args[0] for call in messages.call_args_list))

    def test_current_worker_preserves_and_refuses_unfinished_unit_recovery(self) -> None:
        content = steps.WORKER_MARKER + "\nunchanged"
        unit = f"basaltwater-agent-tasks-{self.account.pw_uid}.service"
        self._write_worker_definition(unit, content)
        marker = Path(self.home) / ".basaltwater-unit-operation.json"
        for malformed in (False, True):
            with self.subTest(malformed=malformed):
                if malformed:
                    marker.write_text("invalid marker; preserve\n")
                else:
                    store = OperationStateStore(str(marker))
                    store.begin("systemd-units", unit, "published")
                    store.close()
                previous = marker.read_bytes()
                with patch.object(steps, "render_task_worker", return_value=content), patch.object(
                    steps, "_active", return_value=True,
                ) as active, patch.object(steps, "_refresh_guard") as guard, patch.object(
                    steps, "replace_units",
                ) as replace, patch.object(steps, "run") as run, self.assertRaisesRegex(
                    OperationStateError, "transaction-recovery.md",
                ):
                    steps.configure_agent_task_worker(self.config)
                self.assertEqual(marker.read_bytes(), previous)
                active.assert_not_called()
                guard.assert_not_called()
                replace.assert_not_called()
                run.assert_not_called()
                marker.unlink()

    def test_current_worker_does_not_bypass_marker_lookup_errors(self) -> None:
        content = steps.WORKER_MARKER + "\nunchanged"
        unit = f"basaltwater-agent-tasks-{self.account.pw_uid}.service"
        self._write_worker_definition(unit, content)
        marker = Path(self.home) / ".basaltwater-unit-operation.json"
        real_lstat = os.lstat

        def lookup(path, *args, **kwargs):
            if os.fspath(path) == str(marker):
                raise PermissionError("marker lookup denied")
            return real_lstat(path, *args, **kwargs)

        with patch.object(steps, "render_task_worker", return_value=content), patch(
            "lib.operation_state.os.lstat", side_effect=lookup,
        ), patch.object(steps, "_refresh_guard") as guard, patch.object(
            steps, "replace_units",
        ) as replace, patch.object(steps, "run") as run, self.assertRaisesRegex(
            OperationStateError, "marker lookup failed",
        ):
            steps.configure_agent_task_worker(self.config)
        guard.assert_not_called()
        replace.assert_not_called()
        run.assert_not_called()

    def test_current_worker_does_not_bypass_an_owned_unit_transaction(self) -> None:
        content = steps.WORKER_MARKER + "\nunchanged"
        unit = f"basaltwater-agent-tasks-{self.account.pw_uid}.service"
        self._write_worker_definition(unit, content)
        store = OperationStateStore(str(Path(self.home) / ".basaltwater-unit-operation.json"))
        with store.ownership(), patch.object(
            steps, "render_task_worker", return_value=content,
        ), patch.object(steps, "_refresh_guard") as guard, patch.object(
            steps, "replace_units",
        ) as replace, patch.object(steps, "run") as run, self.assertRaisesRegex(
            OperationStateError, "owned by another process",
        ):
            steps.configure_agent_task_worker(self.config)
        guard.assert_not_called()
        replace.assert_not_called()
        run.assert_not_called()

    def test_stale_or_unknown_loaded_worker_definition_cannot_restart_busy_work(self) -> None:
        content = steps.WORKER_MARKER + "\nunchanged"
        unit = f"basaltwater-agent-tasks-{self.account.pw_uid}.service"
        self._write_worker_definition(unit, content)
        current = self._current_definition(unit).stdout
        for output in (
            current.replace("NeedDaemonReload=no", "NeedDaemonReload=yes"),
            current.replace(str(Path(self.home) / unit), "/another/worker.service"),
            current.replace("LoadState=loaded", "LoadState=not-found"),
            "LoadState=loaded\n", "",
        ):
            with self.subTest(output=output), patch.object(
                steps, "render_task_worker", return_value=content,
            ), patch.object(steps, "_active", return_value=True), patch.object(
                steps, "_worker_busy", return_value=True,
            ), patch.object(steps, "replace_units") as replace, patch.object(
                steps, "run", return_value=SimpleNamespace(returncode=0, stdout=output),
            ) as run, self.assertRaisesRegex(RuntimeError, "active prompt task"):
                steps.configure_agent_task_worker(self.config)
            replace.assert_not_called()
            self.assertEqual([call.args[0][1] for call in run.call_args_list], ["show"])

    def test_worker_metadata_drift_cannot_bypass_busy_refresh_guard(self) -> None:
        content = steps.WORKER_MARKER + "\nunchanged"
        expected = dict(content=content, mode=0o644, uid=os.geteuid(), gid=os.getegid())
        for field, value in (("mode", 0o666), ("uid", os.geteuid() + 1), ("gid", os.getegid() + 1)):
            with self.subTest(field=field), patch.object(
                steps, "render_task_worker", return_value=content,
            ), patch.object(steps, "snapshot_unit_file", return_value={**expected, field: value}), patch.object(
                steps, "_active", return_value=True,
            ), patch.object(steps, "_worker_busy", return_value=True), patch.object(
                steps, "replace_units",
            ) as replace, patch.object(steps, "run") as run, self.assertRaisesRegex(
                RuntimeError, "active prompt task",
            ):
                steps.configure_agent_task_worker(self.config)
            replace.assert_not_called()
            run.assert_not_called()

    def test_unknown_loaded_worker_definition_falls_back_to_idle_replacement(self) -> None:
        content = steps.WORKER_MARKER + "\nunchanged"
        unit = f"basaltwater-agent-tasks-{self.account.pw_uid}.service"
        self._write_worker_definition(unit, content)
        for code, output in ((1, ""), (0, "LoadState=loaded\n"), (0, "unreadable output")):
            with self.subTest(code=code, output=output), patch.object(
                steps, "render_task_worker", return_value=content,
            ), patch.object(steps, "_active", return_value=True), patch.object(
                steps, "_worker_busy", return_value=False,
            ), patch.object(steps, "_panel_handoff", return_value=nullcontext()), patch.object(
                steps, "replace_units",
            ) as replace, patch.object(
                steps, "run", return_value=SimpleNamespace(returncode=code, stdout=output),
            ) as run:
                steps.configure_agent_task_worker(self.config)
            replace.assert_called_once_with({unit: content}, activate=(unit,), unit_dir=self.home)
            self.assertEqual([call.args[0][1] for call in run.call_args_list], ["show"])

    def _legacy_fixture(self, *, running: bool = False) -> tuple[Path, int]:
        """Use real lifetime and state locks, with all service actions mocked."""
        directory = Path(self.home) / "state"
        directory.mkdir(mode=0o700)
        descriptor = os.open(directory / "worker.lock", os.O_CREAT | os.O_RDWR, 0o600)
        self.addCleanup(os.close, descriptor)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        path = directory / "tasks.json"
        path.write_text(json.dumps({"version": 1, "tasks": [], "runs": [{"status": "running"}] if running else []}))
        path.chmod(0o600)
        probe_account = SimpleNamespace(pw_uid=os.geteuid())
        real_busy = steps._worker_busy
        self.enterContext(patch.object(steps, "_worker_busy", side_effect=lambda _account, parent: real_busy(probe_account, parent)))

        @contextmanager
        def guard(_account: object):
            parent = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            state = os.open(directory / "state.lock", os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(state, fcntl.LOCK_EX | fcntl.LOCK_NB)
                yield parent
            finally:
                os.close(state)
                os.close(parent)

        self.enterContext(patch.object(steps, "_refresh_guard", side_effect=guard))
        return path, descriptor

    def _assert_legacy_migration(self, *, running: bool) -> None:
        path, lease = self._legacy_fixture(running=running)
        before = path.read_bytes()
        events = []

        def blocked_claim() -> None:
            descriptor = os.open(path.parent / "state.lock", os.O_RDWR)
            try:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(descriptor)

        def command(args: list[str], **kwargs: object) -> SimpleNamespace:
            events.append(args[1])
            if args[1] in {"stop", "start"}:
                blocked_claim()
            if args[1] == "stop":
                fcntl.flock(lease, fcntl.LOCK_UN)
            return SimpleNamespace(returncode=0, stdout="agent\n")

        def replace(*args: object, **kwargs: object) -> None:
            blocked_claim()
            events.append("activate")
            self.assertEqual(path.read_bytes(), before)

        with patch.object(steps, "_active", return_value=True), patch.object(steps, "run", side_effect=command), patch.object(
            steps, "replace_units", side_effect=replace,
        ):
            steps.configure_agent_task_worker(self.config)
        self.assertEqual(events, ["show", "stop", "activate", "start"])
        self.assertEqual(path.read_bytes(), before)
        descriptor = os.open(path.parent / "state.lock", os.O_RDWR)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(descriptor)

    def test_idle_legacy_migration_stops_before_rechecking_lease_and_keeps_state(self) -> None:
        self._assert_legacy_migration(running=False)

    def test_active_legacy_migration_interrupts_worker_and_keeps_state(self) -> None:
        self._assert_legacy_migration(running=True)

    def test_legacy_migration_restores_panel_on_failure_or_interruption(self) -> None:
        for failure in (RuntimeError("activation failed"), KeyboardInterrupt()):
            with self.subTest(failure=type(failure).__name__), patch.object(
                steps, "_worker_busy", side_effect=[steps._LegacyWorkerHeld(), False],
            ), patch.object(steps, "_active", return_value=True), patch.object(
                steps, "run", return_value=SimpleNamespace(returncode=0, stdout="agent\n"),
            ) as run, patch.object(steps, "replace_units", side_effect=failure), self.assertRaises(type(failure)):
                steps.configure_agent_task_worker(self.config)
            self.assertEqual([call.args[0][1] for call in run.call_args_list], ["show", "stop", "start"])

    def test_legacy_handoff_refuses_an_unreleased_worker_lease(self) -> None:
        path, _lease = self._legacy_fixture()
        before = path.read_bytes()
        with patch.object(steps, "_active", return_value=True), patch.object(
            steps, "run", return_value=SimpleNamespace(returncode=0, stdout="agent\n"),
        ) as run, patch.object(steps, "replace_units") as replace, self.assertRaises(steps._LegacyWorkerHeld):
            steps.configure_agent_task_worker(self.config)
        self.assertEqual([call.args[0][1] for call in run.call_args_list], ["show", "stop", "start"])
        replace.assert_not_called()
        self.assertEqual(path.read_bytes(), before)

    def test_legacy_handoff_preserves_unmanaged_foreign_or_inactive_panels(self) -> None:
        scenarios = (("missing", True, "agent", 0), ("unmanaged", True, "agent", 0),
                     ("managed", False, "agent", 0), ("managed", True, "another-account", 0),
                     ("managed", True, "", 1))
        for kind, active, owner, code in scenarios:
            with self.subTest(kind=kind, active=active, owner=owner, code=code):
                if kind == "missing":
                    self.panel_path.unlink(missing_ok=True)
                else:
                    self.panel_path.write_text((steps.PANEL_MARKER if kind == "managed" else "# Custom panel") + "\n")
                with patch.object(steps, "_worker_busy", side_effect=steps._LegacyWorkerHeld()), patch.object(
                    steps, "_active", return_value=active,
                ), patch.object(steps, "run", return_value=SimpleNamespace(returncode=code, stdout=owner)) as run, patch.object(
                    steps, "replace_units",
                ) as replace, self.assertRaisesRegex(RuntimeError, "not this account's managed panel"):
                    steps.configure_agent_task_worker(self.config)
                self.assertTrue(all(call.args[0][1] == "show" for call in run.call_args_list))
                replace.assert_not_called()


if __name__ == "__main__":
    unittest.main()
