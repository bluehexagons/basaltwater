"""Shutdown, event-only goals, inspection failures and bounded hot history."""

from __future__ import annotations

import os
import signal
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from lib import agent_tasks as tasks
from lib.agent_task_api import TaskAPI
from lib.agent_task_worker import run_worker
from lib.agent_tasks_mcp import TaskMCP
from tests.test_agent_task_persistence import report


class TaskBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = self.enterContext(tempfile.TemporaryDirectory())
        self.manager = tasks.AgentTasks(self.home)
        self.addCleanup(self.manager.close)
        self.enterContext(patch.object(tasks.AgentTasks, "available", return_value=True))
        self.enterContext(patch.object(tasks, "_tool_path", return_value="/test/codex"))
        self.enterContext(patch("lib.agent_task_api.worker_installed", return_value=False))
        self.values = {"title": "Review", "prompt": "Inspect the checks", "directory": self.home,
                       "mode": "inspect", "interval": "hourly", "persistence": "checkpoint"}

    def test_stopped_manager_does_not_claim_or_prepare_queued_work(self) -> None:
        self.manager.create(self.values, run_now=True, now=0)
        before = self.manager.snapshot()
        self.manager._stop.set()
        with patch.object(self.manager.contexts, "prepare") as prepare, patch.object(tasks, "execute_prompt") as execute:
            self.assertFalse(self.manager.tick(now=1))
        prepare.assert_not_called()
        execute.assert_not_called()
        self.assertEqual(self.manager.snapshot(), before)

    def test_shutdown_during_claim_cannot_be_cleared_and_keeps_a_cancelled_receipt(self) -> None:
        identifier = self.manager.create(self.values, run_now=True, now=0)
        handlers = {}
        advance = tasks._advance_schedule
        delivered = False

        def install(number: int, handler: object) -> object:
            previous = handlers.get(number, signal.SIG_DFL)
            handlers[number] = handler
            return previous

        def stop_during_claim(task: dict, now: float) -> None:
            nonlocal delivered
            advance(task, now)
            if not delivered:
                delivered = True
                handlers[signal.SIGTERM](signal.SIGTERM, None)

        with (patch("lib.agent_task_worker.signal.signal", side_effect=install),
              patch.object(tasks, "_advance_schedule", side_effect=stop_during_claim),
              patch.object(self.manager.contexts, "prepare") as prepare,
              patch.object(tasks, "execute_prompt") as execute):
            self.assertEqual(run_worker(once=True, manager=self.manager), 0)
        prepare.assert_not_called()
        execute.assert_not_called()
        state = self.manager.snapshot()
        self.assertTrue(self.manager._stop.is_set())
        self.assertEqual(state["tasks"][0]["attempts"], 1)
        self.assertTrue(state["tasks"][0]["enabled"])
        self.assertEqual(state["runs"][0]["status"], "cancelled")
        self.assertEqual(self.manager.contexts.history(identifier)[0]["status"], "cancelled")
        self.assertIsNone(self.manager._lease)

    def test_shutdown_still_cancels_after_the_per_run_cancel_flag_is_cleared(self) -> None:
        cancellation = self.manager._cancel_for_task("a" * 32)
        self.manager._stop.set()
        self.manager._cancel.clear()
        with patch.object(self.manager, "snapshot") as snapshot:
            self.assertTrue(cancellation.is_set())
        snapshot.assert_not_called()

    def test_event_goal_retry_request_waits_for_another_explicit_notification(self) -> None:
        identifier = self.manager.create({**self.values, "interval": "event", "continue_until_done": True,
                                          "completion_criteria": "Checks pass", "max_runs": 3}, run_now=False, now=0)
        self.manager.notify(identifier, "First check")
        with patch.object(tasks, "execute_prompt", return_value={"status": "completed", "output": "", "message": "Waiting",
                                                               **report("waiting"), "retry_after_minutes": 1}):
            self.assertTrue(self.manager.tick(now=1))
        task = self.manager.snapshot()["tasks"][0]
        self.assertTrue(task["enabled"])
        self.assertIsNone(task["next_run"])
        with patch.object(tasks, "execute_prompt") as execute:
            self.assertFalse(self.manager.tick(now=100000))
        execute.assert_not_called()
        self.manager.notify(identifier, "Checks changed")
        with patch.object(tasks, "execute_prompt", return_value={"status": "completed", "output": "", "message": "Done",
                                                               **report("complete")}):
            self.assertTrue(self.manager.tick(now=100001))
        self.assertFalse(self.manager.snapshot()["tasks"][0]["enabled"])

    def test_bad_worker_lease_reports_error_without_obscuring_saved_controls(self) -> None:
        api = TaskAPI(self.manager)
        first = api.call("create", {"task": self.values})["id"]
        lease = self.manager.parent / "worker.lock"
        lease.symlink_to(self.manager.path)
        result = api.call("create", {"task": {**self.values, "title": "Another check"}})
        self.assertIn("unavailable", result["worker"]["error"])
        self.assertFalse(result["worker"]["running"])
        self.assertEqual(len(api.call("list", {})["tasks"]), 2)
        self.assertEqual(api.call("show", {"id": first})["task"]["id"], first)
        api.call("action", {"id": first, "action": "pause"})
        self.assertTrue(lease.is_symlink())

    def test_partial_context_returns_mcp_error_and_does_not_crash_or_reset_the_session(self) -> None:
        identifier = self.manager.create(self.values, run_now=False, draft=True, now=0)
        server = TaskMCP(TaskAPI(self.manager))
        server.request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}})
        complete = {"version": 1, "checkpoint": "Keep findings", "session_id": None, "workspace": None}
        for missing in ("session_id", "workspace"):
            value = {name: field for name, field in complete.items() if name != missing}
            self.manager.contexts.save(identifier, value)
            path = self.manager.contexts.directory(identifier) / "context.json"
            before = path.read_bytes()
            reply = server.request({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
                "name": "basaltwater_tasks_show", "arguments": {"id": identifier},
            }})
            self.assertTrue(reply["result"]["isError"])
            self.assertEqual(path.read_bytes(), before)
            reply = server.request({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "basaltwater_tasks_list"}})
            self.assertFalse(reply["result"]["isError"])

    def test_hot_history_eviction_preserves_receipts_counters_and_future_runs(self) -> None:
        identifier = self.manager.create({**self.values, "interval": "event", "failure_limit": 0}, run_now=False, now=0)
        identifiers = []
        for index in range(9):
            for _ in range(32):
                self.manager.notify(identifier, "x" * 4000)
            with patch.object(tasks, "execute_prompt", return_value={"status": "failed", "output": "y" * 8192,
                        "message": "Invalid provider report", "usage": {"input_tokens": 15, "output_tokens": 5}}):
                self.assertTrue(self.manager.tick(now=index + 1))
            identifiers.append(self.manager.snapshot()["runs"][-1]["id"])
        state = self.manager.snapshot()
        self.assertLess(len(state["runs"]), 9)
        self.assertEqual(state["runs"][-1]["id"], identifiers[-1])
        self.assertEqual(state["tasks"][0]["attempts"], 9)
        self.assertEqual(state["tasks"][0]["tokens_used"], 180)
        self.assertTrue(state["tasks"][0]["enabled"])
        self.assertLessEqual(self.manager.path.stat().st_size, tasks.MAX_STATE_BYTES)
        self.assertEqual(len(self.manager.contexts.history(identifier)), 9)
        receipt = self.manager.contexts.receipt(identifier, identifiers[0])
        self.assertEqual(receipt["output"], "y" * 8192)
        self.assertEqual(len(receipt["task"]["events"]), 32)

    def test_pending_events_leave_room_for_an_active_run_to_finish(self) -> None:
        identifier = self.manager.create(self.values, run_now=True, now=0)
        observed = {}

        def execute(*_args: object) -> dict:
            other = tasks.AgentTasks(self.home)
            self.addCleanup(other.close)
            for _ in range(10):
                try:
                    other.notify(identifier, "e" * 1000)
                except RuntimeError as exc:
                    self.assertIn("storage limit", str(exc))
                    observed["rejected"] = True
                    break
            observed["reserved"] = self.manager.path.stat().st_size
            return {"status": "completed", "output": "r" * 1000, "message": "Done", **report()}

        with (patch.object(tasks, "MAX_STATE_BYTES", 6000), patch.object(tasks, "FINISH_RESERVE_BYTES", 1500),
              patch.object(tasks, "execute_prompt", side_effect=execute)):
            self.assertTrue(self.manager.tick(now=1))
        self.assertTrue(observed["rejected"])
        self.assertLessEqual(observed["reserved"], 4500)
        state = self.manager.snapshot()
        self.assertEqual(state["runs"][-1]["status"], "completed")
        self.assertTrue(state["tasks"][0]["queued"])
        self.assertGreater(self.manager.path.stat().st_size, 4500)
        self.assertLessEqual(self.manager.path.stat().st_size, 6000)

    def test_running_provider_honors_absolute_deadline_after_a_forward_clock_change(self) -> None:
        task = tasks.validate_task({**self.values, "deadline_at": 10}, self.home)
        process, selector = MagicMock(), MagicMock()
        process.__enter__.return_value = process
        process.pid, process.wait.return_value = 1234, 0
        selector.__enter__.return_value = selector
        selector.get_map.return_value = {99: object()}
        provider_os = SimpleNamespace(**vars(os))
        with (patch.object(tasks, "os", provider_os), patch.object(provider_os, "geteuid", return_value=1000),
              patch.object(tasks.subprocess, "Popen", return_value=process),
              patch.object(tasks.selectors, "DefaultSelector", return_value=selector),
              patch.object(provider_os, "killpg") as kill,
              patch.object(tasks.time, "monotonic", return_value=0),
              patch.object(tasks.time, "time", side_effect=[1, 11, 11])):
            result = tasks.execute_prompt(task, self.home, threading.Event())
        self.assertEqual(result["status"], "failed")
        self.assertIn("deadline", result["message"])
        selector.select.assert_not_called()
        kill.assert_called_with(1234, signal.SIGKILL)


if __name__ == "__main__":
    unittest.main()
