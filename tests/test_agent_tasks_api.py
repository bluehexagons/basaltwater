"""CLI, MCP and independent-worker access to the same persistent task API."""

from __future__ import annotations

import argparse
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from lib.agent_task_api import TaskAPI, worker_status
from lib.agent_task_worker import run_worker
from lib.agent_tasks import AgentTasks
from lib.agent_tasks_cli import add_tasks_parser, run_tasks_command
from lib.agent_tasks_mcp import TaskMCP, serve


class TaskAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(AgentTasks, "available", return_value=True))
        self.enterContext(patch("lib.agent_tasks._tool_path", return_value="/test/codex"))
        self.api = TaskAPI(AgentTasks(self.home))
        self.values = {"title": "Review", "prompt": "Inspect the project", "directory": self.home,
                       "mode": "inspect", "interval": "hourly"}

    def test_creation_retries_return_same_task_and_never_queue_an_extra_run(self) -> None:
        arguments = {"task": self.values, "run_now": True, "request_key": "create-1"}
        first = self.api.call("create", arguments)
        second = TaskAPI(AgentTasks(self.home)).call("create", arguments)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(self.api.call("list", {})["tasks"]), 1)
        with self.assertRaises(ValueError):
            self.api.call("create", {**arguments, "task": {**self.values, "prompt": "Different work"}})

    def test_partial_update_preserves_other_settings_counters_and_activation(self) -> None:
        identifier = self.api.call("create", {"task": self.values})["id"]
        self.api.call("update", {"id": identifier, "task": {"persistence": "checkpoint", "timeout_minutes": 120}})
        result = self.api.call("show", {"id": identifier})
        self.assertEqual(result["task"]["prompt"], self.values["prompt"])
        self.assertEqual(result["task"]["persistence"], "checkpoint")
        self.assertTrue(result["task"]["draft"])
        self.assertFalse(result["task"]["enabled"])
        self.assertEqual(result["context"]["checkpoint"], "")
        self.assertEqual(result["task"]["attempts"], 0)

    def test_read_only_calls_do_not_create_storage_or_start_work(self) -> None:
        with patch("lib.agent_tasks.execute_prompt") as execute:
            self.api.call("list", {})
            self.api.call("capabilities", {})
        execute.assert_not_called()
        self.assertFalse(self.api.manager.parent.exists())

    def test_capabilities_do_not_claim_upcoming_t3_support_or_authentication(self) -> None:
        runtime = Path(self.home) / ".t3/runtime"
        runtime.mkdir(parents=True)
        (runtime / "service-state.json").write_text(json.dumps({"activeVersion": "0.0.45", "private": "not-returned"}))
        result = self.api.call("capabilities", {})
        self.assertEqual(result["t3"]["active_version"], "0.0.45")
        self.assertFalse(result["t3"]["native_thread_binding"])
        self.assertNotIn("not-returned", json.dumps(result))

    def test_worker_lease_reports_running_without_creating_or_starting_a_service(self) -> None:
        manager = self.api.manager
        with patch("lib.agent_tasks.threading.Thread"):
            manager.start()
            manager._thread.is_alive.return_value = False
        try:
            self.assertTrue(worker_status(AgentTasks(self.home))["running"])
        finally:
            manager.close()
        self.assertFalse(worker_status(AgentTasks(self.home))["running"])

    def test_cli_reads_bounded_json_and_uses_same_api(self) -> None:
        parser = argparse.ArgumentParser()
        add_tasks_parser(parser.add_subparsers(dest="command"))
        path = Path(self.home) / "task.json"
        path.write_text(json.dumps(self.values))
        args = parser.parse_args(["tasks", "create", str(path), "--json"])
        output = io.StringIO()
        with patch("lib.agent_tasks_cli.TaskAPI", return_value=self.api), patch("sys.stdout", output):
            self.assertEqual(run_tasks_command(args), 0)
        identifier = json.loads(output.getvalue())["id"]
        self.assertTrue(self.api.call("show", {"id": identifier})["task"]["draft"])
        with patch("lib.agent_tasks_cli.TaskAPI", return_value=self.api), patch("sys.stdout", io.StringIO()):
            self.assertEqual(run_tasks_command(parser.parse_args(["tasks", "resume", identifier, "--json"])), 0)
        self.assertTrue(self.api.call("show", {"id": identifier})["task"]["enabled"])

    def test_api_rejects_unknown_arguments_and_settings(self) -> None:
        for operation, arguments in (("shell", {}), ("list", {"command": "anything"}),
                                     ("create", {"task": {**self.values, "persistance": "conversation"}})):
            with self.subTest(operation=operation), self.assertRaises(ValueError):
                self.api.call(operation, arguments)


class TaskMCPTests(unittest.TestCase):
    def setUp(self) -> None:
        self.api = MagicMock()
        self.server = TaskMCP(self.api)
        self.server.request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}})

    def test_read_only_catalog_omits_mutations_and_direct_calls_are_denied(self) -> None:
        catalog = self.server.request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertEqual(len(catalog["result"]["tools"]), 5)
        result = self.server.request({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
            "name": "basaltwater_tasks_action", "arguments": {"id": "a" * 32, "action": "run"},
        }})
        self.assertTrue(result["result"]["isError"])
        self.api.call.assert_not_called()

    def test_write_catalog_dispatches_validated_arguments_without_executing_work(self) -> None:
        self.server.allow_write = True
        self.api.call.return_value = {"id": "a" * 32}
        request = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "basaltwater_tasks_action", "arguments": {"id": "a" * 32, "action": "pause"},
        }}
        result = self.server.request(request)
        self.assertFalse(result["result"]["isError"])
        self.api.call.assert_called_once_with("action", {"id": "a" * 32, "action": "pause"})
        request["params"]["arguments"]["shell"] = "unsupported"
        self.assertTrue(self.server.request(request)["result"]["isError"])

    def test_stdio_handles_initialization_notifications_malformed_and_oversized_requests(self) -> None:
        requests = [
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "unknown"}}),
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}), "invalid JSON",
            "x" * (70 * 1024), json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"}),
        ]
        output = io.StringIO()
        self.assertEqual(serve(source=io.StringIO("\n".join(requests) + "\n"), destination=output, api=self.api), 0)
        responses = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(len(responses), 4)
        self.assertEqual(responses[0]["result"]["protocolVersion"], "2025-11-25")
        self.assertEqual(responses[1]["error"]["code"], -32700)
        self.assertEqual(responses[2]["error"]["code"], -32600)
        self.assertEqual(responses[3]["result"], {})

    def test_reply_limit_includes_jsonrpc_encoding_and_rejects_invalid_ids(self) -> None:
        self.api.call.return_value = {"output": "\"" * 260}
        with patch("lib.agent_tasks_mcp.MAX_REPLY_BYTES", 1024):
            reply = self.server.request({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {
                "name": "basaltwater_tasks_list", "arguments": {},
            }})
        self.assertTrue(reply["result"]["isError"])
        invalid = self.server.request({"jsonrpc": "2.0", "id": {}, "method": "ping"})
        self.assertEqual(invalid["error"]["code"], -32600)

    def test_worker_once_owns_lease_and_closes_cleanly(self) -> None:
        manager = MagicMock()
        manager.available.return_value = True
        manager.error = ""
        manager.tick.side_effect = [True, False]
        with patch("lib.agent_task_worker.signal.signal"):
            self.assertEqual(run_worker(once=True, manager=manager), 0)
        manager.start.assert_called_once_with(once=True)
        manager.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
