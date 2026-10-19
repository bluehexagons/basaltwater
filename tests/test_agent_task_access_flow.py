"""Task grants, operator decisions and feedback preserve authorization boundaries."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from lib import agent_task_followups as followups
from lib import agent_tasks as tasks
from lib.agent_task_api import TaskAPI
from lib.agent_task_context import validate_report
from lib.agent_tasks_cli import add_tasks_parser, run_tasks_command
from lib.agent_tasks_mcp import TaskMCP


class TaskAccessFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = os.path.realpath(self.enterContext(tempfile.TemporaryDirectory()))
        self.manager = self.new_manager()
        self.enterContext(patch.object(tasks.AgentTasks, "available", return_value=True))
        self.enterContext(patch.object(tasks, "_tool_path", return_value="/test/codex"))
        self.enterContext(patch.object(tasks, "task_policy_issues", return_value=[]))
        self.enterContext(patch.object(followups, "task_policy_issues", return_value=[]))
        self.values = {"title": "Inspect environment", "prompt": "Inspect the local environment",
                       "directory": self.home, "mode": "inspect", "interval": "once"}
        self.proposal = {"title": "Repair environment", "reason": "A specific repair is needed",
                         "prompt": "Inspect evidence, apply the repair, verify it and retain rollback instructions.",
                         "directory": self.home, "mode": "workspace", "workspace": "run-directory",
                         "network": False, "temporary_files": False, "writable_directories": [],
                         "operator_requirements": [], "service_restarts": []}

    def new_manager(self) -> tasks.AgentTasks:
        manager = tasks.AgentTasks(self.home)
        self.addCleanup(manager.close)
        return manager

    def record_proposal(self, **changes: object) -> tuple[str, str]:
        identifier = self.manager.create(self.values, run_now=True)
        report = validate_report({"outcome": "blocked", "summary": "Repair needs review",
            "checkpoint": "Retain the diagnostic evidence", "evidence": ["Local check failed"],
            "retry_after_minutes": None, "follow_up": {**self.proposal, **changes},
            "environment_feedback": ""})
        with patch.object(tasks, "execute_prompt", return_value={
            "status": "completed", "output": "", "message": "Done", **report,
        }):
            self.assertTrue(self.manager.tick())
        return identifier, self.manager.snapshot()["runs"][-1]["id"]

    def test_write_grants_are_narrow_owned_unshared_and_do_not_follow_symlinks(self) -> None:
        root = Path(self.home)
        allowed = root / ".nvm"
        allowed.mkdir(mode=0o700)
        (allowed / "versions").mkdir(mode=0o700)
        shared = root / "shared"
        shared.mkdir()
        shared.chmod(0o770)
        (shared / "private-child").mkdir(mode=0o700)
        link = root / "linked"
        link.symlink_to(allowed, target_is_directory=True)
        credentials = root / ".ssh"
        credentials.mkdir(mode=0o700)
        (credentials / "keys").mkdir(mode=0o700)
        (root / ".local" / "state" / "basaltwater").mkdir(parents=True, mode=0o700)
        values = {**self.values, "mode": "workspace"}
        task = tasks.validate_task({**values, "writable_directories": [str(allowed), str(allowed)]}, self.home)
        self.assertEqual(task["writable_directories"], [str(allowed)])
        for path in (root, root.parent, credentials, credentials / "keys", root / ".local",
                     root / ".local" / "state", link, link / "versions", shared, shared / "private-child"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                tasks.validate_task({**values, "writable_directories": [str(path)]}, self.home)
        original_lstat = Path.lstat

        def unowned(path: Path, *args: object, **kwargs: object) -> object:
            info = original_lstat(path, *args, **kwargs)
            if path == allowed:
                return SimpleNamespace(st_mode=info.st_mode, st_uid=os.geteuid() + 1)
            return info

        with patch.object(Path, "lstat", unowned), self.assertRaises(ValueError):
            tasks.validate_task({**values, "writable_directories": [str(allowed)]}, self.home)

    def test_codex_command_grants_exact_additional_paths_for_fresh_and_resume(self) -> None:
        roots = [Path(self.home) / name for name in (".nvm", "cache with spaces")]
        for root in roots:
            root.mkdir(mode=0o700)
        task = tasks.validate_task({**self.values, "mode": "workspace",
            "persistence": "conversation", "writable_directories": list(map(str, roots))}, self.home)
        for session in (None, "01234567-89ab-cdef-0123-456789abcdef"):
            with self.subTest(session=session):
                runtime = {"session_id": session, "schema_path": self.home + "/schema.json"}
                command = tasks.codex_command({**task, "_runtime": runtime}, "/test/codex")
                grants = [command[index + 1] for index, arg in enumerate(command) if arg == "--add-dir"]
                self.assertEqual(grants, list(map(str, roots)))
                self.assertEqual(command[-1], "-")
                self.assertNotIn(task["prompt"], command)
                self.assertEqual("resume" in command, session is not None)
                if session:
                    self.assertEqual(command[command.index("resume") + 1], session)

    def test_approval_is_idempotent_across_controllers_and_persists_provenance(self) -> None:
        requirement = "Operator must provision the private repair directory"
        identifier, run_id = self.record_proposal(operator_requirements=[requirement])
        run = self.manager.contexts.receipt(identifier, run_id)
        from common.service_tools import web_panel_agents as agents

        page = agents._render_follow_up(run, self.manager, "csrf")
        self.assertIn(requirement, page)
        self.assertIn("Approve once confirms", page)
        self.assertIn('value="follow-up-approve"', page)
        self.assertIn("Review follow-up task", page)
        with patch.object(followups, "task_policy_issues", return_value=["Network denied by policy"]):
            with self.assertRaisesRegex(ValueError, "Network denied by policy"):
                followups.decide_follow_up(self.manager, identifier, run_id, "approve")
        self.assertEqual(followups.follow_up_state(self.manager, identifier, run_id)["status"], "pending")
        with patch.object(followups, "exchange") as broker:
            child = followups.decide_follow_up(self.manager, identifier, run_id, "approve")
            other = self.new_manager()
            self.assertEqual(followups.decide_follow_up(other, identifier, run_id, "approve"), child)
        broker.assert_not_called()
        saved = other.snapshot()["tasks"]
        self.assertEqual(len(saved), 2)
        task = next(item for item in saved if item["id"] == child)
        self.assertTrue(task["queued"])
        self.assertEqual(task["operator_requirements"], [])
        self.assertEqual(task["writable_directories"], [])
        self.assertEqual((task["source_task_id"], task["source_run_id"], task["max_runs"]),
                         (identifier, run_id, 1))
        self.assertEqual(followups.follow_up_state(other, identifier, run_id)["status"], "approved")
        self.assertEqual(followups.follow_up_state(other, identifier, run_id)["acknowledged_requirements"], [requirement])
        self.assertEqual(self.manager.contexts.receipt(identifier, run_id), run)
        with self.assertRaises(ValueError):
            followups.decide_follow_up(other, identifier, run_id, "block")

    def test_block_is_terminal_after_reload_and_never_creates_work(self) -> None:
        identifier, run_id = self.record_proposal()
        self.assertIsNone(followups.decide_follow_up(self.manager, identifier, run_id, "block"))
        other = self.new_manager()
        for decision in ("block", "approve", "request-privilege"):
            with self.subTest(decision=decision), patch.object(followups, "exchange") as broker:
                with self.assertRaisesRegex(ValueError, "blocked"):
                    followups.decide_follow_up(other, identifier, run_id, decision)
                broker.assert_not_called()
        self.assertEqual(followups.follow_up_state(other, identifier, run_id)["status"], "blocked")
        self.assertEqual(len(other.snapshot()["tasks"]), 1)

    def test_drafts_retain_operator_requirements_and_stop_provider_launch(self) -> None:
        requirement = "Operator must provision the private repair directory"
        identifier, run_id = self.record_proposal(operator_requirements=[requirement])
        child = followups.decide_follow_up(self.manager, identifier, run_id, "approve", draft=True, run_now=False)
        task = next(item for item in self.manager.snapshot()["tasks"] if item["id"] == child)
        self.assertTrue(task["draft"])
        self.assertFalse(task["queued"])
        self.assertEqual(task["operator_requirements"], [requirement])
        self.assertEqual(followups.follow_up_state(self.manager, identifier, run_id)["acknowledged_requirements"], [])
        with (patch.object(tasks.os, "geteuid", return_value=1234),
              patch.object(tasks.subprocess, "Popen") as spawn):
            result = tasks.execute_prompt(task, self.home, threading.Event())
        spawn.assert_not_called()
        self.assertEqual(result["outcome"], "blocked")
        self.assertIn(requirement, result["evidence"])

    def test_service_restart_must_succeed_separately_with_the_exact_plan_even_for_drafts(self) -> None:
        unit = "example.service"
        identifier, run_id = self.record_proposal(service_restarts=[unit],
            operator_requirements=["Confirm the service restart maintenance window"])
        with patch.object(followups, "exchange") as broker:
            for draft in (False, True):
                with self.subTest(missing_request=True, draft=draft), self.assertRaises(ValueError):
                    followups.decide_follow_up(self.manager, identifier, run_id, "approve",
                                              draft=draft, run_now=not draft)
            broker.assert_not_called()
            broker.return_value = {"id": "request-1", "state": "pending", "review_url": "/review/request-1"}
            followups.decide_follow_up(self.manager, identifier, run_id, "request-privilege")
            followups.decide_follow_up(self.new_manager(), identifier, run_id, "request-privilege")
            self.assertEqual(broker.call_count, 2)
            self.assertEqual(broker.call_args_list[0].args[0], {"action": "request", "operation": "service.restart",
                "parameters": {"unit": unit}, "reason": "Task follow-up: Repair environment"})
            plan = {"operation": "service.restart", "parameters": {"unit": unit}}
            invalid = [
                {"state": "approved", "plan": plan},
                {"state": "failed", "plan": plan},
                {"state": "succeeded", "plan": {**plan, "operation": "service.stop"}},
                {"state": "succeeded", "plan": {**plan, "parameters": {"unit": "other.service"}}},
                {"state": "succeeded", "plan": {**plan, "parameters": {"unit": unit, "extra": True}}},
            ]
            for reply in invalid:
                broker.return_value = reply
                for draft in (False, True):
                    with self.subTest(reply=reply, draft=draft), self.assertRaisesRegex(ValueError, "not succeeded"):
                        followups.decide_follow_up(self.new_manager(), identifier, run_id, "approve",
                                                  draft=draft, run_now=not draft)
                    self.assertEqual(len(self.manager.snapshot()["tasks"]), 1)
            broker.return_value = {"state": "succeeded", "plan": plan}
            child = followups.decide_follow_up(self.new_manager(), identifier, run_id, "approve")
            self.assertEqual(broker.call_args.args[0], {"action": "status", "id": "request-1"})
        self.assertEqual(followups.follow_up_state(self.manager, identifier, run_id)["task_id"], child)
        self.assertEqual(len(self.manager.snapshot()["tasks"]), 2)

    def test_legacy_proposal_requires_operator_review_before_one_click_approval(self) -> None:
        requirement = "Review and authorize this separate investigation"
        self.proposal["operator_requirements"] = [requirement]
        for name in ("writable_directories", "service_restarts"):
            self.proposal.pop(name)
        identifier, run_id = self.record_proposal()
        prepared = followups.prepare_follow_up(self.manager, identifier, run_id, review=True)
        self.assertEqual(prepared["operator_requirements"], [requirement])
        self.assertEqual(prepared["writable_directories"], [])
        self.assertEqual(prepared["service_restarts"], [])
        with self.assertRaisesRegex(ValueError, "Review this older proposal before approval"):
            followups.decide_follow_up(self.manager, identifier, run_id, "approve")
        self.assertEqual(len(self.manager.snapshot()["tasks"]), 1)
        child = followups.decide_follow_up(self.manager, identifier, run_id, "approve", settings=prepared)
        task = next(item for item in self.manager.snapshot()["tasks"] if item["id"] == child)
        self.assertTrue(task["queued"])
        self.assertEqual(task["operator_requirements"], [])
        self.assertEqual(task["writable_directories"], [])
        self.assertEqual(followups.follow_up_state(self.manager, identifier, run_id)["acknowledged_requirements"], [requirement])
        self.assertEqual((task["mode"], task["workspace"], task["network"]),
                         (self.proposal["mode"], self.proposal["workspace"], self.proposal["network"]))
        self.assertEqual(followups.decide_follow_up(self.new_manager(), identifier, run_id, "approve"), child)

    def test_invalid_proposal_can_be_blocked_after_its_directory_disappears(self) -> None:
        directory = Path(self.home) / "removed"
        directory.mkdir()
        identifier, run_id = self.record_proposal(directory=str(directory))
        directory.rmdir()
        followups.decide_follow_up(self.manager, identifier, run_id, "block")
        self.assertEqual(followups.follow_up_state(self.manager, identifier, run_id)["status"], "blocked")

    def test_review_corrects_invalid_write_grants_without_losing_provenance(self) -> None:
        identifier, run_id = self.record_proposal(writable_directories=["/var/lib/example"],
            operator_requirements=["Approve this repair"])
        with self.assertRaises(ValueError):
            followups.decide_follow_up(self.manager, identifier, run_id, "approve")
        reviewed = followups.prepare_follow_up(self.manager, identifier, run_id, review=True)
        self.assertEqual(reviewed["writable_directories"], ["/var/lib/example"])
        reviewed["writable_directories"] = []
        child = followups.decide_follow_up(self.manager, identifier, run_id, "approve", settings=reviewed)
        task = next(item for item in self.manager.snapshot()["tasks"] if item["id"] == child)
        self.assertEqual(task["source_run_id"], run_id)
        self.assertEqual(task["writable_directories"], [])

    def test_later_success_does_not_hide_an_unresolved_retained_proposal(self) -> None:
        identifier, run_id = self.record_proposal()
        self.manager.action(identifier, "run")
        with patch.object(tasks, "execute_prompt", return_value={
            "status": "completed", "outcome": "complete", "summary": "Latest diagnostics succeeded",
            "output": "", "message": "Done", "follow_up": None,
        }):
            self.manager.tick()
        pending = self.manager.contexts.pending_follow_ups(identifier)
        self.assertEqual([run["id"] for run in pending], [run_id])
        from common.service_tools import web_panel_agents as agents

        state = self.manager.snapshot()
        self.assertTrue(agents._task_needs_review(state["tasks"][0], state["runs"], {identifier: pending}))
        followups.decide_follow_up(self.manager, identifier, run_id, "block")
        self.assertEqual(self.manager.contexts.pending_follow_ups(identifier), [])

    def test_definitive_submission_failures_can_retry_and_block_cancels_owned_request(self) -> None:
        identifier, run_id = self.record_proposal(service_restarts=["example.service"])
        with patch.object(followups, "exchange", side_effect=FileNotFoundError):
            with self.assertRaises(FileNotFoundError):
                followups.decide_follow_up(self.manager, identifier, run_id, "request-privilege")
        self.assertEqual(followups.follow_up_state(self.manager, identifier, run_id)["requests"], {})
        with patch.object(followups, "exchange", return_value={"id": "request-2", "state": "pending"}) as broker:
            followups.decide_follow_up(self.manager, identifier, run_id, "request-privilege")
            broker.return_value = {"id": "request-2", "state": "cancelled"}
            followups.decide_follow_up(self.manager, identifier, run_id, "block")
            self.assertEqual(broker.call_args.args[0], {"action": "cancel", "id": "request-2"})

    def test_pending_proposals_survive_control_reclamation_and_compact_listing(self) -> None:
        identifier, run_id = self.record_proposal()
        api = TaskAPI(self.manager)
        run = api.call("list", {"summary": True})["runs"][-1]
        self.assertTrue(run["follow_up_requested"])
        self.assertEqual(run["follow_up_status"], "pending")
        with patch.object(tasks, "MAX_TASKS", 1), self.assertRaisesRegex(ValueError, "saved tasks"):
            self.manager.create(self.values, run_now=True)
        followups.decide_follow_up(self.manager, identifier, run_id, "block")
        with patch.object(tasks, "MAX_TASKS", 1):
            child = self.manager.create(self.values, run_now=True)
        self.assertEqual([item["id"] for item in self.manager.snapshot()["tasks"]], [child])
        self.assertEqual(self.manager.contexts.receipt(identifier, run_id)["id"], run_id)

    def test_maximum_utf8_proposal_fits_the_task_prompt_after_composition(self) -> None:
        prompt = "é" * 1900
        identifier, run_id = self.record_proposal(prompt=prompt)
        prepared = followups.prepare_follow_up(self.manager, identifier, run_id)
        self.assertLessEqual(len(prepared["prompt"].encode()), tasks.MAX_PROMPT_BYTES)
        self.assertTrue(prepared["prompt"].endswith(prompt))
        child = followups.decide_follow_up(self.manager, identifier, run_id, "approve")
        self.assertIsNotNone(child)

    def test_report_feedback_is_delivered_by_runner_with_no_provider_inbox_grant(self) -> None:
        identifier = self.manager.create(self.values, run_now=True)
        feedback = "The restricted environment cannot read the service status socket"
        report = validate_report({"outcome": "blocked", "summary": "Operator action needed",
            "checkpoint": "Check the socket", "evidence": ["Socket access denied"],
            "retry_after_minutes": None, "follow_up": None, "environment_feedback": feedback})
        with patch("lib.agent_inbox.AgentInbox") as inbox:
            inbox.return_value.feedback.return_value = {"id": "feedback-1"}

            def provider(task: dict, home: str, cancel: object) -> dict:
                inbox.assert_not_called()
                self.assertEqual(task["mode"], "inspect")
                self.assertEqual(task["writable_directories"], [])
                command = tasks.codex_command(task, "/test/codex")
                self.assertNotIn("--add-dir", command)
                return {"status": "completed", "message": "Done", "output": "", **report}

            with patch.object(tasks, "execute_prompt", side_effect=provider):
                self.assertTrue(self.manager.tick())
            run = self.manager.snapshot()["runs"][-1]
            inbox.assert_called_once_with(self.home)
            inbox.return_value.feedback.assert_called_once_with("task-" + identifier,
                f"Task: Inspect environment\nRun: {run['id']}\n\n{feedback}")
        self.assertEqual(run["feedback_message_id"], "feedback-1")
        self.assertEqual(self.manager.contexts.receipt(identifier, run["id"])["environment_feedback"], feedback)

    def test_allow_feedback_cli_exposes_feedback_without_task_mutations(self) -> None:
        parser = argparse.ArgumentParser()
        add_tasks_parser(parser.add_subparsers(dest="command", required=True))
        args = parser.parse_args(["tasks", "mcp", "--allow-feedback"])
        with patch("lib.agent_tasks_mcp.serve", return_value=0) as serve:
            self.assertEqual(run_tasks_command(args), 0)
        serve.assert_called_once_with(allow_write=False, allow_feedback=True)
        server = TaskMCP(TaskAPI(self.manager), allow_feedback=True)

        def request(method: str, **params: object) -> dict:
            return server.request({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})

        request("initialize")
        tools = request("tools/list")["result"]["tools"]
        writes = [tool["name"] for tool in tools if not tool["annotations"]["readOnlyHint"]]
        self.assertEqual(writes, ["basaltwater_operator_feedback"])
        with patch("lib.agent_inbox.AgentInbox") as inbox, patch.object(self.manager, "create") as create:
            inbox.return_value.feedback.return_value = {"id": "feedback-2"}
            reply = request("tools/call", name="basaltwater_operator_feedback",
                            arguments={"sender": "diagnostic", "message": "Service status unavailable"})
            self.assertFalse(reply["result"]["isError"])
            self.assertEqual(json.loads(reply["result"]["content"][0]["text"]), {"id": "feedback-2"})
            inbox.return_value.feedback.assert_called_once_with("diagnostic", "Service status unavailable")
            reply = request("tools/call", name="basaltwater_tasks_create", arguments={"task": self.values})
            self.assertTrue(reply["result"]["isError"])
            create.assert_not_called()


if __name__ == "__main__":
    unittest.main()
