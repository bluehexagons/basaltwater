"""Agent scheduling access, portable handoffs and strict streamed settings."""

from __future__ import annotations

import argparse
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lib.agent_task_api import TaskAPI
from lib.agent_task_context import MAX_CHECKPOINT_BYTES
from lib.agent_tasks import AgentTasks, validate_task
from lib.agent_tasks_cli import add_tasks_parser, run_tasks_command
from lib.agent_tasks_mcp import TaskMCP, serve
from lib.atomic_io import write_json_atomic


class AgentSchedulingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(AgentTasks, "available", return_value=True))
        self.enterContext(patch("lib.agent_tasks._tool_path", return_value="/test/codex"))
        self.manager = AgentTasks(self.home)
        self.api = TaskAPI(self.manager)
        self.values = {"title": "Continue the repair", "prompt": "Repair checks; leave changes for review.",
                       "directory": self.home, "mode": "inspect", "interval": "hourly",
                       "persistence": "checkpoint", "initial_checkpoint": "T3 findings: reproduce the parser failure."}
        self.parser = argparse.ArgumentParser()
        add_tasks_parser(self.parser.add_subparsers(dest="command"))

    def cli(self, argv: list[str], content: str | bytes) -> tuple[int, dict]:
        output = io.StringIO()
        source = io.TextIOWrapper(io.BytesIO(content)) if isinstance(content, bytes) else io.StringIO(content)
        with (source, patch("lib.agent_tasks_cli.TaskAPI", return_value=self.api),
              patch("sys.stdin", source), patch("sys.stdout", output)):
            status = run_tasks_command(self.parser.parse_args(["tasks", *argv, "--json"]))
        return status, json.loads(output.getvalue())

    def run_task(self, identifier: str, checkpoint: str = "New run findings") -> dict:
        self.manager.action(identifier, "run")
        result = {"status": "completed", "output": "Progress", "message": "Checked",
                  "outcome": "progress", "checkpoint": checkpoint, "evidence": ["Focused checks reproduced the error"],
                  "retry_after_minutes": None}
        with patch("lib.agent_tasks.execute_prompt", return_value=result) as execute:
            self.assertTrue(self.manager.tick())
        return execute.call_args.args[0]["_runtime"]

    def create(self, **changes: object) -> str:
        return self.api.call("create", {"task": {**self.values, **changes}})["id"]

    def test_stdin_round_trip_schedules_then_partially_updates_without_intermediate_files(self) -> None:
        settings = {**self.values, "interval": "once", "start_at": "2027-01-01T09:00:00-06:00"}
        argv = ["create", "-", "--schedule", "--request-key", "t3-check-1"]
        content = json.dumps(settings).encode()
        status, result = self.cli(argv, content)
        self.assertEqual(status, 0)
        identifier = result["id"]
        self.assertEqual(self.cli(argv, content)[1]["id"], identifier)
        task = self.api.call("show", {"id": identifier})["task"]
        self.assertTrue(task["enabled"])
        self.assertFalse(task["queued"])
        self.assertEqual(task["next_run"], task["start_at"])
        status, _ = self.cli(["update", identifier, "-"], '{"timeout_minutes": 10}')
        self.assertEqual(status, 0)
        task = self.api.call("show", {"id": identifier})["task"]
        self.assertEqual(task["timeout_minutes"], 10)
        self.assertEqual(task["initial_checkpoint"], self.values["initial_checkpoint"])
        self.assertEqual(len(self.manager.snapshot()["tasks"]), 1)
        self.assertEqual(list(Path(self.home).glob("*.json")), [])

    def test_streamed_settings_reject_oversize_ambiguous_and_invalid_json_before_mutation(self) -> None:
        requests = ('{"title":"a","title":"b"}', '{"start_at": NaN}', '{"start_at": 1e10000}',
                    "[" * 2000 + "0" + "]" * 2000, '"é"' * 32768, '[]', b"\xff")
        for content in requests:
            with self.subTest(content=str(content)[:40]):
                status, result = self.cli(["create", "-", "--schedule"], content)
                self.assertEqual(status, 1)
                self.assertFalse(result["ok"])
        self.assertFalse(self.manager.parent.exists())

    def test_handoff_seeds_once_then_new_findings_continue_across_managers(self) -> None:
        identifier = self.create()
        self.assertTrue(self.manager.context_snapshot(identifier)["initial_checkpoint_pending"])
        self.assertEqual(self.run_task(identifier)["checkpoint"], self.values["initial_checkpoint"])
        self.assertFalse(self.manager.context_snapshot(identifier)["initial_checkpoint_pending"])
        self.manager = AgentTasks(self.home)
        self.assertEqual(self.run_task(identifier, "Another checkpoint")["checkpoint"], "New run findings")
        receipt = self.manager.contexts.receipt(identifier, self.manager.snapshot()["runs"][0]["id"])
        self.assertEqual(receipt["task"]["initial_checkpoint"], self.values["initial_checkpoint"])

    def test_failed_provider_keeps_handoff_until_a_report_replaces_it(self) -> None:
        identifier = self.create()
        self.manager.action(identifier, "run")
        with patch("lib.agent_tasks.execute_prompt", return_value={"status": "failed", "output": "", "message": "Unavailable"}):
            self.manager.tick()
        self.assertEqual(self.run_task(identifier)["checkpoint"], self.values["initial_checkpoint"])

    def test_reset_before_first_attempt_does_not_reapply_handoff(self) -> None:
        identifier = self.create()
        self.api.call("action", {"id": identifier, "action": "reset-context"})
        self.assertFalse(self.manager.context_snapshot(identifier)["initial_checkpoint_pending"])
        self.assertEqual(self.run_task(identifier)["checkpoint"], "")

    def test_reset_and_persistence_change_do_not_resurrect_original_handoff(self) -> None:
        identifier = self.create()
        self.run_task(identifier)
        self.api.call("action", {"id": identifier, "action": "reset-context"})
        self.assertEqual(self.run_task(identifier)["checkpoint"], "")
        self.api.call("update", {"id": identifier, "task": {"persistence": "conversation"}})
        self.assertEqual(self.run_task(identifier)["checkpoint"], "")
        self.api.call("update", {"id": identifier, "task": {"persistence": "fresh"}})
        self.assertEqual(self.run_task(identifier)["checkpoint"], "")

    def test_fresh_task_consumes_only_the_first_handoff(self) -> None:
        identifier = self.create(persistence="fresh")
        self.assertEqual(self.run_task(identifier)["checkpoint"], self.values["initial_checkpoint"])
        self.assertEqual(self.run_task(identifier)["checkpoint"], "")

    def test_handoff_is_editable_before_start_and_preserved_by_older_forms(self) -> None:
        identifier = self.create()
        self.api.call("update", {"id": identifier, "task": {"initial_checkpoint": "Corrected findings"}})
        self.assertEqual(self.run_task(identifier)["checkpoint"], "Corrected findings")
        values = {key: value for key, value in self.values.items() if key != "initial_checkpoint"}
        self.manager.update(identifier, {**values, "title": "Updated title"})
        self.assertEqual(self.manager.snapshot()["tasks"][0]["initial_checkpoint"], "Corrected findings")
        with self.assertRaisesRegex(ValueError, "after an attempt"):
            self.api.call("update", {"id": identifier, "task": {"initial_checkpoint": "Old evidence again"}})

    def test_old_context_without_marker_preserves_existing_checkpoint(self) -> None:
        identifier = self.create()
        self.manager.contexts.save(identifier, {"version": 1, "checkpoint": "Established context", "session_id": None,
                                                 "workspace": None, "persistence": "checkpoint"})
        self.assertFalse(self.manager.context_snapshot(identifier)["initial_checkpoint_pending"])
        self.assertEqual(self.run_task(identifier)["checkpoint"], "Established context")

    def test_handoff_utf8_limit_and_marker_validation_preserve_bad_records(self) -> None:
        valid = {**self.values, "initial_checkpoint": "é" * (MAX_CHECKPOINT_BYTES // 2)}
        self.assertEqual(validate_task(valid, self.home)["initial_checkpoint"], valid["initial_checkpoint"])
        for handoff in ("é" * (MAX_CHECKPOINT_BYTES // 2 + 1), "a\x00b", True):
            with self.subTest(handoff=str(handoff)[:30]), self.assertRaises(ValueError):
                validate_task({**self.values, "initial_checkpoint": handoff}, self.home)
        identifier = self.create()
        path = self.manager.contexts.directory(identifier, create=True) / "context.json"
        record = {"version": 1, "checkpoint": "", "session_id": None, "workspace": None, "initial_checkpoint_applied": "yes"}
        write_json_atomic(str(path), record)
        with self.assertRaisesRegex(RuntimeError, "initial checkpoint marker"):
            self.manager.context_snapshot(identifier)
        self.assertEqual(json.loads(path.read_text()), record)

    def test_write_mcp_creates_a_bounded_goal_with_portable_context_and_shared_controls(self) -> None:
        server = TaskMCP(self.api, allow_write=True)
        server.request({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        settings = {**self.values, "interval": "once", "continue_until_done": True,
                    "completion_criteria": "Checks pass with recorded evidence", "max_runs": 2}
        reply = server.request({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "basaltwater_tasks_create", "arguments": {"task": settings, "draft": False, "request_key": "goal-1"}}})
        self.assertFalse(reply["result"]["isError"])
        identifier = json.loads(reply["result"]["content"][0]["text"])["id"]
        self.assertTrue(self.api.call("show", {"id": identifier})["task"]["enabled"])
        self.assertEqual(self.run_task(identifier)["checkpoint"], settings["initial_checkpoint"])
        self.api.call("action", {"id": identifier, "action": "pause"})
        self.assertFalse(self.manager.snapshot()["tasks"][0]["enabled"])

    def test_mcp_rejects_duplicate_keys_and_nonfinite_numbers_without_dispatch(self) -> None:
        initialize = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        request = ('{"jsonrpc":"2.0","id":2,"method":"tools/call","params":'
                   '{"name":"basaltwater_tasks_create","arguments":{"task":')
        for invalid in ('{"title":"a","title":"b"}', '{"start_at": Infinity}'):
            output = io.StringIO()
            serve(allow_write=True, source=io.StringIO(initialize + "\n" + request + invalid + "}}}\n"),
                  destination=output, api=self.api)
            reply = json.loads(output.getvalue().splitlines()[-1])
            self.assertEqual(reply["error"]["code"], -32700)
        self.assertFalse(self.manager.parent.exists())


if __name__ == "__main__":
    unittest.main()
