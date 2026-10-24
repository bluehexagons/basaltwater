"""Local panel forms must use disposable stores and simulated host boundaries."""

from __future__ import annotations

import base64
from contextlib import ExitStack
import http.client
from http.server import ThreadingHTTPServer
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from common.service_tools import web_panel_service as panel
from lib.atomic_io import read_json_file
from lib.github_runner_registration import ENDPOINT as RUNNER_REGISTRATION_ENDPOINT
from scripts import panel_dev
from scripts.panel_scenarios import Scenario
from scripts.panel_specialized import SyntheticDesktop


class SyntheticDesktopTests(unittest.IsolatedAsyncioTestCase):
    async def test_browser_cleanup_preserves_permission_and_refresh_does_not_extend_timer(self) -> None:
        desktop = SyntheticDesktop("full")
        with patch("scripts.panel_specialized.time") as clock:
            clock.time.return_value = 1000
            with self.assertRaises(PermissionError):
                await desktop.call("browser-take")
            taken = await desktop.call("browser-take", human_approved=True)
            self.assertEqual(taken["override_remaining"], 900)
            clock.time.return_value = 1100
            refreshed = await desktop.call("browser-refresh")
            self.assertEqual(refreshed["override_until"], taken["override_until"])
            self.assertEqual(refreshed["control_revision"], taken["control_revision"])
            released = await desktop.call("browser-release")
            self.assertIsNone(released["browser_pause"])
            self.assertEqual(released["agent_control"], "human-override")
            clock.time.return_value = 1900
            expired = await desktop.call("status")
            self.assertEqual(expired["agent_control"], "enabled")
            self.assertNotEqual(expired["control_revision"], taken["control_revision"])
            await desktop.call("control", mode="disabled", human_approved=True)
            await desktop.call("browser-logout")
            restarted = await desktop.call("start")
            self.assertEqual(restarted["agent_control"], "disabled")
            indefinite = await desktop.call("browser-take", human_approved=True, override_seconds=None)
            self.assertIsNone(indefinite["override_until"])
            self.assertIsNone(indefinite["override_remaining"])


class PanelDevelopmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.stack = self.enterContext(ExitStack())
        self.stack.enter_context(patch.object(subprocess, "run", side_effect=AssertionError("Unexpected host command")))
        self.stack.enter_context(patch.object(subprocess, "Popen", side_effect=AssertionError("Unexpected host process")))
        self.stack.enter_context(patch("lib.agent_tasks.AgentTasks.available", return_value=True))

    def serve(self, scenario: str = "full") -> None:
        home = self.directory / "home"
        home.mkdir(mode=0o700)
        self.state = panel.WebPanelState({"host": "development.example.test", "username": "operator",
            "title": "Development machine", "system_type": "server_dev", "features": {}, "services": [], "access": [],
            "panel_url": "http://127.0.0.1/"}, agent_home=str(home))
        self.scenario = Scenario(self.state, scenario, self.stack)
        self.settings = {"url": "", "username": "developer", "password": "disposable-test-password",
                         "scenario": scenario, "refresh": True, "https": False}
        handler = panel_dev.development_handler(self.state, self.settings, "generation-1", self.directory)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.settings["url"] = f"http://127.0.0.1:{self.server.server_port}"
        thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, path: str, fields: dict | None = None, *, authenticated: bool = True, headers: dict | None = None):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        supplied = {"Authorization": "Basic " + base64.b64encode(b"developer:disposable-test-password").decode()} if authenticated else {}
        if fields is not None:
            supplied["Content-Type"] = "application/x-www-form-urlencoded"
        supplied.update(headers or {})
        try:
            connection.request("POST" if fields is not None else "GET", path,
                               body=urlencode(fields) if fields is not None else None, headers=supplied)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read().decode()
        finally:
            connection.close()

    def test_every_full_panel_page_renders_without_host_commands(self) -> None:
        self.serve()
        for route in ("/", "/agents", "/agent-tools", "/agent-inbox", "/credentials", "/maintenance-agent",
                      "/publishing", "/admin", "/jobs?load=1", "/logs?load=1", "/services?load=1", "/github-runner?load=1"):
            with self.subTest(route=route):
                status, headers, body = self.request(route)
                self.assertEqual(status, 200)
                self.assertIn('id="panel-development"', body)
                self.assertIn("connect-src 'self'", headers["Content-Security-Policy"])
        self.assertFalse(self.state.credential_transport_available())

    def test_dev_controls_authentication_host_and_csrf_are_required(self) -> None:
        self.serve()
        for path in ("/", "/__dev__/status", "/__dev__/reload.js"):
            self.assertEqual(self.request(path, authenticated=False)[0], 401)
        self.assertEqual(self.request("/", headers={"Host": "attacker.example"})[0], 403)
        fields = {"csrf": "forged", "scenario": "minimal", "action": "scenario"}
        self.assertEqual(self.request("/__dev__/control", fields)[0], 403)
        self.assertEqual(self.request("/__dev__/control", {**fields, "csrf": "invalid-λ"})[0], 403)
        self.assertFalse((self.directory / "request.json").exists())
        fields["csrf"] = self.state.csrf_token
        self.assertEqual(self.request("/__dev__/control", {**fields, "scenario": "unknown"})[0], 400)
        self.assertEqual(self.request("/__dev__/control", fields)[0], 303)
        self.assertEqual(read_json_file(str(self.directory / "request.json")), {"scenario": "minimal"})

    def test_forged_forwarded_https_cannot_enable_credential_writes(self) -> None:
        self.serve()
        status, _, _ = self.request("/actions/credentials/github", {"csrf": self.state.csrf_token, "token": "sample-token"},
                                    headers={"X-Forwarded-Proto": "https"})
        self.assertEqual(status, 403)
        self.assertFalse(self.scenario.data["github_present"])

    def test_identity_and_inbox_forms_persist_only_inside_temporary_home(self) -> None:
        self.serve()
        csrf = self.state.csrf_token
        self.assertEqual(self.request("/actions/credentials/identity", {
            "csrf": csrf, "name": "Example Developer", "email": "dev@example.test"})[0], 303)
        self.assertEqual(self.scenario.identity()["name"], "Example Developer")
        self.assertEqual(self.request("/actions/agent-inbox", {"csrf": csrf, "submit": "send",
            "recipient": "operator", "sender": "developer", "message": "Only temporary data"})[0], 303)
        self.assertIn("Only temporary data", self.request("/agent-inbox")[2])
        self.assertFalse((self.directory / "home/.gitconfig").exists())
        self.assertEqual(self.request("/actions/credentials/identity", {
            "csrf": csrf, "name": "Invalid <name>", "email": "bad"})[0], 422)

    def test_draft_save_and_run_use_real_store_and_synthetic_executor(self) -> None:
        self.serve()
        status, _, _ = self.request("/actions/agent-task/save", {"csrf": self.state.csrf_token, "submit": "draft",
            "title": "Saved from form", "prompt": "Synthetic inspection", "directory": self.state.agent_tasks.home,
            "mode": "inspect", "interval": "once"})
        self.assertEqual(status, 303)
        manager = self.state.agent_tasks
        saved = next(task for task in manager.snapshot()["tasks"] if task["title"] == "Saved from form")
        self.assertTrue(saved["draft"])
        self.assertEqual(self.request("/actions/agent-task", {"csrf": self.state.csrf_token, "id": saved["id"], "action": "run"})[0], 303)
        self.assertTrue(manager.tick())
        run = manager.snapshot()["runs"][-1]
        self.assertEqual(run["status"], "completed")
        self.assertIn("no provider", run["message"])
        self.assertTrue(manager.path.is_relative_to(self.directory))

    def test_minimal_scenario_hides_optional_pages(self) -> None:
        self.serve("minimal")
        self.assertEqual(self.request("/agents")[0], 404)
        self.assertNotIn('href="/credentials"', self.request("/")[2])

    def test_proxmox_scenario_renders_inventory_without_host_commands(self) -> None:
        self.serve("proxmox")
        status, _, body = self.request("/")
        self.assertEqual(status, 200)
        for text in ("Proxmox overview", "build-agent", "web-services", "debian-template", "85% used", "Standalone"):
            self.assertIn(text, body)
        self.assertNotIn('href="/admin"', body)
        self.assertEqual(self.request("/agents")[0], 404)

    def test_maintenance_and_readiness_forms_show_simulated_results(self) -> None:
        self.serve()
        csrf = self.state.csrf_token
        status, headers, _ = self.request("/actions/agent-diagnostics", {"csrf": csrf})
        self.assertEqual(status, 303)
        self.assertIn("Synthetic diagnostics only", self.request(headers["Location"])[2])
        self.assertEqual(self.request("/actions/maintenance-agent", {"csrf": csrf, "submit": "run"})[0], 303)
        self.assertIn("Synthetic development check", self.request("/maintenance-agent")[2])
        self.assertEqual(self.request("/actions/maintenance-agent", {"csrf": csrf, "submit": "hold", "hours": "99"})[0], 422)
        self.assertEqual(self.request("/actions/maintenance-agent", {"csrf": csrf, "submit": "hold", "hours": "2"})[0], 303)
        self.assertIn("Automatic restarts held", self.request("/maintenance-agent")[2])

    def test_admin_form_keeps_review_ticket_and_confirmation_validation(self) -> None:
        self.serve()
        values = {"csrf": self.state.csrf_token, "action": "reboot", "confirmation": "wrong-host",
                  "ticket": self.state.admin.ticket("reboot")}
        self.assertEqual(self.request("/actions/admin", values)[0], 409)
        values["confirmation"] = "development.example.test"
        self.assertEqual(self.request("/actions/admin", values)[0], 303)
        self.assertIn("succeeded", self.request("/admin")[2])
        self.assertEqual(self.request("/actions/admin", values)[0], 409)

    def test_empty_scenario_retains_forms_without_seeded_records(self) -> None:
        self.serve("empty")
        self.assertEqual(self.state.agent_tasks.snapshot()["tasks"], [])
        self.assertIn("No unread messages", self.request("/agent-inbox")[2])
        self.assertEqual(self.request("/agents")[0], 200)

    def test_runner_registration_is_simulated_retryable_and_discards_tokens(self) -> None:
        host_registration = self.stack.enter_context(patch.object(panel, "submit_registration", side_effect=AssertionError("Unexpected host socket")))
        self.serve("empty")
        self.stack.enter_context(patch.object(panel.os, "geteuid", return_value=1000))
        # Simulate the trusted HTTPS proxy boundary without running TLS in this unit fixture.
        self.settings["https"] = True
        self.state.manifest["panel_url"] = "https://127.0.0.1/"
        status, _, body = self.request("/github-runner?load=1")
        self.assertEqual(status, 200)
        self.assertIn("Pending registration", body)
        self.assertIn("example-org", body)
        self.assertIn('type="password"', body)
        fields = {"csrf": self.state.csrf_token, "token": "reject-sample-runner-token"}
        self.assertEqual(self.request(RUNNER_REGISTRATION_ENDPOINT, fields, authenticated=False)[0], 401)
        self.assertEqual(self.request(RUNNER_REGISTRATION_ENDPOINT, {**fields, "csrf": "wrong"})[0], 403)
        self.assertEqual(self.request(RUNNER_REGISTRATION_ENDPOINT, fields)[0], 422)
        self.assertEqual(self.scenario.runner_status().policy["registration"], "pending")
        fields["token"] = "sample-runner-registration-token"
        status, headers, body = self.request(RUNNER_REGISTRATION_ENDPOINT, fields, headers={"X-Basaltwater-User": "forged"})
        self.assertEqual(status, 303)
        self.assertNotIn(fields["token"], body)
        registered = self.request(headers["Location"])[2]
        self.assertIn("Registered", registered)
        self.assertNotIn('type="password"', registered)
        self.assertEqual(self.scenario.runner_status().service["UnitFileState"], "enabled")
        self.assertEqual(self.request(RUNNER_REGISTRATION_ENDPOINT, fields)[0], 422)
        with ExitStack() as reloaded:
            self.assertEqual(Scenario(self.state, "empty", reloaded).runner_status().policy["registration"], "registered")
        for path in self.directory.rglob("*"):
            if path.is_file():
                self.assertNotIn(fields["token"].encode(), path.read_bytes())
                self.assertNotIn(b"reject-sample-runner-token", path.read_bytes())
        host_registration.assert_not_called()

    def test_http_development_rejects_runner_registration_with_forged_https(self) -> None:
        self.serve("empty")
        self.assertEqual(self.request(RUNNER_REGISTRATION_ENDPOINT, {
            "csrf": self.state.csrf_token, "token": "sample-runner-registration-token",
        }, headers={"X-Forwarded-Proto": "https", "X-Basaltwater-User": "operator"})[0], 403)
        self.assertEqual(self.scenario.runner_status().policy["registration"], "pending")

    def test_personal_repositories_register_in_sequence_without_host_operations(self) -> None:
        host = self.stack.enter_context(patch.object(panel, "submit_registration", side_effect=AssertionError("Host socket")))
        self.serve("full")
        # Seed ownership-checked inbox/task files as the real test account;
        # only the subsequent HTTPS registration flow needs a simulated UID.
        self.stack.enter_context(patch.object(panel.os, "geteuid", return_value=1000))
        self.settings["https"] = True
        self.state.manifest["panel_url"] = "https://127.0.0.1/"
        fields = {"csrf": self.state.csrf_token, "token": "sample-runner-registration-token"}
        for repository in ("example/second-project", "example/third-project"):
            page = self.request("/github-runner?load=1")[2]
            self.assertIn(repository + "/settings/actions/runners/new", page)
            self.assertEqual(page.count('name="token"'), 1)
            self.assertEqual(self.request(RUNNER_REGISTRATION_ENDPOINT, fields)[0], 303)
        self.assertNotIn('name="token"', self.request("/github-runner?load=1")[2])
        with ExitStack() as reloaded:
            status = Scenario(self.state, "full", reloaded).runner_status()
            self.assertTrue(all(item.policy["registration"] == "registered" for item in status.instances))
        self.assertNotIn(fields["token"], self.scenario.path.read_text())
        host.assert_not_called()

    def test_degraded_scenario_exposes_failures_and_simulates_failed_runs(self) -> None:
        self.serve("degraded")
        self.assertIn("94% used", self.request("/")[2])
        self.assertIn("Synthetic journal access restricted", self.request("/logs?load=1")[2])
        task = self.state.agent_tasks.snapshot()["tasks"][0]
        self.state.agent_tasks.action(task["id"], "run")
        self.state.agent_tasks.tick()
        self.assertEqual(self.state.agent_tasks.snapshot()["runs"][-1]["status"], "failed")

    def test_extra_watch_files_detect_change_and_deletion(self) -> None:
        watched = self.directory / "extra.css"
        watched.write_text("initial")
        before = panel_dev.source_snapshot([watched])
        watched.write_text("changed content")
        self.assertNotEqual(before, panel_dev.source_snapshot([watched]))
        watched.unlink()
        self.assertNotIn(str(watched), panel_dev.source_snapshot([watched]))

    def test_unsafe_state_directories_are_rejected(self) -> None:
        linked = self.directory / "linked"
        linked.symlink_to(self.directory, target_is_directory=True)
        with self.assertRaises(ValueError):
            panel_dev.private_directory(linked)
        public = self.directory / "public"
        public.mkdir(mode=0o755)
        with self.assertRaises(ValueError):
            panel_dev.private_directory(public)


if __name__ == "__main__":
    unittest.main()
