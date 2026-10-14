"""Scheduled first runs, compact agent controls and retained receipt inspection."""

from __future__ import annotations

import argparse
import io
import json
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from common.service_tools import web_panel_agents as panel
from common.service_tools.web_panel_service import WebPanelState, _PAGE_STYLE
from lib import agent_tasks as tasks
from lib.agent_task_api import TaskAPI
from lib.agent_tasks_cli import add_tasks_parser, run_tasks_command
from lib.agent_tasks_mcp import TaskMCP
from tests.test_agent_task_persistence import report


class TaskControlsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = self.enterContext(tempfile.TemporaryDirectory())
        self.manager = tasks.AgentTasks(self.home)
        self.addCleanup(self.manager.close)
        self.enterContext(patch.object(tasks.AgentTasks, "available", return_value=True))
        self.enterContext(patch.object(tasks, "_tool_path", return_value="/test/codex"))
        self.enterContext(patch.object(panel, "_tool_path", return_value=None))
        self.api = TaskAPI(self.manager)
        self.values = {"title": "Review", "prompt": "Review the check", "directory": self.home,
                       "mode": "inspect", "interval": "once", "start_at": 100}

    def execute(self, *, now: float = 100) -> None:
        with patch.object(tasks, "execute_prompt", return_value={"status": "completed", "message": "Done", "output": "",
                                                               **report()}):
            self.assertTrue(self.manager.tick(now=now))

    def test_scheduled_once_runs_at_its_time_and_cannot_repeat(self) -> None:
        identifier = self.manager.create(self.values, run_now=False, now=0)
        with patch.object(tasks, "execute_prompt") as execute:
            self.assertFalse(self.manager.tick(now=99))
        execute.assert_not_called()
        self.execute()
        saved = tasks.AgentTasks(self.home).snapshot()["tasks"][0]
        self.assertFalse(saved["enabled"])
        self.assertIsNone(saved["next_run"])
        self.assertEqual(saved["attempts"], 1)
        with patch.object(tasks, "execute_prompt") as execute:
            self.assertFalse(self.manager.tick(now=86400))
        execute.assert_not_called()
        with self.assertRaises(ValueError):
            self.manager.action(identifier, "resume", now=101)

    def test_manual_run_consumes_one_time_schedule_and_draft_edit_does_not_activate(self) -> None:
        identifier = self.manager.create(self.values, run_now=False, draft=True, now=0)
        self.manager.update(identifier, {"start_at": 200}, partial=True)
        self.assertFalse(self.manager.snapshot()["tasks"][0]["enabled"])
        self.manager.action(identifier, "resume", now=0)
        self.assertEqual(self.manager.snapshot()["tasks"][0]["next_run"], 200)
        self.manager.action(identifier, "run")
        self.execute(now=1)
        self.assertFalse(self.manager.tick(now=200))

    def test_recurring_first_run_anchors_cadence_and_skips_missed_intervals(self) -> None:
        self.manager.create({**self.values, "interval": "hourly"}, run_now=False, now=0)
        self.execute(now=7400)
        self.assertEqual(self.manager.snapshot()["tasks"][0]["next_run"], 10900)
        self.assertFalse(self.manager.tick(now=7401))

    def test_overdue_once_catches_up_once_and_completed_task_can_be_explicitly_rescheduled(self) -> None:
        identifier = self.manager.create(self.values, run_now=False, now=200)
        self.execute(now=201)
        self.manager.update(identifier, {"start_at": 300}, partial=True)
        self.assertFalse(self.manager.snapshot()["tasks"][0]["enabled"])
        self.manager.action(identifier, "resume", now=202)
        self.execute(now=300)
        self.assertEqual(self.manager.snapshot()["tasks"][0]["attempts"], 2)

    def test_scheduled_goal_starts_once_then_continues_using_its_goal_interval(self) -> None:
        self.manager.create({**self.values, "continue_until_done": True, "completion_criteria": "Checks pass",
                             "continuation_minutes": 5}, run_now=False, now=0)
        self.execute()
        saved = self.manager.snapshot()["tasks"][0]
        self.assertTrue(saved["enabled"])
        self.assertEqual(saved["next_run"], 400)

    def test_start_time_validation_and_timezone_conversion(self) -> None:
        settings = tasks.validate_task({**self.values, "start_at": "2026-11-01T09:00:00-06:00"}, self.home)
        utc = tasks.validate_task({**self.values, "start_at": "2026-11-01T15:00:00Z"}, self.home)
        self.assertEqual(settings["start_at"], utc["start_at"])
        for changes in ({"start_at": True}, {"start_at": "tomorrow"}, {"start_at": "2026-11-01T09:00:00"},
                        {"start_at": -1}, {"deadline_at": 100}, {"interval": "event"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                tasks.validate_task({**self.values, **changes}, self.home)

    def test_compact_list_bounds_history_and_omits_prompts_events_and_output(self) -> None:
        identifier = self.manager.create(self.values, run_now=True, now=0)
        self.execute(now=1)
        self.manager.action(identifier, "run")
        self.execute(now=2)
        result = self.api.call("list", {"summary": True, "history_limit": 1})
        self.assertEqual(len(result["runs"]), 1)
        self.assertEqual(result["tasks"][0]["attempts"], 2)
        self.assertNotIn("prompt", result["tasks"][0])
        self.assertNotIn("events", result["tasks"][0])
        self.assertNotIn("output", result["runs"][0])
        self.assertNotIn(self.values["prompt"], json.dumps(result))
        self.assertEqual(self.api.call("list", {"history_limit": 0})["runs"], [])
        for arguments in ({"summary": 1}, {"history_limit": -1}, {"history_limit": True}, {"history_limit": 41}):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                self.api.call("list", arguments)

    def test_receipts_remain_readable_after_recent_history_eviction_and_task_deletion(self) -> None:
        identifier = self.manager.create({**self.values, "persistence": "checkpoint"}, run_now=True, now=0)
        self.execute(now=1)
        run_id = self.manager.snapshot()["runs"][0]["id"]
        self.manager.action(identifier, "run")
        with patch.object(tasks, "MAX_RUNS", 1):
            self.execute(now=2)
        self.manager.action(identifier, "delete")
        retained = self.api.call("receipt", {"id": identifier, "run_id": run_id})["run"]
        self.assertEqual(retained["evidence"], report()["evidence"])
        self.assertEqual(retained["checkpoint"], report()["checkpoint"])
        self.assertEqual(retained["task"]["prompt"], self.values["prompt"])
        self.assertEqual(len(self.api.call("history", {"id": identifier})["runs"]), 2)

    def test_receipt_queries_reject_path_traversal_mismatched_ids_and_symlinks(self) -> None:
        identifier = self.manager.create(self.values, run_now=True, now=0)
        self.execute(now=1)
        run_id = self.manager.snapshot()["runs"][0]["id"]
        path = self.manager.contexts.directory(identifier) / "runs" / (run_id + ".json")
        value = json.loads(path.read_text())
        for task_id, requested in (("../outside", run_id), (identifier, "../outside")):
            with self.assertRaises(ValueError):
                self.manager.contexts.receipt(task_id, requested)
        value["task"]["id"] = "f" * 32
        path.write_text(json.dumps(value))
        with self.assertRaises(RuntimeError):
            self.manager.contexts.history(identifier)
        path.unlink()
        path.symlink_to(self.manager.path)
        with self.assertRaises(RuntimeError):
            self.manager.contexts.receipt(identifier, run_id)

    def test_receipt_panel_escapes_and_redacts_evidence_and_survives_task_removal(self) -> None:
        identifier = self.manager.create(self.values, run_now=True, now=0)
        self.execute(now=1)
        run_id = self.manager.snapshot()["runs"][0]["id"]
        path = self.manager.contexts.directory(identifier) / "runs" / (run_id + ".json")
        value = json.loads(path.read_text())
        value["evidence"] = ["password=private-marker <script>unsafe()</script>"]
        path.write_text(json.dumps(value))
        self.manager.action(identifier, "delete")
        state = WebPanelState({"host": "example.test", "username": "agent", "features": {}, "services": [], "access": []},
                              agent_home=self.home)
        query = panel.parse_agent_query("receipt=" + identifier + "." + run_id)
        with patch.object(tasks, "execute_prompt") as execute:
            document = panel.render_agents(state, _PAGE_STYLE, query)
        execute.assert_not_called()
        self.assertIn("Retained run receipt", document)
        self.assertIn("&lt;script&gt;", document)
        self.assertNotIn("private-marker", document)
        self.assertNotIn("<script>", document)
        for raw in ("receipt=../outside", "receipt=" + identifier, "receipt=" + identifier + "." + run_id + ".extra"):
            with self.assertRaises(ValueError):
                panel.parse_agent_query(raw)

    def test_cli_and_mcp_expose_compact_listing_and_receipts_without_launching_work(self) -> None:
        parser = argparse.ArgumentParser()
        add_tasks_parser(parser.add_subparsers(dest="command"))
        with patch("lib.agent_tasks_cli.TaskAPI", return_value=self.api), patch("sys.stdout", io.StringIO()):
            self.assertEqual(run_tasks_command(parser.parse_args(["tasks", "list", "--summary", "--history-limit", "0"])), 0)
        mocked = MagicMock()
        mocked.call.return_value = {"tasks": [], "runs": []}
        server = TaskMCP(mocked)
        server.request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}})
        server.request({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "basaltwater_tasks_list"}})
        mocked.call.assert_called_once_with("list", {"summary": True})


if __name__ == "__main__":
    unittest.main()
