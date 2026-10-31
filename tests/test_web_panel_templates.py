"""Shared web-panel document and navigation rendering tests."""

from __future__ import annotations

import unittest
from io import BytesIO
from unittest.mock import Mock, patch
import xml.etree.ElementTree as ET
import tempfile
from pathlib import Path

from common.service_tools import web_panel_service as panel
from common.service_tools import web_panel_features as features
from common.service_tools.web_panel_agent_hub import agent_tabs
from common.service_tools.web_panel_templates import (
    BRAND_PALETTES,
    panel_navigation,
    render_document,
    render_favicon,
    render_sidebar,
)


class WebPanelTemplateTest(unittest.TestCase):
    def test_palette_keeps_text_controls_and_status_readable_in_both_themes(self) -> None:
        def luminance(color: str) -> float:
            values = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
            linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in values]
            return sum(v * weight for v, weight in zip(linear, (.2126, .7152, .0722)))

        for theme, palette in BRAND_PALETTES.items():
            for background in ("bg", "panel", "accent-soft", "workspace-soft", "stone-soft", "sea-soft", "bad-soft"):
                for foreground in ("text", "muted", "accent", "ok", "bad", "warning", "workspace", "stone", "sea"):
                    with self.subTest(theme=theme, foreground=foreground, background=background):
                        a, b = sorted((luminance(palette[foreground]), luminance(palette[background])))
                        self.assertGreaterEqual((b + .05) / (a + .05), 4.5)
            for background in ("bg", "panel"):
                a, b = sorted((luminance(palette["line"]), luminance(palette[background])))
                self.assertGreaterEqual((b + .05) / (a + .05), 3)

    def test_navigation_keeps_core_links_and_marks_only_current_page(self) -> None:
        sidebar = render_sidebar(
            panel_navigation(current="jobs")
        )

        self.assertIn('href="/"', sidebar)
        self.assertNotIn('href="/#', sidebar)
        self.assertNotIn('href="/agents"', sidebar)
        self.assertNotIn('href="/credentials"', sidebar)
        root = ET.fromstring(sidebar)
        groups = [node for node in root.iter("div") if "nav-links" in node.attrib.get("class", "").split()]
        self.assertEqual(len(groups), 2)
        for group in groups:
            self.assertEqual(len(group.findall('.//a[@aria-current="page"]')), 1)
        self.assertIn('href="/jobs" aria-current="page"', sidebar)
        menu = root.find('.//details[@class="nav-mobile"]')
        self.assertNotIn("open", menu.attrib)
        self.assertEqual("".join(menu.find("summary").itertext()), "Panel menuScheduled jobs")
        self.assertEqual([link.attrib["href"] for link in menu.findall('.//a')], ["/", "/services", "/storage", "/jobs", "/logs"])

    def test_every_view_has_identical_navigation_destinations(self) -> None:
        available = features.PanelFeatures(tools=frozenset({"git", "codex", "butler"}), admin=True, maintenance=True).pages
        expected = [(href, label) for href, label, _ in panel_navigation(current="dashboard", available=available)]
        for current in ("agents", "agent-tools", "credentials", "admin", "shares-data", "services", "storage", "jobs", "logs"):
            with self.subTest(current=current):
                self.assertEqual([(href, label) for href, label, _ in panel_navigation(current=current, available=available)], expected)

    def test_agent_tabs_share_one_sidebar_destination_and_follow_feature_gates(self) -> None:
        state = Mock()
        state.features.return_value = features.PanelFeatures(tools=frozenset({"codex"}), maintenance=True)
        destinations = {"dashboard": "/agents", "tasks": "/agents?view=tasks", "history": "/agents?view=history",
                        "tools": "/agent-tools", "inbox": "/agent-inbox", "maintenance": "/maintenance-agent",
                        "diagnostics": "/agents?view=diagnostics"}
        for current, href in destinations.items():
            with self.subTest(current=current):
                tabs = ET.fromstring(agent_tabs(state, current))
                self.assertEqual([link.attrib["href"] for link in tabs.findall('./a[@aria-current="page"]')], [href])
                self.assertEqual({link.attrib["href"] for link in tabs.findall("a")}, set(destinations.values()))
        navigation = panel_navigation(current="agents", available=state.features().pages)
        self.assertIn(("/agents", "Agents", "agents"), navigation)
        self.assertFalse({href for href, _, _ in navigation} & {"/agent-tools", "/agent-inbox", "/maintenance-agent"})
        state.features.return_value = features.PanelFeatures(t3=True)
        tabs = ET.fromstring(agent_tabs(state, "diagnostics"))
        self.assertEqual({link.attrib["href"] for link in tabs.findall("a")},
                         {"/agents", "/agents?view=diagnostics", "/agent-inbox"})

    def test_document_has_one_shared_shell_and_escapes_title(self) -> None:
        document = render_document(
            title="View <test>",
            style="body { color: red; }",
            header="<header>Header</header>",
            content="<p>Content</p>",
            navigation=panel_navigation(current="dashboard"),
            footer="<footer>Footer</footer>",
        )

        self.assertEqual(document.count('<nav class="sidebar"'), 1)
        self.assertIn("View &lt;test&gt;", document)
        self.assertIn('href="#main"', document)
        self.assertIn("<header>Header<svg", document)
        self.assertIn('class="brand-texture brand-texture-basalt"', document)
        self.assertIn("<footer>Footer<svg", document)
        self.assertIn('rel="icon" href="/favicon.svg" type="image/svg+xml"', document)

    def test_favicon_route_serves_only_vector_artwork_without_host_state(self) -> None:
        handler = object.__new__(panel.WebPanelHandler)
        handler.path = "/favicon.svg"
        handler._send = Mock()
        handler.do_GET()
        handler._send.assert_called_once_with(panel.HTTPStatus.OK, render_favicon(), "image/svg+xml")
        root = ET.fromstring(handler._send.call_args.args[1])
        self.assertEqual(root.attrib["viewBox"], "0 0 32 32")
        self.assertIn("prefers-color-scheme:dark", root.find("{http://www.w3.org/2000/svg}style").text)
        self.assertTrue(all(node.tag.rsplit("}", 1)[-1] in {"svg", "style", "path"} for node in root.iter()))

    def test_favicon_csp_allows_same_origin_images_and_keeps_scripts_blocked(self) -> None:
        handler = object.__new__(panel.WebPanelHandler)
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.wfile = BytesIO()
        handler._send(panel.HTTPStatus.OK, render_favicon(), "image/svg+xml")
        headers = dict(call.args for call in handler.send_header.call_args_list)
        policy = headers["Content-Security-Policy"]
        self.assertIn("img-src 'self'", policy)
        self.assertIn("default-src 'none'", policy)
        self.assertNotIn("data:", policy)
        self.assertNotIn("script-src", policy)
        self.assertEqual(headers["Content-Type"], "image/svg+xml; charset=utf-8")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")


class AgentWorkflowFeedbackTest(unittest.TestCase):
    def setUp(self) -> None:
        from common.service_tools import web_panel_agents as agents

        self.home = self.enterContext(tempfile.TemporaryDirectory())
        self.state = panel.WebPanelState({"host": "example.test", "username": "operator", "features": {}}, agent_home=self.home)
        self.addCleanup(self.state.agent_tasks.close)
        self.enterContext(patch.object(self.state, "features", return_value=features.PanelFeatures(tools=frozenset({"codex", "git"}))))
        self.enterContext(patch.object(self.state.agent_tasks, "available", return_value=True))
        self.enterContext(patch.object(agents, "_tool_path", return_value="/test/codex"))
        self.enterContext(patch("lib.agent_tasks._tool_path", return_value="/test/codex"))
        self.enterContext(patch.object(agents, "worker_status", return_value={"installed": False, "running": False}))
        self.values = {"title": "Review repository", "prompt": "Inspect and report.", "directory": self.home, "mode": "inspect", "interval": "weekly"}

    def test_unreadable_storage_is_not_presented_as_an_empty_account(self) -> None:
        with patch.object(self.state.agent_tasks, "snapshot", side_effect=RuntimeError("Unreadable <task storage>")):
            page = panel.render_agents(self.state, panel._PAGE_STYLE, {"view": "tasks"})
            history = panel.render_agents(self.state, panel._PAGE_STYLE, {"view": "history"})
            submitted = panel.render_agents(self.state, panel._PAGE_STYLE, {}, submitted=self.values)
        self.assertIn("Task storage unavailable", page)
        self.assertIn("Unreadable &lt;task storage&gt;", page)
        self.assertIn('href="/agents">Retry task storage', page)
        for misleading in ("No prompt tasks yet", "No prompt runs have been recorded", "0 running", "0 saved", "requires a non-root panel account"):
            self.assertNotIn(misleading, page)
        self.assertIn("Run history could not be loaded", history)
        self.assertIn('value="Review repository"', submitted)
        self.assertIn("Inspect and report.</textarea>", submitted)
        self.assertNotIn("Retry task storage</a>", submitted)
        self.assertIn("Copy your prompt and settings before reloading", submitted)
        self.assertIn('value="run" disabled>Start task', submitted)

    def test_manual_run_label_explains_an_inactive_repeating_draft(self) -> None:
        identifier = self.state.agent_tasks.create(self.values, run_now=False, draft=True)
        page = panel.render_agents(self.state, panel._PAGE_STYLE, {"view": "tasks"})
        self.assertIn('value="run">Run once', page)
        self.assertIn("Run once keeps this schedule inactive. Use Start schedule to enable it.", page)
        self.state.agent_tasks.action(identifier, "resume")
        page = panel.render_agents(self.state, panel._PAGE_STYLE, {"view": "tasks"})
        self.assertIn("Run once keeps this schedule active.", page)
        composer = panel.render_agents(self.state, panel._PAGE_STYLE, {"view": "new"})
        self.assertIn('value="run">Start task', composer)
        self.assertIn("Start task runs now and activates the selected repetition", composer)

    def test_status_refresh_keeps_the_selected_run_and_retained_evidence(self) -> None:
        from lib import agent_tasks

        self.state.agent_tasks.create(self.values, run_now=True)
        with patch.object(agent_tasks, "execute_prompt", return_value={"status": "completed", "output": "Report ready", "message": "Done"}):
            self.state.agent_tasks.tick()
        run = self.state.agent_tasks.snapshot()["runs"][0]
        for query, target in (({"run": run["id"]}, f"/agents?run={run['id']}#run-{run['id']}"),
                              ({"context": run["task"]["id"]}, f"/agents?context={run['task']['id']}"),
                              ({"receipt": run["task"]["id"] + "." + run["id"]}, f"/agents?receipt={run['task']['id']}.{run['id']}")):
            with self.subTest(query=query):
                page = panel.render_agents(self.state, panel._PAGE_STYLE, query)
                self.assertIn(f'href="{target}">Refresh status', page)
                self.assertIn('href="/agents?view=history" aria-current="page"', page)
                self.assertIn('id="agent-history-heading"', page)
                self.assertNotIn('id="agent-tasks-heading"', page)
                self.assertNotIn('class="agent-form"', page)
        self.assertIn("Report ready", page)


class PanelFeatureTest(unittest.TestCase):
    def setUp(self) -> None:
        self.home = self.enterContext(tempfile.TemporaryDirectory())
        self.state = panel.WebPanelState({"host": "minimal.example.test", "username": "operator",
            "system_type": "server_web", "services": [], "access": [],
            "features": {"t3_update": True, "agent_tools": ["codex", "claude", "opencode", "gh"], "git": True}},
            agent_home=self.home)
        self.tool = self.enterContext(patch.object(features, "_tool_path", return_value=None))
        self.units = self.enterContext(patch.object(features, "unit_installed", return_value=False))
        self.enterContext(patch.object(features.os.path, "isfile", return_value=False))
        self.enterContext(patch.object(features.os.path, "isdir", return_value=False))

    def test_minimal_machine_ignores_stale_setup_flags_and_runs_no_checks(self) -> None:
        with patch("subprocess.run") as run, patch("subprocess.Popen") as spawn:
            available = self.state.features()
            sidebar = render_sidebar(self.state.navigation("dashboard"))
        self.assertEqual(available.pages, frozenset({"dashboard", "services", "storage", "jobs", "logs", "admin", "shares-data"}))
        self.assertEqual(sidebar.count('aria-current="page"'), 2)
        self.assertNotIn('href="/#', sidebar)
        self.assertIn('href="/admin"', sidebar)
        for path in ("agents", "credentials", "publishing", "maintenance-agent", "agent-tools", "agent-inbox"):
            self.assertNotIn(f'href="/{path}"', sidebar)
        run.assert_not_called()
        spawn.assert_not_called()

    def test_installation_changes_are_visible_on_next_request_without_sign_in(self) -> None:
        self.assertNotIn("agents", self.state.features().pages)
        self.tool.side_effect = lambda name, home: f"{home}/.local/bin/{name}" if name in {"codex", "git", "gh", "butler"} else None
        detected = self.state.features()
        self.assertTrue(detected.prompts)
        self.assertTrue({"agents", "credentials", "publishing", "agent-tools", "agent-inbox"} <= detected.pages)
        self.assertIn("admin", detected.pages)
        self.assertFalse(detected.admin)
        self.tool.side_effect = None
        self.assertNotIn("agents", self.state.features().pages)

    def test_t3_and_other_agents_expose_diagnostics_without_prompt_tools(self) -> None:
        self.units.side_effect = lambda unit, home, **kwargs: unit == "t3code.service"
        self.assertTrue(self.state.features().agents)
        self.assertFalse(self.state.features().prompts)
        page = panel.render_agents(self.state, panel._PAGE_STYLE, {"view": "diagnostics"})
        self.assertIn('href="/agents" aria-current="page"', page)
        self.assertIn('action="/actions/agent-diagnostics"', page)
        self.assertNotIn('class="agent-form"', page)
        self.assertNotIn('href="/agent-tools"', page)
        with patch.object(self.state, "t3_update_available", return_value=True):
            self.assertIn("Update T3 Code</button>", panel.render_agents(self.state, panel._PAGE_STYLE, {"view": "diagnostics"}))
        self.units.side_effect = None
        self.tool.side_effect = lambda name, home: "/test/claude" if name == "claude" else None
        self.assertTrue(self.state.features().agents)
        self.assertFalse(self.state.features().prompts)

    def test_t3_update_feedback_and_submission_stay_in_agents_readiness(self) -> None:
        self.units.side_effect = lambda unit, home, **kwargs: unit == "t3code.service"
        self.state.action_status = "running"
        self.state.action_message = "Updating T3 Code…"
        self.state.action_started_at = 100.0
        self.state.action_output = "Result <details>"
        with patch.object(self.state, "t3_update_available", return_value=True), patch("common.service_tools.web_panel_agents.time.monotonic", return_value=225.0):
            page = panel.render_agents(self.state, panel._PAGE_STYLE, {"view": "diagnostics"})
        for text in ("Update in progress…", "2m 05s elapsed", "Refresh status for progress", "Result &lt;details&gt;", "View command output"):
            self.assertIn(text, page)
        self.assertIn('<button disabled>', page)
        self.assertNotIn('http-equiv="refresh"', page)
        handler = object.__new__(panel.WebPanelHandler)
        body = ("csrf=" + self.state.csrf_token + "&return=agents").encode()
        handler.state, handler.path, handler.headers = self.state, "/actions/t3-update", {"Content-Length": str(len(body))}
        handler.rfile = BytesIO(body)
        handler.send_response, handler.send_header, handler.end_headers = Mock(), Mock(), Mock()
        with patch.object(self.state, "t3_update_available", return_value=True), patch.object(self.state, "trigger_t3_update", return_value=True) as trigger:
            handler.do_POST()
        trigger.assert_called_once_with()
        handler.send_response.assert_called_once_with(panel.HTTPStatus.SEE_OTHER)
        handler.send_header.assert_any_call("Location", "/agents?view=diagnostics")

    def test_installed_desktop_has_its_own_navigation_destination(self) -> None:
        self.assertNotIn('href="/desktop/"', render_sidebar(self.state.navigation("dashboard")))
        self.units.side_effect = lambda unit, home, **kwargs: unit == "basaltwater-guacamole.service"
        self.assertIn('href="/desktop/"', render_sidebar(self.state.navigation("dashboard")))
        self.assertFalse(self.state.features().agents)

    def test_absent_optional_get_and_post_routes_never_read_or_mutate_state(self) -> None:
        paths = ("agents", "agent-tools", "agent-inbox", "maintenance-agent", "credentials", "publishing")
        actions = ("agent-task/save", "agent-task", "agent-tool/prepare", "agent-diagnostics",
                   "agent-inbox", "maintenance-agent", "credentials/identity", "credentials/github",
                   "credentials/github-remove", "publishing/project", "publishing/writing-task", "admin", "admin/cancel")
        with patch.object(self.state.agent_tasks, "snapshot") as tasks, patch.object(self.state.publishing, "status") as publishing:
            for method, routes in (("do_GET", ["/" + path for path in paths]), ("do_POST", ["/actions/" + action for action in actions])):
                for path in routes:
                    with self.subTest(method=method, path=path):
                        handler = object.__new__(panel.WebPanelHandler)
                        handler.state, handler.path, handler._send = self.state, path, Mock()
                        getattr(handler, method)()
                        self.assertEqual(handler._send.call_args.args[0], panel.HTTPStatus.NOT_FOUND)
        tasks.assert_not_called()
        publishing.assert_not_called()

    def test_runner_admin_page_explains_setup_without_enabling_actions_or_agents(self) -> None:
        self.state.manifest.update(system_type="server_github_runner", features={})
        handler = object.__new__(panel.WebPanelHandler)
        handler.state, handler.path, handler._send = self.state, "/admin", Mock()
        with patch("common.service_tools.web_panel_admin.exchange") as broker, patch.object(self.state.agent_tasks, "snapshot") as tasks:
            handler.do_GET()
        status, page, content_type = handler._send.call_args.args
        self.assertEqual(status, panel.HTTPStatus.OK)
        self.assertEqual(content_type, "text/html")
        self.assertIn('href="/admin" aria-current="page"', page)
        self.assertIn("--privilege-broker --privilege-broker-password", page)
        self.assertNotIn('href="/admin?action=', page)
        self.assertNotIn('href="/agents"', page)
        self.assertFalse(handler._feature_available("/actions/admin"))
        self.assertFalse(handler._feature_available("/actions/admin/cancel"))
        broker.assert_not_called()
        tasks.assert_not_called()

    def test_inspection_pages_have_no_agent_shortcuts_without_codex(self) -> None:
        from common.service_tools import web_panel_jobs as jobs
        from common.service_tools import web_panel_diagnostics as diagnostics

        pages = [panel.render_service_status(self.state, False),
                 jobs.render_jobs(False, panel._PAGE_STYLE, "minimal"),
                 diagnostics.render_diagnostics(diagnostics.DiagnosticQuery(), panel._PAGE_STYLE, "minimal",
                                                sources={"nginx.service": "Web gateway"})]
        for page in pages:
            self.assertNotIn('href="/agent-tools', page)
            self.assertNotIn('href="/agents', page)
        self.assertNotIn('<option value="t3code.service"', pages[-1])
        self.assertNotIn('<option value="gogs.service"', pages[-1])

    def test_git_only_page_omits_github_and_agent_controls(self) -> None:
        self.tool.side_effect = lambda name, home: "/test/git" if name == "git" else None
        with patch.object(self.state.git_settings, "snapshot", return_value={"available": True,
            "name": "Operator", "email": "operator@example.test", "git_error": "", "github_error": "", "github_present": False}):
            page = panel.render_credentials(self.state, panel._PAGE_STYLE, {})
        self.assertIn("Save commit identity", page)
        self.assertNotIn('id="github-token"', page)
        self.assertNotIn('id="provider-sign-in"', page)
        self.assertNotIn('href="/agents"', page)
        handler = object.__new__(panel.WebPanelHandler)
        handler.state, handler.path, handler._send = self.state, "/actions/credentials/github", Mock()
        handler.do_POST()
        self.assertEqual(handler._send.call_args.args[0], panel.HTTPStatus.NOT_FOUND)


class PanelUnitTest(unittest.TestCase):
    def test_user_unit_discovery_includes_masked_and_disabled_installations(self) -> None:
        with tempfile.TemporaryDirectory() as home, patch("subprocess.run") as run:
            directory = Path(home) / ".config/systemd/user"
            directory.mkdir(parents=True)
            unit = directory / "panel-fixture-uninstalled.service"
            self.assertFalse(features.unit_installed(unit.name, home, user=True))
            unit.write_text("[Service]\nExecStart=/fixture/tool\n")
            self.assertTrue(features.unit_installed(unit.name, home, user=True))
            unit.unlink()
            unit.symlink_to("/dev/null")
            self.assertTrue(features.unit_installed(unit.name, home, user=True))
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
