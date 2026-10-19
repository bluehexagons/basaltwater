"""Authenticated panel maintenance controls and persistent agent mail."""

from __future__ import annotations

import http.client
import os
import tempfile
import threading
import unittest
import urllib.parse
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from common.service_tools import web_panel_maintenance as view
from common.service_tools import web_panel_service as panel
from common.service_tools.web_panel_features import PanelFeatures
from common.service_tools.web_panel_templates import panel_navigation
from lib import maintenance_agent as agent
from lib import agent_maintenance as holds
from lib.agent_inbox import AgentInbox
from lib.atomic_io import write_json_atomic
from lib.maintenance_agent_config import DEFAULTS


class WebPanelMaintenanceTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = os.path.realpath(temporary.name)
        self.control = Path(self.home) / "control"
        self.control.mkdir(mode=0o700)
        self.policy = str(self.control / "settings.json")
        write_json_atomic(self.policy, DEFAULTS)
        self.reports = str(Path(self.home) / "reports.json")
        write_json_atomic(self.reports, {"version": 1, "username": "agent", "context": {"system_type": "server_web"}, "reports": []})
        for item in (
            patch.object(view, "load_policy", side_effect=lambda: agent.load_policy(self.policy)),
            patch.object(view, "load_reports", side_effect=lambda: agent.load_reports(self.reports)),
            patch.object(view, "configure_policy", side_effect=lambda values: agent.configure_policy(values, path=self.policy)),
            patch.object(view, "request_check", side_effect=lambda: agent.request_check(directory=str(self.control))),
            patch.object(view, "inspect_agent_maintenance", side_effect=lambda: holds.inspect_agent_maintenance(self.home)),
            patch.object(view, "hold_agent_maintenance", side_effect=lambda hours: holds.hold_agent_maintenance(hours, home=self.home)),
            patch.object(view, "release_agent_maintenance", side_effect=lambda: holds.release_agent_maintenance(home=self.home)),
        ):
            item.start()
            self.addCleanup(item.stop)
        self.state = SimpleNamespace(csrf_token="csrf-fixture", agent_tasks=SimpleNamespace(home=self.home),
                                     manifest={"host": "host.example"})
        self.state.features = lambda: PanelFeatures(tools=frozenset({"codex"}), maintenance=True)
        self.state.navigation = lambda current: panel_navigation(current=current, available=self.state.features().pages)
        handler = type("FixtureHandler", (panel.WebPanelHandler,), {"state": self.state})
        self.server = panel._ThreadingTCPHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)

    def close_server(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, path: str, values: dict[str, str] | list[tuple[str, str]] | None = None) -> tuple[int, str, str | None]:
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            connection.request("GET" if values is None else "POST", path,
                               body=None if values is None else urllib.parse.urlencode(values),
                               headers={} if values is None else {"Content-Type": "application/x-www-form-urlencoded"})
            response = connection.getresponse()
            return response.status, response.read().decode(), response.getheader("Location")
        finally:
            connection.close()

    def test_panel_enables_existing_supervisor_and_requests_check_without_system_mutation(self) -> None:
        values = {key: str(value) for key, value in DEFAULTS.items() if type(value) is not bool}
        values.update(csrf=self.state.csrf_token, submit="save", enabled="1", security="1")
        with patch.object(agent.subprocess, "run") as command:
            status, _, location = self.request("/actions/maintenance-agent", values)
            self.assertEqual(status, 303)
            self.assertEqual(location, "/maintenance-agent")
            self.assertTrue(agent.load_policy(self.policy)["enabled"])
            self.assertFalse(agent.load_policy(self.policy)["routines"])
            status, _, _ = self.request("/actions/maintenance-agent", {"csrf": self.state.csrf_token, "submit": "run"})
            self.assertEqual(status, 303)
        command.assert_not_called()
        self.assertTrue((self.control / "wake.json").is_file())

    def test_controls_reject_csrf_duplicate_fields_and_injectable_policy(self) -> None:
        self.assertEqual(self.request("/actions/maintenance-agent", {"csrf": "wrong", "submit": "run"})[0], 403)
        self.assertEqual(self.request("/actions/maintenance-agent", [("csrf", self.state.csrf_token), ("submit", "run"), ("submit", "save")])[0], 400)
        values = {key: str(value) for key, value in DEFAULTS.items() if type(value) is not bool}
        values.update(csrf=self.state.csrf_token, submit="save", enabled="1", model="bad\nvalue")
        self.assertEqual(self.request("/actions/maintenance-agent", values)[0], 422)
        self.assertEqual(agent.load_policy(self.policy), DEFAULTS)

    def test_manual_check_and_master_toggle_preserve_independent_preferences(self) -> None:
        body = self.request("/maintenance-agent")[1]
        self.assertIn('name="host_checks" value="1" checked', body)
        values = {key: str(value) for key, value in DEFAULTS.items() if type(value) is not bool}
        values.update(csrf=self.state.csrf_token, submit="save", security="1", routines="1", interval="120")
        for enabled in (True, False, True):
            with self.subTest(enabled=enabled):
                selected = values | ({"enabled": "1"} if enabled else {})
                self.assertEqual(self.request("/actions/maintenance-agent", selected)[0], 303)
                saved = agent.load_policy(self.policy)
                self.assertEqual((saved["enabled"], saved["host_checks"], saved["security"], saved["routines"]),
                                 (enabled, False, True, True))
                self.assertEqual(saved["interval"], 120)
                if not enabled:
                    body = self.request("/maintenance-agent")[1]
                    self.assertIn('value="run">Check once now</button>', body)
                    self.assertNotIn('value="run" disabled', body)
                    self.assertEqual(self.request("/actions/maintenance-agent", {
                        "csrf": self.state.csrf_token, "submit": "run"})[0], 303)
                    self.assertTrue((self.control / "wake.json").is_file())
                    self.assertEqual(agent.load_policy(self.policy), saved)

    def test_cached_and_custom_models_round_trip_when_cache_changes(self) -> None:
        values = {key: str(value) for key, value in DEFAULTS.items() if type(value) is not bool}
        values.update(csrf=self.state.csrf_token, submit="save", model="cached-model",
                      escalation_model="cached-large", custom_model="custom-model",
                      custom_escalation_model="custom-large")
        with patch.object(view, "codex_models", return_value=[
            {"slug": "cached-model", "name": "Cached <model>"},
            {"slug": "cached-large", "name": "Cached large"},
        ]) as models:
            body = self.request("/maintenance-agent")[1]
            self.assertIn('<option value="cached-model">Cached &lt;model&gt;</option>', body)
            models.assert_called_with(self.home)
            self.assertEqual(self.request("/actions/maintenance-agent", values)[0], 303)
        saved = agent.load_policy(self.policy)
        self.assertEqual((saved["model"], saved["escalation_model"]), ("custom-model", "custom-large"))
        with patch.object(view, "codex_models", return_value=[]):
            body = self.request("/maintenance-agent")[1]
            for key in ("model", "escalation_model"):
                self.assertIn(f'<option value="{saved[key]}" selected>', body)
                values[key] = saved[key]
                values["custom_" + key] = ""
            self.assertEqual(self.request("/actions/maintenance-agent", values)[0], 303)
        self.assertEqual(agent.load_policy(self.policy), saved)

    def test_restart_hold_round_trip_is_account_local_and_does_not_change_policy(self) -> None:
        self.assertIn("No active automatic restart hold", self.request("/maintenance-agent")[1])
        with patch("subprocess.run") as command, patch("subprocess.Popen") as spawn:
            response = self.request("/actions/maintenance-agent", {"csrf": self.state.csrf_token, "submit": "hold", "hours": "2"})
            self.assertEqual(response[0], 303)
            self.assertTrue(holds.inspect_agent_maintenance(self.home)["active"])
            self.assertIn("Automatic restarts held until", self.request("/maintenance-agent")[1])
            self.assertEqual(agent.load_policy(self.policy), DEFAULTS)
            self.assertEqual(self.request("/actions/maintenance-agent", {"csrf": self.state.csrf_token, "submit": "release"})[0], 303)
            self.assertFalse(holds.inspect_agent_maintenance(self.home)["active"])
        command.assert_not_called()
        spawn.assert_not_called()

    def test_restart_hold_rejects_invalid_hours_csrf_and_extra_fields(self) -> None:
        for hours in ("0", "73", "9999999999", "1.5", "-1", "١", ""):
            with self.subTest(hours=hours):
                self.assertEqual(self.request("/actions/maintenance-agent", {"csrf": self.state.csrf_token, "submit": "hold", "hours": hours})[0], 422)
        self.assertEqual(self.request("/actions/maintenance-agent", {"csrf": "wrong", "submit": "hold", "hours": "2"})[0], 403)
        self.assertEqual(self.request("/actions/maintenance-agent", {"csrf": self.state.csrf_token, "submit": "release", "hours": "2"})[0], 422)
        self.assertFalse(holds.inspect_agent_maintenance(self.home)["active"])

    def test_invalid_settings_preserve_unsaved_values_without_changing_saved_status(self) -> None:
        values = {key: str(value) for key, value in DEFAULTS.items() if type(value) is not bool}
        values.update(csrf=self.state.csrf_token, submit="save", enabled="1", interval="120",
                      model='bad"<script>', daily_calls="4")
        status, body, _ = self.request("/actions/maintenance-agent", values)
        self.assertEqual(status, 422)
        self.assertIn('class="maintenance-settings" open', body)
        self.assertIn('name="interval" min="15" max="10080" value="120"', body)
        self.assertIn('name="daily_calls" min="0" max="24" value="4"', body)
        self.assertIn('name="enabled" value="1" checked', body)
        self.assertNotIn('name="security" value="1" checked', body)
        self.assertIn('value="bad&quot;&lt;script&gt;"', body)
        self.assertIn('>Manual checks only</strong>', body)
        self.assertIn('value="run">Check once now</button>', body)
        self.assertNotIn('value="run" disabled', body)
        self.assertEqual(agent.load_policy(self.policy), DEFAULTS)
        status, body, _ = self.request("/actions/maintenance-agent", {"csrf": self.state.csrf_token, "submit": "hold", "hours": "73"})
        self.assertEqual(status, 422)
        self.assertIn('value="73" required', body)

    def test_inbox_routes_deliver_and_acknowledge_messages_across_instances(self) -> None:
        values = {"csrf": self.state.csrf_token, "submit": "send", "recipient": "parent", "sender": "operator", "message": "Worker complete"}
        status, _, location = self.request("/actions/agent-inbox", values)
        self.assertEqual(status, 303)
        self.assertEqual(location, "/agent-inbox?mailbox=parent")
        inbox = AgentInbox(self.home)
        self.assertEqual(inbox.list("parent")[0]["message"], "Worker complete")
        status, body, _ = self.request("/agent-inbox?mailbox=parent")
        self.assertEqual(status, 200)
        self.assertIn("Worker complete", body)
        status, _, _ = self.request("/actions/agent-inbox", {"csrf": self.state.csrf_token, "submit": "ack", "recipient": "parent", "id": inbox.list("parent")[0]["id"]})
        self.assertEqual(status, 303)
        self.assertEqual(AgentInbox(self.home).list("parent"), [])
        status, body, _ = self.request("/agent-inbox?mailbox=parent")
        self.assertEqual(status, 200)
        self.assertIn("Worker complete", body.split('aria-labelledby="archive-heading"')[1])
        self.assertIn('value="restore">Restore</button>', body)
        self.assertNotIn('value="ack">Archive</button>', body)
        identifier = inbox.list("parent", include_acknowledged=True)[0]["id"]
        status, _, location = self.request("/actions/agent-inbox", {"csrf": self.state.csrf_token, "submit": "restore", "recipient": "parent", "id": identifier})
        self.assertEqual(status, 303)
        self.assertEqual(location, "/agent-inbox?mailbox=parent")
        self.assertEqual(inbox.list("parent")[0]["id"], identifier)

    def test_feedback_archive_is_visible_redacted_and_escaped_on_default_page(self) -> None:
        inbox = AgentInbox(self.home)
        record = inbox.feedback("t3-agent", '<script>issue & evidence</script>\nAuthorization: Bearer fixture-secret')
        inbox.acknowledge("operator", record["id"])
        unread = inbox.send("worker", "operator", "Pending evidence")
        status, body, _ = self.request("/agent-inbox")
        self.assertEqual(status, 200)
        self.assertIn("1 unread · 1 archived/read", body)
        unread_section, archive_section = body.split('aria-labelledby="archive-heading"')
        self.assertIn("Pending evidence", unread_section)
        self.assertIn('value="ack">Archive</button>', unread_section)
        self.assertIn("Environment feedback", archive_section)
        self.assertIn("&lt;script&gt;issue &amp; evidence&lt;/script&gt;", archive_section)
        self.assertNotIn("<script>", body)
        self.assertNotIn("fixture-secret", body)
        self.assertIn("[redacted]", archive_section)
        self.assertIn('name="csrf" value="csrf-fixture"', archive_section)
        self.assertIn('value="restore">Restore</button>', archive_section)
        self.assertIn("basaltw agent inbox feedback --sender", body)
        self.assertIn("self-reported", body)
        self.assertEqual(inbox.list("operator"), [unread])
        self.assertIn("fixture-secret", inbox.list("operator", include_acknowledged=True)[0]["message"])

    def test_archive_restore_reject_csrf_duplicate_and_extra_fields_without_mutation(self) -> None:
        inbox = AgentInbox(self.home)
        record = inbox.feedback("worker", "issue")
        for action in ("ack", "restore"):
            if action == "restore":
                inbox.acknowledge("operator", record["id"])
            before = inbox.path.read_bytes()
            values = {"csrf": self.state.csrf_token, "submit": action, "recipient": "operator", "id": record["id"]}
            for changes, status in (({"csrf": "wrong"}, 403), ({"extra": "1"}, 422),
                                    ({"recipient": "other"}, 422), ({"id": "bad"}, 422)):
                with self.subTest(action=action, changes=changes):
                    self.assertEqual(self.request("/actions/agent-inbox", values | changes)[0], status)
                    self.assertEqual(inbox.path.read_bytes(), before)
            self.assertEqual(self.request("/actions/agent-inbox", [*values.items(), ("id", record["id"])])[0], 400)
            self.assertEqual(inbox.path.read_bytes(), before)

    def test_get_views_escape_messages_and_validate_routes(self) -> None:
        AgentInbox(self.home).send("worker", "operator", "<script>do_bad_things()</script>")
        status, body, _ = self.request("/agent-inbox")
        self.assertEqual(status, 200)
        self.assertIn("&lt;script&gt;", body)
        self.assertNotIn("<script>", body)
        self.assertEqual(self.request("/agent-inbox?mailbox=../private")[0], 400)
        self.assertEqual(self.request("/agent-inbox?mailbox=operator&mailbox=parent")[0], 400)
        self.assertEqual(self.request("/maintenance-agent?run=1")[0], 400)
        status, body, _ = self.request("/maintenance-agent")
        self.assertEqual(status, 200)
        self.assertIn("Save maintenance settings", body)
        self.assertIn("Healthy checks use no AI", body)
        self.assertIn('href="/agent-inbox"', body)

    def test_agent_pages_use_hub_tabs_while_sidebar_keeps_one_agents_destination(self) -> None:
        for path in ("/agent-tools", "/agent-inbox", "/maintenance-agent"):
            with self.subTest(path=path):
                status, body, _ = self.request(path)
                self.assertEqual(status, 200)
                self.assertIn('<nav class="agent-tabs" aria-label="Agents">', body)
                self.assertIn(f'href="{path}" aria-current="page"', body)
                sidebar = body.split('<nav class="sidebar"', 1)[1].split('</nav>', 1)[0]
                self.assertIn('href="/agents"', sidebar)
                for child in ("/agent-tools", "/agent-inbox", "/maintenance-agent"):
                    self.assertNotIn(f'href="{child}"', sidebar)

    def test_failed_message_keeps_mailbox_and_draft_without_delivering(self) -> None:
        inbox = AgentInbox(self.home)
        inbox.send("worker", "parent", "Parent mailbox evidence")
        for recipient in ("parent", "../invalid"):
            status, body, _ = self.request("/actions/agent-inbox", {
                "csrf": self.state.csrf_token, "submit": "send", "recipient": recipient,
                "sender": "bad sender", "message": '<script>Keep this draft & fix the sender</script>',
            })
            self.assertEqual(status, 422)
            self.assertIn('class="maintenance-settings" open', body)
            self.assertIn(f'name="recipient" maxlength="64" value="{recipient}"', body)
            self.assertIn('value="bad sender"', body)
            self.assertIn('&lt;script&gt;Keep this draft &amp; fix the sender&lt;/script&gt;', body)
            self.assertNotIn('<script>', body)
            if recipient == "parent":
                self.assertIn('Showing <strong>parent</strong> · 1 unread', body)
                self.assertIn("Parent mailbox evidence", body)
        self.assertEqual(len(inbox.list("parent")), 1)
        self.assertEqual(inbox.list("operator"), [])

    def test_message_timestamp_cannot_make_existing_mail_unreadable(self) -> None:
        inbox = AgentInbox(self.home)
        record = inbox.send("worker", "operator", "Still readable")
        record["created_at"] = 10 ** 100
        write_json_atomic(str(inbox.path), {"version": 1, "messages": [record]})
        status, body, _ = self.request("/agent-inbox")
        self.assertEqual(status, 200)
        self.assertIn("Still readable", body)
        self.assertIn("Time unavailable", body)

    def test_records_show_interruption_and_corrupt_inbox_is_actionable(self) -> None:
        report = {"checked_at": "earlier", "check_status": "interrupted", "issues": [], "routines": [], "review": {"status": "failed"}}
        write_json_atomic(self.reports, {"version": 1, "context": {}, "reports": [report]})
        self.assertIn("Check interrupted", self.request("/maintenance-agent")[1])
        body = self.request("/maintenance-agent")[1]
        self.assertIn('Incomplete check · 0 findings', body)
        self.assertNotIn('No findings in the checks performed.', body)
        inbox = AgentInbox(self.home)
        inbox.send("worker", "operator", "hello")
        inbox.path.write_text('{"version":2,"messages":[]}')
        status, body, _ = self.request("/agent-inbox")
        self.assertEqual(status, 200)
        self.assertIn("preserve and inspect", body)
        self.assertEqual(inbox.path.read_text(), '{"version":2,"messages":[]}')
        status, body, _ = self.request("/actions/agent-inbox", {"csrf": self.state.csrf_token,
            "submit": "send", "recipient": "operator", "sender": "operator", "message": "Keep this draft during recovery"})
        self.assertEqual(status, 422)
        self.assertIn("Keep this draft during recovery", body)
        self.assertIn('class="maintenance-settings" open', body)
        self.assertEqual(inbox.path.read_text(), '{"version":2,"messages":[]}')


if __name__ == "__main__":
    unittest.main()
