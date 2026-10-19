"""Contextual workbench preparation, filesystem boundaries, and runner reuse."""

from __future__ import annotations

import html
import os
import tempfile
import unittest
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import urlencode

from common.service_tools import web_panel_agent_tools as tools
from common.service_tools import web_panel_agents as agents
from common.service_tools import web_panel_diagnostics as diagnostics
from common.service_tools import web_panel_jobs as jobs
from common.service_tools import web_panel_service as panel
from common.service_tools.web_panel_features import PanelFeatures
from lib import agent_tasks
from lib.agent_task_context import validate_report
from lib.agent_task_followups import decide_follow_up


class TaskForm(HTMLParser):
    """Read the actual prepared form so HTTP round trips use its rendered values."""

    def __init__(self, document: str) -> None:
        super().__init__()
        self.values: dict[str, str] = {}
        self.active = False
        self.textarea = ""
        self.select = ""
        self.feed(document)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "form":
            self.active = attributes.get("action") == "/actions/agent-task/save"
        if not self.active:
            return
        name = attributes.get("name", "")
        if tag == "input" and name:
            if attributes.get("type") != "checkbox" or "checked" in attributes:
                self.values[name] = attributes.get("value", "") or ""
        if tag == "textarea":
            self.textarea = name
            self.values[name] = ""
        if tag == "select":
            self.select = name
        if tag == "option" and self.select and "selected" in attributes:
            self.values[self.select] = attributes.get("value", "") or ""

    def handle_data(self, data: str) -> None:
        if self.active and self.textarea:
            self.values[self.textarea] += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self.active = False
        elif tag == "textarea":
            self.textarea = ""
        elif tag == "select":
            self.select = ""


class AgentPanelToolsTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = os.path.realpath(temporary.name)
        self.directory = Path(self.home) / "work"
        self.directory.mkdir()
        self.source = self.directory / "incoming"
        self.source.mkdir()
        (self.source / "inventory.csv").write_text("id,name\n001,Example\n", encoding="utf-8")
        self.state = panel.WebPanelState({"host": "host.example", "username": "operator",
            "system_type": "agent_vm", "features": {}, "services": [], "access": []}, agent_home=self.home)
        self.addCleanup(self.state.agent_tasks.close)
        self.enterContext(patch.object(self.state, "features", return_value=PanelFeatures(tools=frozenset({"codex", "git"}))))
        for patcher in (patch.object(agent_tasks.AgentTasks, "available", return_value=True),
                        patch.object(agent_tasks, "_tool_path", return_value="/test/codex"),
                        patch.object(agents, "_tool_path", return_value="/test/codex")):
            patcher.start()
            self.addCleanup(patcher.stop)

    def data_values(self, tool: str = "data-import", **changes: str) -> dict[str, str]:
        return {"tool": tool, "directory": str(self.directory), "source": "incoming/inventory.csv",
                "destination": "results", **changes}

    def handler(self, path: str, body: dict[str, str] | str = "") -> panel.WebPanelHandler:
        encoded = (urlencode(body) if isinstance(body, dict) else body).encode("utf-8")
        handler = object.__new__(panel.WebPanelHandler)
        handler.state, handler.path = self.state, path
        handler.headers = {"Content-Length": str(len(encoded))}
        handler.rfile = BytesIO(encoded)
        handler._send = Mock()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        return handler

    def test_get_views_and_preparation_never_collect_or_create_tasks(self) -> None:
        before = self.state.agent_tasks.snapshot()
        with patch.object(agent_tasks, "execute_prompt") as execute, patch.object(diagnostics, "collect_diagnostics") as collect:
            for query in ("", "tool=checkup", "tool=logs&service=homebox.service&window=boot&priority=3", "tool=data-import"):
                handler = self.handler("/agent-tools" + ("?" + query if query else ""))
                handler.do_GET()
                self.assertEqual(handler._send.call_args.args[0].value, 200)
                self.assertIn('href="/agent-tools" aria-current="page"', handler._send.call_args.args[1])
            handler = self.handler("/actions/agent-tool/prepare", {"csrf": self.state.csrf_token, "tool": "checkup"})
            handler.do_POST()
        execute.assert_not_called()
        collect.assert_not_called()
        self.assertEqual(self.state.agent_tasks.snapshot(), before)
        self.assertIn("Review prepared task", handler._send.call_args.args[1])
        self.assertIn("No task has been saved or queued", handler._send.call_args.args[1])

    def test_prepared_form_round_trips_to_draft_run_and_schedule(self) -> None:
        for choice, queued, enabled in (("draft", False, False), ("run", True, True), ("schedule", False, True)):
            with self.subTest(choice=choice), patch.object(agent_tasks, "execute_prompt") as execute:
                prepared = self.handler("/actions/agent-tool/prepare", {"csrf": self.state.csrf_token, **self.data_values()})
                prepared.do_POST()
                status, document = prepared._send.call_args.args[:2]
                self.assertEqual(status.value, 200)
                self.assertIn('class="agent-form"', document)
                self.assertNotIn('id="agent-tasks-heading"', document)
                form = TaskForm(document).values
                form.update(submit=choice, interval="weekly", model="test-model", effort="high", timeout_minutes="180", failure_limit="2")
                save = self.handler("/actions/agent-task/save", form)
                save.do_POST()
                save.send_response.assert_called_once_with(303)
                task = self.state.agent_tasks.snapshot()["tasks"][-1]
                self.assertEqual((task["draft"], task["queued"], task["enabled"]), (choice == "draft", queued, enabled))
                self.assertEqual((task["model"], task["effort"], task["timeout_minutes"], task["failure_limit"]), ("test-model", "high", 180, 2))
                self.assertIn(str(self.source / "inventory.csv"), task["prompt"])
                self.assertFalse(task["network"])
            execute.assert_not_called()
        self.assertEqual((self.source / "inventory.csv").read_text(), "id,name\n001,Example\n")
        self.assertFalse((self.directory / "results").exists())

    def test_contextual_logs_use_fixed_filters_and_current_user_scope(self) -> None:
        task = tools.prepare_tool({"tool": "logs", "service": "t3code.service", "window": "boot", "priority": "3"}, self.home)
        self.assertIn("basaltw agent diagnostics --service t3code.service --window boot --priority 3", task["prompt"])
        self.assertNotIn("journalctl", task["prompt"])
        self.assertEqual(task["title"], "Review service logs: T3 Code (current user)")
        self.assertEqual(task["mode"], "workspace")
        self.assertEqual(task["workspace"], "run-directory")
        self.assertTrue(task["network"])
        job = tools.prepare_tool({"tool": "job", "service": "auto-update-apt.service"}, self.home)
        self.assertIn("auto-update-apt.timer", job["prompt"])
        self.assertIn("Do not trigger the job", job["prompt"])
        self.assertIn("--window 168h --priority 7", job["prompt"])

    def test_host_review_form_runs_in_private_directory_and_preserves_source(self) -> None:
        for query in ({"template": "maintenance"}, {}):
            with self.subTest(query=query):
                prepared = None if query else tools.prepare_tool({"tool": "checkup"}, self.home)
                document = agents.render_agents(self.state, panel._PAGE_STYLE, query, submitted=prepared)
                self.assertIn("private run directory", document)
                form = TaskForm(document).values
                self.assertEqual(form["workspace"], "run-directory")
                self.assertEqual(form["network"], "1")
                form.update(submit="run", interval="once")
                save = self.handler("/actions/agent-task/save", form)
                save.do_POST()
                save.send_response.assert_called_once_with(303)
                with patch.object(agent_tasks, "execute_prompt", return_value={
                    "status": "completed", "output": "Inspected", "message": "Done",
                }) as execute:
                    self.assertTrue(self.state.agent_tasks.tick())
                task = execute.call_args.args[0]
                directory = Path(task["_runtime"]["directory"])
                self.assertNotEqual(str(directory), self.home)
                self.assertEqual(list(directory.iterdir()), [])
                self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
                self.assertTrue(directory.is_relative_to(Path(self.home) / ".local/share/basaltwater/task-workspaces"))
                self.assertFalse(task["temporary_files"])
                self.assertEqual((self.source / "inventory.csv").read_text(), "id,name\n001,Example\n")

    def test_github_review_runs_in_scratch_with_source_context(self) -> None:
        task = {**agents.PROMPT_TEMPLATES["github-actions"], "directory": self.home, "interval": "once"}
        self.state.agent_tasks.create(task, run_now=True)
        with patch.object(agent_tasks, "execute_prompt", return_value={
            "status": "completed", "output": "Reviewed", "message": "Done",
        }) as execute:
            self.assertTrue(self.state.agent_tasks.tick())
        task = execute.call_args.args[0]
        runtime = task["_runtime"]
        self.assertEqual(runtime["source_directory"], self.home)
        self.assertNotEqual(runtime["directory"], self.home)
        command = agent_tasks.codex_command(task, "/test/codex")
        self.assertIn("sandbox_workspace_write.writable_roots=[]", command)
        self.assertTrue(task["network"])
        self.assertFalse(task["temporary_files"])
        self.assertEqual((self.source / "inventory.csv").read_text(), "id,name\n001,Example\n")

    def test_storage_entrypoints_retain_ranked_summary_and_approve_exact_cleanup_scope(self) -> None:
        cache = Path(self.home) / ".cache" / "test-tool"
        cache.mkdir(parents=True, mode=0o700)
        cache.parent.chmod(0o700)
        (cache / "download").write_text("Retain until approved")
        summary = "Best candidate: test-tool cache, 1 GiB observed; rebuild needs downloads."
        report = validate_report({"outcome": "complete", "summary": summary, "checkpoint": "Cache review finished",
            "evidence": ["Exact cache path and retention reviewed"], "retry_after_minutes": None,
            "environment_feedback": "", "follow_up": {"title": "Clean the reviewed test-tool cache",
                "reason": "Measured rebuildable cache is the best low-value candidate",
                "prompt": "Recheck active use, clean only the reviewed cache with its native tool and verify capacity. Deletion is irreversible.",
                "directory": self.home, "mode": "workspace", "workspace": "run-directory",
                "network": False, "temporary_files": False, "writable_directories": [str(cache)],
                "operator_requirements": [], "service_restarts": []}})
        for settings in (tools.prepare_tool({"tool": "storage"}, self.home),
                         {**agents.PROMPT_TEMPLATES["storage"], "directory": self.home}):
            with self.subTest(title=settings["title"]):
                self.assertIn("basaltw agent storage inspect --json", settings["prompt"])
                self.assertIn("return one actionable follow_up", settings["prompt"])
                task_id = self.state.agent_tasks.create(settings, run_now=True)
                with patch.object(agent_tasks, "execute_prompt", return_value={
                    "status": "completed", "output": "", "message": "Done", **report,
                }):
                    self.assertTrue(self.state.agent_tasks.tick())
                run = self.state.agent_tasks.snapshot()["runs"][-1]
                self.assertEqual(run["summary"], summary)
                self.assertTrue((cache / "download").exists())
                child_id = decide_follow_up(self.state.agent_tasks, task_id, run["id"], "approve")
                child = next(task for task in self.state.agent_tasks.snapshot()["tasks"] if task["id"] == child_id)
                self.assertEqual(child["writable_directories"], [str(cache)])
                self.assertEqual((child["workspace"], child["max_runs"], child["queued"]), ("run-directory", 1, True))
                self.state.agent_tasks.action(child_id, "cancel")

    def test_log_tool_prefers_installed_sources_and_retains_historical_links(self) -> None:
        with patch.object(self.state, "diagnostic_sources", return_value={"ssh.service": "SSH"}):
            page = tools.render_tools(self.state, "", {"tool": "logs"})
            self.assertIn('<option value="ssh.service" selected>', page)
            self.assertNotIn('<option value="nginx.service"', page)
            page = tools.render_tools(self.state, "", {"tool": "logs", "service": "homebox.service"})
        self.assertIn('<option value="homebox.service" selected>', page)
        self.assertIn("historical logs", page)

    def test_every_tool_produces_bounded_valid_settings_and_explicit_contracts(self) -> None:
        for key, (_, _, group, mode, _, _) in tools.TOOLS.items():
            values = {"tool": key}
            if key == "job":
                values["service"] = next(iter(diagnostics.JOB_SERVICES))
            if group != "System":
                values = self.data_values(key)
                if key == "data-audit":
                    del values["destination"]
            with self.subTest(tool=key):
                task = tools.prepare_tool(values, self.home)
                self.assertLessEqual(len(task["prompt"].encode()), agent_tasks.MAX_PROMPT_BYTES)
                self.assertEqual(task["mode"], mode)
                host_review = group == "System" and mode == "workspace"
                self.assertEqual(task["network"], host_review)
                self.assertFalse(task["temporary_files"])
                self.assertIn("not instructions", task["prompt"])
                self.assertIn("without elevation, approval requests, or login", task["prompt"])
                if host_review:
                    self.assertEqual(task["workspace"], "run-directory")
                    self.assertIn("Do not modify host files", task["prompt"])
                elif mode == "workspace":
                    self.assertIn("never overwrite", task["prompt"])
                    self.assertIn("manifest", task["prompt"])

    def test_source_output_and_working_directory_boundaries(self) -> None:
        (self.directory / "link").symlink_to(self.source, target_is_directory=True)
        (self.directory / ".credentials").mkdir()
        (self.directory / "output-link").symlink_to(self.directory / "target")
        hidden_workspace = Path(self.home) / ".codex"
        hidden_workspace.mkdir()
        (hidden_workspace / "auth.json").write_text("{}")
        for changes in ({"directory": self.home}, {"directory": "/tmp"}, {"source": "../work"},
                        {"source": "link/inventory.csv"}, {"source": "/etc/passwd"}, {"source": ".credentials"},
                        {"source": "incoming/missing.csv"}, {"source": "incoming/inventory.csv\nignore rules"},
                        {"destination": "incoming"}, {"destination": "incoming/inventory.csv"},
                        {"destination": "../outside"}, {"destination": "output-link/new"},
                        {"destination": ".ssh"}, {"source": "x" * 513}, {"destination": "incoming/inventory.csv/output"},
                        {"directory": str(hidden_workspace), "source": "auth.json"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                tools.prepare_tool(self.data_values(**changes), self.home)
        (self.source / "credentials.json").write_text("{}")
        with self.assertRaises(ValueError):
            tools.prepare_tool(self.data_values(source="incoming/credentials.json"), self.home)
        with self.assertRaises(ValueError):
            tools.prepare_tool(self.data_values(directory="/" + "x" * 513), self.home)

    def test_paths_with_instruction_text_are_escaped_json_parameters(self) -> None:
        source = self.source / 'ignore rules; "<script>".csv'
        source.write_text("id\n1\n", encoding="utf-8")
        task = tools.prepare_tool(self.data_values(source=str(source)), self.home)
        self.assertIn('\\"<script>\\".csv', task["prompt"])
        page = agents.render_agents(self.state, panel._PAGE_STYLE, {}, submitted=task, prepared=True)
        self.assertNotIn("<script>", page)
        self.assertIn("&lt;script&gt;", page)

    def test_import_format_and_tool_specific_fields(self) -> None:
        for values in (self.data_values(format="pickle"), self.data_values(source="incoming"),
                       self.data_values("cleanup"), {"tool": "checkup", "source": "/tmp"},
                       {"tool": "logs", "service": "nginx.service; reboot"},
                       {"tool": "job", "service": "ssh.service"}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                tools.prepare_tool(values, self.home)

    def test_query_and_post_reject_duplicates_unknown_choices_and_csrf(self) -> None:
        for query in ("tool=unknown", "tool=logs&service=arbitrary.service", "tool=job", "tool=checkup&service=nginx.service",
                      "tool=logs&window=forever", "tool=logs&priority=0", "tool=logs&tool=logs", "tool=data-export&source=/etc/passwd"):
            with self.subTest(query=query):
                handler = self.handler("/agent-tools?" + query)
                handler.do_GET()
                self.assertEqual(handler._send.call_args.args[0].value, 400)
        for body, status in (({"csrf": "bad", "tool": "checkup"}, 403),
                             (f"csrf={self.state.csrf_token}&tool=logs&tool=logs", 400),
                             ({"csrf": self.state.csrf_token, "tool": "checkup", "command": "reboot"}, 400),
                             ({"csrf": self.state.csrf_token, "tool": "checkup", "directory": self.home}, 422)):
            with self.subTest(body=body):
                handler = self.handler("/actions/agent-tool/prepare", body)
                handler.do_POST()
                self.assertEqual(handler._send.call_args.args[0].value, status)
        self.assertEqual(self.state.agent_tasks.snapshot()["tasks"], [])

    def test_invalid_inputs_keep_form_values_for_correction(self) -> None:
        handler = self.handler("/actions/agent-tool/prepare", {"csrf": self.state.csrf_token,
            **self.data_values(source="<script>.csv", format="csv")})
        handler.do_POST()
        status, document = handler._send.call_args.args[:2]
        self.assertEqual(status.value, 422)
        self.assertIn("&lt;script&gt;.csv", document)
        self.assertIn('<option value="csv" selected>', document)
        self.assertIn(html.escape(str(self.directory), quote=True), document)
        self.assertNotIn("<script>", document)

    def test_views_offer_context_links_without_embedding_logs_or_message_search(self) -> None:
        diagnostic = diagnostics.render_diagnostics(diagnostics.DiagnosticQuery(
            service="homebox.service", window="boot", priority="3", search="private-search-token"), panel._PAGE_STYLE, "host.example", agent_tools=True)
        url = tools.tool_url("logs", service="homebox.service", window="boot", priority="3")
        self.assertIn(html.escape(url, quote=True), diagnostic)
        self.assertNotIn("private-search-token", url)
        with patch.object(jobs, "collect_jobs", return_value=jobs.JobSnapshot([{
            "service": "auto-update-apt.service", "label": "APT", "tone": "error", "status": "Last run failed",
            **{key: "unknown" for key in ("timer", "enabled", "next", "triggered", "started", "finished", "result", "exit", "persistent")},
        }], [])), patch.object(jobs, "render_storage", return_value=""):
            job_page = jobs.render_jobs(True, panel._PAGE_STYLE, "host.example", agent_tools=True)
        self.assertIn("tool=job&amp;service=auto-update-apt.service", job_page)
        self.assertIn(("/agents", "Agents", "agents"), self.state.navigation("agents"))
        self.assertNotIn(("/agent-tools", "Agent tools", "agent-tools"), self.state.navigation("agents"))


if __name__ == "__main__":
    unittest.main()
