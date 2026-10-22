"""Storage dashboard rendering, scoped actions and development isolation."""

from __future__ import annotations

from contextlib import ExitStack
import http.client
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlencode

from common.service_tools.web_panel_features import PanelFeatures
from common.service_tools.web_panel_service import WebPanelHandler, WebPanelState, _PAGE_STYLE
from common.service_tools.web_panel_storage_usage import parse_storage_query, parse_storage_scan, render_storage_usage
from common.service_tools.web_panel_templates import panel_navigation
from scripts.panel_scenarios import Scenario
from scripts.panel_storage_samples import storage_snapshot


class StorageDashboardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.snapshot = storage_snapshot(loaded=False)
        self.manager = Mock()
        self.manager.snapshot.side_effect = lambda: self.snapshot
        self.state = SimpleNamespace(storage_diagnostics=self.manager, manifest={"host": "vm.example.test"},
                                     csrf_token="test-csrf", navigation=lambda current: panel_navigation(current=current))

    def render(self, view: str = "areas") -> str:
        return render_storage_usage(self.state, _PAGE_STYLE, view)

    def handler(self, path: str, fields: dict | None = None):
        handler = object.__new__(WebPanelHandler)
        handler.state = self.state
        handler.path = path
        handler.headers = http.client.HTTPMessage()
        body = urlencode(fields or {}, doseq=True).encode()
        handler.headers["Content-Length"] = str(len(body))
        handler.rfile = io.BytesIO(body)
        handler._send = Mock()
        return handler

    def test_storage_is_available_without_agent_tools(self) -> None:
        self.assertIn("storage", PanelFeatures().pages)
        self.assertIn(("/storage", "Storage", "storage"), panel_navigation(current="storage"))

    def test_query_only_selects_a_single_known_view(self) -> None:
        self.assertEqual(parse_storage_query(""), "areas")
        self.assertEqual(parse_storage_query("view=projects"), "projects")
        for query in ("view=", "view=all", "path=/root", "view=areas&view=projects", "view=areas&x=1", "view", "x=" + "a" * 65):
            with self.subTest(query=query), self.assertRaises(ValueError):
                parse_storage_query(query)

    def test_idle_get_never_starts_collection(self) -> None:
        handler = self.handler("/storage")
        handler.do_GET()
        self.assertEqual(handler._send.call_args.args[0], 200)
        document = handler._send.call_args.args[1]
        self.assertIn("Run diagnostic", document)
        self.assertIn("does not read file contents or remove data", document)
        self.manager.start.assert_not_called()
        self.assertNotIn('http-equiv="refresh"', document)
        self.assertNotIn("conic-gradient", document.split("</style>")[-1])

    def test_invalid_get_and_post_cannot_start_scan(self) -> None:
        for path, fields, status in (("/storage?path=/", None, 400),
                                      ("/actions/storage-scan", {"csrf": "wrong"}, 403),
                                      ("/actions/storage-scan", {"csrf": ["test-csrf", "test-csrf"]}, 403),
                                      ("/actions/storage-scan", {"csrf": "test-csrf", "path": "/"}, 400),
                                      ("/actions/storage-scan", {"csrf": "test-csrf", "mode": "all"}, 400),
                                      ("/actions/storage-scan", {"csrf": "test-csrf", "mode": ["quick", "extended"]}, 400),
                                      ("/actions/storage-scan", {"csrf": "test-csrf", "entry_limit": "5000000"}, 400),
                                      ("/actions/storage-scan?view=areas", {"csrf": "test-csrf"}, 400)):
            handler = self.handler(path, fields)
            handler.do_GET() if fields is None else handler.do_POST()
            self.assertEqual(handler._send.call_args.args[0], status)
        self.manager.start.assert_not_called()

    def test_post_starts_scan_and_duplicate_redirects_to_existing_job(self) -> None:
        for started in (True, False):
            self.manager.start.return_value = started
            handler = self.handler("/actions/storage-scan", {"csrf": "test-csrf"})
            handler.do_POST()
            handler._send.assert_called_once_with(303, "", "text/plain", headers={"Location": "/storage"})
        self.assertEqual(self.manager.start.call_count, 2)
        self.manager.start.assert_called_with(mode="quick")

    def test_extended_mode_is_validated_and_selected_through_reruns(self) -> None:
        self.assertEqual(parse_storage_scan({"csrf": ["test-csrf"]}), "quick")
        for fields in ({"mode": []}, {"mode": [""]}):
            with self.assertRaises(ValueError):
                parse_storage_scan(fields)
        handler = self.handler("/actions/storage-scan", {"csrf": "test-csrf", "mode": "extended"})
        handler.do_POST()
        self.assertEqual(handler._send.call_args.args[0], 303)
        self.manager.start.assert_called_once_with(mode="extended")
        self.snapshot = storage_snapshot(loaded=True, mode="extended")
        self.snapshot["state"] = "running"
        document = self.render()
        self.assertIn('<option value="extended" selected>', document)
        self.assertIn('name="mode" disabled', document)
        self.assertIn("60 seconds and 2,000,000 entries", document)

    def test_quick_limit_notice_distinguishes_budget_from_permissions(self) -> None:
        self.snapshot = storage_snapshot(loaded=True, degraded=True)
        self.assertNotIn("Quick scan limit reached", self.render())
        self.snapshot["report"]["entries_scanned"] = 200000
        document = self.render()
        self.assertIn("Quick scan limit reached", document)
        self.assertIn("Choose Extended", document)
        self.assertIn("Permission-denied paths remain partial", document)
        self.snapshot = storage_snapshot(loaded=True, mode="extended")
        self.snapshot["report"]["entries_scanned"] = 2000000
        self.assertNotIn("Quick scan limit reached", self.render())

    def test_running_keeps_last_report_and_refresh_requires_user_action(self) -> None:
        self.snapshot = storage_snapshot(loaded=True)
        self.snapshot["state"] = "running"
        document = self.render("projects")
        for expected in ('Use Refresh status to check progress', "Previous results remain visible", " disabled", "infra-tools", 'href="/storage?view=projects" aria-current="page"'):
            self.assertIn(expected, document)
        self.assertNotIn('http-equiv="refresh"', document)
        self.manager.start.assert_not_called()

    def test_results_include_statistics_accessible_visual_and_coverage(self) -> None:
        self.snapshot = storage_snapshot(loaded=True, degraded=True)
        document = self.render()
        for expected in ("Partial estimate", 'role="img"', "96.0% used", "76.8 GiB", "58,304", "62,420", "3.8s", "Allocated", "Logical size", "≥ 8.8 GiB", "Coverage", "200,000 entries", "root filesystem", "nested mounts are skipped"):
            self.assertIn(expected, document)
        self.assertNotIn('http-equiv="refresh"', document)
        self.snapshot["report"]["root_filesystem"] = None
        self.assertIn("Capacity unavailable", self.render())

    def test_views_do_not_add_overlapping_service_and_project_totals(self) -> None:
        self.snapshot = storage_snapshot(loaded=True)
        for view, expected, excluded in (("services", "T3 Code", "infra-tools"), ("projects", "infra-tools", "T3 Code")):
            document = self.render(view)
            self.assertIn(expected, document)
            self.assertNotIn(excluded, document)
            self.assertIn("do not add these totals together", document)

    def test_unavailable_empty_failure_and_escaping(self) -> None:
        self.snapshot = storage_snapshot(loaded=True, degraded=True)
        row = self.snapshot["report"]["areas"][0]
        row.update(label='<script>alert("x")</script>', path="/home/<unsafe>", allocated_bytes=0, files=0,
                   complete=False, issues=["<private>"])
        self.snapshot["message"] = "<failure>"
        document = self.render()
        self.assertIn("&lt;script&gt;", document)
        self.assertIn("&lt;private&gt;", document)
        self.assertIn("Unavailable", document)
        self.assertIn("Usage unknown", document)
        self.assertNotIn("0.0% of observed allocation", document)
        self.assertIn("% of observed allocation", document)
        self.assertNotIn("<script>", document)
        self.snapshot["report"]["projects"] = []
        self.assertIn("No projects measured", self.render("projects"))
        self.snapshot = {**storage_snapshot(loaded=False), "state": "failed", "message": "Unable to inspect storage."}
        self.assertIn("Diagnostic could not finish", self.render())
        self.assertIn("Run diagnostic", self.render())

    def test_development_scan_never_calls_host_collector(self) -> None:
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            collector = stack.enter_context(patch("lib.storage_diagnostics.collect_storage_usage", side_effect=AssertionError("Host scan")))
            state = WebPanelState({"host": "development.example.test", "username": "operator", "services": [], "features": {}}, agent_home=directory)
            scenario = Scenario(state, "degraded", stack)
            self.assertEqual(state.storage_diagnostics.snapshot()["state"], "idle")
            state.storage_diagnostics.start(mode="extended")
            snapshot = state.storage_diagnostics.snapshot()
            self.assertEqual(snapshot["state"], "partial")
            self.assertIn("Synthetic", snapshot["message"])
            self.assertEqual(snapshot["scan_mode"], "extended")
            self.assertEqual(snapshot["report"]["limits"]["entry_limit"], 2000000)
            with ExitStack() as reloaded:
                Scenario(state, "degraded", reloaded)
                self.assertEqual(state.storage_diagnostics.snapshot()["state"], "partial")
            self.assertTrue((Path(directory) / "scenario.json").exists())
            self.assertEqual(scenario.data["storage_loaded"], True)
            collector.assert_not_called()

    def test_non_utf8_project_names_remain_renderable(self) -> None:
        self.snapshot = storage_snapshot(loaded=True)
        self.snapshot["report"]["projects"][0].update(label="project-\udcff", path="/home/operator/repos/project-\udcff")
        document = self.render("projects")
        self.assertIn("project-\\udcff", document)
        document.encode("utf-8")
