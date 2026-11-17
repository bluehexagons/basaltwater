"""Native T3 handoff, retired actions and saved independent task contracts."""

from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from common.service_tools import web_panel_agents as agents
from common.service_tools import web_panel_service as panel
from common.service_tools import web_panel_t3 as t3
from common.service_tools.web_panel_features import PanelFeatures, t3_selected

collect_routes = panel.discover_basaltwater_web_services


class T3WorkspaceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.home = self.enterContext(tempfile.TemporaryDirectory())
        self.state = panel.WebPanelState({"features": {}, "services": [], "host": "vm.example.test", "username": "operator"}, agent_home=self.home)
        self.addCleanup(self.state.agent_tasks.close)
        self.features = self.enterContext(patch.object(self.state, "features", return_value=PanelFeatures(t3=True)))
        self.routes = self.enterContext(patch.object(panel, "discover_basaltwater_web_services", return_value=[]))

    def test_registered_forward_survives_service_deduplication_and_uses_allocated_port(self) -> None:
        url = "https://coding.example.test:8457/"
        self.state.manifest["services"] = [{"label": "Coding", "url": url}]
        with patch.object(panel.shutil, "which", return_value="/fixture/basaltwater-web"), patch.object(
            panel, "_internal_web_landing_service", return_value=None,
        ), patch.object(panel, "_run_json", side_effect=[
            {"forwards": [{"name": "t3code", "url": url, "ready": False}]},
            {"sites": [{"name": "t3code", "url": "https://site.example.test/"}]},
        ]) as command:
            discovered = collect_routes()
        self.assertEqual([record.get("application") for record in discovered], ["t3code", None])
        self.routes.return_value = discovered
        self.assertEqual(t3.workspace_url(self.state), url)
        self.assertEqual(command.call_count, 2)
        self.assertEqual(self.state.web_services().value[0]["application"], "t3code")

    def test_handoff_requires_an_unambiguous_safe_https_root_and_application_identity(self) -> None:
        valid = "https://coding.example.test:8457/"
        invalid = ("http://coding.example.test/", "https://user:secret@coding.example.test/",
                   "https://@coding.example.test/",
                   "https://coding.example.test/pair", valid + "?", valid + "#token=secret",
                   "https://coding.example.test:0/", "https://coding.example.test:65536/",
                   "https://coding.example.test:bad/", "https://bad host/", "https://bad\\host/",
                   "https://coding.example.test/\n", "https://-invalid/", "//coding.example.test/", None)
        for url in invalid:
            with self.subTest(url=url), patch.object(self.state, "web_services", return_value=SimpleNamespace(
                value=[{"application": "t3code", "url": url}],
            )):
                self.assertIsNone(t3.workspace_url(self.state))
        for records in ([{"label": "T3 Code", "url": valid}],
                        [{"application": "t3code", "url": url} for url in (valid, "https://other.example.test/")]):
            with self.subTest(records=records), patch.object(self.state, "web_services", return_value=SimpleNamespace(value=records)):
                self.assertIsNone(t3.workspace_url(self.state))
        with patch.object(self.state, "web_services", return_value=SimpleNamespace(value=[
            {"application": "t3code", "url": "https://[2001:db8::1]:8457"},
        ])):
            self.assertEqual(t3.workspace_url(self.state), "https://[2001:db8::1]:8457/")

    def test_t3_only_view_does_not_need_task_storage_or_create_authentication(self) -> None:
        self.routes.return_value = [{"application": "t3code", "label": "Coding", "url": "https://coding.example.test:8457/"}]
        with patch.object(self.state.agent_tasks, "snapshot", side_effect=RuntimeError("Unreadable tasks")) as storage, patch(
            "subprocess.Popen",
        ) as spawn, patch.object(self.state.agent_diagnostics, "trigger") as diagnose:
            page = agents.render_agents(self.state, panel._PAGE_STYLE, agents.parse_agent_query("view=t3"))
        storage.assert_not_called()
        spawn.assert_not_called()
        diagnose.assert_not_called()
        self.assertIn('href="https://coding.example.test:8457/" target="_blank" rel="noopener noreferrer"', page)
        self.assertIn('href="/agents?view=t3" aria-current="page"', page)
        self.assertIn("Manage T3 updates in its environment settings", page)
        self.assertNotIn('action="/actions/t3-update"', page)
        self.assertNotIn('class="agent-form"', page)

    def test_missing_route_has_recovery_guidance_and_unselected_t3_does_not_discover(self) -> None:
        page = t3.render_workspace(self.state)
        self.assertIn("T3 HTTPS address is unavailable", page)
        self.assertIn("existing T3 Connect connection", page)
        self.assertNotIn("Open T3 Code ↗</a>", page)
        self.features.return_value = PanelFeatures()
        with patch.object(self.state, "web_services") as discover:
            self.assertEqual(t3.render_workspace(self.state), "")
        discover.assert_not_called()

    def test_retired_update_post_cannot_spawn_commands_or_queue_work(self) -> None:
        handler = object.__new__(panel.WebPanelHandler)
        handler.state, handler.path = self.state, "/actions/t3-update"
        body = ("csrf=" + self.state.csrf_token).encode()
        handler.headers, handler.rfile, handler._send = {"Content-Length": str(len(body))}, BytesIO(body), Mock()
        with patch("subprocess.Popen") as spawn, patch.object(self.state.agent_tasks, "create") as create:
            handler.do_POST()
        self.assertEqual(handler._send.call_args.args[0], 404)
        spawn.assert_not_called()
        create.assert_not_called()

    def test_retired_repository_starters_leave_saved_tasks_and_receipts_editable(self) -> None:
        self.features.return_value = PanelFeatures(tools=frozenset({"codex", "git"}), t3=True)
        with patch.object(self.state.agent_tasks, "available", return_value=True), patch("lib.agent_tasks._tool_path", return_value="/fixture/codex"):
            identifier = self.state.agent_tasks.create({"title": "Old repository review", "prompt": "Inspect the repository",
                "directory": self.home, "mode": "inspect", "interval": "weekly"}, run_now=True)
            with patch("lib.agent_tasks.execute_prompt", return_value={"status": "completed", "message": "Done", "output": "Retained report"}):
                self.state.agent_tasks.tick()
            before = self.state.agent_tasks.snapshot()
            with patch.object(agents, "worker_status", return_value={"installed": False, "running": False}):
                page = agents.render_agents(self.state, panel._PAGE_STYLE, {"edit": identifier})
            self.assertIn('value="Old repository review"', page)
            self.assertIn("Save changes</button>", page)
            self.assertEqual(self.state.agent_tasks.snapshot(), before)
            self.assertEqual(before["runs"][0]["output"], "Retained report")
        for name in ("repository", "ci-repair", "dependencies", "documentation", "security-review",
                     "release-review", "github-actions", "browser-smoke", "godot-export"):
            with self.subTest(template=name), self.assertRaises(ValueError):
                agents.parse_agent_query("template=" + name)

    def test_saved_selection_migrates_without_reenabling_an_explicit_opt_out(self) -> None:
        self.assertTrue(t3_selected({"t3_update": True}))
        self.assertTrue(t3_selected({"t3code": True}))
        self.assertFalse(t3_selected({"t3code": False, "t3_update": True}))
        self.assertFalse(t3_selected({}))


if __name__ == "__main__":
    unittest.main()
