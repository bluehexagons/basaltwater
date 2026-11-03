"""Exercise browser authentication and review binding without privileged effects."""

from __future__ import annotations

import base64
import copy
import http.client
import os
import re
import socket
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlencode

from common.service_tools.privilege_approval import ApprovalServer, PanelApprovalServer, render_queue
from common.service_tools.privilege_broker import Broker
from lib.privilege_auth import authenticate, password_record
from lib.privilege_execution import ExecutionResult
from tests.test_agent_privilege_broker import policy


class ApprovalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password = "test-only approval password"
        cls.auth = password_record("operator", cls.password)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.runner = Mock(return_value=ExecutionResult("succeeded", exit_code=0))
        self.broker = Broker(self.directory.name + "/state.db", lambda: policy(), self.runner)
        self.addCleanup(self.broker.db.close)
        status = patch("common.service_tools.privilege_broker.job_status", return_value={"blocked": {}, "unit_state": "inactive"})
        self.job_status = status.start()
        self.addCleanup(status.stop)
        self.request = self.broker.request(1000, "service.restart", {"unit": "demo.service"}, '<script>alert("x")</script>')
        self.origin = "https://vm.example:9444"
        self.server = ApprovalServer(("127.0.0.1", 0), copy.deepcopy(self.auth), self.origin,
                                     lambda message: self.broker.dispatch(message, 999, approval=True))
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        self.path = "/requests/" + self.request["id"]

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def call(self, method="GET", path=None, body=None, headers=None, *, login=True):
        combined = {"Host": "vm.example:9444"}
        if login:
            combined["Authorization"] = "Basic " + base64.b64encode(("operator:" + self.password).encode()).decode()
        combined.update(headers or {})
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        self.addCleanup(connection.close)
        connection.request(method, path or self.path, body, combined)
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read().decode()

    def form(self, decision="approve"):
        status, headers, content = self.call()
        self.assertEqual(status, 200)
        self.assertEqual(headers["Cache-Control"], "no-store")
        return {"csrf": re.search(r'name="csrf" value="([^"]+)"', content)[1],
                "digest": self.request["digest"], "decision": decision}

    def post(self, form, origin=None):
        return self.call("POST", body=urlencode(form), headers={"Origin": origin or self.origin,
                         "Content-Type": "application/x-www-form-urlencoded"})

    def test_authentication_required_and_plain_text_explanation(self):
        self.assertEqual(self.call(login=False)[0], 401)
        status, headers, content = self.call()
        self.assertEqual(status, 200)
        self.assertIn("&lt;script&gt;", content)
        self.assertNotIn("<script>", content)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertEqual(headers["Referrer-Policy"], "same-origin")
        self.assertEqual(self.broker.status(self.request["id"])["state"], "pending")

    def test_click_approve_executes_once(self):
        form = self.form()
        self.assertEqual(self.post(form)[0], 303)
        self.assertEqual(self.post(form)[0], 409)
        self.broker.execute_next()
        self.runner.assert_called_once()
        self.assertIn("succeeded", self.call()[2])

    def test_failed_execution_shows_safe_exit_metadata(self):
        self.runner.return_value = ExecutionResult("failed", exit_code=7, failure="nonzero_exit")
        self.assertEqual(self.post(self.form())[0], 303)
        self.broker.execute_next()
        content = self.call()[2]
        self.assertIn("Review window", content)
        self.assertIn("Exit code", content)
        self.assertIn("nonzero_exit", content)
        self.assertNotIn("Approve once", content)

    def test_cross_origin_and_forged_csrf_rejected(self):
        form = self.form()
        self.assertEqual(self.post(form, "https://vm.example:443")[0], 409)
        self.assertEqual(self.post(form, "null")[0], 409)
        form["csrf"] = "é"
        self.assertEqual(self.post(form)[0], 409)
        form["csrf"] = "0" * 64
        self.assertEqual(self.post(form)[0], 409)
        self.runner.assert_not_called()

    def test_review_token_cannot_approve_another_request(self):
        form = self.form()
        other = self.broker.request(1000, "system.reboot", {}, "different action")
        self.path = "/requests/" + other["id"]
        form["digest"] = other["digest"]
        self.assertEqual(self.post(form)[0], 409)
        self.assertEqual(self.broker.status(other["id"])["state"], "pending")

    def test_unconfirmed_decision_links_to_status_without_replaying(self):
        form = self.form()
        self.assertEqual(self.post(form)[0], 303)
        status, _, content = self.post(form)
        self.assertEqual(status, 409)
        self.assertIn(f'<a href="{self.path}">Check request status</a>', content)
        self.assertIn('<a href="/">All requests</a>', content)
        self.assertIn("approved", self.call()[2])
        self.runner.assert_not_called()

    def test_invalid_request_path_has_only_safe_recovery_link(self):
        status, _, content = self.call("POST", path='//evil.example/<script>', body="", headers={
            "Origin": self.origin, "Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(status, 409)
        self.assertNotIn("Check request status", content)
        self.assertEqual(re.findall(r'href="([^"]+)"', content), ["/favicon.svg", "/"])
        self.runner.assert_not_called()

    def test_denial_and_queue(self):
        self.assertIn(self.request["id"], self.call(path="/")[2])
        self.assertEqual(self.post(self.form("deny"))[0], 303)
        self.assertFalse(self.broker.execute_next())
        self.assertIn("denied", self.call()[2])

    def test_login_throttling_and_invalid_host(self):
        self.assertEqual(self.call(headers={"Host": "evil.example:9444"})[0], 400)
        for _ in range(20):
            self.assertEqual(self.call(headers={"Authorization": "Basic invalid"})[0], 401)
        self.assertEqual(self.call()[0], 429)

    def test_changed_digest_and_duplicate_form_fields_rejected(self):
        form = self.form()
        form["digest"] = "0" * 64
        self.assertEqual(self.post(form)[0], 409)
        self.assertEqual(self.call("POST", body=urlencode(self.form()) + "&decision=deny",
                                  headers={"Origin": self.origin, "Content-Type": "application/x-www-form-urlencoded"})[0], 409)

    def test_password_rotation_takes_effect_independently(self):
        self.server.auth = password_record("operator", "a different test-only password")
        self.assertEqual(self.call()[0], 401)
        self.assertFalse(authenticate("Basic " + base64.b64encode("é:wrong".encode()).decode(), self.auth))

    def direct_form(self, action="reboot"):
        self.path = "/approvals/admin/" + action
        status, _, content = self.call()
        self.assertEqual(status, 200)
        return {"ticket": re.search(r'name="ticket" value="([^"]+)"', content)[1],
                "variant": action, "decision": "approve"}

    def test_action_review_has_no_side_effect_and_approves_in_one_step(self):
        before = self.broker.db.execute("SELECT count(*) FROM requests").fetchone()[0]
        form = self.direct_form()
        content = self.call()[2]
        self.assertIn('value="reboot" checked', content)
        self.assertIn("In 5 seconds", content)
        self.assertIn("restart in 2 minutes", content)
        self.assertEqual(self.broker.db.execute("SELECT count(*) FROM requests").fetchone()[0], before)
        status, headers, _ = self.post(form)
        self.assertEqual(status, 303)
        request = self.broker.status(headers["Location"].split("/")[-1])
        self.assertEqual(request["state"], "approved")
        self.assertEqual(request["plan"]["parameters"], {"action": "reboot"})
        self.assertEqual(self.post(form)[0], 409)
        self.broker.execute_next()
        self.runner.assert_called_once()

    def test_restart_warning_is_an_explicit_reviewed_variant(self):
        form = self.direct_form()
        form["variant"] = "reboot-warning"
        status, headers, _ = self.post(form)
        self.assertEqual(status, 303)
        request = self.broker.status(headers["Location"].split("/")[-1])
        self.assertEqual(request["plan"]["parameters"], {"action": "reboot-warning"})
        self.assertIn("2 minutes", request["plan"]["effect"])

    def test_direct_review_rejects_origin_forgery_and_action_substitution(self):
        form = self.direct_form()
        self.assertEqual(self.post(form, "https://evil.example")[0], 409)
        form["variant"] = "shutdown"
        self.assertEqual(self.post(form)[0], 409)
        self.assertEqual(self.broker.db.execute("SELECT count(*) FROM requests WHERE state='approved'").fetchone()[0], 0)
        self.runner.assert_not_called()

    def test_direct_review_expiry_and_changed_plan_prevent_acceptance(self):
        form = self.direct_form()
        self.server.admin_tickets[form["ticket"]]["expires"] = 0
        self.assertEqual(self.post(form)[0], 409)
        form = self.direct_form()
        self.broker.policy_loader = lambda: {**policy(), "ttl_seconds": 90}
        self.assertEqual(self.post(form)[0], 409)
        self.assertEqual(self.broker.db.execute("SELECT count(*) FROM requests WHERE state='approved'").fetchone()[0], 0)

    def test_direct_review_does_not_retry_an_accepted_lost_response(self):
        form = self.direct_form()
        dispatch = self.server.exchange

        def lose_response(message):
            dispatch(message)
            raise OSError("Response lost after acceptance")

        self.server.exchange = Mock(side_effect=lose_response)
        self.assertEqual(self.post(form)[0], 409)
        self.assertEqual(self.post(form)[0], 409)
        self.server.exchange.assert_called_once()
        self.assertEqual(self.broker.db.execute("SELECT count(*) FROM requests WHERE state='approved'").fetchone()[0], 1)

    def test_direct_reviews_are_bounded_and_duplicate_fields_cannot_approve(self):
        first = self.direct_form()
        for _ in range(32):
            self.direct_form()
        self.assertEqual(len(self.server.admin_tickets), 32)
        self.assertEqual(self.post(first)[0], 409)
        form = self.direct_form()
        self.assertEqual(self.call("POST", body=urlencode(form) + "&variant=shutdown", headers={
            "Origin": self.origin, "Content-Type": "application/x-www-form-urlencoded"})[0], 409)
        self.assertEqual(self.broker.db.execute("SELECT count(*) FROM requests WHERE state='approved'").fetchone()[0], 0)
        self.runner.assert_not_called()

    def test_fixed_admin_interface_cannot_be_used_by_requester_or_accept_arbitrary_commands(self):
        preview = self.broker.dispatch({"action": "admin-preview", "admin_action": "reboot"}, 999, approval=True)
        message = {"action": "admin-approve", "admin_action": "reboot", "digest": preview["digest"], "actor": "operator"}
        for item in ({"action": "admin-catalog"}, {"action": "admin-preview", "admin_action": "reboot"}, message):
            with self.assertRaises(PermissionError):
                self.broker.dispatch(item, 1000)
        with self.assertRaises(PermissionError):
            self.broker.dispatch({**message, "argv": ["sh"]}, 999, approval=True)
        self.job_status.return_value["unit_state"] = "active"
        with self.assertRaises(ValueError):
            self.broker.dispatch(message, 999, approval=True)
        self.job_status.return_value["unit_state"] = "inactive"
        self.broker.policy_loader = lambda: {**policy(), "reboot": "deny"}
        for action in ("reboot", "reboot-warning", "shutdown"):
            with self.assertRaises(PermissionError):
                self.broker.admin_preview(action)

    def test_catalog_and_direct_review_explain_unavailable_actions_without_forms(self):
        self.broker.policy_loader = lambda: {**policy(), "reboot": "deny"}
        content = self.call(path="/approvals/admin")[2]
        self.assertIn("Power control is denied by administrator policy.", content)
        self.assertNotIn('href="/approvals/admin/reboot"', content)
        status, _, content = self.call(path="/approvals/admin/reboot")
        self.assertEqual(status, 200)
        self.assertIn("Power control is denied by administrator policy.", content)
        self.assertNotIn('<form', content)
        self.assertEqual(self.server.admin_tickets, {})
        self.runner.assert_not_called()

    def test_catalog_blocks_busy_and_outstanding_maintenance_without_creating_requests(self):
        self.job_status.return_value["unit_state"] = "unavailable"
        blocked = self.broker.admin_catalog()["blocked"]
        self.assertIn("refresh", blocked)
        self.assertNotIn("cancel-shutdown", blocked)
        self.job_status.return_value["unit_state"] = "inactive"
        self.broker.request(1000, "admin.run", {"action": "refresh"}, "example")
        status, _, content = self.call(path="/approvals/admin/check-web")
        self.assertEqual(status, 200)
        self.assertIn("outstanding administration request", content)
        self.assertNotIn('<form', content)
        self.assertEqual(self.broker.db.execute("SELECT count(*) FROM requests").fetchone()[0], 2)

    def test_unavailable_restart_variant_cannot_be_forged_into_the_review(self):
        self.job_status.return_value["blocked"] = {"reboot": "Five-second scheduler unavailable"}
        content = self.call(path="/approvals/admin")[2]
        self.assertIn('href="/approvals/admin/reboot"', content)
        form = self.direct_form()
        content = self.call()[2]
        self.assertIn('value="reboot" disabled', content)
        self.assertIn('value="reboot-warning" checked', content)
        self.assertEqual(self.post(form)[0], 409)
        form = self.direct_form()
        form["variant"] = "reboot-warning"
        self.assertEqual(self.post(form)[0], 303)

    def test_ambiguous_form_headers_are_rejected_without_acceptance(self):
        for header, value in (("Content-Type", "application/x-www-form-urlencoded"), ("Transfer-Encoding", "")):
            form = self.direct_form()
            handler = object.__new__(self.server.RequestHandlerClass)
            handler.server, handler.path = self.server, self.path
            handler.headers = http.client.HTTPMessage()
            handler.headers["Origin"] = self.origin
            handler.headers["Content-Type"] = "application/x-www-form-urlencoded"
            handler.headers["Content-Length"] = str(len(urlencode(form)))
            handler.headers[header] = value
            handler.authorized = lambda: True
            handler.approval_origin = self.origin
            handler.respond = Mock()
            handler.do_POST()
            self.assertEqual(handler.respond.call_args.args[0], 409)
        self.assertEqual(self.broker.db.execute("SELECT count(*) FROM requests WHERE state='approved'").fetchone()[0], 0)

    def test_decision_precedes_technical_details_but_command_argv_stays_visible(self):
        content = self.call()[2]
        self.assertLess(content.index('value="approve"'), content.index('<details class="approval-details">'))
        request = self.broker.request(1000, "command.run", {"argv": ["/usr/bin/id", "-u"]}, "example")
        content = self.call(path="/requests/" + request["id"])[2]
        self.assertLess(content.index("/usr/bin/id"), content.index('value="approve"'))

    def test_queue_names_actions_and_folds_older_history(self):
        request = self.broker.request(1000, "admin.run", {"action": "reboot-warning"}, "example")
        records = self.broker.dispatch({"action": "list"}, 999, approval=True)["requests"]
        content = render_queue(records)
        self.assertIn("Restart host with warning", content)
        self.assertIn("Restart service: demo.service", content)
        self.assertIn(request["id"], content)
        recent = [{**records[0], "state": "succeeded"} for _ in range(6)]
        content = render_queue(recent)
        self.assertIn("No active approval requests", content)
        self.assertIn("Show 3 older requests", content)
        self.assertNotIn("<details open", content)


class IntegratedApprovalTests(ApprovalTests):
    """Exercise the same approval invariants through the authenticated Unix proxy."""

    def setUp(self):
        super().setUp()
        self.close_server()
        self.origin = "https://vm.example"
        with patch("common.service_tools.privilege_approval.os.chown"):
            self.server = PanelApprovalServer(self.directory.name + "/review.sock",
                ["https://vm.example:443", "https://alias.example:443"], "operator", os.getuid(), os.getgid(),
                lambda message: self.broker.dispatch(message, 999, approval=True))
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()

    def call(self, method="GET", path=None, body=None, headers=None, *, login=True):
        combined = {"Host": "vm.example"}
        if login:
            combined["X-Basaltwater-User"] = "operator"
        combined.update(headers or {})
        connection = http.client.HTTPConnection("localhost", timeout=5)
        self.addCleanup(connection.close)
        connection.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.sock.settimeout(5)
        connection.sock.connect(self.server.server_address)
        connection.request(method, path or self.path, body, combined)
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read().decode()

    def test_authentication_required_and_plain_text_explanation(self):
        self.assertEqual(self.call(login=False)[0], 403)
        content = self.call()[2]
        self.assertIn("&lt;script&gt;", content)
        self.assertIn('href="/admin"', content)
        self.assertIn('href="/approvals"', content)
        self.server.proxy_uid = os.getuid() + 1
        self.assertEqual(self.call()[0], 403)

    def test_login_throttling_and_invalid_host(self):
        self.assertEqual(self.call(headers={"Host": "evil.example"})[0], 400)
        self.assertEqual(self.call(headers={"X-Basaltwater-User": "another-user"})[0], 403)
        form = self.form()
        self.assertEqual(self.call("POST", body=urlencode(form), headers={
            "Host": "alias.example", "Origin": "https://alias.example",
            "Content-Type": "application/x-www-form-urlencoded"})[0], 303)

    def test_unconfirmed_decision_links_to_status_without_replaying(self):
        form = self.form()
        self.assertEqual(self.post(form)[0], 303)
        status, _, content = self.post(form)
        self.assertEqual(status, 409)
        self.assertIn(f'<a href="{self.path}">Check request status</a>', content)
        self.assertIn('<a href="/approvals">All requests</a>', content)

    def test_invalid_request_path_has_only_safe_recovery_link(self):
        status, _, content = self.call("POST", path='//evil.example/<script>', body="", headers={
            "Origin": self.origin, "Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(status, 409)
        self.assertNotIn("Check request status", content)
        self.assertNotIn("evil.example", content)
        self.runner.assert_not_called()

    def test_denial_and_queue(self):
        self.assertIn(self.request["id"], self.call(path="/approvals")[2])
        self.assertEqual(self.post(self.form("deny"))[0], 303)
        self.assertFalse(self.broker.execute_next())
        self.assertIn("denied", self.call()[2])

    def test_password_rotation_takes_effect_independently(self):
        # The standalone password never authorizes the integrated listener.
        self.assertEqual(self.call(login=False, headers={"Authorization": "Basic invalid"})[0], 403)

    def test_default_http_port_matches_the_browser_origin(self):
        self.server.origins = {"http://vm.example"}
        self.origin = "http://vm.example"
        self.assertEqual(self.post(self.form())[0], 303)
