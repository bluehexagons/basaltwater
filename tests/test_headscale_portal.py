"""Portal routing and durable review never carry native registration secrets."""

from __future__ import annotations

import copy
import http.client
import io
import json
import os
import socket
import stat
import tempfile
import threading
import unittest
from contextlib import redirect_stderr
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import urlencode

from common.service_tools.privilege_approval import render_request
from common.service_tools.privilege_broker import Broker
from common.service_tools.web_panel_features import PanelFeatures, discover_features
from common.service_tools.web_panel_service import WebPanelHandler, WebPanelState, _ThreadingUnixHTTPServer
from common.service_tools.web_panel_templates import panel_navigation
from lib import headscale_portal as boundary
from tests.test_agent_privilege_broker import policy

INVENTORY = {"users": [{"id": "12", "name": "alice"}], "nodes": []}
SECRET = "verified_auth_id_123456789"
NATIVE_ADAPTER = boundary.native
REQUIRE_INSTALLED = boundary.require_installed
REVIEW_ENDPOINT = boundary.review_endpoint
ENDPOINT = "https://vpn.example.com"


class HeadscaleBrokerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = os.path.join(self.temp.name, "broker.db")
        self.gate = patch.object(boundary, "require_installed")
        self.gate.start()
        self.addCleanup(self.gate.stop)
        self.endpoint = self.enterContext(patch.object(boundary, "review_endpoint", return_value=ENDPOINT))
        self.inventory = patch.object(boundary, "snapshot", return_value=copy.deepcopy(INVENTORY))
        self.inventory.start()
        self.addCleanup(self.inventory.stop)
        self.native = patch.object(boundary, "native", return_value={}).start()
        self.addCleanup(patch.stopall)
        self.broker = Broker(self.path, policy)
        self.registration_count = 0
        self.addCleanup(lambda: self.broker.db.close())

    def register(self, **changes):
        secret = SECRET + (str(self.registration_count) if self.registration_count else "")
        self.registration_count += 1
        return self.broker.dispatch({"action": "headscale-register", "user": "12", "auth_id": secret, **changes}, 1000)

    def approve(self, request):
        return self.broker.decide(request["id"], request["digest"], True, "reviewer")

    def test_separate_review_then_exact_native_dispatch_once_without_secret_persistence(self):
        request = self.register()
        self.assertEqual(request["plan"]["mode"], "approve")
        self.assertFalse(self.broker.execute_next())
        self.assertNotIn(SECRET, json.dumps(request))
        self.assertNotIn(SECRET, render_request(request, "csrf"))
        self.assertEqual(request["plan"]["argv"], [])
        self.approve(request)
        self.assertTrue(self.broker.execute_next())
        self.assertFalse(self.broker.execute_next())
        self.native.assert_called_once_with("register", {"user": "12", "auth_id": SECRET},
                                            expected_endpoint=ENDPOINT)
        self.assertEqual(self.broker.registrations.entries, {})
        self.broker.db.execute("PRAGMA wal_checkpoint(FULL)")
        for filename in os.listdir(self.temp.name):
            with open(os.path.join(self.temp.name, filename), "rb") as source:
                self.assertNotIn(SECRET.encode(), source.read())

    def test_unauthorized_forged_and_wrong_user_rejected(self):
        for message in ({"action": "headscale-inspect"}, {"action": "headscale-register", "user": "12", "auth_id": SECRET}):
            with self.assertRaises(PermissionError):
                self.broker.dispatch(message, 1001)
        with self.assertRaises(ValueError):
            self.register(user="99")
        with self.assertRaises(ValueError):
            self.broker.request(1000, "headscale.admin", {"action": "register", "user": "12", "auth_id": SECRET}, SECRET)
        request = self.register()
        with self.assertRaises(ValueError):
            self.broker.request(1000, "headscale.admin", request["plan"]["parameters"], "")
        with self.assertRaises(ValueError):
            self.register(auth_id=SECRET + "1")
        with self.assertRaises(PermissionError):
            self.broker.dispatch({"action": "decide", "id": request["id"], "digest": request["digest"], "approve": True, "actor": "forged"}, 1000)

    def test_denial_expiry_cancellation_and_restart_drop_secrets(self):
        for outcome in ("deny", "expire", "cancel", "restart"):
            with self.subTest(outcome=outcome):
                request = self.register()
                if outcome == "deny":
                    self.broker.decide(request["id"], request["digest"], False, "reviewer")
                elif outcome == "expire":
                    with patch("common.service_tools.privilege_broker.time.time", return_value=request["expires"] + 1):
                        self.broker.status(request["id"])
                elif outcome == "cancel":
                    self.broker.cancel(request["id"], 1000)
                else:
                    self.broker.db.close()
                    self.broker = Broker(self.path, policy)
                self.assertFalse(self.broker.execute_next())
                self.assertEqual(self.broker.registrations.entries, {})
        self.native.assert_not_called()

    def test_lost_ack_and_exception_never_retry_or_log_secret(self):
        request = self.register()  # Simulate losing its transport acknowledgement.
        self.assertFalse(self.broker.execute_next())
        self.approve(request)
        self.native.side_effect = RuntimeError(SECRET)
        output = io.StringIO()
        with redirect_stderr(output):
            self.broker.execute_next()
        self.assertEqual(self.broker.status(request["id"])["state"], "uncertain")
        self.assertFalse(self.broker.execute_next())
        self.assertNotIn(SECRET, output.getvalue())
        self.assertNotIn(SECRET, json.dumps(self.broker.status(request["id"])))

    def test_transient_ttl_and_exact_review_binding(self):
        for tamper in ("ttl", "user", "digest"):
            request = self.register()
            self.approve(request)
            if tamper == "ttl":
                with patch.object(boundary.time, "monotonic", return_value=float("inf")):
                    self.broker.execute_next()
            else:
                entry = self.broker.registrations.entries[request["plan"]["parameters"]["token"]]
                changed = list(entry)
                changed[1 if tamper == "user" else 4] = "wrong"
                self.broker.registrations.entries[request["plan"]["parameters"]["token"]] = tuple(changed)
                self.broker.execute_next()
        self.native.assert_not_called()

    def test_delete_person_with_nodes_blocked_at_execution(self):
        request = self.broker.request(1000, "headscale.admin", {"action": "users_delete", "id": "12"}, "")
        self.approve(request)
        with patch.object(boundary, "snapshot", return_value={"nodes": [{"user_id": "12"}]}):
            self.broker.execute_next()
        self.native.assert_not_called()

    def test_endpoint_change_invalidates_approval_and_discards_registration(self):
        for action in ("node_delete", "register"):
            with self.subTest(action=action):
                self.endpoint.return_value = ENDPOINT
                request = (self.register() if action == "register" else
                           self.broker.request(1000, "headscale.admin", {"action": action, "id": "7"}, ""))
                self.assertEqual(request["plan"]["headscale_endpoint"], ENDPOINT)
                self.assertIn(ENDPOINT, render_request(request, "csrf"))
                self.approve(request)
                self.endpoint.return_value = "https://replacement.example.com"
                self.assertTrue(self.broker.execute_next())
                self.assertEqual(self.broker.status(request["id"])["state"], "invalidated")
                self.assertEqual(self.broker.registrations.entries, {})
        self.native.assert_not_called()

    def test_unavailable_endpoint_invalidates_approval_before_native_dispatch(self):
        request = self.register()
        self.approve(request)
        self.endpoint.side_effect = ValueError("Managed Headscale endpoint unavailable")
        self.assertTrue(self.broker.execute_next())
        self.assertEqual(self.broker.status(request["id"])["state"], "invalidated")
        self.assertEqual(self.broker.registrations.entries, {})
        self.native.assert_not_called()

    def test_review_endpoint_uses_verified_native_state_and_bounds_errors(self):
        adapter = Mock(endpoint=ENDPOINT)
        with patch("lib.headscale_admin.NativeAdmin", return_value=adapter):
            self.assertEqual(REVIEW_ENDPOINT(), ENDPOINT)
            adapter.verify.assert_called_once_with()
            adapter.verify.side_effect = RuntimeError(SECRET)
            with self.assertRaises(ValueError) as raised:
                REVIEW_ENDPOINT()
            self.assertNotIn(SECRET, str(raised.exception))

    def test_fixed_mutations_require_review_and_dispatch_only_valid_fields(self):
        for parameters in ({"action": "users_create", "name": "alice"},
                           {"action": "node_expire", "id": "7"}, {"action": "node_delete", "id": "7"}):
            request = self.broker.request(1000, "headscale.admin", parameters, SECRET)
            self.assertFalse(self.broker.execute_next())
            self.assertNotIn(SECRET, json.dumps(request))
            self.approve(request)
            self.broker.execute_next()
            self.native.assert_called_with(parameters["action"], {key: value for key, value in parameters.items() if key != "action"},
                                           expected_endpoint=ENDPOINT)
        with patch.object(boundary, "require_installed", side_effect=PermissionError("Unavailable")):
            with self.assertRaises(PermissionError):
                self.broker.request(1000, "headscale.admin", {"action": "node_delete", "id": "7"}, "")

    def test_native_adapter_suppresses_secret_exception_and_uses_lazy_contract(self):
        with patch.dict("sys.modules", {"lib.headscale_admin": SimpleNamespace(execute_admin_action=Mock(side_effect=RuntimeError(SECRET)))}):
            with self.assertRaises(RuntimeError) as raised:
                NATIVE_ADAPTER("register", {"user": "12", "auth_id": SECRET})
            self.assertNotIn(SECRET, str(raised.exception))
            self.assertTrue(raised.exception.__suppress_context__)
        execute = Mock(return_value={})
        with patch.dict("sys.modules", {"lib.headscale_admin": SimpleNamespace(execute_admin_action=execute)}):
            NATIVE_ADAPTER("node_delete", {"id": "7"}, expected_endpoint=ENDPOINT)
        execute.assert_called_once_with("node_delete", {"id": "7"}, expected_endpoint=ENDPOINT)

    def test_allowlist_discards_secret_urls(self):
        self.native.side_effect = [{"users": [{"id": "12", "name": "alice", "url": SECRET}]},
                                   {"nodes": []}]
        # Call the real serializer despite the fixture's inventory mock.
        self.inventory.stop()
        self.assertNotIn(SECRET, json.dumps(boundary.snapshot()))

    def test_root_gate_requires_exported_selection_and_native_socket(self):
        with patch("lib.privilege_policy.protected_path"), patch.object(boundary, "read_json_file") as manifest, \
                patch.object(boundary, "managed_installed", return_value=True), \
                patch.object(boundary.os.path, "islink", return_value=False), \
                patch.object(boundary.os, "stat", return_value=SimpleNamespace(st_mode=stat.S_IFSOCK)):
            manifest.return_value = {"features": {"managed_headscale": True}}
            REQUIRE_INSTALLED()
            for value in ({"features": {}}, {"system_type": "server_proxmox", "features": {"managed_headscale": True}}):
                manifest.return_value = value
                with self.assertRaises(PermissionError):
                    REQUIRE_INSTALLED()
            manifest.return_value = {"features": {"managed_headscale": True}}
            with patch.object(boundary.os, "stat", return_value=SimpleNamespace(st_mode=stat.S_IFREG)):
                with self.assertRaises(PermissionError):
                    REQUIRE_INSTALLED()


class HeadscaleRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.enabled = True
        self.state = SimpleNamespace(manifest={"username": "admin", "system_type": "server_lite"}, csrf_token="csrf",
                                     credential_transport_available=lambda: True,
                                     features=lambda: PanelFeatures(headscale=self.enabled))
        self.state.navigation = lambda current: panel_navigation(current=current, available=self.state.features().pages)
        handler = type("HeadscaleHandler", (WebPanelHandler,), {"state": self.state})
        self.server = _ThreadingUnixHTTPServer(os.path.join(self.temp.name, "http.sock"), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close)
        self.peer = patch("common.service_tools.web_panel_service.pwd.getpwnam", return_value=SimpleNamespace(pw_uid=os.getuid()))
        self.peer.start()
        self.addCleanup(self.peer.stop)
        self.exchange = patch("common.service_tools.web_panel_headscale.exchange", return_value=INVENTORY).start()
        self.addCleanup(patch.stopall)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, method="GET", body=None, authenticated=True):
        connection = http.client.HTTPConnection("localhost")
        connection.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.sock.connect(self.server.server_address)
        headers = {"X-Forwarded-Proto": "https", "Content-Type": "application/x-www-form-urlencoded"}
        if authenticated:
            headers["X-Basaltwater-User"] = "admin"
        connection.request(method, "/headscale" if method == "GET" else "/actions/headscale", body, headers)
        response = connection.getresponse()
        status, cache, content = response.status, response.getheader("Cache-Control"), response.read().decode()
        connection.close()
        self.assertEqual(cache, "no-store")
        return status, content

    def test_routes_auth_csrf_duplicate_fields_and_off_gate(self):
        self.assertEqual(self.request(authenticated=False)[0], 403)
        with patch("common.service_tools.web_panel_service.pwd.getpwnam", return_value=SimpleNamespace(pw_uid=os.getuid() + 1)):
            self.assertEqual(self.request()[0], 403)
        self.assertEqual(self.request()[0], 200)
        for body, status in (("action=users_create&name=alice", 403),
                             ("csrf=csrf&action=users_create&name=alice&name=bob", 400),
                             ("csrf=csrf&action=users_create&name=alice", 303),
                             ("csrf=csrf&action=command.run&argv=sh", 409)):
            self.assertEqual(self.request("POST", body)[0], status)
        self.enabled = False
        self.assertEqual(self.request()[0], 404)
        self.assertEqual(self.request("POST", "csrf=csrf")[0], 404)
        self.assertNotIn("headscale", self.state.features().pages)

    def test_native_html_is_escaped_and_secret_form_never_echoed(self):
        self.exchange.return_value = {"users": [{"id": "12", "name": "<script>"}], "nodes": []}
        status, content = self.request()
        self.assertEqual(status, 200)
        self.assertIn("&lt;script&gt;", content)
        self.assertNotIn("<script>", content)
        self.exchange.side_effect = RuntimeError(SECRET)
        status, content = self.request("POST", urlencode({"csrf": "csrf", "action": "register", "user": "12", "verified": "yes", "auth_id": SECRET}))
        self.assertEqual(status, 409)
        self.assertNotIn(SECRET, content)

    def test_external_registry_cannot_enable_page(self):
        with patch("common.service_tools.web_panel_features._tool_path", return_value=None), patch("common.service_tools.web_panel_features.unit_installed", return_value=True), patch("os.path.isfile", return_value=True):
            self.assertFalse(discover_features("/fictional", admin=True, configured={"headscale": "external"}).headscale)
            self.assertTrue(discover_features("/fictional", admin=True, configured={"managed_headscale": True}).headscale)
        restricted = SimpleNamespace(manifest={"system_type": "server_proxmox"})
        self.assertFalse(WebPanelState.features(restricted).headscale)
