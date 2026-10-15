"""Fast browser-harness safeguards and first-party favicon regressions."""

from __future__ import annotations

import base64
from contextlib import redirect_stderr
import http.client
from io import StringIO
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from http.server import ThreadingHTTPServer

from common.service_tools.device_pairing_service import PairingRequestHandler, PairingState
from common.service_tools.privilege_approval import ApprovalServer
from lib.privilege_auth import password_record
from scripts import run_browser_tests


class BrowserHarnessSafeguards(unittest.TestCase):
    def test_ci_refuses_browser_work_before_any_runtime_probe(self) -> None:
        with patch.dict(os.environ, {"CI": "true"}), patch.object(run_browser_tests, "_runtime") as runtime, redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit) as error:
                run_browser_tests.main([])
        self.assertEqual(error.exception.code, 2)
        runtime.assert_not_called()

    def test_mismatched_runtime_never_downloads_or_launches(self) -> None:
        with patch.dict(os.environ, {"CI": ""}), patch.object(run_browser_tests, "_runtime", return_value={"version": "1.0.0"}), \
                patch.object(run_browser_tests.subprocess, "Popen") as process, redirect_stderr(StringIO()):
            self.assertEqual(run_browser_tests.main([]), 1)
        process.assert_not_called()

    def test_unreviewed_guacamole_script_never_launches(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            asset = Path(directory) / "all.js"
            asset.write_text("unreviewed code")
            (Path(directory) / "package.json").write_text('{"name":"playwright"}')
            with patch.dict(os.environ, {"CI": ""}), \
                    patch.object(run_browser_tests, "_runtime", return_value={"version": run_browser_tests.PLAYWRIGHT_VERSION, "module": directory}), \
                    patch.object(run_browser_tests.shutil, "which", return_value="/tools/existing"), \
                    patch.object(run_browser_tests.subprocess, "Popen") as process, redirect_stderr(StringIO()):
                self.assertEqual(run_browser_tests.main(["--guacamole-assets", str(asset)]), 1)
            process.assert_not_called()


class BrowserFaviconTests(unittest.TestCase):
    def serve(self, server):
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server

    def get(self, server, path, headers=None):
        connection = http.client.HTTPConnection(*server.server_address, timeout=5)
        self.addCleanup(connection.close)
        connection.request("GET", path, headers=headers or {})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read().decode()

    def test_pairing_has_explicit_same_origin_svg_without_creating_nonce_for_icon(self) -> None:
        server = self.serve(ThreadingHTTPServer(("127.0.0.1", 0), PairingRequestHandler))
        state = server.pairing_state = PairingState({})
        status, headers, body = self.get(server, "/favicon.svg")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "image/svg+xml")
        self.assertIn("<svg", body)
        self.assertEqual(state._nonces, {})
        status, headers, body = self.get(server, "/devices")
        self.assertEqual(status, 200)
        self.assertIn('rel="icon" href="/favicon.svg"', body)
        self.assertIn("img-src 'self'", headers["Content-Security-Policy"])

    def test_approval_icon_remains_authenticated_and_does_not_contact_broker(self) -> None:
        password = "disposable browser test password"
        broker = Mock(return_value={"requests": []})
        server = self.serve(ApprovalServer(("127.0.0.1", 0), password_record("operator", password),
                                          "https://fixture.test", broker))
        headers = {"Host": "fixture.test"}
        self.assertEqual(self.get(server, "/favicon.svg", headers)[0], 401)
        headers["Authorization"] = "Basic " + base64.b64encode(f"operator:{password}".encode()).decode()
        status, response_headers, body = self.get(server, "/favicon.svg", headers)
        self.assertEqual(status, 200)
        self.assertEqual(response_headers["Content-Type"], "image/svg+xml")
        self.assertIn("img-src 'self'", response_headers["Content-Security-Policy"])
        self.assertIn("<svg", body)
        broker.assert_not_called()
        self.assertIn('rel="icon" href="/favicon.svg"', self.get(server, "/", headers)[2])


if __name__ == "__main__":
    unittest.main()
