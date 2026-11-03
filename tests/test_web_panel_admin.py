"""Admin pages never perform host actions on navigation or bypass approvals."""

from __future__ import annotations

import http.client
import io
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlencode

from common.service_tools.web_panel_admin import PanelAdmin, _review_url, parse_admin_query, render_admin
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

    def test_query_is_a_single_finite_view(self):
        self.assertEqual(parse_admin_query("view=requests"), {"view": "requests"})
        with self.assertRaises(ValueError):
            parse_admin_query("view=requests&action=reboot")
        for query in ("action=refresh", "action=refresh&action=reboot", "action=refresh&argv=sh", "action=", "action=--help", "load=1", "view=unknown"):
            with self.subTest(query=query), self.assertRaises(ValueError):
                parse_admin_query(query)

    def test_navigation_only_reads_status_and_links_to_isolated_review(self):
        with patch("common.service_tools.web_panel_admin.exchange", return_value=status()) as exchange:
            document = render_admin(self.state, _PAGE_STYLE, {})
            exchange.assert_called_once_with({"action": "admin-status"})
        self.assertIn('href="/admin" aria-current="page"', document)
        self.assertIn('href="https://vm.example:9444/approvals/admin/reboot"', document)
        self.assertNotIn('action="/actions/admin"', document)
        self.assertEqual([href for href, _, _ in panel_navigation(current="admin")], [href for href, _, _ in panel_navigation(current="agents")])

    def test_no_broker_still_renders_tools_without_privileged_actions(self):
        self.state.admin = PanelAdmin({"host": "vm.example", "services": []})
        with patch("common.service_tools.web_panel_admin.exchange") as exchange:
            document = render_admin(self.state, _PAGE_STYLE, {})
            exchange.assert_not_called()
        self.assertIn("included with the web panel", document)
        with patch("common.service_tools.web_panel_admin.exchange"):
            self.assertIn('href="/logs"', render_admin(self.state, _PAGE_STYLE, {"view": "activity"}))
        self.assertNotIn('href="/admin?action=', document)

    def test_integrated_queue_uses_panel_login_and_local_request_links(self):
        self.state.admin = PanelAdmin({"host": "vm.example", "features": {"approvals": True}})
        request = {"id": "a" * 32, "operation": "command.run", "state": "pending",
                   "created": 0, "expires": 3600, "review_url": "https://vm.example/requests/" + "a" * 32}
        with patch("common.service_tools.web_panel_admin.exchange", return_value={
                **status(), "privilege_requests": [request]}):
            document = render_admin(self.state, _PAGE_STYLE, {"view": "requests"})
        self.assertIn('href="/approvals"', document)
        self.assertIn('href="/requests/' + "a" * 32 + '"', document)
        self.assertIn("Uses your web panel sign-in", document)
        self.assertNotIn("separate approval password", document)

    def test_review_links_support_panel_http_ipv6_and_strict_local_paths(self):
        for url in ("http://vm.example/requests/" + "a" * 32,
                    "https://[2001:db8::1]/requests/" + "a" * 32, "/approvals", "/requests/" + "a" * 32):
            with self.subTest(url=url):
                self.assertEqual(_review_url(url), url)
        for url in ("//evil.example/requests/" + "a" * 32, "/requests/invalid", "javascript:alert(1)",
                    "https://user:password@vm.example/requests/" + "a" * 32):
            with self.subTest(url=url):
                self.assertIsNone(_review_url(url))

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
            document = render_admin(self.state, _PAGE_STYLE, {"view": "activity"})
        for text in ("Command index", "Command exited unsuccessfully", "Setup phase", "RuntimeError"):
            self.assertIn(text, document)
        self.assertNotIn("root-secret", document)
        latest.update(stage="root-secret", failure="root-secret", command_index=True)
        latest["setup_context"]["job_id"] = "b" * 32
        with patch("common.service_tools.web_panel_admin.exchange", return_value={**status(), "latest": latest}):
            document = render_admin(self.state, _PAGE_STYLE, {"view": "activity"})
        self.assertIn("Unavailable", document)
        self.assertNotIn("root-secret", document)
        self.assertNotIn("Setup phase", document)

    def test_busy_or_unavailable_host_disables_action_links(self):
        for state in ("active", "activating", "deactivating", "unavailable"):
            with patch("common.service_tools.web_panel_admin.exchange", return_value={**status(), "unit_state": state}) as exchange:
                document = render_admin(self.state, _PAGE_STYLE, {})
                self.assertNotIn('href="https://vm.example:9444/approvals/admin/refresh"', document)
                exchange.assert_called_once_with({"action": "admin-status"})

    def test_restart_review_remains_available_when_only_warning_variant_is_supported(self):
        with patch("common.service_tools.web_panel_admin.exchange", return_value={**status(), "blocked": {"reboot": "No scheduler"}}):
            document = render_admin(self.state, _PAGE_STYLE, {})
        self.assertIn('href="https://vm.example:9444/approvals/admin/reboot"', document)

    def test_unconfirmed_job_state_blocks_action_review_controls(self):
        with patch("common.service_tools.web_panel_admin.exchange", return_value={
                **status(), "unit_state": "unavailable", "latest": {"status": "unavailable"}}):
            document = render_admin(self.state, _PAGE_STYLE, {})
        self.assertIn("maintenance service state is unavailable", document)
        self.assertNotIn("Create approval request", document)
        self.assertNotIn('action="/actions/admin"', document)

    def test_output_is_escaped_and_arbitrary_review_urls_are_not_links(self):
        value = {**status(), "requests": [{"id": "a" * 32, "action": "refresh", "state": "<script>", "created": 0, "review_url": "javascript:alert(1)"}]}
        with patch("common.service_tools.web_panel_admin.exchange", return_value=value):
            document = render_admin(self.state, _PAGE_STYLE, {"view": "requests"})
        self.assertNotIn("javascript:", document)
        self.assertNotIn("<script>", document)
        self.assertIn("&lt;script&gt;", document)

    def test_pending_requests_can_be_cancelled_without_exposing_approval(self):
        value = {**status(), "requests": [{"id": "a" * 32, "action": "refresh", "state": "pending", "created": 0, "review_url": "https://vm.example:9444/requests/" + "a" * 32}]}
        with patch("common.service_tools.web_panel_admin.exchange", return_value=value):
            document = render_admin(self.state, _PAGE_STYLE, {"view": "requests"})
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
            document = render_admin(self.state, _PAGE_STYLE, {"view": "requests"})
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

    def test_actions_are_whole_row_links_to_isolated_review(self):
        self.state.admin = PanelAdmin({"host": "vm.example", "features": {"approvals": True}})
        document = self.render()
        self.assertRegex(document, r'<a class="action-row[^\"]*admin-action-link[^\"]*" href="/approvals/admin/reboot">.*?<h3>Restart host</h3>')
        self.assertNotIn('href="/approvals/admin/reboot-warning"', document)
        self.assertNotIn('href="#admin-', document)
        self.assertNotIn('aria-labelledby="privilege-requests-heading"', document)
        self.assertNotIn('Latest job:', document)
        self.assertEqual(self.manager.action_url("reboot"), "https://vm.example:9444/approvals/admin/reboot")

    def test_refresh_preserves_view_without_showing_the_action_catalog(self):
        for view in ("requests", "activity"):
            document = self.render({"view": view})
            self.assertIn(f'href="/admin?view={view}">Refresh status', document)
            self.assertNotIn('id="admin-updates"', document)

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

    def test_http_cancel_rejects_missing_csrf_extra_fields_and_duplicate_ids(self):
        for values, expected in (({"id": "a" * 32}, 403),
                                 ({"csrf": "csrf", "id": "a" * 32, "approve": "1"}, 400),
                                 ({"csrf": "csrf", "id": ["a" * 32, "b" * 32]}, 400)):
            handler = self.handler("/actions/admin/cancel", values)
            with patch.object(self.manager, "cancel") as cancel:
                handler.do_POST()
                self.assertEqual(handler._send.call_args.args[0], expected)
                cancel.assert_not_called()

    def test_http_valid_cancellation_returns_to_requests(self):
        handler = self.handler("/actions/admin/cancel", {"csrf": "csrf", "id": "a" * 32})
        with patch("common.service_tools.web_panel_admin.exchange") as exchange:
            handler.do_POST()
            handler.send_response.assert_called_once_with(303)
            handler.send_header.assert_any_call("Location", "/admin?view=requests")
            exchange.assert_called_once_with({"action": "cancel", "id": "a" * 32})

    def test_main_panel_cannot_submit_through_removed_two_stage_endpoint(self):
        handler = self.handler("/actions/admin", {"csrf": "csrf", "action": "reboot"})
        with patch("common.service_tools.web_panel_admin.exchange") as exchange:
            handler.do_POST()
        self.assertEqual(handler._send.call_args.args[0], 404)
        exchange.assert_not_called()
