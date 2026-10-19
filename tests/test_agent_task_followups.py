"""Permission proposals remain inert until an operator submits a separate task."""

from __future__ import annotations

import json
import os
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
from lib.agent_task_api import TaskAPI
from lib.agent_task_context import CodexEvents, REPORT_SCHEMA, validate_report
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
        return identifier, self.manager.snapshot()["runs"][0]["id"]

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
        self.assertIn("Review follow-up task", page)
        self.assertEqual(self.manager.contexts.receipt(identifier, run_id)["follow_up"], self.proposal)

    def test_invalid_requests_and_unavailable_proposals_do_not_prepare_work(self) -> None:
        for changes in ({"mode": "unrestricted"}, {"network": "yes"}, {"directory": "relative"},
                        {"directory": "/tmp\npath"}, {"prompt": "a" * 4001}, {"title": ""},
                        {"workspace": "external"}, {"mode": "inspect"}, {"command": "sudo repair"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_report({**report("blocked"), "follow_up": {**self.proposal, **changes}})
        self.proposal.update(directory="/etc", workspace="existing")
        identifier, run_id = self.record_request()
        before = self.manager.snapshot()
        page = agents.render_agents(self.state, panel._PAGE_STYLE, {"follow-up": identifier + "." + run_id})
        self.assertIn("Follow-up request unavailable", page)
        self.assertEqual(TaskForm(page).values["mode"], "inspect")
        self.assertEqual(self.manager.snapshot(), before)
        for reference in ("missing", "../path", "a" * 32 + "." + "z" * 32):
            with self.subTest(reference=reference), self.assertRaises(ValueError):
                agents.parse_agent_query("follow-up=" + reference)
        page = agents.render_agents(self.state, panel._PAGE_STYLE, {"follow-up": "a" * 32 + "." + "b" * 32})
        self.assertIn("Follow-up request unavailable", page)

    def test_reports_without_requests_remain_readable(self) -> None:
        self.assertEqual(validate_report(report()), report())
        self.assertIsNone(validate_report({**report(), "follow_up": None})["follow_up"])
        self.assertIn("follow_up", REPORT_SCHEMA["required"])

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
        for query in ({}, {"edit": identifier}, {"copy": identifier}, {"reuse": run_id},
                      {"receipt": identifier + "." + run_id}):
            with self.subTest(query=query):
                page = agents.render_agents(self.state, panel._PAGE_STYLE, query)
                self.assertIn("Inspection access is restricted", page)
                self.assertIn('href="/agents?template=maintenance">Review current host-check template', page)
                self.assertIn("then pause the old schedule", page)
                if query and "receipt" not in query:
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
                                                "interval": "once", "mode": mode, "network": network,
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
