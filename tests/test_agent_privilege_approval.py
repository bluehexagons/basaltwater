"""Exercise browser authentication and review binding without privileged effects."""

from __future__ import annotations

import base64
import copy
import http.client
import json
import re
import tempfile
import threading
import unittest
from unittest.mock import Mock
from urllib.parse import urlencode

from common.service_tools.privilege_approval import ApprovalServer
from common.service_tools.privilege_broker import Broker
from lib.privilege_auth import authenticate, password_record
from tests.test_agent_privilege_broker import policy


class ApprovalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password = "test-only approval password"
        cls.auth = password_record("operator", cls.password)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.runner = Mock(return_value="succeeded")
        self.broker = Broker(self.directory.name + "/state.db", lambda: policy(), self.runner)
        self.addCleanup(self.broker.db.close)
        self.request = self.broker.request(1000, "service.restart", {"unit": "demo.service"}, '<script>alert("x")</script>')
        self.origin = "https://vm.example:9444"
        self.server = ApprovalServer(("127.0.0.1", 0), copy.deepcopy(self.auth), self.origin,
                                     lambda message: self.broker.dispatch(message, 999, approval=True))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
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
