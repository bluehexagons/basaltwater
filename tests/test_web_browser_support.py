"""Fast browser-harness safeguards and first-party favicon regressions."""

from __future__ import annotations

import base64
from contextlib import redirect_stderr
import http.client
from io import StringIO
import json
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
from tests.browser import fixtures


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


class BrowserQualificationReports(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def report(self, *, firefox_status: str = "passed", guacamole_status: str = "passed", versions: bool = True) -> None:
        specs = []
        for title in ("panel form", "Guacamole boundary"):
            tests = []
            for engine in ("chromium", "firefox"):
                status = guacamole_status if title.startswith("Guacamole") else firefox_status if engine == "firefox" else "passed"
                details = {"engine": engine, "browser_version": "153.0" if engine == "firefox" else "152.0",
                           "playwright_version": run_browser_tests.PLAYWRIGHT_VERSION}
                attachments = [{"name": "browser-context", "body": base64.b64encode(json.dumps(details).encode()).decode()}] if versions else []
                tests.append({"projectName": engine, "expectedStatus": "passed", "status": "expected" if status == "passed" else status,
                              "results": [{"status": status, "attachments": attachments}]})
            specs.append({"title": title, "tests": tests})
        (self.directory / "results.json").write_text(json.dumps({"errors": [], "suites": [{"suites": [{"specs": specs}]}]}))

    def summarize(self, *, guacamole_available: bool = True, returncode: int = 0) -> dict:
        return run_browser_tests.summarize_results(self.directory, engines=["chromium", "firefox"],
            playwright_version=run_browser_tests.PLAYWRIGHT_VERSION, context="managed",
            returncode=returncode, guacamole_available=guacamole_available)

    def test_completed_report_preserves_versions_and_each_engine_outcome(self) -> None:
        self.report()
        summary = self.summarize()
        self.assertEqual(summary["exit_code"], 0)
        self.assertEqual(summary["outcomes"]["firefox"]["browser_versions"], ["153.0"])
        for engine in summary["outcomes"].values():
            self.assertTrue(engine["application_tested"])
            self.assertEqual(engine["first_party"], {"status": "passed", "counts": {"passed": 1}})
            self.assertEqual(engine["guacamole"]["status"], "passed")

    def test_missing_or_invalid_report_never_qualifies_a_zero_exit_code(self) -> None:
        for content in (None, "{", "[]", '{"suites":[]}'):
            with self.subTest(content=content):
                if content is not None:
                    (self.directory / "results.json").write_text(content)
                summary = self.summarize()
                self.assertEqual(summary["exit_code"], 1)
                self.assertTrue(all(not item["application_tested"] for item in summary["outcomes"].values()))

    def test_failed_or_skipped_first_party_engine_is_never_qualified(self) -> None:
        for status in ("failed", "timedOut", "interrupted", "skipped"):
            with self.subTest(status=status):
                self.report(firefox_status=status)
                summary = self.summarize()
                self.assertEqual(summary["exit_code"], 1)
                self.assertTrue(summary["outcomes"]["chromium"]["application_tested"])
                self.assertFalse(summary["outcomes"]["firefox"]["application_tested"])

    def test_guacamole_failure_cannot_be_hidden_by_available_assets(self) -> None:
        for status in ("failed", "skipped"):
            self.report(guacamole_status=status)
            summary = self.summarize()
            self.assertEqual(summary["exit_code"], 1)
            self.assertEqual(summary["outcomes"]["firefox"]["guacamole"]["status"], status)

    def test_optional_missing_guacamole_coverage_is_explicitly_skipped(self) -> None:
        self.report(guacamole_status="skipped")
        summary = self.summarize(guacamole_available=False)
        self.assertEqual(summary["exit_code"], 0)
        self.assertEqual(summary["outcomes"]["firefox"]["guacamole"],
                         {"status": "skipped", "counts": {"skipped": 1}, "reason": "pinned assets missing"})

    def test_missing_browser_versions_and_runner_errors_prevent_success(self) -> None:
        self.report(versions=False)
        self.assertEqual(self.summarize()["exit_code"], 1)
        self.report()
        self.assertEqual(self.summarize(returncode=2)["exit_code"], 2)
        path = self.directory / "results.json"
        report = json.loads(path.read_text())
        report["errors"] = [{"message": "Worker crashed"}]
        path.write_text(json.dumps(report))
        self.assertEqual(self.summarize()["exit_code"], 1)

    def test_focused_results_never_claim_full_application_qualification(self) -> None:
        self.report()
        summary = run_browser_tests.summarize_results(self.directory, engines=["chromium", "firefox"],
            playwright_version=run_browser_tests.PLAYWRIGHT_VERSION, context="managed",
            returncode=0, guacamole_available=True, selection="panel")
        self.assertEqual(summary["exit_code"], 0)
        self.assertEqual(summary["scope"], "focused")
        self.assertTrue(all(outcome["selected_checks_passed"] for outcome in summary["outcomes"].values()))
        self.assertTrue(all(not outcome["application_tested"] for outcome in summary["outcomes"].values()))

    def test_focused_first_party_skips_are_failures_even_without_guacamole(self) -> None:
        self.report(firefox_status="skipped", guacamole_status="skipped")
        summary = run_browser_tests.summarize_results(self.directory, engines=["chromium", "firefox"],
            playwright_version=run_browser_tests.PLAYWRIGHT_VERSION, context="managed",
            returncode=0, guacamole_available=False, selection="panel")
        self.assertEqual(summary["exit_code"], 1)


class BrowserFixtureSafeguards(unittest.TestCase):
    def test_existing_files_or_linked_directories_are_rejected_before_openssl(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private = root / "ca.key"
            private.write_text("unrelated private key")
            link = root / "link"
            link.symlink_to(root, target_is_directory=True)
            for candidate in (root, link):
                with patch.object(fixtures.subprocess, "run") as command:
                    with self.assertRaises(ValueError):
                        fixtures.create_certificates(candidate)
                    command.assert_not_called()
            self.assertEqual(private.read_text(), "unrelated private key")


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
