"""Admin pages never perform host actions on navigation or bypass approvals."""

from __future__ import annotations

import http.client
import io
import re
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlencode

from common.service_tools.web_panel_admin import PanelAdmin, parse_admin_query, render_admin
from common.service_tools.web_panel_service import WebPanelHandler, _PAGE_STYLE
from common.service_tools.web_panel_templates import panel_navigation
from common.service_tools.web_panel_features import PanelFeatures


def manifest():
    return {"host": "vm.example", "services": [{"label": "Privilege approvals", "url": "https://vm.example:9444/"}]}


def status():
    return {"unit_state": "inactive", "latest": {}, "requests": [], "blocked": {}}


class AdminScreenTests(unittest.TestCase):
    def setUp(self):
        self.manager = PanelAdmin(manifest())
        self.state = SimpleNamespace(manifest=manifest(), admin=self.manager, csrf_token="csrf",
                                     agent_tasks=Mock(), t3_update_available=Mock(return_value=False))
        self.state.agent_tasks.snapshot.return_value = {"tasks": [], "runs": []}
        self.state.features = Mock(return_value=PanelFeatures(tools=frozenset({"codex"}), admin=True))
        self.state.navigation = lambda current: panel_navigation(current=current, available=self.state.features().pages)

    def render(self, query=None):
        with patch("common.service_tools.web_panel_admin.exchange", return_value=status()):
            return render_admin(self.state, _PAGE_STYLE, query or {})

    def test_query_is_a_single_finite_review_action(self):
        self.assertEqual(parse_admin_query("action=refresh"), {"action": "refresh"})
        for query in ("action=refresh&action=reboot", "action=refresh&argv=sh", "action=", "action=--help", "load=1"):
            with self.subTest(query=query), self.assertRaises(ValueError):
                parse_admin_query(query)

    def test_navigation_and_review_only_read_status(self):
        with patch("common.service_tools.web_panel_admin.exchange", return_value=status()) as exchange:
            document = render_admin(self.state, _PAGE_STYLE, {"action": "reboot"})
            exchange.assert_called_once_with({"action": "admin-status"})
        self.assertIn('href="/admin" aria-current="page"', document)
        self.assertIn("Confirm host name", document)
        self.assertIn("Create approval request", document)
        self.assertEqual([href for href, _, _ in panel_navigation(current="admin")], [href for href, _, _ in panel_navigation(current="agents")])

    def test_no_broker_still_renders_tools_without_privileged_actions(self):
        self.state.admin = PanelAdmin({"host": "vm.example", "services": []})
        with patch("common.service_tools.web_panel_admin.exchange") as exchange:
            document = render_admin(self.state, _PAGE_STYLE, {})
            exchange.assert_not_called()
        self.assertIn("optional privilege approval service", document)
        self.assertIn('href="/logs"', document)
        self.assertNotIn('href="/admin?action=', document)

    def test_admin_does_not_render_or_query_agent_controls(self):
        self.state.t3_update_available.return_value = True
        document = self.render()
        for marker in ("Panel prompt work", "Agent maintenance tools", 'href="/agent-tools?',
                       'action="/actions/t3-update"', "T3 Code maintenance"):
            self.assertNotIn(marker, document)
        self.state.agent_tasks.snapshot.assert_not_called()
        self.state.t3_update_available.assert_not_called()

    def test_safe_job_diagnostics_and_correlated_setup_context_are_visible(self):
        latest = {"action": "refresh", "status": "failed", "stage": "command", "command_index": 1,
                  "failure": "nonzero_exit", "job_id": "a" * 32,
                  "setup_context": {"job_id": "a" * 32, "phase": "recovery", "setup_stage": "steps",
                                    "step_index": 3, "step_count": 4, "error_type": "RuntimeError", "secret": "root-secret"}}
        with patch("common.service_tools.web_panel_admin.exchange", return_value={**status(), "latest": latest}):
            document = render_admin(self.state, _PAGE_STYLE, {})
        for text in ("Command index", "Command exited unsuccessfully", "Setup phase", "RuntimeError"):
            self.assertIn(text, document)
        self.assertNotIn("root-secret", document)
        latest.update(stage="root-secret", failure="root-secret", command_index=True)
        latest["setup_context"]["job_id"] = "b" * 32
        with patch("common.service_tools.web_panel_admin.exchange", return_value={**status(), "latest": latest}):
            document = render_admin(self.state, _PAGE_STYLE, {})
        self.assertIn("Unavailable", document)
        self.assertNotIn("root-secret", document)
        self.assertNotIn("Setup phase", document)

    def test_one_time_review_ticket_prevents_repeated_submission(self):
        ticket = self.manager.ticket("refresh")
        with patch("common.service_tools.web_panel_admin.exchange", side_effect=[status(), {"state": "pending"}]) as exchange:
            self.manager.submit("refresh", ticket, "")
            self.assertEqual(exchange.call_args.args[0]["operation"], "admin.run")
            with self.assertRaisesRegex(ValueError, "already submitted"):
                self.manager.submit("refresh", ticket, "")
            self.assertEqual(exchange.call_count, 2)

    def test_power_confirmation_and_expired_ticket_cannot_submit(self):
        ticket = self.manager.ticket("shutdown")
        with patch("common.service_tools.web_panel_admin.exchange") as exchange:
            with self.assertRaisesRegex(ValueError, "host exactly"):
                self.manager.submit("shutdown", ticket, "wrong-host")
            exchange.assert_not_called()
        with patch("common.service_tools.web_panel_admin.time.monotonic", return_value=float("inf")), patch("common.service_tools.web_panel_admin.exchange") as exchange:
            with self.assertRaisesRegex(ValueError, "expired"):
                self.manager.submit("shutdown", ticket, "vm.example")
            exchange.assert_not_called()

    def test_ambiguous_response_is_not_retried_and_ticket_is_consumed(self):
        ticket = self.manager.ticket("refresh")
        with patch("common.service_tools.web_panel_admin.exchange", side_effect=[status(), OSError("Lost response")]) as exchange:
            with self.assertRaises(OSError):
                self.manager.submit("refresh", ticket, "")
            with self.assertRaises(ValueError):
                self.manager.submit("refresh", ticket, "")
            self.assertEqual(exchange.call_count, 2)

    def test_busy_or_unavailable_host_cannot_submit_another_job(self):
        for state in ("active", "activating", "deactivating", "unavailable"):
            with patch("common.service_tools.web_panel_admin.exchange", return_value={**status(), "unit_state": state}) as exchange:
                with self.assertRaises(ValueError):
                    self.manager.submit("refresh", self.manager.ticket("refresh"), "")
                exchange.assert_called_once_with({"action": "admin-status"})

    def test_unconfirmed_job_state_blocks_action_review_controls(self):
        with patch("common.service_tools.web_panel_admin.exchange", return_value={
                **status(), "unit_state": "unavailable", "latest": {"status": "unavailable"}}):
            document = render_admin(self.state, _PAGE_STYLE, {"action": "refresh"})
        self.assertIn("maintenance service state is unavailable", document)
        self.assertNotIn("Create approval request", document)
        self.assertNotIn('href="/admin?action=refresh"', document)

    def test_output_is_escaped_and_arbitrary_review_urls_are_not_links(self):
        value = {**status(), "requests": [{"id": "a" * 32, "action": "refresh", "state": "<script>", "created": 0, "review_url": "javascript:alert(1)"}]}
        with patch("common.service_tools.web_panel_admin.exchange", return_value=value):
            document = render_admin(self.state, _PAGE_STYLE, {})
        self.assertNotIn("javascript:", document)
        self.assertNotIn("<script>", document)
        self.assertIn("&lt;script&gt;", document)

    def test_pending_requests_can_be_cancelled_without_exposing_approval(self):
        value = {**status(), "requests": [{"id": "a" * 32, "action": "refresh", "state": "pending", "created": 0, "review_url": "https://vm.example:9444/requests/" + "a" * 32}]}
        with patch("common.service_tools.web_panel_admin.exchange", return_value=value):
            document = render_admin(self.state, _PAGE_STYLE, {})
        self.assertIn("Cancel request", document)
        self.assertIn("Open approval review", document)
        self.assertNotIn('href="/admin?action=', document)
        self.assertNotIn('name="approve"', document)
        value['privilege_requests'] = [{"id": "b" * 32, "operation": "service.restart", "target": "auto-update-node.service",
            "state": "pending", "created": 0, "expires": 3600, "execution": None,
            "review_url": "https://vm.example:9444/requests/" + "b" * 32},
            {"id": "c" * 32, "operation": "command.run", "state": "failed", "target": "",
             "execution": {"exit_code": 7, "failure": "nonzero_exit"}, "review_url": "javascript:alert(1)"}]
        with patch("common.service_tools.web_panel_admin.exchange", return_value=value) as broker:
            document = render_admin(self.state, _PAGE_STYLE, {})
        broker.assert_called_once_with({"action": "admin-status"})
        for text in ('Privilege approvals', 'auto-update-node.service', 'Approval expires',
                     'Review exact action', 'nonzero_exit', 'Exit code: 7', 'Privileged command'):
            self.assertIn(text, document)
        self.assertNotIn('javascript:', document)
        self.assertNotIn('name="approve"', document)
        self.assertIn('target="_blank" rel="noopener noreferrer"', document)
        with patch("common.service_tools.web_panel_admin.exchange") as broker:
            self.manager.cancel('b' * 32)
        broker.assert_called_once_with({"action": "cancel", "id": 'b' * 32})

    def handler(self, path, values):
        handler = object.__new__(WebPanelHandler)
        handler.state = self.state
        handler.path = path
        body = urlencode(values, doseq=True).encode()
        handler.headers = http.client.HTTPMessage()
        handler.headers["Content-Length"] = str(len(body))
        handler.rfile = io.BytesIO(body)
        handler.wfile = io.BytesIO()
        handler._send = Mock()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        return handler

    def test_http_rejects_missing_csrf_extra_fields_and_duplicate_actions(self):
        for values, expected in (({"action": "reboot"}, 403),
                                 ({"csrf": "csrf", "id": "a" * 32, "approve": "1"}, 400),
                                 ({"csrf": "csrf", "action": ["refresh", "shutdown"], "ticket": "t", "confirmation": ""}, 400)):
            handler = self.handler("/actions/admin", values)
            with patch.object(self.manager, "submit") as submit:
                handler.do_POST()
                self.assertEqual(handler._send.call_args.args[0], expected)
                submit.assert_not_called()

    def test_http_valid_submission_creates_request_and_redirects(self):
        document = self.render({"action": "refresh"})
        ticket = re.search(r'name="ticket" value="([^"]+)"', document).group(1)
        handler = self.handler("/actions/admin", {"csrf": "csrf", "action": "refresh", "ticket": ticket, "confirmation": ""})
        with patch("common.service_tools.web_panel_admin.exchange", side_effect=[status(), {"state": "pending"}]) as exchange:
            handler.do_POST()
            handler.send_response.assert_called_once_with(303)
            handler.send_header.assert_any_call("Location", "/admin")
            self.assertEqual(exchange.call_args.args[0]["parameters"], {"action": "refresh"})
