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
        ):
            item.start()
            self.addCleanup(item.stop)
        self.state = SimpleNamespace(csrf_token="csrf-fixture", agent_tasks=SimpleNamespace(home=self.home))
        self.state.features = lambda: PanelFeatures(tools=frozenset({"codex"}), maintenance=True)
        self.state.navigation = lambda current: panel_navigation(current=current, available=self.state.features().pages)
        handler = type("FixtureHandler", (panel.WebPanelHandler,), {"state": self.state})
        self.server = panel._ThreadingTCPHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
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

    def test_records_show_interruption_and_corrupt_inbox_is_actionable(self) -> None:
        report = {"checked_at": "earlier", "check_status": "interrupted", "issues": [], "routines": [], "review": {"status": "failed"}}
        write_json_atomic(self.reports, {"version": 1, "context": {}, "reports": [report]})
        self.assertIn("Check interrupted", self.request("/maintenance-agent")[1])
        inbox = AgentInbox(self.home)
        inbox.send("worker", "operator", "hello")
        inbox.path.write_text('{"version":2,"messages":[]}')
        status, body, _ = self.request("/agent-inbox")
        self.assertEqual(status, 200)
        self.assertIn("preserve and inspect", body)
        self.assertEqual(inbox.path.read_text(), '{"version":2,"messages":[]}')


if __name__ == "__main__":
    unittest.main()
