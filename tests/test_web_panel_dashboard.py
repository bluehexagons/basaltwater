"""Service collection and navigation regressions for the dashboard."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from common.service_tools import web_panel_service as panel
from common.service_tools import web_panel_diagnostics as diagnostics
from common.service_tools import web_panel_jobs as jobs
from common.service_tools.web_panel_features import PanelFeatures


class DashboardTest(unittest.TestCase):
    def setUp(self) -> None:
        self.enterContext(patch.object(panel, "collect_filesystems", return_value=[]))
        self.enterContext(patch.object(panel, "collect_pressure", return_value=[]))
        self.enterContext(patch.object(panel.WebPanelState, "features", return_value=PanelFeatures()))
        # Agent activity has its own temporary-state integration tests. Other
        # dashboard checks must not read the developer account's private tasks.
        snapshot = patch.object(panel.AgentTasks, "snapshot", return_value={"tasks": [], "runs": []})
        snapshot.start()
        self.addCleanup(snapshot.stop)

    def test_non_codex_panel_startup_leaves_task_storage_uninspected_and_absent(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            state = panel.WebPanelState({"features": {}}, agent_home=home)
            for features in (PanelFeatures(), PanelFeatures(tools=frozenset({"git"})),
                             PanelFeatures(tools=frozenset({"claude", "opencode"}), t3=True)):
                with self.subTest(features=features), patch.object(state, "features", return_value=features), patch.object(
                    panel, "worker_status",
                ) as worker, patch.object(state.agent_tasks, "start") as start:
                    state.start_background_workers()
                worker.assert_not_called()
                start.assert_not_called()
                self.assertEqual(list(Path(home).iterdir()), [])

    def test_codex_panel_falls_back_only_without_an_independent_worker(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            state = panel.WebPanelState({"features": {}}, agent_home=home)
            for installed, running in ((False, False), (True, False), (False, True), (True, True)):
                with self.subTest(installed=installed, running=running), patch.object(
                    state, "features", return_value=PanelFeatures(tools=frozenset({"codex"})),
                ), patch.object(panel, "worker_status", return_value={"installed": installed, "running": running}) as worker, patch.object(
                    state.agent_tasks, "start",
                ) as start:
                    state.start_background_workers()
                worker.assert_called_once_with(state.agent_tasks)
                start.assert_called_once_with(scheduler=not (installed or running))

    def test_publishing_poll_does_not_require_codex_or_inspect_its_lease(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            state = panel.WebPanelState({"features": {}}, agent_home=home)
            for provider in ("butler", "steamcmd"):
                with self.subTest(provider=provider), patch.object(
                    state, "features", return_value=PanelFeatures(tools=frozenset({provider})),
                ), patch.object(panel, "worker_status") as worker, patch.object(state.agent_tasks, "start") as start:
                    state.start_background_workers()
                worker.assert_not_called()
                start.assert_called_once_with(scheduler=False)

    def test_host_snapshot_reports_load_swap_inodes_and_kernel(self) -> None:
        def proc_file(path: str, **_kwargs: object) -> StringIO:
            if path == "/proc/uptime":
                return StringIO("3600 100\n")
            if path == "/proc/meminfo":
                return StringIO("MemTotal: 8000 kB\nMemAvailable: 2000 kB\nSwapTotal: 4000 kB\nSwapFree: 3000 kB\n")
            raise AssertionError(f"Unexpected file read: {path}")

        with (
            patch("builtins.open", side_effect=proc_file),
            patch.object(panel.os, "getloadavg", return_value=(6.0, 4.0, 2.0)),
            patch.object(panel.os, "cpu_count", return_value=4),
            patch.object(panel.os, "statvfs", return_value=SimpleNamespace(f_files=1000, f_ffree=100)),
            patch.object(panel.os, "uname", return_value=SimpleNamespace(release="test-kernel", machine="x86_64")),
            patch.object(panel.shutil, "disk_usage", return_value=SimpleNamespace(total=100, used=50, free=50)),
            patch.object(panel, "_timer_properties", return_value={"LoadState": "loaded", "ActiveState": "active"}),
            patch.object(panel.os.path, "exists", return_value=True),
        ):
            records = {record["label"]: record for record in panel.collect_system_overview()}
        self.assertEqual(len(records), 8)
        self.assertEqual(records["Load average (1m)"]["value"], "6.00")
        self.assertEqual(records["Load average (1m)"]["status"], "warning")
        self.assertEqual(records["Load average (1m)"]["description"], "5m 4.00 · 15m 2.00 · 4 logical CPUs")
        self.assertEqual(records["Memory"]["value"], "75% used")
        self.assertEqual(records["Swap"]["value"], "25% used")
        self.assertEqual(records["Root inodes"]["value"], "90% used")
        self.assertEqual(records["Root inodes"]["description"], "100 free of 1,000")
        self.assertEqual(records["Kernel"]["value"], "test-kernel")
        self.assertEqual(records["Kernel"]["description"], "x86_64")
        self.assertEqual(records["Maintenance"]["value"], "Reboot required")

    def test_missing_host_readings_and_zero_capacity_remain_explicit(self) -> None:
        cases = (
            ({}, "Unavailable", SimpleNamespace(f_files=0, f_ffree=0), "Not reported"),
            ({"MemTotal": 8000, "SwapTotal": 0, "SwapFree": 0}, "Not configured", OSError("No filesystem"), "Unavailable"),
        )
        for memory, swap, filesystem, inodes in cases:
            with (
                self.subTest(memory=memory),
                patch("builtins.open", side_effect=OSError("No proc")),
                patch.object(panel, "_read_proc_values", return_value=memory),
                patch.object(panel.os, "getloadavg", side_effect=OSError("No load")),
                patch.object(panel.os, "cpu_count", return_value=None),
                patch.object(panel.os, "statvfs", side_effect=filesystem if isinstance(filesystem, OSError) else None, return_value=filesystem),
                patch.object(panel.os, "uname", side_effect=OSError("No kernel")),
                patch.object(panel.shutil, "disk_usage", side_effect=OSError("No disk")),
                patch.object(panel, "_timer_properties", return_value={}),
                patch.object(panel.os.path, "exists", return_value=False),
            ):
                records = {record["label"]: record for record in panel.collect_system_overview()}
            for label in ("Uptime", "Load average (1m)", "Memory", "Root disk", "Kernel"):
                self.assertEqual(records[label]["value"], "Unavailable")
            self.assertEqual(records["Swap"]["value"], swap)
            self.assertEqual(records["Root inodes"]["value"], inodes)

    def test_missing_gateway_readiness_is_not_reported_as_responding(self) -> None:
        with (
            patch.object(panel.shutil, "which", return_value="/test/basaltwater-web"),
            patch.object(panel, "_internal_web_landing_service", return_value=None),
            patch.object(panel, "_run_json", side_effect=[{"forwards": [
                {"name": "unknown", "url": "https://example.test/"},
                {"name": "up", "url": "https://example.test:8444/", "ready": True},
                {"name": "down", "url": "https://example.test:8445/", "ready": False},
            ]}, {}]),
        ):
            services = panel.discover_basaltwater_web_services()
        self.assertEqual([record["description"] for record in services], ["Readiness not reported", "live", "not responding"])

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
                jobs.render_jobs(False, panel._PAGE_STYLE, "example.test", navigation=state.navigation("jobs")),
                diagnostics.render_diagnostics(diagnostics.DiagnosticQuery(), panel._PAGE_STYLE, "example.test", navigation=state.navigation("logs")),
            ]
        service_health.assert_not_called()
        collect_jobs.assert_not_called()
        collect_diagnostics.assert_not_called()
        sidebars = [page.split('<nav class="sidebar"', 1)[1].split('</nav>', 1)[0] for page in pages]
        for sidebar in sidebars:
            self.assertEqual(sidebar.count('aria-current="page"'), 1)
            self.assertEqual(sidebar.replace(' aria-current="page"', ''), sidebars[0].replace(' aria-current="page"', ''))
            self.assertNotIn("One machine", sidebar)
            self.assertNotIn("whole workspace", sidebar)

    def test_service_summary_uses_existing_results_and_counts_each_service_once(self) -> None:
        state = panel.WebPanelState({
            "host": "example.test", "username": "agent", "system_type": "server_dev",
            "features": {}, "access": [], "services": [
                {"label": "Editor", "url": "https://example.test:8444/", "description": "live"},
                {"label": "Review", "url": "https://example.test:8445/", "description": "not responding"},
                {"label": "Library", "url": "https://example.test:8446/", "description": "Readiness not reported"},
                {"label": "HomeBox", "url": "https://example.test:8447/", "description": "live",
                 "probe": {"kind": "homebox", "port": 8447}},
            ],
        })
        with (
            patch.object(panel, "discover_basaltwater_web_services", return_value=[]),
            patch.object(panel, "discover_certificate_trust", return_value=None),
            patch.object(panel, "_probe_homebox", return_value=("attention", "Registration is open")) as probe,
            patch.object(state, "system_overview", return_value=[]),
            patch.object(state, "audit_snapshot", return_value={"status": "ok", "events": []}),
        ):
            page = panel.render_page(state)
        probe.assert_called_once_with(8447)
        self.assertIn("4 services · 1 responding · 2 need attention · 1 not checked", page)
        self.assertIn("Registration is open", page)

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
            records = panel.collect_service_health(user_services=True)
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
            records = panel.collect_service_health(user_services=True)
        self.assertEqual([r["value"] for r in records], ["Unavailable", "Unavailable"])

    def test_machine_without_t3_does_not_query_a_user_service_manager(self) -> None:
        with patch.object(panel.subprocess, "run", return_value=SimpleNamespace(returncode=1, stdout="")) as run:
            records = panel.collect_service_health(user_services=False)
        self.assertEqual([row["label"] for row in records], ["System services"])
        run.assert_called_once()
        self.assertNotIn("--user", run.call_args.args[0])

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
        self.assertNotIn('href="/#', page)
        self.assertIn('href="#maintenance-heading"', page)
        self.assertIn('id="maintenance-heading"', page)
        for anchor in ("notifications-heading", "trust", "agent-activity-heading", "services-heading", "access-heading"):
            self.assertNotIn(f'id="{anchor}"', page)
            self.assertNotIn(f'href="#{anchor}"', page)
        self.assertIn("Show 3 more events", page)
        self.assertIn("8 warning/error events", page)
        self.assertIn("Event &lt;7&gt;", page)
        self.assertNotIn("Event <7>", page)
        self.assertIn('href="/services"', page)
        self.assertNotIn("homebox.service · failed", page)
        self.assertLess(page.index('id="overview-heading"'), page.index('id="audit-heading"'))

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
