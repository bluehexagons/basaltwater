"""Durable task context, cross-process controls and evidence-based continuation."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from lib import agent_tasks as tasks
from lib.agent_task_context import CodexEvents, TaskContextStore, validate_report


SESSION = "01234567-89ab-cdef-0123-456789abcdef"


def report(outcome: str = "progress") -> dict:
    return {"outcome": outcome, "summary": "Inspected the remaining failure.",
            "checkpoint": "One failing case remains; reproduce it next.",
            "evidence": ["Focused check: one failure."], "retry_after_minutes": None}


class PersistentTaskTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = self.enterContext(tempfile.TemporaryDirectory())
        self.manager = tasks.AgentTasks(self.home)
        self.enterContext(patch.object(tasks.AgentTasks, "available", return_value=True))
        self.enterContext(patch.object(tasks, "_tool_path", return_value="/test/codex"))
        self.values = {"title": "Fix checks", "prompt": "Fix the failing checks.", "directory": self.home,
                       "mode": "workspace", "interval": "hourly", "persistence": "checkpoint"}
        self.addCleanup(self.manager.close)

    def run_task(self, result: dict, *, now: float = 1) -> None:
        with patch.object(tasks, "execute_prompt", return_value={"status": "completed", "message": "Done", "output": "", **result}):
            self.assertTrue(self.manager.tick(now=now))

    def test_separate_controllers_reload_each_other_without_lost_updates(self) -> None:
        other = tasks.AgentTasks(self.home)
        self.assertEqual(other.snapshot()["tasks"], [])
        first = self.manager.create(self.values, run_now=False, now=0)
        second = other.create({**self.values, "title": "Another task"}, run_now=False, now=0)
        self.manager.action(first, "pause")
        other.action(second, "run")
        state = self.manager.snapshot()
        self.assertEqual([task["id"] for task in state["tasks"]], [first, second])
        self.assertFalse(state["tasks"][0]["enabled"])
        self.assertTrue(state["tasks"][1]["queued"])

    def test_separate_controller_cancels_running_work_and_preserves_other_changes(self) -> None:
        identifier = self.manager.create(self.values, run_now=True)
        other = tasks.AgentTasks(self.home)

        def execute(_task: dict, _home: str, cancellation: object) -> dict:
            self.assertFalse(other.tick())
            other.create({**self.values, "title": "Later"}, run_now=False)
            other.action(identifier, "cancel")
            self.assertTrue(cancellation.is_set())
            return {"status": "cancelled", "output": "", "message": "Cancelled"}

        with patch.object(tasks, "execute_prompt", side_effect=execute):
            self.manager.tick()
        self.assertEqual(len(self.manager.snapshot()["tasks"]), 2)
        self.assertEqual(self.manager.snapshot()["runs"][0]["status"], "cancelled")

    def test_checkpoint_and_full_receipts_survive_hot_history_eviction(self) -> None:
        identifier = self.manager.create(self.values, run_now=True, now=0)
        with patch.object(tasks, "MAX_RUNS", 1):
            self.run_task(report())
            self.manager.action(identifier, "run")
            self.run_task({**report(), "checkpoint": "New evidence", "evidence": ["Second check"]}, now=2)
        self.assertEqual(len(self.manager.snapshot()["runs"]), 1)
        store = TaskContextStore(self.home)
        self.assertEqual(store.load(identifier)["checkpoint"], "New evidence")
        receipts = list((store.directory(identifier) / "runs").glob("*.json"))
        self.assertEqual(len(receipts), 2)
        self.assertEqual(json.loads(receipts[0].read_text())["task"]["prompt"], self.values["prompt"])
        self.assertNotIn("checkpoint", self.manager.snapshot()["runs"][0])
        self.assertFalse((Path(self.home) / "context.json").exists())

    def test_persistent_directory_is_reused_and_reset_preserves_files(self) -> None:
        identifier = self.manager.create({**self.values, "workspace": "directory"}, run_now=True, now=0)
        self.run_task(report())
        workspace = Path(self.manager.snapshot()["runs"][0]["execution_directory"])
        (workspace / "unfinished.txt").write_text("keep")
        self.manager.action(identifier, "reset-context")
        self.manager.action(identifier, "run")
        self.run_task(report(), now=2)
        self.assertEqual(self.manager.snapshot()["runs"][1]["execution_directory"], str(workspace))
        self.assertEqual((workspace / "unfinished.txt").read_text(), "keep")
        with self.assertRaises(ValueError):
            self.manager.update(identifier, {**self.values, "workspace": "existing"})
        self.manager.action(identifier, "delete")
        self.assertTrue(workspace.exists())

    def test_fresh_run_directories_differ_and_conversations_cannot_use_them(self) -> None:
        identifier = self.manager.create({**self.values, "workspace": "run-directory"}, run_now=True, now=0)
        self.run_task(report())
        self.manager.action(identifier, "run")
        self.run_task(report(), now=2)
        runs = self.manager.snapshot()["runs"]
        self.assertNotEqual(runs[0]["execution_directory"], runs[1]["execution_directory"])
        with self.assertRaises(ValueError):
            tasks.validate_task({**self.values, "workspace": "run-directory", "persistence": "conversation"}, self.home)

    def test_worktree_creation_is_owned_revalidated_and_never_cleans_changes(self) -> None:
        from lib import agent_workspace

        identifier = self.manager.create({**self.values, "workspace": "worktree"}, run_now=True, now=0)
        workspace = Path(self.home) / "managed-worktree"
        workspace.mkdir()
        record = {"path": str(workspace), "repository": self.home, "branch": "agent/test",
                  "managed": True, "task": "prompt-" + identifier, "dirty": True}
        with patch.object(agent_workspace, "create_agent_worktree", return_value=record) as create, patch.object(
            agent_workspace, "inspect_agent_worktree", return_value=record,
        ) as inspect:
            self.run_task(report())
            self.manager.action(identifier, "run")
            self.run_task(report(), now=2)
        create.assert_called_once_with(self.home, "prompt-" + identifier, home=self.home)
        self.assertEqual(inspect.call_count, 2)
        self.assertEqual(self.manager.context_snapshot(identifier)["workspace"]["path"], str(workspace))
        with self.assertRaises(ValueError):
            self.manager.update(identifier, {**self.values, "workspace": "directory"})

    def test_evidence_retention_removes_only_generated_receipts_and_logs(self) -> None:
        identifier = self.manager.create({**self.values, "history_limit": 1}, run_now=True, now=0)
        self.run_task(report())
        directory = TaskContextStore(self.home).directory(identifier) / "runs"
        retained = directory / "user-artifact.txt"
        retained.write_text("preserve")
        self.manager.action(identifier, "run")
        self.run_task(report(), now=2)
        self.assertEqual(len(list(directory.glob("*.json"))), 1)
        self.assertEqual(retained.read_text(), "preserve")

    def test_completed_process_reporting_blocked_counts_toward_failure_pausing(self) -> None:
        self.manager.create({**self.values, "failure_limit": 1}, run_now=True, now=0)
        self.run_task(report("blocked"))
        task = self.manager.snapshot()["tasks"][0]
        self.assertFalse(task["enabled"])
        self.assertTrue(task["auto_paused"])
        self.assertEqual(self.manager.snapshot()["runs"][0]["status"], "completed")

    def test_goal_stops_after_verified_completion_and_attempt_limit_is_durable(self) -> None:
        identifier = self.manager.create({**self.values, "interval": "once", "continue_until_done": True,
                                          "completion_criteria": "Focused checks pass", "max_runs": 2}, run_now=True, now=0)
        self.run_task(report())
        self.assertTrue(self.manager.snapshot()["tasks"][0]["enabled"])
        self.manager.action(identifier, "run")
        self.run_task(report("complete"), now=2)
        task = self.manager.snapshot()["tasks"][0]
        self.assertFalse(task["enabled"])
        self.assertIn("Goal completed", task["stop_reason"])
        self.manager.action(identifier, "run")
        with patch.object(tasks, "execute_prompt") as execute:
            self.assertTrue(self.manager.tick(now=3))
        execute.assert_not_called()
        self.assertEqual(self.manager.snapshot()["tasks"][0]["attempts"], 2)

    def test_deadline_and_token_accounting_prevent_further_calls(self) -> None:
        identifier = self.manager.create({**self.values, "token_budget": 100}, run_now=True, now=0)
        self.run_task({**report(), "usage": {"input_tokens": 60, "output_tokens": 50}})
        task = self.manager.snapshot()["tasks"][0]
        self.assertEqual(task["tokens_used"], 110)
        self.assertEqual(task["stop_reason"], "Token budget reached")
        self.manager.action(identifier, "delete")
        self.manager.create({**self.values, "deadline_at": 1}, run_now=True, now=0)
        with patch.object(tasks, "execute_prompt") as execute:
            self.manager.tick(now=2)
        execute.assert_not_called()
        self.assertEqual(self.manager.snapshot()["tasks"][0]["stop_reason"], "Task deadline reached")

    def test_daily_attempt_limit_defers_until_next_utc_day(self) -> None:
        identifier = self.manager.create({**self.values, "daily_limit": 1}, run_now=True, now=0)
        self.run_task(report())
        self.manager.action(identifier, "run")
        with patch.object(tasks, "execute_prompt") as execute:
            self.manager.tick(now=2)
        execute.assert_not_called()
        task = self.manager.snapshot()["tasks"][0]
        self.assertEqual(task["next_run"], 86400)
        self.assertTrue(task["enabled"])
        self.run_task(report(), now=86400)
        self.assertEqual(self.manager.snapshot()["tasks"][0]["daily_attempts"], 1)

    def test_wake_events_dedupe_are_consumed_once_and_survive_a_running_turn(self) -> None:
        identifier = self.manager.create(self.values, run_now=False, now=0)
        self.assertTrue(self.manager.notify(identifier, "CI changed", key="delivery-1"))
        self.assertFalse(self.manager.notify(identifier, "CI changed", key="delivery-1"))

        def execute(task: dict, *_args: object) -> dict:
            self.assertEqual(task["events"], ["CI changed"])
            tasks.AgentTasks(self.home).notify(identifier, "Another event", key="delivery-2")
            return {"status": "completed", "output": "", "message": "Done", **report()}

        with patch.object(tasks, "execute_prompt", side_effect=execute):
            self.manager.tick(now=1)
        task = self.manager.snapshot()["tasks"][0]
        self.assertTrue(task["queued"])
        self.assertEqual(task["events"], ["Another event"])
        self.manager.action(identifier, "pause")
        with self.assertRaises(ValueError):
            self.manager.notify(identifier, "Do not reactivate paused work")

    def test_event_tasks_wait_for_notifications_and_keep_pending_events_on_resume(self) -> None:
        identifier = self.manager.create({**self.values, "interval": "event"}, run_now=False, now=0)
        with patch.object(tasks, "execute_prompt") as execute:
            self.assertFalse(self.manager.tick(now=86400))
        execute.assert_not_called()
        self.manager.notify(identifier, "New result", key="result-1")
        self.manager.action(identifier, "pause")
        self.manager.action(identifier, "resume", now=0)
        self.assertTrue(self.manager.snapshot()["tasks"][0]["queued"])
        self.run_task({**report("waiting"), "retry_after_minutes": 1})
        self.assertIsNone(self.manager.snapshot()["tasks"][0]["next_run"])
        self.assertFalse(self.manager.tick(now=86400))

    def test_event_notifications_do_not_bypass_failure_backoff(self) -> None:
        identifier = self.manager.create({**self.values, "interval": "event", "backoff_minutes": 10}, run_now=True, now=0)
        with patch.object(tasks.time, "time", return_value=1):
            self.run_task(report("blocked"))
        self.manager.notify(identifier, "Retry after new evidence", key="result-1")
        with patch.object(tasks, "execute_prompt") as execute:
            self.assertFalse(self.manager.tick(now=600))
        execute.assert_not_called()
        self.run_task(report(), now=601)

    def test_waiting_report_changes_only_an_authorized_goal_cadence(self) -> None:
        identifier = self.manager.create(self.values, run_now=True, now=0)
        with patch.object(tasks.time, "time", return_value=1):
            self.run_task({**report("waiting"), "retry_after_minutes": 1})
        self.assertEqual(self.manager.snapshot()["tasks"][0]["next_run"], 3600)
        self.manager.action(identifier, "delete")
        self.manager.create({**self.values, "continue_until_done": True,
                             "completion_criteria": "Checks pass"}, run_now=True, now=0)
        with patch.object(tasks.time, "time", return_value=1):
            self.run_task({**report("waiting"), "retry_after_minutes": 1})
        self.assertEqual(self.manager.snapshot()["tasks"][0]["next_run"], 61)

    def test_recovery_archives_interrupted_claim_without_reexecuting_it(self) -> None:
        identifier = self.manager.create(self.values, run_now=True, now=0)
        with patch.object(tasks, "execute_prompt", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.manager.tick(now=1)
        self.manager.start(once=True)
        self.assertEqual(self.manager.error, "")
        recovered = self.manager.snapshot()["runs"][0]
        self.assertEqual(recovered["status"], "interrupted")
        self.assertEqual(TaskContextStore(self.home).history(identifier)[0]["status"], "interrupted")
        self.assertEqual(self.manager.snapshot()["tasks"][0]["attempts"], 1)

    def test_recovery_reconciles_final_receipt_after_hot_state_write_failed(self) -> None:
        identifier = self.manager.create({**self.values, "continue_until_done": True,
                                          "completion_criteria": "Checks pass"}, run_now=True, now=0)
        write = tasks.write_text_atomic
        count = 0

        def interrupted_write(*args: object, **kwargs: object) -> None:
            nonlocal count
            count += 1
            if count == 2:
                raise OSError("Simulated final state write failure")
            write(*args, **kwargs)

        with patch.object(tasks, "write_text_atomic", side_effect=interrupted_write), self.assertRaises(OSError):
            self.run_task({**report("complete"), "usage": {"input_tokens": 15, "output_tokens": 5}})
        self.assertEqual(self.manager.snapshot()["runs"][0]["status"], "running")
        restored = tasks.AgentTasks(self.home)
        self.addCleanup(restored.close)
        with patch.object(tasks, "execute_prompt") as execute:
            restored.start(once=True)
            self.assertFalse(restored.tick(now=100000))
        execute.assert_not_called()
        self.assertEqual(restored.error, "")
        self.assertEqual(restored.snapshot()["runs"][0]["outcome"], "complete")
        self.assertEqual(restored.snapshot()["tasks"][0]["tokens_used"], 20)
        self.assertFalse(restored.snapshot()["tasks"][0]["enabled"])
        self.assertEqual(TaskContextStore(self.home).history(identifier)[0]["outcome"], "complete")

    def test_legacy_state_refuses_writes_while_an_old_worker_owns_it(self) -> None:
        import fcntl

        self.manager.create(self.values, run_now=False, now=0)
        state = self.manager.snapshot()
        state["version"] = 1
        self.manager.path.write_text(json.dumps(state))
        descriptor = os.open(self.manager.parent / "worker.lock", os.O_CREAT | os.O_RDWR, 0o600)
        self.addCleanup(os.close, descriptor)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with self.assertRaisesRegex(RuntimeError, "legacy panel/task worker"):
            self.manager.create({**self.values, "title": "New task"}, run_now=False)
        self.assertEqual(json.loads(self.manager.path.read_text())["version"], 1)
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        self.manager.create({**self.values, "title": "New task"}, run_now=False)
        self.assertEqual(json.loads(self.manager.path.read_text())["version"], 2)

    def test_invalid_context_limits_and_corrupt_or_linked_state_are_preserved(self) -> None:
        for changes in ({"persistence": "all"}, {"executor": "t3"}, {"token_budget": True}, {"history_limit": 0},
                        {"deadline_at": "tomorrow"}, {"deadline_at": "2026-01-01T09:00:00"},
                        {"continue_until_done": True}, {"continue_until_done": True, "completion_criteria": "Checks pass",
                                                       "max_runs": 0, "deadline_at": "0", "token_budget": "0"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                tasks.validate_task({**self.values, **changes}, self.home)
        identifier = self.manager.create(self.values, run_now=True)
        self.run_task(report())
        path = TaskContextStore(self.home).directory(identifier) / "context.json"
        path.write_text("invalid")
        with self.assertRaises(ValueError):
            self.manager.context_snapshot(identifier)
        self.assertEqual(path.read_text(), "invalid")
        path.unlink()
        outside = Path(self.home) / "outside.json"
        outside.write_text("{}")
        path.symlink_to(outside)
        with self.assertRaises(RuntimeError):
            self.manager.context_snapshot(identifier)
        self.assertEqual(outside.read_text(), "{}")


class CodexTaskProtocolTests(unittest.TestCase):
    def test_cancelled_claim_never_launches_a_provider(self) -> None:
        cancellation = threading.Event()
        cancellation.set()
        with patch.object(tasks.os, "geteuid", return_value=1000), patch.object(tasks.subprocess, "Popen") as spawn:
            result = tasks.execute_prompt({}, "/test", cancellation)
        self.assertEqual(result["status"], "cancelled")
        spawn.assert_not_called()

    def test_incremental_events_capture_session_report_and_actual_usage(self) -> None:
        payload = "\n".join(json.dumps(item) for item in (
            {"type": "thread.started", "thread_id": SESSION},
            {"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(report())}},
            {"type": "turn.completed", "usage": {"input_tokens": 15, "cached_input_tokens": 10, "output_tokens": 5}},
        )).encode()
        events = CodexEvents()
        for start in range(0, len(payload), 7):
            events.feed(payload[start:start + 7])
        events.feed(b"", final=True)
        self.assertEqual(events.session_id, SESSION)
        self.assertEqual(events.report, report())
        self.assertEqual(events.usage, {"input_tokens": 15, "output_tokens": 5})
        events.feed(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 19, "output_tokens": 7}}).encode() + b"\n")
        self.assertEqual(events.usage, {"input_tokens": 19, "output_tokens": 7})

    def test_large_valid_report_survives_fragmented_provider_events(self) -> None:
        value = {**report(), "evidence": ["é" * 1000] * 40}
        payload = json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(value)}}).encode() + b"\n"
        events = CodexEvents()
        for start in range(0, len(payload), 4096):
            events.feed(payload[start:start + 4096])
        self.assertEqual(events.report, value)

    def test_completion_needs_evidence_and_last_message_is_authoritative(self) -> None:
        with self.assertRaises(ValueError):
            validate_report({**report("complete"), "evidence": []})
        events = CodexEvents()
        for value in (json.dumps(report()), "unstructured final output"):
            events.feed(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": value}}).encode() + b"\n")
        self.assertIsNone(events.report)

    def test_resume_pins_scope_and_never_picks_an_unrelated_last_session(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            values = tasks.validate_task({"title": "Continue", "prompt": "Continue the review", "directory": home,
                                          "mode": "inspect", "interval": "once", "persistence": "conversation"}, home)
            command = tasks.codex_command({**values, "_runtime": {"session_id": SESSION, "schema_path": home + "/schema.json"}}, "/test/codex")
        self.assertEqual(command[command.index("resume") + 1], SESSION)
        self.assertLess(command.index("--sandbox"), command.index("resume"))
        self.assertIn('approval_policy="never"', command)
        self.assertIn("--output-schema", command)
        self.assertNotIn("--last", command)
        self.assertNotIn("--ephemeral", command)

    def test_structured_execution_keeps_full_private_log_and_persists_first_session(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            identifier, run_id = "a" * 32, "b" * 32
            task = tasks.validate_task({"title": "Continue", "prompt": "Continue safely", "directory": home,
                                        "mode": "workspace", "interval": "once", "persistence": "conversation"}, home)
            task["id"] = identifier
            task["events"] = ["A" * 4000] * 32
            runtime = TaskContextStore(home).prepare(task, run_id)
            payload = b"\n".join(json.dumps(item).encode() for item in (
                {"type": "thread.started", "thread_id": SESSION},
                {"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(report())}},
                {"type": "turn.completed", "usage": {"input_tokens": 15, "output_tokens": 5}},
            )) + b"\n"
            process, selector = MagicMock(), MagicMock()
            process.__enter__.return_value = process
            process.pid, process.wait.return_value = 1234, 0
            selector.__enter__.return_value = selector
            key = SimpleNamespace(fd=99, fileobj=process.stdout)
            selector.get_map.side_effect = [{99: key}, {}]
            selector.select.return_value = [(key, 1)]

            def launch(_command: list, **kwargs: object) -> object:
                prompt_file = kwargs["stdin"]
                self.assertGreater(len(prompt_file.read()), 64 * 1024)
                self.assertEqual(os.fstat(prompt_file.fileno()).st_mode & 0o777, 0o600)
                prompt_file.seek(0)
                return process

            # Mock the provider's identity without changing the owner used by
            # the private context store; Debian CI itself runs as root.
            provider_os = SimpleNamespace(**vars(os))
            with (patch.object(tasks, "os", provider_os),
                  patch.object(provider_os, "geteuid", return_value=1000),
                  patch.object(tasks, "_tool_path", return_value="/test/codex"),
                  patch.object(tasks.subprocess, "Popen", side_effect=launch),
                  patch.object(tasks.selectors, "DefaultSelector", return_value=selector),
                  patch.object(tasks.os, "read", return_value=payload), patch.object(tasks.os, "killpg")):
                result = tasks.execute_prompt({**task, "_runtime": runtime}, home, threading.Event())
            self.assertEqual(result["outcome"], "progress")
            self.assertEqual(result["output"], report()["summary"])
            self.assertEqual(Path(runtime["log_path"]).read_bytes(), payload)
            self.assertEqual(TaskContextStore(home).load(identifier)["session_id"], SESSION)
            process.stdin.write.assert_not_called()


if __name__ == "__main__":
    unittest.main()
