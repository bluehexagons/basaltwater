"""Task lifecycle boundaries found during the persistent-task review."""

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
from lib import agent_task_context as contexts
from tests.test_agent_task_persistence import SESSION, report


class TaskReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = self.enterContext(tempfile.TemporaryDirectory())
        self.manager = tasks.AgentTasks(self.home)
        self.addCleanup(self.manager.close)
        self.enterContext(patch.object(tasks.AgentTasks, "available", return_value=True))
        self.enterContext(patch.object(tasks, "_tool_path", return_value="/test/codex"))
        self.values = {"title": "Review", "prompt": "Review the remaining checks", "directory": self.home,
                       "mode": "workspace", "interval": "hourly", "persistence": "checkpoint"}

    def execute(self, result: dict | None = None, *, now: float = 1) -> None:
        with patch.object(tasks, "execute_prompt", return_value={"status": "completed", "output": "", "message": "Done",
                          **report(), **(result or {})}):
            self.assertTrue(self.manager.tick(now=now))

    def test_daily_limit_keeps_a_queued_one_time_invocation_until_next_day(self) -> None:
        identifier = self.manager.create({**self.values, "interval": "once", "daily_limit": 1}, run_now=True, now=0)
        self.execute()
        self.manager.action(identifier, "run")
        with patch.object(tasks, "execute_prompt") as execute:
            self.assertTrue(self.manager.tick(now=2))
            self.assertFalse(self.manager.tick(now=86399))
        execute.assert_not_called()
        saved = self.manager.snapshot()["tasks"][0]
        self.assertTrue(saved["queued"])
        self.assertFalse(saved["enabled"])
        self.assertEqual(saved["not_before"], 86400)
        self.execute(now=86400)
        self.assertEqual(self.manager.snapshot()["tasks"][0]["attempts"], 2)

    def test_idle_and_deferred_event_tasks_expire_without_provider_calls(self) -> None:
        for queued in (False, True):
            identifier = self.manager.create({**self.values, "interval": "event", "deadline_at": 10}, run_now=queued, now=0)
            with self.manager._transaction():
                self.manager._load()["tasks"][-1]["not_before"] = 86400
                self.manager._save()
        with patch.object(tasks, "execute_prompt") as execute:
            self.assertTrue(self.manager.tick(now=10))
            self.assertFalse(self.manager.tick(now=86400))
        execute.assert_not_called()
        for saved in self.manager.snapshot()["tasks"]:
            self.assertFalse(saved["enabled"])
            self.assertFalse(saved["queued"])
            self.assertEqual(saved["stop_reason"], "Task deadline reached")

    def test_failed_provider_cannot_change_goal_cadence_with_waiting_report(self) -> None:
        self.manager.create({**self.values, "continue_until_done": True, "completion_criteria": "Checks pass"}, run_now=True, now=0)
        with patch.object(tasks.time, "time", return_value=1):
            self.execute({**report("waiting"), "status": "failed", "retry_after_minutes": 1})
        self.assertEqual(self.manager.snapshot()["tasks"][0]["next_run"], 3600)

    def test_context_failure_preserves_receipt_and_pauses_without_more_paid_work(self) -> None:
        identifier = self.manager.create({**self.values, "failure_limit": 0}, run_now=True, now=0)
        real_save = self.manager.contexts.save
        def save(task_id: str, value: dict) -> None:
            if "last_run_id" in value:
                raise OSError("Simulated checkpoint write failure")
            real_save(task_id, value)
        with patch.object(self.manager.contexts, "save", side_effect=save):
            self.execute(report("complete"))
        saved = self.manager.snapshot()["tasks"][0]
        self.assertFalse(saved["enabled"])
        self.assertIn("storage failed", saved["stop_reason"])
        receipt = self.manager.contexts.history(identifier)[0]
        self.assertEqual(receipt["outcome"], "complete")
        with patch.object(tasks, "execute_prompt") as execute:
            self.assertFalse(self.manager.tick(now=86400))
        execute.assert_not_called()

    def test_replaced_source_is_not_silently_resolved_into_a_different_checkout(self) -> None:
        source, other = Path(self.home) / "source", Path(self.home) / "other"
        source.mkdir()
        other.mkdir()
        identifier = self.manager.create({**self.values, "directory": str(source)}, run_now=True, now=0)
        self.execute()
        source.rmdir()
        source.symlink_to(other, target_is_directory=True)
        self.assertEqual(self.manager.snapshot()["tasks"][0]["directory"], str(source))
        self.manager.action(identifier, "run")
        with patch.object(tasks, "execute_prompt") as execute:
            self.manager.tick(now=2)
        execute.assert_not_called()
        self.assertIn("moved or changed", self.manager.snapshot()["runs"][-1]["message"])

    def test_missing_managed_directory_is_not_recreated_as_an_empty_workspace(self) -> None:
        identifier = self.manager.create({**self.values, "workspace": "directory"}, run_now=True, now=0)
        self.execute()
        workspace = Path(self.manager.context_snapshot(identifier)["workspace"]["path"])
        workspace.rmdir()
        self.manager.action(identifier, "run")
        with patch.object(tasks, "execute_prompt") as execute:
            self.manager.tick(now=2)
        execute.assert_not_called()
        self.assertFalse(workspace.exists())
        self.assertIn("workspace is missing", self.manager.snapshot()["runs"][-1]["message"])

    def test_falsey_invalid_control_values_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.manager.create(self.values, run_now=0)
        identifier = self.manager.create({**self.values, "interval": "event"}, run_now=False)
        for key in (None, 0, False):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.manager.notify(identifier, "New evidence", key=key)

    def test_context_source_metadata_requires_an_absolute_path(self) -> None:
        identifier = self.manager.create(self.values, run_now=True, now=0)
        self.execute()
        context = self.manager.contexts.load(identifier)
        self.manager.contexts.save(identifier, {**context, "source_directory": "relative"})
        with self.assertRaisesRegex(RuntimeError, "source directory"):
            self.manager.contexts.load(identifier)

    def test_failed_persistence_update_does_not_erase_a_saved_checkpoint(self) -> None:
        identifier = self.manager.create(self.values, run_now=True, now=0)
        self.execute()
        checkpoint = self.manager.contexts.load(identifier)["checkpoint"]
        with patch.object(tasks, "write_text_atomic", side_effect=OSError("Full state disk")), self.assertRaises(OSError):
            self.manager.update(identifier, {"persistence": "fresh"}, partial=True)
        self.assertEqual(self.manager.contexts.load(identifier)["checkpoint"], checkpoint)
        self.assertEqual(self.manager.snapshot()["tasks"][0]["persistence"], "checkpoint")
        self.manager.update(identifier, {"persistence": "fresh"}, partial=True)
        self.assertEqual(self.manager.contexts.load(identifier)["checkpoint"], checkpoint)
        self.assertEqual(self.manager.context_snapshot(identifier)["checkpoint"], "")
        self.assertTrue(self.manager.context_snapshot(identifier)["reset_pending"])
        self.manager.action(identifier, "run")
        self.execute(now=2)
        self.assertEqual(self.manager.contexts.load(identifier)["checkpoint"], "")
        self.assertFalse(self.manager.context_snapshot(identifier)["reset_pending"])


class ProviderTurnReviewTests(unittest.TestCase):
    def feed(self, events: contexts.CodexEvents, value: dict) -> None:
        events.feed(json.dumps(value).encode() + b"\n")

    def test_new_turn_invalidates_an_earlier_completion_report(self) -> None:
        events = contexts.CodexEvents()
        self.feed(events, {"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(report("complete"))}})
        self.feed(events, {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 5}})
        self.assertTrue(events.final_turn_completed)
        self.feed(events, {"type": "turn.started"})
        self.assertIsNone(events.report)
        self.assertFalse(events.final_turn_completed)

    def test_missing_turn_usage_never_becomes_a_partial_budget_total(self) -> None:
        events = contexts.CodexEvents()
        self.feed(events, {"type": "turn.completed"})
        self.feed(events, {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 5}})
        self.assertIsNone(events.usage)
        self.assertTrue(events.usage_incomplete)

    def test_changed_conversation_id_remains_a_protocol_failure_after_valid_report(self) -> None:
        events = contexts.CodexEvents()
        for session in (SESSION, "ffffffff-ffff-ffff-ffff-ffffffffffff"):
            self.feed(events, {"type": "thread.started", "thread_id": session})
        self.feed(events, {"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(report())}})
        self.assertTrue(events.protocol_error)
        self.assertEqual(events.session_id, SESSION)

    def test_zero_exit_without_terminal_turn_is_a_failed_invocation(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            task = tasks.validate_task({"title": "Review", "prompt": "Review", "directory": home,
                                      "mode": "workspace", "interval": "once", "persistence": "conversation"}, home)
            task["id"] = "a" * 32
            runtime = contexts.TaskContextStore(home).prepare(task, "b" * 32)
            payload = b"\n".join(json.dumps(event).encode() for event in (
                {"type": "thread.started", "thread_id": SESSION},
                {"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(report("complete"))}},
            )) + b"\n"
            process, selector = MagicMock(), MagicMock()
            process.__enter__.return_value = process
            process.pid, process.wait.return_value = 1234, 0
            selector.__enter__.return_value = selector
            key = SimpleNamespace(fd=99, fileobj=process.stdout)
            selector.get_map.side_effect = [{99: key}, {}]
            selector.select.return_value = [(key, 1)]
            provider_os = SimpleNamespace(**vars(os))
            with (patch.object(tasks, "os", provider_os), patch.object(provider_os, "geteuid", return_value=1000),
                  patch.object(tasks, "_tool_path", return_value="/test/codex"), patch.object(tasks.subprocess, "Popen", return_value=process),
                  patch.object(tasks.selectors, "DefaultSelector", return_value=selector), patch.object(provider_os, "read", return_value=payload),
                  patch.object(provider_os, "killpg")):
                result = tasks.execute_prompt({**task, "_runtime": runtime}, home, threading.Event())
            self.assertEqual(result["status"], "failed")
            self.assertNotIn("outcome", result)
            self.assertIsNone(result["usage"])


if __name__ == "__main__":
    unittest.main()
