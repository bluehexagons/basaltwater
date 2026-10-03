"""Service collection and navigation regressions for the dashboard."""

from __future__ import annotations

import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from common.service_tools import web_panel_service as panel
from common.service_tools import web_panel_diagnostics as diagnostics
from common.service_tools import web_panel_jobs as jobs


class DashboardTest(unittest.TestCase):
    def test_rendered_views_share_sidebar_without_collecting_on_demand_data(self) -> None:
        state = panel.WebPanelState({
            "host": "example.test", "username": "agent", "system_type": "server_dev",
            "features": {}, "services": [], "access": [],
        })
        with (
            patch.object(panel, "discover_basaltwater_web_services", return_value=[]),
            patch.object(panel, "discover_certificate_trust", return_value=None),
            patch.object(state, "system_overview", return_value=[]),
            patch.object(state, "audit_snapshot", return_value={"status": "ok", "events": []}),
            patch.object(state, "service_health") as service_health,
            patch.object(jobs, "collect_jobs") as collect_jobs,
            patch.object(diagnostics, "collect_diagnostics") as collect_diagnostics,
        ):
            pages = [
                panel.render_page(state), panel.render_service_status(state, False),
                jobs.render_jobs(False, panel._PAGE_STYLE, "example.test"),
                diagnostics.render_diagnostics(diagnostics.DiagnosticQuery(), panel._PAGE_STYLE, "example.test"),
            ]
        service_health.assert_not_called()
        collect_jobs.assert_not_called()
        collect_diagnostics.assert_not_called()
        sidebars = [page.split('<nav class="sidebar"', 1)[1].split('</nav>', 1)[0] for page in pages]
        for sidebar in sidebars:
            self.assertEqual(sidebar.count('aria-current="page"'), 1)
            self.assertEqual(sidebar.replace(' aria-current="page"', ''), sidebars[0].replace(' aria-current="page"', ''))

    def test_usage_meters_are_bounded_and_service_states_keep_text_labels(self) -> None:
        state = panel.WebPanelState({
            "host": "example.test", "username": "agent", "system_type": "server_dev",
            "features": {}, "access": [], "services": [
                {"label": "HTTPS service: editor <team>", "url": "https://example.test:8444/", "description": "live"},
                {"label": "HTTPS service: review", "url": "https://example.test:8445/", "description": "not responding"},
            ],
        })
        for value, meters in (
            ("85% used", 1), ("Unavailable", 0), ("101% used", 0),
            ('<script>% used', 0), ("²% used", 0), ("9" * 5000 + "% used", 0),
        ):
            with (
                self.subTest(value=value),
                patch.object(panel, "discover_basaltwater_web_services", return_value=[]),
                patch.object(panel, "discover_certificate_trust", return_value=None),
                patch.object(state, "system_overview", return_value=[{"label": "Memory", "value": value, "description": "Example data"}]),
                patch.object(state, "audit_snapshot", return_value={"status": "ok", "events": []}),
            ):
                page = panel.render_page(state)
                self.assertEqual(page.count('<meter '), meters)
                self.assertIn('class="card-status ready">Responding', page)
                self.assertIn('class="card-status unavailable">Not responding', page)
                self.assertIn('editor &lt;team&gt;', page)
                self.assertNotIn('<script>', page)
                if meters:
                    self.assertIn('value="85" aria-label="Memory used"', page)
                    self.assertIn('class="metric-value warning"', page)

    def test_partial_systemctl_failure_preserves_installed_units(self) -> None:
        output = (
            "Id=nginx.service\nLoadState=loaded\nActiveState=active\nSubState=running\n\n"
            "Id=homebox.service\nLoadState=loaded\nActiveState=failed\nSubState=failed\n\n"
            "Id=gogs.service\nLoadState=not-found\nActiveState=inactive\n\n"
            "Id=unrelated.service\nLoadState=loaded\nActiveState=active\n"
        )
        with patch.object(panel.subprocess, "run", side_effect=[
            SimpleNamespace(returncode=1, stdout=output),
            OSError("No user bus"),
        ]) as run:
            records = panel.collect_service_health()
        self.assertEqual([r["value"] for r in records], ["active", "failed", "Unavailable"])
        self.assertEqual([r["label"] for r in records], ["Web gateway", "HomeBox", "User services"])
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args.kwargs["timeout"], 2)
        self.assertIn("--user", run.call_args.args[0])

    def test_timeouts_and_empty_output_are_not_healthy(self) -> None:
        with patch.object(panel.subprocess, "run", side_effect=[
            subprocess.TimeoutExpired("systemctl", 2),
            SimpleNamespace(returncode=1, stdout=""),
        ]):
            records = panel.collect_service_health()
        self.assertEqual([r["value"] for r in records], ["Unavailable", "Unavailable"])

    def test_empty_service_result_is_cached_and_expires(self) -> None:
        state = panel.WebPanelState({"features": {}})
        with (
            patch.object(panel, "collect_service_health", return_value=[]) as collect,
            patch.object(panel.time, "monotonic", side_effect=[0, 1, 31]),
        ):
            for _ in range(3):
                self.assertEqual(state.service_health(), [])
        self.assertEqual(collect.call_count, 2)

    def test_navigation_and_history_preserve_escaped_content(self) -> None:
        state = panel.WebPanelState({
            "title": "Example <server>", "host": "example.test", "username": "agent",
            "system_type": "server_dev", "features": {}, "services": [], "access": [],
        })
        events = [{"meaning": f"Event <{i}>", "severity": "warning"} for i in range(8)]
        with (
            patch.object(panel, "discover_basaltwater_web_services", return_value=[]),
            patch.object(panel, "discover_certificate_trust", return_value=None),
            patch.object(state, "system_overview", return_value=[]),
            patch.object(state, "audit_snapshot", return_value={"events": events, "status": "ok"}),
        ):
            page = panel.render_page(state)
        self.assertIn('aria-label="Panel sections"', page)
        self.assertIn('href="/#services-heading"', page)
        for anchor in ("notifications-heading", "maintenance-heading", "trust"):
            self.assertIn(f'href="/#{anchor}"', page)
            self.assertIn(f'id="{anchor}"', page)
        self.assertIn("Remote notifications are not enabled", page)
        self.assertIn("No managed gateway certificate information", page)
        self.assertIn("Show 3 more events", page)
        self.assertIn("Event &lt;7&gt;", page)
        self.assertNotIn("Event <7>", page)
        self.assertIn('href="/services"', page)
        self.assertNotIn("homebox.service · failed", page)
        self.assertLess(page.index('id="overview-heading"'), page.index('id="services-heading"'))

    def test_dashboard_does_not_probe_local_services(self) -> None:
        state = panel.WebPanelState({
            "title": "Example", "host": "example.test", "username": "agent",
            "system_type": "server_dev", "features": {}, "services": [], "access": [],
        })
        with (
            patch.object(panel, "discover_basaltwater_web_services", return_value=[]),
            patch.object(panel, "discover_certificate_trust", return_value=None),
            patch.object(state, "service_health") as service_health,
        ):
            panel.render_page(state)
        service_health.assert_not_called()

    def test_service_status_requires_explicit_load(self) -> None:
        state = panel.WebPanelState({"host": "example.test", "features": {}})
        health = [{
            "label": "HomeBox", "value": "failed",
            "description": "homebox.service · failed", "unit": "homebox.service",
        }]
        with patch.object(state, "service_health", return_value=health) as collect:
            unloaded = panel.render_service_status(state, False)
        collect.assert_not_called()
        self.assertIn("no automatic refresh", unloaded)
        with patch.object(state, "service_health", return_value=health) as collect:
            loaded = panel.render_service_status(state, True)
        collect.assert_called_once()
        self.assertIn("1 inactive or unavailable", loaded)
        self.assertIn('/logs?service=homebox.service', loaded)

    def test_service_status_route_rejects_bad_query_without_probe(self) -> None:
        handler = object.__new__(panel.WebPanelHandler)
        handler.path = "/services?load=1&load=1"
        handler._send = Mock()
        with patch.object(panel, "render_service_status") as render:
            handler.do_GET()
        render.assert_not_called()
        self.assertEqual(handler._send.call_args.args[0].value, 400)


if __name__ == "__main__":
    unittest.main()
