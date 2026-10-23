"""Scheduled job state, bounded collection, and navigation regression tests."""

from __future__ import annotations

import unittest
from http import HTTPStatus
from types import SimpleNamespace
from unittest.mock import Mock, patch

from common.service_tools import web_panel_jobs as jobs
from common.service_tools import web_panel_service as panel
from common.service_tools.web_panel_templates import panel_navigation
from common.service_tools.web_panel_features import PanelFeatures
from lib.systemd_timers import format_timer_next_trigger


def _pair(name: str = "auto-update-apt", *, timer: str = "active", started: str = "", result: str = "success", active: str = "inactive") -> str:
    return (
        f"Id={name}.timer\nLoadState=loaded\nActiveState={timer}\nUnitFileState=enabled\n"
        f"Unit={name}.service\nNextElapseUSecRealtime=Tue 2026-09-08 06:00:00 CDT\nPersistent=yes\n\n"
        f"Id={name}.service\nLoadState=loaded\nActiveState={active}\n"
        f"Result={result}\nExecMainStartTimestamp={started}\nExecMainStatus=0\n"
    )


class JobTest(unittest.TestCase):
    def collect(self, output: str, issue: str = "") -> jobs.JobSnapshot:
        with (
            patch.object(jobs, "JOB_SERVICES", {"auto-update-apt.service": "Package updates"}),
            patch.object(jobs, "_bounded_command", return_value=(output, issue)) as command,
        ):
            snapshot = jobs.collect_jobs()
        args = command.call_args.args[0]
        self.assertIn("auto-update-apt.timer", args)
        self.assertIn("auto-update-apt.service", args)
        self.assertNotIn("start", args)
        command.assert_called_once()
        return snapshot

    def test_default_success_without_start_is_not_a_completed_run(self) -> None:
        row = self.collect(_pair()).jobs[0]
        self.assertEqual(row["status"], "No run recorded")
        self.assertEqual(row["result"], "No result recorded")
        self.assertEqual(row["tone"], "info")

    def test_trigger_without_retained_service_details_needs_attention(self) -> None:
        output = _pair().replace(
            "Persistent=yes", "Persistent=yes\nLastTriggerUSec=Mon 2026-09-07 10:00:00 CDT"
        )
        row = self.collect(output).jobs[0]
        self.assertEqual(row["status"], "Timer triggered; result unavailable")
        self.assertEqual(row["tone"], "warning")

    def test_idle_oneshot_success_is_normal(self) -> None:
        row = self.collect(_pair(started="Mon 2026-09-07 10:00:00 CDT")).jobs[0]
        self.assertEqual(row["status"], "Last run succeeded")
        self.assertEqual(row["tone"], "info")

    def test_failure_running_and_disabled_timer_are_distinct(self) -> None:
        row = self.collect(_pair(result="exit-code", active="failed")).jobs[0]
        self.assertEqual((row["status"], row["tone"]), ("Last run failed", "error"))
        row = self.collect(_pair(active="activating")).jobs[0]
        self.assertEqual(row["status"], "Running")
        row = self.collect(_pair(timer="inactive", started="yesterday")).jobs[0]
        self.assertEqual(row["status"], "Last run succeeded")
        self.assertEqual(row["tone"], "warning")
        self.assertEqual(row["next"], "Timer is not active")

    def test_missing_timer_is_separate_from_unreadable_output(self) -> None:
        snapshot = self.collect("Id=auto-update-apt.timer\nLoadState=not-found\n")
        self.assertEqual((snapshot.absent, snapshot.jobs, snapshot.issues), (1, [], []))
        snapshot = self.collect("", "Query timed out")
        self.assertEqual(snapshot.absent, 0)
        self.assertIn("Query timed out", snapshot.issues)
        self.assertIn("timer information unavailable", snapshot.issues[1])

    def test_redirected_timer_does_not_claim_known_job_succeeded(self) -> None:
        output = _pair(started="today").replace("Unit=auto-update-apt.service", "Unit=other.service")
        row = self.collect(output).jobs[0]
        self.assertEqual(row["status"], "Service information unavailable")

    def test_interval_and_calendar_deadlines(self) -> None:
        timer = {"ActiveState": "active", "NextElapseUSecMonotonic": "1h 2min 500ms"}
        with patch("lib.systemd_timers.time.monotonic", return_value=3600):
            self.assertEqual(format_timer_next_trigger(timer), "In about 2 minutes")
            timer["NextElapseUSecRealtime"] = "tomorrow"
            self.assertIn("whichever is earlier", format_timer_next_trigger(timer))
            timer["NextElapseUSecMonotonic"] = "infinity"
            self.assertEqual(format_timer_next_trigger(timer), "tomorrow")

    def test_unavailable_inactive_and_due_deadlines(self) -> None:
        for deadline in ("", "n/a", "infinity", "0", "0s", "bad", "9" * 400 + "s"):
            with self.subTest(deadline=deadline):
                timer = {"ActiveState": "active", "NextElapseUSecMonotonic": deadline}
                self.assertEqual(format_timer_next_trigger(timer), "Next trigger not reported")
                timer["NextElapseUSecRealtime"] = "tomorrow"
                self.assertEqual(format_timer_next_trigger(timer), "tomorrow")
        with patch("lib.systemd_timers.time.monotonic", return_value=3600):
            timer = {"ActiveState": "active", "NextElapseUSecMonotonic": "1h"}
            self.assertEqual(format_timer_next_trigger(timer), "Due; waiting for timer dispatch")
            timer["ActiveState"] = "inactive"
            self.assertEqual(format_timer_next_trigger(timer), "Timer is not active")

    def test_native_month_and_year_spans_after_long_uptime(self) -> None:
        for uptime, deadline in (
            (32 * 86400, "1month 1d 13h 35min"),
            (31557600, "1y 5min"),
            (31557600 + 2629800, "1y 1month 5min"),
        ):
            with self.subTest(deadline=deadline), patch("lib.systemd_timers.time.monotonic", return_value=uptime):
                output = _pair().replace(
                    "NextElapseUSecRealtime=Tue 2026-09-08 06:00:00 CDT",
                    "NextElapseUSecRealtime=\nNextElapseUSecMonotonic=" + deadline,
                )
                self.assertEqual(self.collect(output).jobs[0]["next"], "In about 5 minutes")
                timer = {"ActiveState": "active", "NextElapseUSecMonotonic": deadline,
                         "NextElapseUSecRealtime": "tomorrow"}
                self.assertEqual(format_timer_next_trigger(timer), "tomorrow or in about 5 minutes, whichever is earlier")

    def test_collected_mixed_deadlines_include_boot_trigger(self) -> None:
        output = _pair().replace("Persistent=yes", "Persistent=yes\nNextElapseUSecMonotonic=15min")
        with patch("lib.systemd_timers.time.monotonic", return_value=600):
            row = self.collect(output).jobs[0]
        self.assertEqual(
            row["next"],
            "Tue 2026-09-08 06:00:00 CDT or in about 5 minutes, whichever is earlier",
        )

    def test_opening_page_does_not_collect_and_bad_queries_are_rejected(self) -> None:
        with patch.object(jobs, "collect_jobs") as collect:
            page = jobs.render_jobs(jobs.parse_job_query(""), "", "host<test>")
        collect.assert_not_called()
        self.assertIn("host&lt;test&gt;", page)
        for raw in ("load=0", "load=1&load=1", "unit=ssh.service", "x" * 65):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                jobs.parse_job_query(raw)

    def test_job_logs_link_keeps_collection_explicit(self) -> None:
        snapshot = self.collect(_pair(started="today"))
        with patch.object(jobs, "collect_jobs", return_value=snapshot):
            page = jobs.render_jobs(True, "", "host")
        self.assertIn('/logs?service=auto-update-apt.service&amp;window=24h&amp;priority=7', page)
        self.assertNotIn("priority=7&amp;load=1", page)

    def test_route_uses_no_dashboard_discovery(self) -> None:
        handler = object.__new__(panel.WebPanelHandler)
        handler.path = "/jobs"
        handler.state = SimpleNamespace(manifest={"host": "host"}, navigation=lambda current: panel_navigation(current=current),
                                        features=lambda: PanelFeatures())
        handler._send = Mock()
        with patch.object(panel, "render_page") as dashboard, patch.object(jobs, "collect_jobs") as collect:
            handler.do_GET()
        dashboard.assert_not_called()
        collect.assert_not_called()
        self.assertEqual(handler._send.call_args.args[0], HTTPStatus.OK)

    def test_busy_request_does_not_start_another_command(self) -> None:
        with patch.object(jobs, "_COLLECTOR") as lock, patch.object(jobs, "_bounded_command") as command:
            lock.acquire.return_value = False
            snapshot = jobs.collect_jobs()
        command.assert_not_called()
        self.assertIn("inspection is running", snapshot.issues[0])


if __name__ == "__main__":
    unittest.main()
