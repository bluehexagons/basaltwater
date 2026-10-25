"""Permission proposals remain inert until an operator submits a separate task."""

from __future__ import annotations

import json
import os
import socketserver
import tempfile
import threading
import tomllib
import unittest
from io import BytesIO
from unittest.mock import MagicMock, Mock, patch
from urllib.parse import urlencode

from common.service_tools import web_panel_agents as agents
from common.service_tools import web_panel_service as panel
from common.service_tools.web_panel_features import PanelFeatures
from lib import agent_tasks as tasks
from lib import privilege_client
from lib.agent_task_api import TaskAPI
from lib.agent_task_context import CodexEvents, FOLLOW_UP_WARNING, REPORT_SCHEMA, validate_follow_up, validate_report
from lib.agent_task_followups import (
    ServiceApprovalPending, ServiceApprovalUnavailable, decide_follow_up, follow_up_state, prepare_follow_up,
)
from lib.privilege_client import PrivilegeRequestUnconfirmed
from tests.test_agent_panel_tools import TaskForm
from tests.test_agent_task_persistence import report


class TaskFollowUpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = os.path.realpath(self.enterContext(tempfile.TemporaryDirectory()))
        self.state = panel.WebPanelState({"host": "host.example", "username": "operator",
            "system_type": "agent_vm", "features": {}, "services": [], "access": []}, agent_home=self.home)
        self.manager = self.state.agent_tasks
        self.addCleanup(self.manager.close)
        self.enterContext(patch.object(tasks.AgentTasks, "available", return_value=True))
        self.enterContext(patch.object(tasks, "_tool_path", return_value="/test/codex"))
        self.enterContext(patch.object(agents, "_tool_path", return_value="/test/codex"))
        self.enterContext(patch("lib.agent_task_followups.load_policy", return_value={"services": {}}))
        self.enterContext(patch.object(self.state, "features", return_value=PanelFeatures(tools=frozenset({"codex", "git"}))))
        self.proposal = {"title": "Verify live maintenance state", "reason": "The local service socket was denied <check>.\nAuthorization: Bearer follow-up-secret",
                         "prompt": "Read live unit state and verify the T3 endpoint. Do not change the host.",
                         "directory": self.home, "mode": "workspace", "workspace": "run-directory",
                         "network": True, "temporary_files": False}

    def record_request(self) -> tuple[str, str]:
        identifier = self.manager.create({"title": "Restricted check", "prompt": "Inspect only", "directory": self.home,
                                          "mode": "inspect", "interval": "once"}, run_now=True)
        value = validate_report({**report("blocked"), "follow_up": self.proposal})
        events = CodexEvents()
        events.feed(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(value)}}).encode() + b"\n")
        self.assertEqual(events.report, value)
        with patch.object(tasks, "execute_prompt", return_value={"status": "completed", "message": "Done", "output": "", **events.report}):
            self.assertTrue(self.manager.tick())
        return identifier, self.manager.snapshot()["runs"][-1]["id"]

    def test_request_survives_receipt_and_hot_history_without_activating_work(self) -> None:
        identifier, run_id = self.record_request()
        before = self.manager.snapshot()
        api = TaskAPI(self.manager)
        self.assertEqual(api.call("receipt", {"id": identifier, "run_id": run_id})["run"]["follow_up"], self.proposal)
        self.assertEqual(before["runs"][0]["follow_up"], self.proposal)
        self.assertEqual(len(before["tasks"]), 1)
        self.assertEqual(before["tasks"][0]["mode"], "inspect")
        self.assertFalse(before["tasks"][0]["network"])
        reference = identifier + "." + run_id
        with patch.object(tasks, "execute_prompt") as execute:
            page = agents.render_agents(self.state, panel._PAGE_STYLE, {"run": run_id})
            self.assertIn("Review follow-up task", page)
            self.assertIn("This older proposal needs an access review", page)
            self.assertNotIn('value="follow-up-approve"', page)
            query = agents.parse_agent_query("follow-up=" + reference)
            page = agents.render_agents(self.state, panel._PAGE_STYLE, query)
            self.assertIn("No task has been saved or queued", page)
            self.assertIn("&lt;check&gt;", page)
            self.assertNotIn("denied <check>", page)
            self.assertNotIn("follow-up-secret", page)
            self.assertFalse(self.manager.tick())
        execute.assert_not_called()
        self.assertEqual(self.manager.snapshot(), before)
        form = TaskForm(page).values
        self.assertEqual(form["id"], "")
        self.assertEqual((form["mode"], form["workspace"], form["network"]), ("workspace", "run-directory", "1"))
        self.assertEqual((form["interval"], form["max_runs"], form["start_at"]), ("once", "1", ""))
        self.assertEqual(form["operator_requirements"], "")
        self.assertEqual(form["writable_directories"], "")
        self.assertIn('value="run">Approve once</button>', page)
        form["submit"] = "draft"
        body = urlencode(form).encode()
        handler = object.__new__(panel.WebPanelHandler)
        handler.state, handler.path = self.state, "/actions/agent-task/save"
        handler.headers, handler.rfile = {"Content-Length": str(len(body))}, BytesIO(body)
        for name in ("_send", "send_response", "send_header", "end_headers"):
            setattr(handler, name, Mock())
        handler.do_POST()
        handler.send_response.assert_called_once_with(303)
        new_id = self.manager.snapshot()["tasks"][-1]["id"]
        saved = api.call("show", {"id": new_id})["task"]
        self.assertTrue(saved["draft"])
        self.assertEqual(saved["operator_requirements"], [])
        self.assertFalse(saved["queued"])
        self.assertFalse(saved["enabled"])
        self.assertTrue(saved["prompt"].endswith(self.proposal["prompt"]))
        self.assertIn(identifier, saved["prompt"])
        self.assertIn(run_id, saved["prompt"])
        self.assertEqual(api.call("show", {"id": identifier})["task"]["mode"], "inspect")
        self.manager.action(new_id, "run")
        with patch.object(tasks, "MAX_RUNS", 1), patch.object(tasks, "execute_prompt", return_value={
            "status": "completed", "message": "Done", "output": "", **report("complete"),
        }):
            self.assertTrue(self.manager.tick())
        self.assertNotEqual(self.manager.snapshot()["runs"][0]["id"], run_id)
        self.manager.action(identifier, "delete")
        page = agents.render_agents(self.state, panel._PAGE_STYLE, {"receipt": reference})
        self.assertIn("Follow-up draft.", page)
        self.assertEqual(self.manager.contexts.receipt(identifier, run_id)["follow_up"], self.proposal)

    def test_invalid_requests_and_unavailable_proposals_do_not_prepare_work(self) -> None:
        for changes in ({"mode": "unrestricted"}, {"network": "yes"}, {"directory": "relative"},
                        {"directory": "/tmp\npath"}, {"prompt": "a" * 4001}, {"title": ""},
                        {"workspace": "external"}, {"mode": "inspect"}, {"command": "sudo repair"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_follow_up({**self.proposal, **changes})
            diagnostic = {**report("blocked"), "follow_up": {**self.proposal, **changes}}
            retained = validate_report(diagnostic)
            self.assertIsNone(retained["follow_up"])
            self.assertEqual(retained["summary"], diagnostic["summary"])
            self.assertEqual(retained["follow_up_warning"], FOLLOW_UP_WARNING)
        self.proposal.update(directory="/etc", workspace="existing")
        identifier, run_id = self.record_request()
        before = self.manager.snapshot()
        page = agents.render_agents(self.state, panel._PAGE_STYLE, {"follow-up": identifier + "." + run_id})
        self.assertIn("Requested by a previous run", page)
        self.assertEqual(TaskForm(page).values["directory"], "/etc")
        self.assertEqual(TaskForm(page).values["mode"], "workspace")
        self.assertEqual(self.manager.snapshot(), before)
        for reference in ("missing", "../path", "a" * 32 + "." + "z" * 32):
            with self.subTest(reference=reference), self.assertRaises(ValueError):
                agents.parse_agent_query("follow-up=" + reference)
        page = agents.render_agents(self.state, panel._PAGE_STYLE, {"follow-up": "a" * 32 + "." + "b" * 32})
        self.assertIn("Follow-up request unavailable", page)

    def test_operator_checklist_is_confirmed_by_direct_and_reviewed_http_approval(self) -> None:
        requirement = ("Review and authorize the separate read-only investigation; "
                       "any subsequent repair requires separate authorization.")
        self.proposal.update(writable_directories=[], operator_requirements=[requirement], service_restarts=[])
        for route in ("direct", "review"):
            with self.subTest(route=route):
                identifier, run_id = self.record_request()
                reference = identifier + "." + run_id
                before = self.manager.snapshot()
                for query in ({"view": "tasks"}, {"run": run_id}, {"receipt": reference}):
                    page = agents.render_agents(self.state, panel._PAGE_STYLE, query)
                    self.assertIn(requirement, page)
                    self.assertIn('value="follow-up-approve">Approve once</button>', page)
                    self.assertIn("Approve once confirms", page)
                self.assertEqual(self.manager.snapshot(), before)
                if route == "review":
                    page = agents.render_agents(self.state, panel._PAGE_STYLE, {"follow-up": reference})
                    form = TaskForm(page).values
                    self.assertEqual(form["operator_requirements"], requirement)
                    self.assertIn("you do not need to remove resolved lines", page)
                    form["submit"] = "run"
                    path = "/actions/agent-task/save"
                else:
                    form = {"csrf": self.state.csrf_token, "id": identifier,
                            "run_id": run_id, "action": "follow-up-approve"}
                    path = "/actions/agent-task"
                body = urlencode(form).encode()
                handler = object.__new__(panel.WebPanelHandler)
                handler.state, handler.path = self.state, path
                handler.headers, handler.rfile = {"Content-Length": str(len(body))}, BytesIO(body)
                for name in ("_send", "send_response", "send_header", "end_headers"):
                    setattr(handler, name, Mock())
                for _ in range(2):
                    handler.rfile = BytesIO(body)
                    handler.do_POST()
                handler._send.assert_not_called()
                self.assertEqual([call.args for call in handler.send_response.call_args_list], [(303,), (303,)])
                decision = follow_up_state(self.manager, identifier, run_id)
                self.assertEqual(decision["acknowledged_requirements"], [requirement])
                children = [task for task in self.manager.snapshot()["tasks"] if task["source_run_id"] == run_id]
                self.assertEqual(len(children), 1)
                child = children[0]
                self.assertEqual(decision["task_id"], child["id"])
                self.assertTrue(child["queued"])
                self.assertEqual(child["operator_requirements"], [])
                self.assertEqual((child["interval"], child["max_runs"], child["writable_directories"]), ("once", 1, []))
                self.assertEqual(self.manager.contexts.receipt(identifier, run_id)["follow_up"], self.proposal)
                with patch.object(tasks, "execute_prompt", return_value={
                    "status": "completed", "message": "Done", "output": "", **report("complete"),
                }) as execute:
                    self.assertTrue(self.manager.tick())
                    self.assertFalse(self.manager.tick())
                self.assertEqual(execute.call_args.args[0]["id"], child["id"])

    def test_failed_final_decision_publication_keeps_a_recoverable_submission_intent(self) -> None:
        self.proposal.update(writable_directories=[], operator_requirements=[], service_restarts=[])
        identifier, run_id = self.record_request()
        from lib.atomic_io import write_json_atomic

        def fail_approval_publication(path, value, **kwargs):
            if value.get("status") == "approved":
                raise OSError("simulated final decision write failure")
            return write_json_atomic(path, value, **kwargs)

        with patch("lib.agent_task_followups.write_json_atomic", side_effect=fail_approval_publication):
            with self.assertRaisesRegex(OSError, "final decision"):
                decide_follow_up(self.manager, identifier, run_id, "approve")

        decision = follow_up_state(self.manager, identifier, run_id)
        self.assertEqual(decision["status"], "pending")
        self.assertEqual(decision["submission"]["request_key"], f"follow-up-{identifier}-{run_id}")
        children = [task for task in self.manager.snapshot()["tasks"] if task["source_run_id"] == run_id]
        self.assertEqual(len(children), 1)
        self.assertTrue(children[0]["queued"])
        self.assertEqual(decision["submission"]["task"]["source_run_id"], run_id)

    def test_block_cannot_publish_over_a_child_after_final_decision_failure(self) -> None:
        self.proposal.update(writable_directories=[], operator_requirements=[], service_restarts=[])
        identifier, run_id = self.record_request()
        from lib.atomic_io import write_json_atomic

        def fail_approval_publication(path, value, **kwargs):
            if value.get("status") == "approved":
                raise OSError("simulated final decision write failure")
            return write_json_atomic(path, value, **kwargs)

        with patch("lib.agent_task_followups.write_json_atomic", side_effect=fail_approval_publication):
            with self.assertRaises(OSError):
                decide_follow_up(self.manager, identifier, run_id, "approve")

        with self.assertRaisesRegex(ValueError, "matching follow-up task"):
            decide_follow_up(self.manager, identifier, run_id, "block")
        decision = follow_up_state(self.manager, identifier, run_id)
        self.assertEqual(decision["status"], "pending")
        self.assertTrue(self.manager.snapshot()["tasks"][-1]["queued"])

    def test_panel_can_finish_reviewed_legacy_and_current_saved_submissions(self) -> None:
        original = dict(self.proposal)
        from lib.atomic_io import write_json_atomic

        def fail_final(path, value, **kwargs):
            if value.get("status") == "approved":
                raise OSError("simulated final decision write failure")
            return write_json_atomic(path, value, **kwargs)

        for legacy in (True, False):
            with self.subTest(legacy=legacy):
                self.proposal = dict(original)
                if not legacy:
                    self.proposal.update(writable_directories=[], operator_requirements=[], service_restarts=[])
                identifier, run_id = self.record_request()
                settings = prepare_follow_up(self.manager, identifier, run_id, review=True)
                with patch("lib.agent_task_followups.write_json_atomic", side_effect=fail_final):
                    with self.assertRaises(OSError):
                        decide_follow_up(self.manager, identifier, run_id, "approve", settings=settings)
                child = self.manager.snapshot()["tasks"][-1]
                for query in ({"view": "tasks"}, {"run": run_id}, {"receipt": identifier + "." + run_id}):
                    page = agents.render_agents(self.state, panel._PAGE_STYLE, query)
                    self.assertIn('value="follow-up-approve">Finish saved submission</button>', page)
                    self.assertNotIn("This older proposal needs an access review", page)
                body = urlencode({"csrf": self.state.csrf_token, "id": identifier,
                                  "run_id": run_id, "action": "follow-up-approve"}).encode()
                handler = object.__new__(panel.WebPanelHandler)
                handler.state, handler.path = self.state, "/actions/agent-task"
                handler.headers, handler.rfile = {"Content-Length": str(len(body))}, BytesIO(body)
                for name in ("_send", "send_response", "send_header", "end_headers"):
                    setattr(handler, name, Mock())
                with patch.object(self.manager, "create", wraps=self.manager.create) as create:
                    handler.do_POST()
                    create.assert_not_called()
                handler.send_response.assert_called_once_with(303)
                self.assertEqual(follow_up_state(self.manager, identifier, run_id)["task_id"], child["id"])
                self.manager.action(child["id"], "cancel")

    def test_approve_retry_recovers_same_child_without_replaying_service_action(self) -> None:
        unit = "auto-update-node.service"
        operation = "command.run"
        parameters = {"argv": ["/usr/bin/systemctl", "--no-ask-password", "restart", "--", unit]}
        self.proposal.update(writable_directories=[], operator_requirements=[], service_restarts=[unit])
        identifier, run_id = self.record_request()
        request_id = "c" * 32
        request = {"id": request_id, "state": "pending",
                   "review_url": "https://host.example:9444/requests/" + request_id}
        succeeded = {"id": request_id, "state": "succeeded",
                     "plan": {"operation": operation, "parameters": parameters}}
        from lib.atomic_io import write_json_atomic
        failed = False

        def fail_final_once(path, value, **kwargs):
            nonlocal failed
            if value.get("status") == "approved" and not failed:
                failed = True
                raise OSError("simulated final decision write failure")
            return write_json_atomic(path, value, **kwargs)

        with patch("lib.agent_task_followups.exchange", side_effect=[request, succeeded]) as broker:
            with self.assertRaises(ServiceApprovalPending):
                decide_follow_up(self.manager, identifier, run_id, "approve")
            with patch("lib.agent_task_followups.write_json_atomic", side_effect=fail_final_once):
                with self.assertRaisesRegex(OSError, "final decision"):
                    decide_follow_up(self.manager, identifier, run_id, "approve")
                first_child = [task for task in self.manager.snapshot()["tasks"]
                               if task["source_run_id"] == run_id][0]
                with patch.object(self.manager, "create", wraps=self.manager.create) as create:
                    recovered = decide_follow_up(self.manager, identifier, run_id, "approve")
                create.assert_not_called()
            self.assertEqual(recovered, first_child["id"])
            self.assertEqual([call.args[0]["action"] for call in broker.call_args_list], ["request", "status"])

        children = [task for task in self.manager.snapshot()["tasks"] if task["source_run_id"] == run_id]
        self.assertEqual(len(children), 1)
        self.assertEqual(follow_up_state(self.manager, identifier, run_id)["task_id"], first_child["id"])
        self.assertEqual(follow_up_state(self.manager, identifier, run_id)["status"], "approved")

    def test_missing_completed_child_cannot_be_recreated_by_approval_retry(self) -> None:
        self.proposal.update(writable_directories=[], operator_requirements=[], service_restarts=[])
        from lib.atomic_io import write_json_atomic

        def fail_final(path, value, **kwargs):
            if value.get("status") == "approved":
                raise OSError("simulated final decision write failure")
            return write_json_atomic(path, value, **kwargs)

        for removal in ("delete", "reclaim"):
            with self.subTest(removal=removal):
                identifier, run_id = self.record_request()
                with patch("lib.agent_task_followups.write_json_atomic", side_effect=fail_final):
                    with self.assertRaises(OSError):
                        decide_follow_up(self.manager, identifier, run_id, "approve")
                child = self.manager.snapshot()["tasks"][-1]
                with patch.object(tasks, "execute_prompt", return_value={
                    "status": "completed", "message": "Done", "output": "", **report("complete"),
                }) as execute:
                    self.assertTrue(self.manager.tick())
                    if removal == "delete":
                        self.manager.action(child["id"], "delete")
                    else:
                        limit = len(self.manager.snapshot()["tasks"])
                        with patch.object(tasks, "MAX_TASKS", limit):
                            self.manager.create({"title": "Unrelated draft", "prompt": "Inspect only",
                                                 "directory": self.home, "mode": "inspect"},
                                                run_now=False, draft=True)
                    self.assertNotIn(child["id"], [item["id"] for item in self.manager.snapshot()["tasks"]])
                    with patch.object(self.manager, "create", wraps=self.manager.create) as create:
                        with self.assertRaisesRegex(ValueError, "creation outcome is unconfirmed"):
                            decide_follow_up(self.manager, identifier, run_id, "approve")
                        create.assert_not_called()
                    self.assertFalse(self.manager.tick())
                    self.assertEqual(execute.call_count, 1)
                self.assertEqual(follow_up_state(self.manager, identifier, run_id)["status"], "pending")
                decide_follow_up(self.manager, identifier, run_id, "block")
                self.manager.action(identifier, "delete")

    def test_intent_without_child_requires_inspection_before_replacement(self) -> None:
        self.proposal.update(writable_directories=[], operator_requirements=[], service_restarts=[])
        identifier, run_id = self.record_request()
        with patch.object(self.manager, "create", side_effect=OSError("task store unavailable")):
            with self.assertRaises(OSError):
                decide_follow_up(self.manager, identifier, run_id, "approve")
        with patch.object(self.manager, "create", wraps=self.manager.create) as create:
            with self.assertRaisesRegex(ValueError, "creation outcome is unconfirmed"):
                decide_follow_up(self.manager, identifier, run_id, "approve")
            create.assert_not_called()
        self.assertEqual(len(self.manager.snapshot()["tasks"]), 1)
        self.assertIsNone(decide_follow_up(self.manager, identifier, run_id, "block"))
        self.assertEqual(follow_up_state(self.manager, identifier, run_id)["status"], "blocked")

    def test_draft_retry_preserves_the_saved_draft_without_queuing_it(self) -> None:
        requirement = "Check the maintenance window before any later repair."
        self.proposal.update(writable_directories=[], operator_requirements=[requirement], service_restarts=[])
        identifier, run_id = self.record_request()
        settings = prepare_follow_up(self.manager, identifier, run_id, review=True)
        from lib.atomic_io import write_json_atomic

        def fail_draft_publication(path, value, **kwargs):
            if value.get("status") == "draft":
                raise OSError("simulated final decision write failure")
            return write_json_atomic(path, value, **kwargs)

        with patch("lib.agent_task_followups.write_json_atomic", side_effect=fail_draft_publication):
            with self.assertRaisesRegex(OSError, "final decision"):
                decide_follow_up(self.manager, identifier, run_id, "approve", settings=settings,
                                 run_now=False, draft=True)

        child = [task for task in self.manager.snapshot()["tasks"] if task["source_run_id"] == run_id][0]
        self.assertTrue(child["draft"])
        self.assertFalse(child["queued"])
        self.assertEqual(child["operator_requirements"], [requirement])
        self.assertEqual(decide_follow_up(self.manager, identifier, run_id, "approve"), child["id"])
        self.assertEqual(follow_up_state(self.manager, identifier, run_id)["status"], "draft")
        recovered = self.manager.snapshot()["tasks"][-1]
        self.assertTrue(recovered["draft"])
        self.assertFalse(recovered["queued"])
        self.assertEqual(recovered["operator_requirements"], [requirement])

    def test_block_reconciles_an_orphaned_child_without_a_saved_intent(self) -> None:
        identifier, run_id = self.record_request()
        task = prepare_follow_up(self.manager, identifier, run_id)
        task.update(interval="once", max_runs=1, continue_until_done=False, start_at=0,
                    source_task_id=identifier, source_run_id=run_id)
        task = tasks.validate_task(task, self.home)
        acknowledged = task["operator_requirements"]
        task["operator_requirements"] = []
        child_id = self.manager.create(task, run_now=True,
                                       request_key=f"follow-up-{identifier}-{run_id}")

        with self.assertRaisesRegex(ValueError, "matching follow-up task"):
            decide_follow_up(self.manager, identifier, run_id, "block")
        self.assertEqual(follow_up_state(self.manager, identifier, run_id)["status"], "pending")
        self.assertEqual(decide_follow_up(self.manager, identifier, run_id, "approve"), child_id)
        self.assertEqual(follow_up_state(self.manager, identifier, run_id)["acknowledged_requirements"], acknowledged)
        self.assertEqual(len([task for task in self.manager.snapshot()["tasks"]
                              if task["source_run_id"] == run_id]), 1)

    def test_concurrent_approve_and_block_cannot_leave_a_queued_child_blocked(self) -> None:
        self.proposal.update(writable_directories=[], operator_requirements=[], service_restarts=[])
        identifier, run_id = self.record_request()
        entered_create, release_create, block_waiting = threading.Event(), threading.Event(), threading.Event()
        real_create = self.manager.create
        import fcntl
        real_flock = fcntl.flock
        outcomes: dict[str, object] = {}

        def delayed_create(*args, **kwargs):
            entered_create.set()
            if not release_create.wait(5):
                raise TimeoutError("test did not release child creation")
            return real_create(*args, **kwargs)

        def approve() -> None:
            try:
                outcomes["approve"] = decide_follow_up(self.manager, identifier, run_id, "approve")
            except Exception as exc:  # Captured for assertion in the main test thread.
                outcomes["approve_error"] = exc

        def block() -> None:
            try:
                outcomes["block"] = decide_follow_up(self.manager, identifier, run_id, "block")
            except Exception as exc:  # Captured for assertion in the main test thread.
                outcomes["block_error"] = exc

        def observe_block_lock(descriptor, operation):
            if threading.current_thread().name == "follow-up-blocker" and operation == fcntl.LOCK_EX:
                block_waiting.set()
            return real_flock(descriptor, operation)

        with (
            patch.object(self.manager, "create", side_effect=delayed_create),
            patch("lib.agent_task_followups.fcntl.flock", side_effect=observe_block_lock),
        ):
            approver = threading.Thread(target=approve)
            approver.start()
            self.assertTrue(entered_create.wait(3))
            blocker = threading.Thread(target=block, name="follow-up-blocker")
            blocker.start()
            try:
                self.assertTrue(block_waiting.wait(3))
            finally:
                release_create.set()
                approver.join(5)
                blocker.join(5)

        self.assertFalse(approver.is_alive())
        self.assertFalse(blocker.is_alive())
        self.assertNotIn("approve_error", outcomes)
        self.assertEqual(outcomes.get("approve"), follow_up_state(self.manager, identifier, run_id)["task_id"])
        self.assertIsInstance(outcomes.get("block_error"), ValueError)
        self.assertEqual(follow_up_state(self.manager, identifier, run_id)["status"], "approved")
        self.assertEqual(len([task for task in self.manager.snapshot()["tasks"]
                              if task["source_run_id"] == run_id]), 1)

    def test_reports_without_requests_remain_readable(self) -> None:
        self.assertEqual(validate_report(report()), report())
        self.assertIsNone(validate_report({**report(), "follow_up": None})["follow_up"])
        self.assertIn("follow_up", REPORT_SCHEMA["required"])

    def test_service_approval_is_staged_inline_then_launches_once_after_success(self) -> None:
        unit = "auto-update-node.service"
        self.proposal.update(writable_directories=[], operator_requirements=[], service_restarts=[unit])
        for reviewed in (False, True):
            with self.subTest(reviewed=reviewed):
                identifier, run_id = self.record_request()
                page = agents.render_agents(self.state, panel._PAGE_STYLE, {"follow-up": identifier + "." + run_id})
                form = TaskForm(page).values
                form.update(submit="run", prompt="Retain my reviewed verification")
                entries = form if reviewed else {"csrf": self.state.csrf_token, "id": identifier,
                    "run_id": run_id, "action": "follow-up-approve"}
                body = urlencode(entries).encode()
                handler = object.__new__(panel.WebPanelHandler)
                handler.state = self.state
                handler.path = "/actions/agent-task/save" if reviewed else "/actions/agent-task"
                handler.headers = {"Content-Length": str(len(body))}
                for name in ("_send", "send_response", "send_header", "end_headers"):
                    setattr(handler, name, Mock())
                request = {"id": "c" * 32, "state": "pending", "review_url": "https://host.example:9444/requests/" + "c" * 32}
                with patch("lib.agent_task_followups.exchange", return_value=request) as broker, \
                     patch("lib.agent_task_followups.load_policy", return_value={"services": {} if reviewed else {unit: "approve"}}):
                    for _ in range(2):
                        handler.rfile = BytesIO(body)
                        handler.do_POST()
                        self.assertEqual(handler._send.call_args.args[0], 200)
                        document = handler._send.call_args.args[1]
                        self.assertNotIn('role="alert"', document)
                        self.assertIn('target="_blank" rel="noopener noreferrer">Open service approval', document)
                        self.assertIn("Check approval and launch", document)
                        self.assertEqual([item for item in self.manager.snapshot()["tasks"] if item["queued"]], [])
                        if reviewed:
                            self.assertEqual(TaskForm(document).values["prompt"], form["prompt"])
                    self.assertEqual([call.args[0]["action"] for call in broker.call_args_list], ["request", "status"])
                    operation = "command.run" if reviewed else "service.restart"
                    parameters = {"argv": ["/usr/bin/systemctl", "--no-ask-password", "restart", "--", unit]} if reviewed else {"unit": unit}
                    self.assertEqual(broker.call_args_list[0].args[0]["operation"], operation)
                    self.assertEqual(broker.call_args_list[0].args[0]["parameters"], parameters)
                    if reviewed:
                        for wrong in ({"operation": "service.restart", "parameters": {"unit": unit}},
                                      {"operation": "command.run", "parameters": {"argv": parameters["argv"] + ["--no-block"]}},
                                      {"operation": "command.run", "parameters": {"argv": parameters["argv"][:-1] + ["other.service"]}}):
                            broker.return_value = {"state": "succeeded", "plan": wrong}
                            handler.rfile = BytesIO(body)
                            handler.do_POST()
                            self.assertEqual(handler._send.call_args.args[0], 422)
                            self.assertEqual([task for task in self.manager.snapshot()["tasks"] if task["queued"]], [])
                    broker.return_value = {"state": "succeeded", "plan": {
                        "operation": operation, "parameters": parameters}}
                    for _ in range(2):
                        handler.rfile = BytesIO(body)
                        handler.do_POST()
                    self.assertEqual([call.args for call in handler.send_response.call_args_list], [(303,), (303,)])
                children = [task for task in self.manager.snapshot()["tasks"] if task["source_run_id"] == run_id]
                self.assertEqual(len(children), 1)
                self.assertTrue(children[0]["queued"])
                if reviewed:
                    self.assertEqual(children[0]["prompt"], form["prompt"])
                self.manager.action(children[0]["id"], "cancel")

    def test_unknown_service_submission_is_not_retried_or_reported_cancelled(self) -> None:
        unit = "auto-update-node.service"
        self.proposal.update(writable_directories=[], operator_requirements=[], service_restarts=[unit])
        identifier, run_id = self.record_request()
        failure = PrivilegeRequestUnconfirmed("Broker did not confirm request acceptance: persistence may be uncertain")
        with patch("lib.agent_task_followups.exchange", side_effect=failure) as broker:
            with self.assertRaises(ServiceApprovalUnavailable):
                decide_follow_up(self.manager, identifier, run_id, "request-privilege")

            with self.assertRaisesRegex(ServiceApprovalPending, "outcome is unknown"):
                decide_follow_up(self.manager, identifier, run_id, "request-privilege")
            with self.assertRaisesRegex(ServiceApprovalPending, "Waiting for service approval"):
                decide_follow_up(self.manager, identifier, run_id, "approve")
            self.assertEqual(len(self.manager.snapshot()["tasks"]), 1)

            self.assertIsNone(decide_follow_up(self.manager, identifier, run_id, "block"))
            broker.assert_called_once()
            self.assertEqual(broker.call_args.args[0]["action"], "request")
            state = follow_up_state(self.manager, identifier, run_id)
            self.assertEqual(state["status"], "blocked")
            self.assertEqual(state["requests"][unit]["state"], "submission-uncertain")
            self.assertNotIn("id", state["requests"][unit])

            with self.assertRaisesRegex(ValueError, "blocked"):
                decide_follow_up(self.manager, identifier, run_id, "approve")
            with self.assertRaisesRegex(ValueError, "blocked"):
                decide_follow_up(self.manager, identifier, run_id, "request-privilege")
            broker.assert_called_once()
        self.assertEqual(len(self.manager.snapshot()["tasks"]), 1)

    def test_follow_up_never_queues_from_success_status_with_unbound_identity(self) -> None:
        unit = "auto-update-node.service"
        operation = "command.run"
        parameters = {"argv": ["/usr/bin/systemctl", "--no-ask-password", "restart", "--", unit]}
        self.proposal.update(writable_directories=[], operator_requirements=[], service_restarts=[unit])
        for bad_identity in ("missing", "wrong", "malformed"):
            with self.subTest(identity=bad_identity):
                identifier, run_id = self.record_request()
                request_id = "a" * 32
                messages = []

                class ResponseHandler(socketserver.StreamRequestHandler):
                    def handle(self):
                        message = json.loads(self.rfile.readline())
                        messages.append(message)
                        if message["action"] == "request":
                            result = {"id": request_id, "state": "pending",
                                      "review_url": "https://host.example:9444/requests/" + request_id}
                        else:
                            result = {"state": "succeeded", "plan": {
                                "operation": operation, "parameters": parameters}}
                            if bad_identity == "wrong":
                                result["id"] = "b" * 32
                            elif bad_identity == "malformed":
                                result["id"] = "not-a-request-id"
                        self.wfile.write((json.dumps({"ok": True, "result": result}) + "\n").encode())

                with tempfile.TemporaryDirectory() as directory:
                    path = os.path.join(directory, "broker.sock")
                    server = socketserver.UnixStreamServer(path, ResponseHandler)
                    worker = threading.Thread(target=server.serve_forever)
                    worker.start()
                    try:
                        with patch("lib.agent_task_followups.exchange",
                                   side_effect=lambda message: privilege_client.exchange(message, path)):
                            self.assertIsNone(decide_follow_up(self.manager, identifier, run_id, "request-privilege"))
                            with self.assertRaises(ServiceApprovalUnavailable):
                                decide_follow_up(self.manager, identifier, run_id, "approve")
                    finally:
                        server.shutdown()
                        server.server_close()
                        worker.join(3)
                self.assertFalse(worker.is_alive())
                self.assertEqual([message["action"] for message in messages], ["request", "status"])
                children = [task for task in self.manager.snapshot()["tasks"]
                            if task.get("source_run_id") == run_id]
                self.assertEqual(children, [])

    def test_invalid_follow_up_does_not_discard_diagnostic_report(self) -> None:
        diagnostic = {**report("blocked"), "summary": "Service check completed", "evidence": ["unit was inactive"],
                      "environment_feedback": "é" * 1500,
                      "follow_up": {**self.proposal, "mode": "inspect"}}
        events = CodexEvents()
        event = {"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(diagnostic)}}
        events.feed(json.dumps(event).encode() + b"\n")
        self.assertEqual(events.report["outcome"], "blocked")
        self.assertEqual(events.report["summary"], diagnostic["summary"])
        self.assertEqual(events.report["evidence"], diagnostic["evidence"])
        self.assertEqual(events.report["environment_feedback"], diagnostic["environment_feedback"])
        self.assertIsNone(events.report["follow_up"])
        self.assertEqual(events.report["follow_up_warning"], FOLLOW_UP_WARNING)
        self.assertLess(len(FOLLOW_UP_WARNING), 500)
        self.assertEqual(events.error, "")
        self.assertEqual(diagnostic["follow_up"]["mode"], "inspect")
        identifier = self.manager.create({"title": "Host review", "prompt": "Inspect only", "directory": self.home,
                                          "mode": "inspect", "interval": "once", "persistence": "checkpoint"}, run_now=True)
        with (patch.object(tasks, "execute_prompt", return_value={"status": "completed", "message": FOLLOW_UP_WARNING,
                  "output": diagnostic["summary"], **events.report}),
              patch("lib.agent_inbox.AgentInbox.feedback", return_value={"id": "feedback-id"}) as feedback):
            self.assertTrue(self.manager.tick())
        self.assertTrue(feedback.call_args.args[1].endswith(diagnostic["environment_feedback"]))
        run = self.manager.snapshot()["runs"][0]
        receipt = self.manager.contexts.receipt(identifier, run["id"])
        self.assertEqual(receipt["evidence"], diagnostic["evidence"])
        self.assertEqual(receipt["environment_feedback"], diagnostic["environment_feedback"])
        self.assertEqual(receipt["follow_up_warning"], FOLLOW_UP_WARNING)
        self.assertEqual(self.manager.contexts.load(identifier)["checkpoint"], diagnostic["checkpoint"])
        self.assertEqual(self.manager.contexts.pending_follow_ups(identifier), [])
        self.assertEqual(len(self.manager.snapshot()["tasks"]), 1)
        for changes in ({"summary": None}, {"evidence": []}, {"environment_feedback": "x" * 3001}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_report({**diagnostic, "outcome": "complete", **changes})

    def test_maintenance_starter_is_diagnostic_and_repairs_use_follow_up_review(self) -> None:
        review = agents.PROMPT_TEMPLATES["maintenance"]
        self.assertEqual((review["mode"], review["workspace"], review["network"]), ("workspace", "run-directory", True))
        self.assertIn("follow_up", review["prompt"])
        for removed in ("maintenance-local", "maintenance-repair", "regression-tests"):
            with self.subTest(removed=removed), self.assertRaises(ValueError):
                agents.parse_agent_query("template=" + removed)

    def test_legacy_inspection_tasks_offer_current_templates_without_changing_access(self) -> None:
        identifier = self.manager.create({"title": "Host maintenance review", "prompt": "Inspect live service state",
            "directory": self.home, "mode": "inspect", "interval": "daily"}, run_now=True)
        with patch.object(tasks, "execute_prompt", return_value={
            "status": "completed", "message": "Done", "output": "", **report("blocked"),
        }):
            self.assertTrue(self.manager.tick())
        before = self.manager.snapshot()
        run_id = before["runs"][0]["id"]
        for query in ({"view": "tasks"}, {"edit": identifier}, {"copy": identifier}, {"reuse": run_id},
                      {"receipt": identifier + "." + run_id}):
            with self.subTest(query=query):
                page = agents.render_agents(self.state, panel._PAGE_STYLE, query)
                self.assertIn("Inspection access is restricted", page)
                self.assertIn('href="/agents?template=maintenance">Review current host-check template', page)
                self.assertIn("then pause the old schedule", page)
                if set(query) & {"edit", "copy", "reuse"}:
                    form = TaskForm(page).values
                    self.assertEqual((form["mode"], form["workspace"]), ("inspect", "existing"))
                    self.assertNotIn("network", form)
        page = agents.render_agents(self.state, panel._PAGE_STYLE, {"template": "maintenance"})
        form = TaskForm(page).values
        self.assertEqual((form["id"], form["mode"], form["workspace"], form["network"]),
                         ("", "workspace", "run-directory", "1"))
        self.assertEqual(self.manager.snapshot(), before)

    def test_inspection_guidance_does_not_claim_every_blocker_is_a_permission_failure(self) -> None:
        custom = {"title": "Custom check", "mode": "inspect"}
        self.assertEqual(agents._render_inspection_access(custom), "")
        self.assertIn('href="/agents?view=templates"', agents._render_inspection_access(custom, blocked=True))
        for key in ("repository", "storage", "maintenance"):
            self.assertEqual(agents._render_inspection_access(agents.PROMPT_TEMPLATES[key]), "")
        self.assertEqual(agents._render_inspection_access({**custom, "mode": "workspace"}, blocked=True), "")
        self.assertIn('href="/agent-tools?tool=checkup">Review current guided task',
                      agents._render_inspection_access({"title": "System checkup", "mode": "inspect"}))


class TaskPermissionProfileTests(unittest.TestCase):
    def test_task_account_bus_is_pinned_for_provider_and_shell_commands(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            process, selector = MagicMock(), MagicMock()
            process.__enter__.return_value = process
            process.wait.return_value = 0
            selector.__enter__.return_value = selector
            selector.get_map.return_value = {}
            polluted = {"XDG_RUNTIME_DIR": "/run/user/9999", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/wrong/bus",
                        "SYSTEMCTL_FORCE_BUS": "0", "CODEX_HOME": "/wrong/codex"}
            with (
                patch.dict(os.environ, polluted),
                patch.object(tasks.os, "geteuid", return_value=1234),
                patch.object(tasks, "_tool_path", return_value="/test/codex"),
                patch.object(tasks.subprocess, "Popen", return_value=process) as spawn,
                patch.object(tasks.selectors, "DefaultSelector", return_value=selector),
                patch.object(tasks.os, "killpg"),
            ):
                result = tasks.execute_prompt({"title": "Check", "prompt": "Inspect live service state",
                    "directory": home, "interval": "once", **agents.HOST_REVIEW_SETTINGS}, home, threading.Event())
            self.assertEqual(result["status"], "completed")
            command = spawn.call_args.args[0]
            overrides = tomllib.loads("\n".join(command[index + 1] for index, value in enumerate(command) if value == "-c"))
            expected = {"HOME": home, "CODEX_HOME": home + "/.codex", "XDG_RUNTIME_DIR": "/run/user/1234",
                        "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1234/bus", "SYSTEMCTL_FORCE_BUS": "1"}
            self.assertEqual(overrides["shell_environment_policy"]["set"], expected)
            for name, value in expected.items():
                self.assertEqual(spawn.call_args.kwargs["env"][name], value)
            self.assertNotIn("inherit", overrides["shell_environment_policy"])
            self.assertNotIn("filters", overrides["shell_environment_policy"])

    def test_task_access_overrides_managed_defaults_in_both_codex_config_generations(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            for mode, network, temporary in (("inspect", False, False), ("workspace", False, False),
                                              ("workspace", True, False), ("workspace", True, True)):
                with self.subTest(mode=mode, network=network, temporary=temporary):
                    task = tasks.validate_task({"title": "Check", "prompt": "Check", "directory": home,
                                                "interval": "once", "mode": mode,
                                                "workspace": "run-directory" if mode == "workspace" else "existing",
                                                "network": network,
                                                "temporary_files": temporary}, home)
                    command = tasks.codex_command(task, "/test/codex")
                    overrides = tomllib.loads("\n".join(command[index + 1] for index, argument in enumerate(command) if argument == "-c"))
                    self.assertEqual(overrides["approval_policy"], "never")
                    profile = overrides["default_permissions"]
                    if mode == "inspect":
                        self.assertEqual(profile, ":read-only")
                        self.assertNotIn("permissions", overrides)
                    else:
                        another = tasks.codex_command(task, "/test/codex")
                        another_config = tomllib.loads("\n".join(another[index + 1] for index, argument in enumerate(another) if argument == "-c"))
                        self.assertNotEqual(another_config["default_permissions"], profile)
                        access = overrides["permissions"][profile]
                        self.assertEqual(access["extends"], ":workspace")
                        self.assertEqual(access["workspace_roots"], {})
                        self.assertEqual(access["network"], {"enabled": network})
                        expected = "write" if temporary else "read"
                        self.assertEqual(access["filesystem"], {":tmpdir": expected, ":slash_tmp": expected})
                        legacy = overrides["sandbox_workspace_write"]
                        self.assertEqual(legacy["network_access"], network)
                        self.assertEqual(legacy["writable_roots"], [])
                        self.assertEqual(legacy["exclude_slash_tmp"], not temporary)
                        self.assertEqual(legacy["exclude_tmpdir_env_var"], not temporary)


if __name__ == "__main__":
    unittest.main()
