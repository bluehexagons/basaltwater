"""Passive reads preserve privacy, source age and finite concurrency budgets."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from email.message import Message
import http.client
import json
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from common.service_tools import web_panel_service as panel
from common.service_tools import web_panel_status as status
from common.service_tools.web_panel_features import PanelFeatures


class StatusTest(unittest.TestCase):
    def setUp(self) -> None:
        self.home = self.enterContext(tempfile.TemporaryDirectory())
        self.state = panel.WebPanelState({
            "host": "example.test", "username": "operator", "system_type": "server_lite",
            "features": {}, "services": [], "access": [],
        }, agent_home=self.home, audit_snapshot_path=self.home + "/audit.json")
        self.enterContext(patch.object(self.state, "features", return_value=PanelFeatures()))

    def handler(self, path: str, *, authorized: bool = True) -> panel.WebPanelHandler:
        handler = object.__new__(panel.WebPanelHandler)
        handler.path, handler.state = status.API_PREFIX + path, self.state
        handler.headers = Message()
        if authorized:
            handler.headers["X-Basaltwater-User"] = "operator"
        handler._send = Mock()
        return handler

    def test_source_clock_states_and_revision(self) -> None:
        for stamp, expected in ((1000, "fresh"), (900, "stale"), (None, "unknown"),
                                (1001, "unknown"), (True, "unknown"), (float("nan"), "unknown"),
                                ("1970-01-01T00:16:40Z", "fresh"), ("1970-01-01T00:16:40", "unknown")):
            with self.subTest(stamp=stamp):
                value = status.snapshot("host", {"value": None}, stamp, interval=30, stale_after=90, now=1000)
                self.assertEqual(value["freshness"], expected)
                again = status.snapshot("host", {"value": None}, stamp, interval=30, stale_after=90, now=1000)
                self.assertEqual(value["revision"], again["revision"])
        partial = status.snapshot("host", {}, 1000, interval=30, stale_after=90, incomplete=True, now=1000)
        self.assertEqual(partial["freshness"], "incomplete")
        self.assertTrue(partial["warnings"])
        self.assertEqual(status.snapshot("host", {}, None, interval=30, stale_after=90,
                                         unavailable=True, now=1000)["freshness"], "unavailable")

    def test_conditional_response_has_no_body_and_remains_private(self) -> None:
        records = [{"label": "Memory", "value": "Unavailable", "description": "Cannot read memory"},
                   {"kind": "filesystem", "label": "/private/data", "value": "SECRET"}]
        with patch.object(panel, "collect_system_overview", return_value=records) as collect:
            handler = self.handler("host")
            handler.do_GET()
            code, body, content_type = handler._send.call_args.args
            self.assertEqual(code, 200)
            value = json.loads(body)
            self.assertEqual(value["freshness"], "incomplete")
            self.assertNotIn("SECRET", body)
            self.assertNotIn("/private", body)
            self.assertEqual(content_type, "application/json")
            handler.headers["If-None-Match"] = handler._send.call_args.kwargs["headers"]["ETag"]
            handler._send.reset_mock()
            handler.do_GET()
            self.assertEqual(handler._send.call_args.args[:2], (304, ""))
            collect.assert_called_once()

    def test_authentication_and_feature_gates_precede_validators(self) -> None:
        for resource, authorized, expected in (("host", False, 403), ("agents", True, 404),
                                               ("proxmox", True, 404), ("unknown", True, 404),
                                               ("host?load=1", True, 400)):
            with self.subTest(resource=resource), patch.object(self.state.status, "read") as read:
                handler = self.handler(resource, authorized=authorized)
                handler.headers["If-None-Match"] = '"unchanged"'
                handler.do_GET()
                read.assert_not_called()
                self.assertEqual(handler._send.call_args.args[0], expected)
        handler = self.handler("host")
        handler.headers["X-Basaltwater-User"] = "operator"
        handler.do_GET()
        self.assertEqual(handler._send.call_args.args[0], 403)

    def test_disconnected_status_clients_release_request_budget(self) -> None:
        for failure in (BrokenPipeError, ConnectionResetError):
            for phase in ("headers", "body"):
                with self.subTest(failure=failure, phase=phase):
                    handler = self.handler("host")
                    del handler._send
                    handler.send_response = Mock()
                    handler.send_header = Mock()
                    handler.end_headers = Mock(side_effect=failure if phase == "headers" else None)
                    handler.wfile = Mock()
                    handler.wfile.write.side_effect = failure if phase == "body" else None
                    with patch.object(panel, "collect_system_overview", return_value=[]):
                        handler.do_GET()
                    self.assertTrue(handler.close_connection)
                    acquired = 0
                    try:
                        for _ in range(status.MAX_REQUESTS):
                            self.assertTrue(self.state.status.requests.acquire(blocking=False))
                            acquired += 1
                    finally:
                        for _ in range(acquired):
                            self.state.status.requests.release()

    def test_request_budget_returns_retry_immediately(self) -> None:
        for _ in range(status.MAX_REQUESTS):
            self.assertTrue(self.state.status.requests.acquire(blocking=False))
        try:
            handler = self.handler("host")
            with patch.object(self.state.status, "read") as read:
                handler.do_GET()
            read.assert_not_called()
            self.assertEqual(handler._send.call_args.args[0], 503)
            self.assertEqual(handler._send.call_args.kwargs["headers"], {"Retry-After": "5"})
        finally:
            for _ in range(status.MAX_REQUESTS):
                self.state.status.requests.release()

    def test_cache_single_flight_and_global_collector_budget(self) -> None:
        cache = status.StatusCache()
        entered = [threading.Event(), threading.Event()]
        release = threading.Event()
        def collect(index: int) -> str:
            entered[index].set()
            self.assertTrue(release.wait(5))
            return str(index)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(cache.read, str(i), 30, lambda i=i: collect(i)) for i in range(2)]
            try:
                self.assertTrue(all(event.wait(5) for event in entered))
                for resource in ("0", "1", "third"):
                    with self.assertRaises(status.StatusBusy):
                        cache.read(resource, 30, Mock(side_effect=AssertionError("No duplicate collector")))
            finally:
                release.set()
            self.assertEqual([future.result().value for future in futures], ["0", "1"])

    def test_failed_collection_preserves_timestamp_and_negative_cache(self) -> None:
        cache = status.StatusCache()
        with patch.object(status.time, "monotonic", return_value=0), patch.object(status.time, "time", return_value=100):
            first = cache.read("source", 30, lambda: {"value": "good"})
        collect = Mock(side_effect=OSError("PRIVATE raw failure"))
        with patch.object(status.time, "monotonic", return_value=31):
            failed = cache.read("source", 30, collect)
            self.assertEqual(cache.read("source", 30, collect), failed)
        self.assertEqual(failed.value, first.value)
        self.assertEqual(failed.observed_at, 100)
        self.assertTrue(failed.failed)
        collect.assert_called_once()

    def test_warm_followers_do_not_wait_or_duplicate_collection(self) -> None:
        cache = status.StatusCache()
        with patch.object(status.time, "monotonic", return_value=0):
            first = cache.read("source", 30, lambda: "old")
        entered, release = threading.Event(), threading.Event()
        def collect() -> str:
            entered.set()
            self.assertTrue(release.wait(5))
            return "new"
        with patch.object(status.time, "monotonic", return_value=31), ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(cache.read, "source", 30, collect)
            try:
                self.assertTrue(entered.wait(5))
                self.assertEqual(cache.read("source", 30, Mock(side_effect=AssertionError())), first)
            finally:
                release.set()
            self.assertEqual(future.result().value, "new")

    def test_status_never_runs_inspections_or_mutations(self) -> None:
        with patch.object(panel, "collect_service_health", side_effect=AssertionError("Explicit load required")), \
             patch.object(self.state.storage_diagnostics, "start", side_effect=AssertionError("No scan")), \
             patch.object(self.state.agent_tasks, "start", side_effect=AssertionError("No worker")):
            with self.assertRaises(status.StatusBusy):
                self.state.status.read("services")
            value = self.state.status.read("storage")
            self.assertEqual(value["data"]["state"], "idle")
        self.assertEqual(list(Path(self.home).iterdir()), [])

    def test_agent_and_scan_summaries_exclude_private_content(self) -> None:
        task = {"queued": True, "prompt": "SECRET", "directory": "/private/work"}
        run = {"status": "running", "started_at": 100, "task": task, "output": "SECRET"}
        with patch.object(self.state, "features", return_value=PanelFeatures(tools=frozenset({"codex"}))), \
             patch.object(self.state.agent_tasks, "snapshot", return_value={"tasks": [task], "runs": [run]}):
            self.assertTrue(self.state.status.available("agents"))
            value = self.state.status.read("agents")
        self.assertEqual(value["interval_seconds"], 5)
        self.assertEqual(value["data"]["running"], 1)
        self.assertNotIn("SECRET", json.dumps(value))
        self.assertNotIn("/private", json.dumps(value))
        self.state.storage_diagnostics._snapshot.update({
            "state": "running", "started_at": "2026-01-01T00:00:00Z", "scan_mode": "quick",
            "message": "SECRET", "report": {"generated_at": "2025-01-01T00:00:00Z", "paths": ["/private"]},
        })
        with patch("lib.storage_diagnostics.deepcopy", side_effect=AssertionError("No report copy")):
            value = self.state.status.read("storage")
        self.assertNotIn("SECRET", json.dumps(value))
        self.assertNotIn("/private", json.dumps(value))
        self.assertEqual(value["data"]["report_generated_at"], "2025-01-01T00:00:00Z")

    def test_scan_state_changes_immediately_without_a_cached_idle_interval(self) -> None:
        self.assertEqual(self.state.status.read("storage")["interval_seconds"], 60)
        self.state.storage_diagnostics._snapshot["state"] = "running"
        value = self.state.status.read("storage")
        self.assertEqual(value["data"]["state"], "running")
        self.assertEqual(value["interval_seconds"], 5)

    def test_partial_service_discovery_remains_incomplete_with_configured_entries(self) -> None:
        self.state.manifest["services"] = [{"label": "Configured", "url": "https://example.test/"}]
        discovered = panel._DiscoveredServices()
        discovered.incomplete = True
        with patch.object(panel, "discover_basaltwater_web_services", return_value=discovered):
            value = self.state.status.read("web")
        self.assertEqual(value["freshness"], "incomplete")
        self.assertTrue(value["warnings"])

    def test_exported_partial_unavailable_and_stale_sources(self) -> None:
        self.state.manifest["system_type"] = "server_proxmox"
        with patch.object(self.state, "proxmox_snapshot", return_value={
            "available": True, "inventory": False, "guests": [], "inventory_complete": False,
            "generated_at": 100, "issues": ["/private/path"],
        }), patch.object(status.time, "time", return_value=500):
            value = self.state.status.read("proxmox")
        self.assertEqual(value["freshness"], "stale")
        self.assertIsNone(value["data"]["guests"])
        self.assertNotIn("/private", json.dumps(value))
        with patch.object(self.state, "audit_snapshot", return_value={"status": "unavailable"}):
            value = self.state.status.read("audit")
        self.assertEqual(value["freshness"], "unavailable")
        self.assertIsNone(value["data"]["events"])

    def test_discovery_and_probes_shared_with_dashboard(self) -> None:
        self.state.manifest["services"] = [{"label": "HomeBox", "url": "https://example.test:8444/",
                                            "probe": {"kind": "homebox", "port": 8444}}]
        with patch.object(panel, "discover_basaltwater_web_services", return_value=[]) as discover, \
             patch.object(panel, "_probe_homebox", return_value=("ready", "HomeBox is ready")) as probe:
            first = self.state.web_services()
            for _ in range(10):
                value = self.state.status.read("web")
            self.assertEqual(value["data"]["counts"]["ready"], 1)
            self.assertAlmostEqual(first.observed_at, status.timestamp(value["observed_at"]), places=5)
        discover.assert_called_once()
        probe.assert_called_once()
        self.assertNotIn("example.test", json.dumps(value))

    def test_independently_cached_probe_cannot_receive_a_new_discovery_timestamp(self) -> None:
        self.state.manifest["services"] = [{"label": "HomeBox", "url": "https://example.test/",
                                            "probe": {"kind": "homebox", "port": 8444}}]
        with patch.object(status.time, "time", return_value=100):
            self.state.status_cache.read("homebox-8444", 60, lambda: ("ready", "HomeBox is ready"))
        with patch.object(panel, "discover_basaltwater_web_services", return_value=[]), \
             patch.object(status.time, "time", return_value=200):
            value = self.state.status.read("web")
        self.assertEqual(status.timestamp(value["observed_at"]), 100)

    def test_real_transport_headers_and_server_thread_overload(self) -> None:
        class Handler(panel.WebPanelHandler):
            state = self.state
        server = panel._ThreadingTCPHTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with patch.object(panel, "collect_system_overview", return_value=[]):
                connection = http.client.HTTPConnection(*server.server_address, timeout=3)
                connection.request("GET", status.API_PREFIX + "host", headers={"X-Basaltwater-User": "operator"})
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(response.getheader("Cache-Control"), "no-store")
                self.assertEqual(response.getheader("X-Content-Type-Options"), "nosniff")
                validator = response.getheader("ETag")
                response.read()
                connection.request("GET", status.API_PREFIX + "host", headers={
                    "X-Basaltwater-User": "operator", "If-None-Match": validator,
                })
                response = connection.getresponse()
                self.assertEqual(response.status, 304)
                self.assertIsNone(response.getheader("Content-Length"))
                self.assertEqual(response.read(), b"")
                connection.close()
            for _ in range(32):
                self.assertTrue(server._request_slots.acquire(timeout=3))
            try:
                with socket.create_connection(server.server_address, timeout=3) as connection:
                    response = connection.recv(1024)
                    self.assertIn(b"503", response)
                    self.assertIn(b"Retry-After: 5", response)
            finally:
                for _ in range(32):
                    server._request_slots.release()
        finally:
            server.shutdown()
            worker.join(5)
            server.server_close()

    def test_loopback_probe_deadline_interrupts_a_dribbling_response(self) -> None:
        listener = socket.socket()
        self.addCleanup(listener.close)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.settimeout(2)
        stop = threading.Event()
        def peer() -> None:
            connection, _ = listener.accept()
            with connection:
                connection.settimeout(2)
                connection.recv(4096)
                try:
                    connection.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 1000\r\n\r\n")
                    while not stop.wait(0.02):
                        connection.sendall(b" ")
                except OSError:
                    pass
        worker = threading.Thread(target=peer, daemon=True)
        worker.start()
        started = time.monotonic()
        try:
            with patch.object(panel, "_SERVICE_PROBE_TIMEOUT_SECONDS", 0.1):
                result = panel._probe_homebox(listener.getsockname()[1])
            self.assertEqual(result[0], "unavailable")
            self.assertLess(time.monotonic() - started, 1)
        finally:
            stop.set()
            worker.join(3)


if __name__ == "__main__":
    unittest.main()
