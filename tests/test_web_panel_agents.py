"""Prompt scheduling, execution boundaries, and agent screen regressions."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
import urllib.parse
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from common.service_tools import web_panel_agents as agents
from common.service_tools import web_panel_service as panel
from lib import agent_tasks as tasks


class AgentTaskTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = os.path.realpath(self.temporary.name)
        self.manager = tasks.AgentTasks(self.home)
        self.addCleanup(self.manager.close)
        self.available = patch.object(tasks.AgentTasks, "available", return_value=True)
        self.available.start()
        self.addCleanup(self.available.stop)
        self.tool = patch.object(tasks, "_tool_path", return_value="/test/codex")
        self.tool.start()
        self.addCleanup(self.tool.stop)
        self.values = {"title": "Dependencies", "prompt": "Update dependencies; preserve unrelated work.",
                       "directory": self.home, "mode": "workspace", "interval": "weekly", "network": True, "model": ""}

    def test_schedule_survives_restart_without_replaying_all_missed_intervals(self) -> None:
        identifier = self.manager.create(self.values, run_now=False, now=100)
        restored = tasks.AgentTasks(self.home)
        due = 100 + 604800 * 5 + 20
        with patch.object(tasks, "execute_prompt", return_value={"status": "completed", "output": "Checks passed", "message": "Done"}) as execute:
            self.assertFalse(restored.tick(now=101))
            self.assertTrue(restored.tick(now=due))
            self.assertFalse(restored.tick(now=due))
        execute.assert_called_once()
        snapshot = tasks.AgentTasks(self.home).snapshot()
        self.assertEqual(snapshot["tasks"][0]["id"], identifier)
        self.assertEqual(snapshot["tasks"][0]["next_run"], 100 + 604800 * 6)
        self.assertEqual(snapshot["runs"][0]["status"], "completed")
        self.assertEqual(os.stat(self.manager.path).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(self.manager.parent).st_mode & 0o777, 0o700)

    def test_manual_run_pause_resume_and_edit_keep_history_and_deadline(self) -> None:
        identifier = self.manager.create(self.values, run_now=True, now=100)
        with patch.object(tasks, "execute_prompt", return_value={"status": "completed", "output": "Done", "message": "Done"}):
            self.assertTrue(self.manager.tick(now=101))
            self.assertFalse(self.manager.tick(now=102))
            self.manager.action(identifier, "pause")
            self.assertFalse(self.manager.tick(now=9999999))
            self.manager.update(identifier, {**self.values, "prompt": "Updated prompt"})
            self.assertFalse(self.manager.snapshot()["tasks"][0]["enabled"])
            self.manager.action(identifier, "resume", now=200)
        snapshot = self.manager.snapshot()
        self.assertEqual(snapshot["tasks"][0]["next_run"], 200 + 604800)
        self.assertEqual(snapshot["runs"][0]["task"]["prompt"], self.values["prompt"])
        self.manager.action(identifier, "delete")
        self.assertEqual(self.manager.snapshot()["tasks"], [])
        self.assertEqual(len(self.manager.snapshot()["runs"]), 1)

    def test_one_time_prompt_runs_once_and_cannot_become_a_schedule_by_resume(self) -> None:
        identifier = self.manager.create({**self.values, "interval": "once"}, run_now=True)
        with patch.object(tasks, "execute_prompt", return_value={"status": "failed", "output": "Blocked", "message": "Denied"}):
            self.assertTrue(self.manager.tick())
            self.assertFalse(self.manager.tick(now=9999999999))
        with self.assertRaises(ValueError):
            self.manager.action(identifier, "resume")
        with self.assertRaises(ValueError):
            self.manager.create({**self.values, "interval": "once"}, run_now=False)

    def test_drafts_stay_inactive_across_restart_until_explicitly_started(self) -> None:
        once = self.manager.create({**self.values, "interval": "once"}, run_now=False, draft=True, now=100)
        repeating = self.manager.create(self.values, run_now=False, draft=True, now=100)
        restored = tasks.AgentTasks(self.home)
        for task in restored.snapshot()["tasks"]:
            self.assertTrue(task["draft"])
            self.assertFalse(task["enabled"])
            self.assertFalse(task["queued"])
            self.assertIsNone(task["next_run"])
        with patch.object(tasks, "execute_prompt", return_value={"status": "completed", "output": "", "message": "Done"}) as execute:
            self.assertFalse(restored.tick(now=99999999))
            restored.action(once, "run")
            self.assertTrue(restored.tick(now=200))
        execute.assert_called_once()
        restored.action(repeating, "resume", now=200)
        task = restored.snapshot()["tasks"][1]
        self.assertTrue(task["enabled"])
        self.assertFalse(task["draft"])
        self.assertEqual(task["next_run"], 200 + 604800)

    def test_draft_can_be_saved_before_codex_installation_but_never_queued_on_save(self) -> None:
        with patch.object(tasks, "_tool_path", return_value=None), patch.object(tasks, "execute_prompt") as execute:
            self.manager.create(self.values, run_now=False, draft=True)
            self.assertFalse(self.manager.tick(now=9999999999))
            with self.assertRaises(ValueError):
                self.manager.create(self.values, run_now=True, draft=True)
        execute.assert_not_called()

    def test_failure_limit_pauses_schedule_and_persists_beyond_history_retention(self) -> None:
        identifier = self.manager.create({**self.values, "failure_limit": 3}, run_now=True, now=0)
        manager = self.manager
        with patch.object(tasks, "MAX_RUNS", 1), patch.object(tasks, "execute_prompt", return_value={"status": "failed", "output": "", "message": "Denied"}):
            for count, now in enumerate((1, 604800, 1209600), start=1):
                self.assertTrue(manager.tick(now=now))
                manager = tasks.AgentTasks(self.home)
                task = manager.snapshot()["tasks"][0]
                self.assertEqual(task["consecutive_failures"], count)
                self.assertEqual(task["enabled"], count < 3)
            self.assertTrue(task["auto_paused"])
            self.assertFalse(manager.tick(now=9999999))
        self.assertEqual(len(manager.snapshot()["runs"]), 1)
        manager.action(identifier, "resume", now=2000000)
        task = manager.snapshot()["tasks"][0]
        self.assertFalse(task["auto_paused"])
        self.assertEqual(task["consecutive_failures"], 0)
        self.assertEqual(task["next_run"], 2000000 + 604800)

    def test_cancellation_preserves_failure_streak_and_completion_resets_it(self) -> None:
        identifier = self.manager.create(self.values, run_now=True, now=0)
        for status, count in (("failed", 1), ("cancelled", 1), ("completed", 0), ("failed", 1)):
            with self.subTest(status=status), patch.object(tasks, "execute_prompt", return_value={"status": status, "output": "", "message": status}):
                self.assertTrue(self.manager.tick(now=1))
                task = self.manager.snapshot()["tasks"][0]
                self.assertEqual(task["consecutive_failures"], count)
                self.assertTrue(task["enabled"])
                self.manager.action(identifier, "run")

    def test_manual_recovery_run_does_not_reenable_an_automatically_paused_schedule(self) -> None:
        identifier = self.manager.create({**self.values, "failure_limit": 1}, run_now=True, now=0)
        with patch.object(tasks, "execute_prompt", return_value={"status": "failed", "output": "", "message": "Blocked"}):
            self.manager.tick(now=1)
        self.manager.action(identifier, "run")
        with patch.object(tasks, "execute_prompt", return_value={"status": "completed", "output": "", "message": "Recovered"}):
            self.manager.tick(now=2)
        task = self.manager.snapshot()["tasks"][0]
        self.assertFalse(task["enabled"])
        self.assertFalse(task["auto_paused"])
        self.assertEqual(task["consecutive_failures"], 0)

    def test_disabled_failure_limit_keeps_repeating_and_manual_pause_is_preserved(self) -> None:
        identifier = self.manager.create({**self.values, "failure_limit": 0}, run_now=True, now=0)
        with patch.object(tasks, "execute_prompt", return_value={"status": "failed", "output": "", "message": "Blocked"}):
            for now in (1, 604800, 1209600, 1814400):
                self.assertTrue(self.manager.tick(now=now))
        self.assertTrue(self.manager.snapshot()["tasks"][0]["enabled"])
        self.manager.update(identifier, {**self.values, "failure_limit": 1})
        self.manager.action(identifier, "run")
        def pause_during_run(*_args: object) -> dict[str, str]:
            self.manager.action(identifier, "pause")
            return {"status": "failed", "output": "", "message": "Blocked"}
        with patch.object(tasks, "execute_prompt", side_effect=pause_during_run):
            self.manager.tick(now=1814401)
        task = self.manager.snapshot()["tasks"][0]
        self.assertFalse(task["enabled"])
        self.assertFalse(task["auto_paused"])

    def test_restart_interruption_counts_once_and_can_pause_a_schedule(self) -> None:
        self.manager.create({**self.values, "failure_limit": 1}, run_now=True, now=0)
        with patch.object(tasks, "execute_prompt", return_value={"status": "running", "output": "", "message": "Running"}):
            self.manager.tick(now=1)
        restored = tasks.AgentTasks(self.home)
        self.addCleanup(restored.close)
        with patch.object(tasks.threading, "Thread"):
            restored.start()
        task = restored.snapshot()["tasks"][0]
        self.assertTrue(task["auto_paused"])
        self.assertFalse(task["enabled"])
        self.assertEqual(task["consecutive_failures"], 1)
        restored.close()
        another = tasks.AgentTasks(self.home)
        self.addCleanup(another.close)
        with patch.object(tasks.threading, "Thread"):
            another.start()
        self.assertEqual(another.snapshot()["tasks"][0]["consecutive_failures"], 1)

    def test_run_duration_uses_monotonic_time_and_survives_reload(self) -> None:
        self.manager.create(self.values, run_now=True, now=100)
        with patch.object(tasks.time, "monotonic", side_effect=(10, 135)), patch.object(tasks.time, "time", return_value=50), patch.object(
            tasks, "execute_prompt", return_value={"status": "completed", "output": "", "message": "Done", "exit_code": 0},
        ):
            self.manager.tick(now=100)
        run = tasks.AgentTasks(self.home).snapshot()["runs"][0]
        self.assertEqual(run["duration_seconds"], 125)
        self.assertEqual(run["exit_code"], 0)
        self.assertLess(run["finished_at"], run["started_at"])

    def test_older_state_receives_policy_defaults_without_reclassifying_past_runs(self) -> None:
        self.manager.create(self.values, run_now=True)
        with patch.object(tasks, "execute_prompt", return_value={"status": "failed", "output": "", "message": "Blocked"}):
            self.manager.tick()
        value = self.manager.snapshot()
        for key in ("failure_limit", "consecutive_failures", "auto_paused", "draft"):
            value["tasks"][0].pop(key)
        value["runs"][0]["task"].pop("failure_limit")
        value["runs"][0].pop("duration_seconds")
        self.manager.path.write_text(json.dumps(value))
        restored = tasks.AgentTasks(self.home).snapshot()
        self.assertEqual(restored["tasks"][0]["failure_limit"], 3)
        self.assertEqual(restored["tasks"][0]["consecutive_failures"], 0)
        self.assertTrue(restored["tasks"][0]["enabled"])
        self.assertNotIn("duration_seconds", restored["runs"][0])

    def test_invalid_persisted_failure_and_draft_state_is_rejected(self) -> None:
        self.manager.create(self.values, run_now=False)
        original = self.manager.snapshot()
        for changes in ({"consecutive_failures": -1}, {"consecutive_failures": True},
                        {"auto_paused": True}, {"draft": True}, {"draft": "yes"}):
            with self.subTest(changes=changes):
                value = json.loads(json.dumps(original))
                value["tasks"][0].update(changes)
                self.manager.path.write_text(json.dumps(value))
                with self.assertRaises(RuntimeError):
                    tasks.AgentTasks(self.home).snapshot()

    def test_run_now_requests_do_not_duplicate_and_take_priority(self) -> None:
        repeating = self.manager.create(self.values, run_now=False, now=0)
        manual = self.manager.create({**self.values, "interval": "once", "title": "Manual"}, run_now=True)
        with self.assertRaises(ValueError):
            self.manager.action(manual, "run")
        with patch.object(tasks, "execute_prompt", return_value={"status": "completed", "output": "", "message": "Done"}) as execute:
            self.manager.tick(now=604800)
        self.assertEqual(execute.call_args.args[0]["id"], manual)
        self.manager.action(repeating, "run")
        self.manager.action(repeating, "cancel")
        self.assertFalse(self.manager.snapshot()["tasks"][0]["queued"])

    def test_concurrent_tick_cannot_launch_a_second_prompt(self) -> None:
        identifier = self.manager.create(self.values, run_now=True)
        def execute(_task: object, _home: str, _cancel: threading.Event) -> dict[str, str]:
            self.assertFalse(self.manager.tick())
            with self.assertRaises(ValueError):
                self.manager.action(identifier, "run")
            with self.assertRaises(ValueError):
                self.manager.action(identifier, "delete")
            self.manager.action(identifier, "cancel")
            self.assertTrue(_cancel.is_set())
            return {"status": "cancelled", "output": "", "message": "Cancelled"}
        with patch.object(tasks, "execute_prompt", side_effect=execute):
            self.manager.tick()
        self.assertEqual(self.manager.snapshot()["runs"][0]["status"], "cancelled")

    def test_restart_marks_active_runs_interrupted_and_worker_lock_prevents_duplicates(self) -> None:
        self.manager.create(self.values, run_now=True)
        with patch.object(tasks, "execute_prompt", return_value={"status": "running", "output": "", "message": "Running"}):
            self.manager.tick()
        restored = tasks.AgentTasks(self.home)
        other = tasks.AgentTasks(self.home)
        self.addCleanup(restored.close)
        self.addCleanup(other.close)
        with patch.object(tasks.threading, "Thread"):
            restored.start()
            other.start()
        snapshot = restored.snapshot()
        self.assertEqual(snapshot["runs"][0]["status"], "interrupted")
        self.assertFalse(snapshot["tasks"][0]["queued"])
        self.assertIn("already running", other.error)

    def test_unsafe_or_corrupt_storage_is_rejected_without_overwrite(self) -> None:
        self.manager.create(self.values, run_now=False)
        self.manager.path.chmod(0o644)
        with self.assertRaises(RuntimeError):
            tasks.AgentTasks(self.home).snapshot()
        self.manager.path.chmod(0o600)
        for invalid in ({}, [], None):
            self.manager.path.write_text(json.dumps({"version": 1, "tasks": [invalid], "runs": []}), encoding="utf-8")
            original = self.manager.path.read_text()
            with self.assertRaises(RuntimeError):
                tasks.AgentTasks(self.home).create(self.values, run_now=True)
            self.assertEqual(self.manager.path.read_text(), original)
        self.manager.path.unlink()
        target = Path(self.home) / "untouched"
        target.write_text("private")
        self.manager.path.symlink_to(target)
        with self.assertRaises(OSError):
            tasks.AgentTasks(self.home).snapshot()
        self.assertEqual(target.read_text(), "private")

    def test_failed_write_does_not_leave_a_runnable_unsaved_prompt(self) -> None:
        with patch.object(tasks, "write_text_atomic", side_effect=OSError("Full disk")):
            with self.assertRaises(OSError):
                self.manager.create(self.values, run_now=True)
        self.assertEqual(self.manager.snapshot()["tasks"], [])

    def test_prompt_settings_reject_unsafe_modes_paths_models_and_unbounded_text(self) -> None:
        for changes in (
            {"mode": "root"}, {"mode": "inspect", "network": True}, {"interval": "cron"},
            {"directory": "relative/path"}, {"directory": "/tmp", "mode": "workspace"},
            {"model": "--dangerously-bypass-approvals-and-sandbox"}, {"title": "bad\nname"},
            {"prompt": "é" * 3000}, {"network": "true"}, {"directory": self.home + "/missing"},
            {"timeout_minutes": "0"}, {"timeout_minutes": 10081}, {"timeout_minutes": True},
            {"timeout_minutes": "1.5"}, {"timeout_minutes": "-1"}, {"effort": "unlimited"},
            {"web_search": "shell"}, {"session_history": "true"}, {"temporary_files": "true"},
            {"mode": "inspect", "temporary_files": True},
            {"failure_limit": -1}, {"failure_limit": 11}, {"failure_limit": True},
            {"failure_limit": "1.5"}, {"failure_limit": "unlimited"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                tasks.validate_task({**self.values, **changes}, self.home)
        with tempfile.TemporaryDirectory() as outside:
            link = Path(self.home) / "link"
            link.symlink_to(outside)
            with self.assertRaises(ValueError):
                tasks.validate_task({**self.values, "directory": str(link)}, self.home)
        result = tasks.validate_task({**self.values, "directory": "~"}, self.home)
        self.assertEqual(result["directory"], self.home)

    def test_runtime_and_original_model_settings_survive_restart_and_edit(self) -> None:
        settings = {**self.values, "timeout_minutes": "10080", "model": "test-model", "effort": "high",
                    "web_search": "cached", "session_history": True}
        identifier = self.manager.create(settings, run_now=True, now=100)
        restored = tasks.AgentTasks(self.home)
        with patch.object(tasks, "execute_prompt", return_value={"status": "completed", "output": "", "message": "Done"}) as execute:
            restored.tick(now=101)
        self.assertEqual(execute.call_args.args[0]["timeout_minutes"], 10080)
        restored.update(identifier, {**settings, "timeout_minutes": 5, "effort": "low"})
        snapshot = tasks.AgentTasks(self.home).snapshot()
        self.assertEqual(snapshot["tasks"][0]["timeout_minutes"], 5)
        self.assertEqual(snapshot["runs"][0]["task"]["timeout_minutes"], 10080)
        self.assertEqual(snapshot["runs"][0]["task"]["effort"], "high")

    def test_model_cache_filters_private_metadata_and_validates_known_efforts(self) -> None:
        cache = Path(self.home) / ".codex/models_cache.json"
        cache.parent.mkdir()
        cache.write_text(json.dumps({"identity": "private-account-data", "models": [
            {"slug": "test-model", "display_name": "Test model", "visibility": "list",
             "supported_reasoning_levels": [{"effort": "low"}, {"effort": "high"}]},
            {"slug": "hidden", "display_name": "Hidden", "visibility": "hide"},
            {"slug": "--bad", "visibility": "list"},
        ]}))
        models = tasks.codex_models(self.home)
        self.assertEqual(models, [{"slug": "test-model", "name": "Test model", "efforts": ["low", "high"]}])
        with self.assertRaisesRegex(ValueError, "low, high"):
            self.manager.create({**self.values, "model": "test-model", "effort": "ultra"}, run_now=True)
        self.manager.create({**self.values, "model": "test-model", "effort": "high"}, run_now=True)
        cache.write_text("broken")
        self.assertEqual(tasks.codex_models(self.home), [])
        self.assertEqual(tasks.AgentTasks(self.home).snapshot()["tasks"][0]["model"], "test-model")

    def test_model_cache_rejects_symlinks_oversized_files_and_nonregular_files(self) -> None:
        cache = Path(self.home) / ".codex/models_cache.json"
        cache.parent.mkdir()
        target = Path(self.home) / "cache-target"
        target.write_text('{"models": []}')
        cache.symlink_to(target)
        self.assertEqual(tasks.codex_models(self.home), [])
        cache.unlink()
        cache.write_bytes(b" " * (tasks.MAX_STATE_BYTES + 1))
        self.assertEqual(tasks.codex_models(self.home), [])
        cache.unlink()
        os.mkfifo(cache)
        self.assertEqual(tasks.codex_models(self.home), [])

    def test_command_pins_permissions_effort_search_and_session_retention(self) -> None:
        task = tasks.validate_task({**self.values, "model": "test-model", "effort": "high", "web_search": "live"}, self.home)
        command = tasks.codex_command(task, "/test/codex")
        self.assertIn('model_reasoning_effort="high"', command)
        self.assertIn('web_search="live"', command)
        self.assertIn('approval_policy="never"', command)
        self.assertIn("sandbox_workspace_write.writable_roots=[]", command)
        self.assertIn("sandbox_workspace_write.exclude_slash_tmp=true", command)
        self.assertIn("--ephemeral", command)
        self.assertEqual(command[command.index("--model") + 1], "test-model")
        self.assertNotIn("--ephemeral", tasks.codex_command({**task, "session_history": True}, "/test/codex"))
        self.assertIn("sandbox_workspace_write.exclude_slash_tmp=false", tasks.codex_command({**task, "temporary_files": True}, "/test/codex"))

    def test_prompt_is_stdin_and_workspace_network_permission_is_explicit(self) -> None:
        process = MagicMock()
        process.__enter__.return_value = process
        process.pid, process.wait.return_value = 1234, 0
        key = SimpleNamespace(fd=99, fileobj=process.stdout)
        selector = MagicMock()
        selector.__enter__.return_value = selector
        selector.get_map.side_effect = [{99: key}, {}]
        selector.select.return_value = [(key, 1)]
        with (
            patch.object(tasks.os, "geteuid", return_value=1000),
            patch.object(tasks.subprocess, "Popen", return_value=process) as spawn,
            patch.object(tasks.selectors, "DefaultSelector", return_value=selector),
            patch.object(tasks.os, "read", return_value=b"Validation passed"),
            patch.object(tasks.os, "killpg") as kill,
        ):
            result = tasks.execute_prompt(self.values, self.home, threading.Event())
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["output"], "Validation passed")
        command = spawn.call_args.args[0]
        self.assertNotIn(self.values["prompt"], command)
        self.assertEqual(command[-1], "-")
        self.assertIn("sandbox_workspace_write.network_access=true", command)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", command)
        self.assertIn(self.values["prompt"].encode(), process.stdin.write.call_args.args[0])
        self.assertTrue(spawn.call_args.kwargs["start_new_session"])
        kill.assert_called_with(1234, tasks.signal.SIGKILL)

    def test_cancel_and_timeout_terminate_the_process_group(self) -> None:
        for cancelled in (True, False):
            with self.subTest(cancelled=cancelled):
                process, selector = MagicMock(), MagicMock()
                process.__enter__.return_value = process
                selector.__enter__.return_value = selector
                selector.get_map.return_value = {1: object()}
                process.pid, process.wait.return_value = 1234, -9
                cancel = threading.Event()
                if cancelled:
                    cancel.set()
                with (
                    patch.object(tasks.os, "geteuid", return_value=1000),
                    patch.object(tasks.subprocess, "Popen", return_value=process),
                    patch.object(tasks.selectors, "DefaultSelector", return_value=selector),
                    patch.object(tasks.time, "monotonic", side_effect=[0, 9999, 9999]),
                    patch.object(tasks.os, "killpg") as kill,
                ):
                    result = tasks.execute_prompt(self.values, self.home, cancel)
                self.assertEqual(result["status"], "cancelled" if cancelled else "failed")
                kill.assert_called_with(1234, tasks.signal.SIGKILL)

    def test_excessive_output_is_stopped_and_only_a_bounded_tail_is_retained(self) -> None:
        process, selector = MagicMock(), MagicMock()
        process.__enter__.return_value = process
        selector.__enter__.return_value = selector
        process.pid, process.wait.return_value = 1234, -9
        key = SimpleNamespace(fd=99, fileobj=process.stdout)
        selector.get_map.return_value = {99: key}
        selector.select.return_value = [(key, 1)]
        with (
            patch.object(tasks.os, "geteuid", return_value=1000),
            patch.object(tasks.subprocess, "Popen", return_value=process),
            patch.object(tasks.selectors, "DefaultSelector", return_value=selector),
            patch.object(tasks.os, "read", return_value=b"x" * 4096),
            patch.object(tasks, "MAX_STREAM_BYTES", 8192),
            patch.object(tasks.os, "killpg") as kill,
        ):
            result = tasks.execute_prompt(self.values, self.home, threading.Event())
        self.assertEqual(result["status"], "failed")
        self.assertIn("output limit", result["message"])
        self.assertTrue(result["output"].startswith("… earlier output omitted …"))
        self.assertLessEqual(len(result["output"].encode()), tasks.MAX_OUTPUT_BYTES + 128)
        kill.assert_called_with(1234, tasks.signal.SIGKILL)

    def test_long_runtime_allows_waiting_beyond_default_even_after_stdout_closes(self) -> None:
        process, selector = MagicMock(), MagicMock()
        process.__enter__.return_value = process
        selector.__enter__.return_value = selector
        selector.get_map.return_value = {}
        process.pid = 1234
        with (
            patch.object(tasks.os, "geteuid", return_value=1000),
            patch.object(tasks.subprocess, "Popen", return_value=process),
            patch.object(tasks.selectors, "DefaultSelector", return_value=selector),
            patch.object(tasks.time, "monotonic", side_effect=[0, 4000, 7201]),
            patch.object(tasks.os, "killpg") as kill,
        ):
            def wait(timeout: float) -> int:
                if process.wait.call_count == 1:
                    kill.assert_not_called()
                    raise tasks.subprocess.TimeoutExpired("codex", timeout)
                return -9
            process.wait.side_effect = wait
            result = tasks.execute_prompt({**self.values, "timeout_minutes": 120}, self.home, threading.Event())
        self.assertEqual(result["status"], "failed")
        self.assertIn("120-minute limit", result["message"])
        kill.assert_called_with(1234, tasks.signal.SIGKILL)

    def test_short_runtime_is_enforced_and_malformed_output_remains_bounded(self) -> None:
        process, selector = MagicMock(), MagicMock()
        process.__enter__.return_value = process
        selector.__enter__.return_value = selector
        process.pid, process.wait.return_value = 1234, -9
        key = SimpleNamespace(fd=99, fileobj=process.stdout)
        selector.get_map.return_value = {99: key}
        selector.select.return_value = [(key, 1)]
        with (
            patch.object(tasks.os, "geteuid", return_value=1000),
            patch.object(tasks.subprocess, "Popen", return_value=process),
            patch.object(tasks.selectors, "DefaultSelector", return_value=selector),
            patch.object(tasks.time, "monotonic", side_effect=[0, 59, 61, 61]),
            patch.object(tasks.os, "read", return_value=b"\xff" * 4096),
            patch.object(tasks.os, "killpg"),
        ):
            result = tasks.execute_prompt({**self.values, "timeout_minutes": 1}, self.home, threading.Event())
        self.assertEqual(result["status"], "failed")
        self.assertIn("1-minute limit", result["message"])
        self.assertLessEqual(len(result["output"].encode()), tasks.MAX_OUTPUT_BYTES + 128)

    def test_root_execution_is_blocked_and_missing_codex_fails_explicitly(self) -> None:
        with patch.object(tasks.os, "geteuid", return_value=0), patch.object(tasks.subprocess, "Popen") as spawn:
            with self.assertRaises(RuntimeError):
                tasks.execute_prompt(self.values, self.home, threading.Event())
        spawn.assert_not_called()
        with patch.object(tasks, "_tool_path", return_value=None):
            with self.assertRaises(ValueError):
                self.manager.create(self.values, run_now=True)


class AgentScreenTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.state = panel.WebPanelState({"host": "example.test", "username": "agent", "system_type": "agent_vm",
                                         "features": {}, "services": [], "access": []}, agent_home=self.temporary.name)
        for target, attribute, value in ((tasks.AgentTasks, "available", True), (tasks, "_tool_path", "/test/codex")):
            mocked = patch.object(target, attribute, return_value=value)
            mocked.start()
            self.addCleanup(mocked.stop)
        tools_mock = patch.object(agents, "_tool_path", side_effect=lambda name, home: "/test/codex" if name == "codex" else None)
        tools_mock.start()
        self.addCleanup(tools_mock.stop)

    def handler(self, path: str, values: dict[str, str] | str) -> panel.WebPanelHandler:
        handler = object.__new__(panel.WebPanelHandler)
        handler.state, handler.path = self.state, path
        body = (urllib.parse.urlencode(values) if isinstance(values, dict) else values).encode()
        handler.headers = {"Content-Length": str(len(body))}
        handler.rfile = BytesIO(body)
        for name in ("_send", "send_response", "send_header", "end_headers"):
            setattr(handler, name, Mock())
        return handler

    def test_screen_works_without_t3_and_rendering_never_launches_checks_or_prompts(self) -> None:
        with patch.object(self.state.agent_diagnostics, "trigger") as checks, patch.object(tasks, "execute_prompt") as execute:
            page = agents.render_agents(self.state, panel._PAGE_STYLE, {"template": "dependencies"})
        checks.assert_not_called()
        execute.assert_not_called()
        self.assertIn('href="/agents" aria-current="page"', page)
        self.assertIn("Update repository dependencies", page)
        self.assertIn("Create schedule", page)
        self.assertNotIn("Update T3 Code</button>", page)
        self.assertIn("read-only sandbox", page)

    def test_host_templates_prefill_home_and_repository_templates_require_a_checkout(self) -> None:
        host_templates = {"maintenance", "backups", "storage", "incident", "certificates"}
        with patch.object(tasks, "execute_prompt") as execute, patch.object(self.state.agent_diagnostics, "trigger") as checks:
            for key in agents._available_templates(self.state.manifest, self.temporary.name):
                with self.subTest(template=key):
                    query = agents.parse_agent_query("template=" + key)
                    page = agents.render_agents(self.state, panel._PAGE_STYLE, query)
                    directory = self.temporary.name if key in host_templates else ""
                    self.assertIn(f'name="directory" value="{directory}"', page)
                    self.assertIn(f'href="/agents?template={key}" aria-current="true"', page)
        execute.assert_not_called()
        checks.assert_not_called()
        self.assertEqual(self.state.agent_tasks.snapshot()["tasks"], [])

    def test_optional_templates_are_hidden_without_features_or_commands(self) -> None:
        self.state.manifest.update({"services": [None, "Gogs", {"label": []}],
                                    "access": [{"label": None}],
                                    "features": {"t3_update": False, "notification_ingest": "true"}})
        with patch.object(agents, "inspect_agent_tools") as inspect_tools, patch.object(agents, "inspect_t3code") as inspect_t3:
            page = agents.render_agents(self.state, panel._PAGE_STYLE, {})
        inspect_tools.assert_not_called()
        inspect_t3.assert_not_called()
        self.assertIn("Repository work · 7 templates", page)
        self.assertIn("Host checks · 5 templates", page)
        for key, template in agents.PROMPT_TEMPLATES.items():
            if "requires" in template:
                self.assertNotIn(f'href="/agents?template={key}"', page)

    def test_optional_templates_follow_specific_configuration_and_installed_commands(self) -> None:
        cases = (
            ("t3-readiness", {"features": {"t3_update": True}}, (), "T3 Code configured"),
            ("containers", {}, ("docker",), "Container CLI detected"),
            ("containers", {}, ("podman",), "Container CLI detected"),
            ("web-hosting", {"services": [{"label": "Web server"}]}, (), "Web server configured"),
            ("web-hosting", {}, ("nginx",), "Web hosting command detected"),
            ("web-hosting", {}, ("basaltwater-web",), "Web hosting command detected"),
            ("shared-storage", {"access": [{"label": "Samba / SMB"}]}, (), "Samba configured"),
            ("shared-storage", {}, ("smbd",), "Shared storage command detected"),
            ("shared-storage", {}, ("mount.cifs",), "Shared storage command detected"),
            ("shared-storage", {}, ("sshfs",), "Shared storage command detected"),
            ("remote-desktop", {"access": [{"label": "Remote desktop"}]}, (), "Remote desktop configured"),
            ("remote-desktop", {}, ("xrdp",), "xrdp command detected"),
            ("github-actions", {}, ("gh",), "GitHub CLI detected"),
            ("browser-smoke", {}, ("basaltwater-playwright-mcp",), "Managed browser launcher detected"),
            ("godot-export", {}, ("godot",), "Godot command detected"),
            ("godot-export", {}, ("godot4",), "Godot command detected"),
            ("homebox-health", {"services": [{"label": "HomeBox"}]}, (), "HomeBox configured"),
            ("gogs-health", {"services": [{"label": "Gogs"}]}, (), "Gogs configured"),
            ("gogs-health", {}, ("gogs",), "Gogs command detected"),
            ("antistatic-health", {"services": [{"label": "Antistatic lobby"}]}, (), "Antistatic configured"),
            ("antistatic-health", {"services": [{"label": "Antistatic DB"}]}, (), "Antistatic configured"),
            ("notifications-health", {"features": {"notification_ingest": True}}, (), "Notification ingest enabled"),
            ("privilege-health", {"services": [{"label": "Privilege approvals"}]}, (), "Privilege approvals configured"),
        )
        original = self.state.manifest.copy()
        for key, configuration, commands, reason in cases:
            with self.subTest(template=key, configuration=configuration, commands=commands), patch.object(
                agents, "_tool_path", side_effect=lambda name, home: f"/test/{name}" if name in (*commands, "codex") else None,
            ), patch.object(tasks.subprocess, "Popen") as spawn:
                self.state.manifest = {**original, **configuration}
                templates = agents._available_templates(self.state.manifest, self.temporary.name)
                self.assertEqual({key for key, template in templates.items() if "requires" in template}, {key})
                self.assertEqual(templates[key]["availability"], reason)
                page = agents.render_agents(self.state, panel._PAGE_STYLE, agents.parse_agent_query("template=" + key))
                self.assertIn(f'href="/agents?template={key}" aria-current="true"', page)
                self.assertIn(reason, page)
                directory = self.temporary.name if templates[key]["scope"] == "host" else ""
                self.assertIn(f'name="directory" value="{directory}"', page)
                self.assertEqual(self.state.agent_tasks.snapshot()["tasks"], [])
                spawn.assert_not_called()

    def test_t3_runtime_enables_template_without_a_manifest_flag(self) -> None:
        runtime = Path(self.temporary.name) / ".t3/runtime"
        runtime.mkdir(parents=True)
        page = agents.render_agents(self.state, panel._PAGE_STYLE, {"template": "t3-readiness"})
        self.assertIn("T3 Code runtime detected", page)
        self.assertIn('href="/agents?template=t3-readiness" aria-current="true"', page)
        runtime.rmdir()
        self.assertNotIn("t3-readiness", agents._available_templates(self.state.manifest, self.temporary.name))

    def test_unavailable_template_link_shows_error_without_prefilling_or_disabling_custom_work(self) -> None:
        query = agents.parse_agent_query("template=containers")
        page = agents.render_agents(self.state, panel._PAGE_STYLE, query)
        self.assertIn('role="alert"', page)
        self.assertIn("This template is unavailable", page)
        self.assertIn('name="title" value=""', page)
        self.assertNotIn(agents.PROMPT_TEMPLATES["containers"]["title"], page)
        self.assertNotIn('value="run" disabled', page)
        self.assertEqual(self.state.agent_tasks.snapshot()["tasks"], [])

    def test_saved_conditional_task_remains_editable_after_software_is_removed(self) -> None:
        template = agents.PROMPT_TEMPLATES["containers"]
        identifier = self.state.agent_tasks.create({**template, "directory": self.temporary.name}, run_now=False)
        before = self.state.agent_tasks.snapshot()
        page = agents.render_agents(self.state, panel._PAGE_STYLE, {"edit": identifier})
        self.assertIn(f'name="title" value="{template["title"]}"', page)
        self.assertIn("Save changes</button>", page)
        self.assertNotIn('href="/agents?template=containers"', page)
        self.assertEqual(self.state.agent_tasks.snapshot(), before)

    def test_new_template_defaults_are_visible_and_save_with_matching_permissions(self) -> None:
        self.state.manifest.update({"features": {"t3_update": True, "notification_ingest": True},
                                    "services": [{"label": label} for label in ("HomeBox", "Gogs", "Antistatic DB", "Privilege approvals")]})
        installed = patch.object(agents, "_tool_path", return_value="/test/tool")
        installed.start()
        self.addCleanup(installed.stop)
        cases = (
            ("ci-repair", "workspace", "once", 60, False, True, "disabled"),
            ("regression-tests", "workspace", "once", 60, False, True, "disabled"),
            ("documentation", "workspace", "weekly", 30, False, False, "disabled"),
            ("security-review", "inspect", "weekly", 30, False, False, "live"),
            ("release-review", "inspect", "once", 20, False, False, "disabled"),
            ("backups", "inspect", "daily", 15, False, False, "disabled"),
            ("storage", "inspect", "weekly", 15, False, False, "disabled"),
            ("incident", "inspect", "once", 20, False, False, "disabled"),
            ("certificates", "inspect", "daily", 10, False, False, "disabled"),
            ("t3-readiness", "inspect", "daily", 15, False, False, "disabled"),
            ("containers", "inspect", "daily", 15, False, False, "disabled"),
            ("web-hosting", "inspect", "daily", 15, False, False, "disabled"),
            ("shared-storage", "inspect", "daily", 15, False, False, "disabled"),
            ("remote-desktop", "inspect", "daily", 10, False, False, "disabled"),
            ("github-actions", "workspace", "daily", 20, True, False, "disabled"),
            ("browser-smoke", "workspace", "once", 30, True, True, "disabled"),
            ("godot-export", "workspace", "once", 60, False, True, "disabled"),
            ("homebox-health", "inspect", "daily", 15, False, False, "disabled"),
            ("gogs-health", "inspect", "daily", 15, False, False, "disabled"),
            ("antistatic-health", "inspect", "daily", 15, False, False, "disabled"),
            ("notifications-health", "inspect", "daily", 10, False, False, "disabled"),
            ("privilege-health", "inspect", "weekly", 15, False, False, "disabled"),
        )
        for key, mode, interval, runtime, network, temporary_files, web_search in cases:
            with self.subTest(template=key):
                template = agents.PROMPT_TEMPLATES[key]
                page = agents.render_agents(self.state, panel._PAGE_STYLE, {"template": key})
                self.assertIn(f'<option value="{mode}" selected>', page)
                self.assertIn(f'<option value="{interval}" selected>', page)
                self.assertIn(f'name="timeout_minutes" min="1" max="10080" step="1" value="{runtime}"', page)
                self.assertIn(f'<option value="{web_search}" selected>', page)
                self.assertIn('<details class="agent-template-group" open>', page)
                values = {"csrf": self.state.csrf_token, "title": template["title"], "prompt": template["prompt"],
                          "directory": self.temporary.name, "mode": template["mode"], "interval": template["interval"],
                          "timeout_minutes": str(template["timeout_minutes"]), "web_search": template.get("web_search", "disabled"),
                          "submit": "run" if interval == "once" else "schedule"}
                if template["network"]:
                    values["network"] = "1"
                if template.get("temporary_files"):
                    values["temporary_files"] = "1"
                handler = self.handler("/actions/agent-task/save", values)
                handler.do_POST()
                handler.send_response.assert_called_with(303)
                saved = self.state.agent_tasks.snapshot()["tasks"][-1]
                self.assertEqual((saved["mode"], saved["interval"], saved["timeout_minutes"], saved["network"],
                                  saved["temporary_files"], saved["web_search"]),
                                 (mode, interval, runtime, network, temporary_files, web_search))
                self.assertEqual(saved["queued"], interval == "once")
        self.assertIn("Repository work · 10 templates", page)
        self.assertIn("Host checks · 15 templates", page)
        self.assertIn("20 min cap · command network", page)
        self.assertIn("30 min cap · command network · temporary writes", page)

    def test_form_creates_schedule_after_csrf_and_validation_then_redirects(self) -> None:
        values = {"csrf": self.state.csrf_token, "title": "Review", "prompt": "Inspect this host",
                  "directory": self.temporary.name, "mode": "inspect", "interval": "daily", "submit": "schedule"}
        handler = self.handler("/actions/agent-task/save", values)
        with patch.object(tasks, "execute_prompt") as execute:
            handler.do_POST()
        execute.assert_not_called()
        handler.send_response.assert_called_with(303)
        handler.send_header.assert_any_call("Location", "/agents")
        snapshot = self.state.agent_tasks.snapshot()
        self.assertTrue(snapshot["tasks"][0]["enabled"])
        self.assertFalse(snapshot["tasks"][0]["queued"])

    def test_save_draft_accepts_every_form_field_without_launching_or_scheduling(self) -> None:
        values = {"csrf": self.state.csrf_token, "id": "", "title": "Prepare work", "prompt": "Inspect later",
                  "directory": self.temporary.name, "mode": "workspace", "interval": "daily", "submit": "draft",
                  "model": "", "custom_model": "test-model", "effort": "high", "timeout_minutes": "120",
                  "web_search": "cached", "network": "1", "temporary_files": "1", "session_history": "1", "failure_limit": "5"}
        self.assertEqual(len(values), 17)
        handler = self.handler("/actions/agent-task/save", values)
        with patch.object(tasks, "execute_prompt") as execute:
            handler.do_POST()
            self.assertFalse(self.state.agent_tasks.tick(now=9999999999))
        execute.assert_not_called()
        handler.send_response.assert_called_with(303)
        task = self.state.agent_tasks.snapshot()["tasks"][0]
        self.assertEqual(task["failure_limit"], 5)
        self.assertTrue(task["draft"])
        self.assertFalse(task["enabled"])
        self.assertFalse(task["queued"])
        page = agents.render_agents(self.state, panel._PAGE_STYLE, {})
        self.assertIn("Draft<small>daily", page)
        self.assertIn("Start schedule</button>", page)

    def test_one_time_draft_can_be_prepared_without_codex_and_does_not_skip_csrf(self) -> None:
        values = {"csrf": self.state.csrf_token, "title": "Prepare", "prompt": "Inspect later",
                  "directory": self.temporary.name, "mode": "inspect", "interval": "once", "submit": "draft"}
        with patch.object(tasks, "_tool_path", return_value=None), patch.object(agents, "_tool_path", return_value=None):
            page = agents.render_agents(self.state, panel._PAGE_STYLE, {})
            self.assertIn('value="run" disabled', page)
            self.assertIn('value="draft">Save draft', page)
            denied = self.handler("/actions/agent-task/save", {**values, "csrf": "wrong"})
            denied.do_POST()
            self.assertEqual(denied._send.call_args.args[0].value, 403)
            handler = self.handler("/actions/agent-task/save", values)
            handler.do_POST()
            handler.send_response.assert_called_with(303)
        self.assertEqual(len(self.state.agent_tasks.snapshot()["tasks"]), 1)

    def test_duplicate_and_reuse_links_prefill_reviewable_settings_without_mutation(self) -> None:
        settings = {"title": "Review <repo>", "prompt": "Original <prompt>", "directory": self.temporary.name,
                    "mode": "workspace", "interval": "weekly", "network": True, "temporary_files": True,
                    "model": "historical-model", "effort": "high", "timeout_minutes": 120, "web_search": "cached", "failure_limit": 5}
        identifier = self.state.agent_tasks.create(settings, run_now=True)
        with patch.object(tasks, "execute_prompt", return_value={"status": "completed", "output": "", "message": "Done"}):
            self.state.agent_tasks.tick()
        run = self.state.agent_tasks.snapshot()["runs"][0]
        self.state.agent_tasks.update(identifier, {**settings, "prompt": "Edited prompt", "model": "current-model"})
        before = self.state.agent_tasks.snapshot()
        for name, source, expected_prompt, expected_model, interval in (
            ("copy", identifier, "Edited prompt", "current-model", "weekly"),
            ("reuse", run["id"], "Original &lt;prompt&gt;", "historical-model", "once"),
        ):
            with self.subTest(name=name), patch.object(tasks, "execute_prompt") as execute:
                query = agents.parse_agent_query(f"{name}={source}")
                page = agents.render_agents(self.state, panel._PAGE_STYLE, query)
            execute.assert_not_called()
            self.assertIn('name="id" value=""', page)
            self.assertIn(expected_prompt, page)
            self.assertIn(f'<option value="{expected_model}" selected>', page)
            self.assertIn(f'<option value="{interval}" selected>', page)
            self.assertIn('value="120"', page)
            self.assertIn('name="failure_limit" min="0" max="10" step="1" value="5"', page)
            self.assertIn('value="draft">Save draft', page)
            self.assertNotIn("Save changes</button>", page)
            self.assertEqual(self.state.agent_tasks.snapshot(), before)
        self.state.agent_tasks.action(identifier, "delete")
        page = agents.render_agents(self.state, panel._PAGE_STYLE, {"reuse": run["id"]})
        self.assertIn("Original &lt;prompt&gt;", page)

    def test_missing_copy_or_history_source_is_reported_without_creating_a_task(self) -> None:
        for name in ("copy", "reuse"):
            with self.subTest(name=name):
                page = agents.render_agents(self.state, panel._PAGE_STYLE, {name: "a" * 32})
                self.assertIn("source task or run is no longer available", page)
                self.assertIn('name="id" value=""', page)
                self.assertIn('name="title" value=""', page)
                self.assertEqual(self.state.agent_tasks.snapshot()["tasks"], [])

    def test_summary_shows_outcomes_runtime_exit_codes_and_automatic_pause(self) -> None:
        settings = {"title": "Review", "prompt": "Inspect", "directory": self.temporary.name,
                    "mode": "inspect", "interval": "daily", "failure_limit": 1}
        identifier = self.state.agent_tasks.create(settings, run_now=True, now=100)
        with patch.object(tasks.time, "monotonic", side_effect=(10, 135)), patch.object(
            tasks, "execute_prompt", return_value={"status": "failed", "output": "", "message": "password=private-value", "exit_code": 2},
        ):
            self.state.agent_tasks.tick(now=100)
        page = agents.render_agents(self.state, panel._PAGE_STYLE, {})
        self.assertIn("0 drafts · 1 paused after failures", page)
        self.assertIn("1 failed/interrupted · 0 cancelled · latest 1 finished runs", page)
        self.assertIn("2m 05s duration · exit code 2", page)
        self.assertIn("Auto-paused<small>daily", page)
        self.assertIn("1 consecutive failure · pause at 1", page)
        self.assertIn(f'href="/agents?copy={identifier}"', page)
        self.assertIn("Reuse run settings", page)
        self.assertNotIn("private-value", page)

    def test_active_run_timing_and_remaining_cap_are_snapshots_not_auto_reload(self) -> None:
        settings = {"title": "Long review", "prompt": "Inspect", "directory": self.temporary.name,
                    "mode": "inspect", "interval": "daily", "timeout_minutes": 10}
        self.state.agent_tasks.create(settings, run_now=True, now=100)
        def inspect_running(*_args: object) -> dict[str, str]:
            with patch.object(agents.time, "time", return_value=225):
                page = agents.render_agents(self.state, panel._PAGE_STYLE, {})
            self.assertIn("Running: Long review", page)
            self.assertIn("2m 05s elapsed · 7m 55s remaining at page load", page)
            self.assertIn("1 running", page)
            self.assertNotIn('http-equiv="refresh"', page)
            return {"status": "completed", "output": "", "message": "Done"}
        with patch.object(tasks, "execute_prompt", side_effect=inspect_running):
            self.state.agent_tasks.tick(now=100)

    def test_invalid_csrf_duplicate_fields_and_unknown_actions_do_not_mutate(self) -> None:
        for body, status in (
            ({"csrf": "wrong", "id": "a" * 32, "action": "run"}, 403),
            ({"csrf": "é", "id": "a" * 32, "action": "run"}, 403),
            (f"csrf={self.state.csrf_token}&csrf={self.state.csrf_token}&id={'a' * 32}&action=run", 403),
            ({"csrf": self.state.csrf_token, "id": "a" * 32, "action": "shell"}, 422),
            ({"csrf": self.state.csrf_token, "command": "rm -rf /"}, 400),
        ):
            with self.subTest(body=body):
                handler = self.handler("/actions/agent-task", body)
                handler.do_POST()
                self.assertEqual(handler._send.call_args.args[0].value, status)
        self.assertEqual(self.state.agent_tasks.snapshot()["tasks"], [])

    def test_validation_error_preserves_escaped_prompt_for_correction(self) -> None:
        handler = self.handler("/actions/agent-task/save", {"csrf": self.state.csrf_token, "title": "<test>",
            "prompt": "<script>bad()</script>", "directory": "relative", "mode": "inspect", "interval": "once", "submit": "run"})
        handler.do_POST()
        status, page = handler._send.call_args.args[:2]
        self.assertEqual(status.value, 422)
        self.assertIn("&lt;script&gt;", page)
        self.assertNotIn("<script>", page)
        self.assertNotIn('value="run" disabled', page)
        self.assertEqual(self.state.agent_tasks.snapshot()["tasks"], [])

    def test_settings_form_saves_custom_model_effort_runtime_and_options(self) -> None:
        values = {"csrf": self.state.csrf_token, "title": "Long review", "prompt": "Wait for a backup, then review",
                  "directory": self.temporary.name, "mode": "workspace", "interval": "daily", "submit": "schedule",
                  "model": "", "custom_model": "custom-model", "effort": "medium", "timeout_minutes": "1440",
                  "web_search": "cached", "network": "1", "session_history": "1", "temporary_files": "1", "id": ""}
        handler = self.handler("/actions/agent-task/save", values)
        handler.do_POST()
        handler.send_response.assert_called_with(303)
        task = self.state.agent_tasks.snapshot()["tasks"][0]
        self.assertEqual(task["timeout_minutes"], 1440)
        self.assertEqual(task["model"], "custom-model")
        self.assertEqual(task["effort"], "medium")
        self.assertTrue(task["network"])
        self.assertTrue(task["session_history"])
        self.assertTrue(task["temporary_files"])
        self.assertEqual(task["web_search"], "cached")
        page = agents.render_agents(self.state, panel._PAGE_STYLE, {"edit": task["id"]})
        self.assertIn('<option value="custom-model" selected>', page)
        self.assertIn('value="1440"', page)
        self.assertIn("1440 min cap", page)
        overview = agents.render_agents(self.state, panel._PAGE_STYLE, {})
        self.assertIn('<details><summary>Create a prompt task</summary>', overview)
        self.assertLess(overview.index('id="agent-tasks-heading"'), overview.index('class="agent-form"'))

    def test_model_dropdown_uses_only_cache_labels_and_keeps_default_available(self) -> None:
        with patch.object(agents, "codex_models", return_value=[{"slug": "test-model", "name": "Test <model>", "efforts": ["low"]}]):
            page = agents.render_agents(self.state, panel._PAGE_STYLE, {})
        self.assertIn('<select name="model">', page)
        self.assertIn('<option value="test-model">Test &lt;model&gt;</option>', page)
        self.assertIn("Configured default", page)
        self.assertIn('name="effort"', page)
        self.assertIn('name="timeout_minutes"', page)

    def test_invalid_runtime_and_checkbox_are_rejected_without_creating_tasks(self) -> None:
        values = {"csrf": self.state.csrf_token, "title": "Review", "prompt": "Inspect host",
                  "directory": self.temporary.name, "mode": "inspect", "interval": "once", "submit": "run"}
        for changes in ({"timeout_minutes": "0"}, {"session_history": "true"}, {"effort": "unlimited"}, {"failure_limit": "11"}):
            with self.subTest(changes=changes):
                handler = self.handler("/actions/agent-task/save", {**values, **changes})
                handler.do_POST()
                self.assertEqual(handler._send.call_args.args[0].value, 422)
        self.assertEqual(self.state.agent_tasks.snapshot()["tasks"], [])

    def test_query_rejects_unsupported_filters_before_rendering(self) -> None:
        for query in ("load=1", "template=no", "edit=../../etc/passwd", "template=repository&template=maintenance",
                      "copy=../task", "reuse=no", f"copy={'a' * 32}&reuse={'b' * 32}"):
            with self.subTest(query=query), self.assertRaises(ValueError):
                agents.parse_agent_query(query)

    def test_diagnostics_skip_t3_when_absent_and_do_not_repair_when_present(self) -> None:
        with (
            patch.object(agents, "inspect_agent_tools", return_value=[]) as tools_check,
            patch.object(agents, "inspect_agent_maintenance", return_value={"status": "inactive", "active": False}),
            patch.object(agents, "load_agent_readiness_record", return_value=None),
            patch.object(agents, "inspect_t3code", return_value={"status": "healthy", "checks": {}}) as t3_check,
        ):
            self.state.agent_diagnostics._collect()
            t3_check.assert_not_called()
            self.state.agent_diagnostics.t3_configured = True
            self.state.agent_diagnostics._collect()
        tools_check.assert_called_with(["codex", "claude", "opencode", "gh"], home=self.temporary.name)
        t3_check.assert_called_once_with(home=self.temporary.name, fix=False)

    def test_t3_readiness_and_update_controls_use_the_agents_screen(self) -> None:
        self.state.agent_diagnostics._snapshot = {
            "status": "loaded", "checked_at": 100, "tools": [],
            "t3": {"status": "unhealthy", "version": None, "checks": {"runtime": False}},
            "maintenance": {"status": "inactive", "active": False}, "record": None, "record_error": "",
        }
        with patch.object(self.state, "t3_update_available", return_value=True):
            page = agents.render_agents(self.state, panel._PAGE_STYLE, {})
        self.assertIn("Readiness details", page)
        self.assertIn("Needs attention", page)
        self.assertIn("Update T3 Code</button>", page)
        handler = self.handler("/actions/t3-update", {"csrf": self.state.csrf_token, "return": "agents"})
        with patch.object(self.state, "trigger_t3_update", return_value=True) as update:
            handler.do_POST()
        update.assert_called_once()
        handler.send_header.assert_any_call("Location", "/agents")

    def test_history_redacts_common_credentials_and_shows_original_prompt(self) -> None:
        self.state.agent_tasks.create({"title": "Check <host>", "prompt": "Inspect <host>", "directory": self.temporary.name,
                                       "mode": "inspect", "interval": "once", "network": False}, run_now=True)
        with patch.object(tasks, "execute_prompt", return_value={"status": "failed", "message": "Denied", "output": "password=private-value <script>"}):
            self.state.agent_tasks.tick()
        page = agents.render_agents(self.state, panel._PAGE_STYLE, {})
        self.assertNotIn("private-value", page)
        self.assertNotIn("<script>", page)
        self.assertIn("Inspect &lt;host&gt;", page)
        self.assertIn("failed", page)


if __name__ == "__main__":
    unittest.main()
