from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
from email.message import Message
import io
import json
import ssl
import subprocess
import unittest
from unittest.mock import MagicMock, patch
import urllib.error

from lib import deployment_receipt as receipt


class DeploymentReceiptTest(unittest.TestCase):
    revision = "a" * 40

    def run_receipt(self, *options: str) -> tuple[int, dict]:
        parser = argparse.ArgumentParser()
        receipt.add_deployment_receipt_parser(parser.add_subparsers())
        args = parser.parse_args([
            "deployment-receipt", "--revision", self.revision, "--json",
            "--loopback-url", "http://[::1]:8080/health",
            "--lan-url", "https://service.example.test:8449/health", *options,
        ])
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(io.StringIO()):
            code = receipt.run_deployment_receipt(args)
        return code, json.loads(output.getvalue())

    def test_stale_gateway_revision_is_detected_even_when_both_health_checks_pass(self) -> None:
        responses = []
        for revision in (self.revision, "b" * 40):
            response = MagicMock()
            response.__enter__.return_value.status = 200
            headers = Message()
            headers["X-Release-Revision"] = revision
            headers["Set-Cookie"] = "secret"
            response.__enter__.return_value.headers = headers
            responses.append(response)
        with patch.object(receipt, "open_direct", side_effect=responses):
            code, result = self.run_receipt()
        self.assertEqual(code, 1)
        self.assertEqual(result["stages"]["loopback_revision"]["status"], "passed")
        self.assertEqual(result["stages"]["lan_https"]["status"], "passed")
        self.assertEqual(result["stages"]["lan_https_revision"]["issue"], "revision_mismatch")
        self.assertFalse(result["client_device_checked"])
        self.assertNotIn("secret", json.dumps(result))

    def test_restart_failure_is_recorded_and_skips_later_units_and_health(self) -> None:
        with (
            patch.object(receipt.subprocess, "run", side_effect=[
                subprocess.CompletedProcess([], 0), subprocess.CompletedProcess([], 5),
            ]) as command,
            patch.object(receipt, "open_direct") as probe,
        ):
            code, result = self.run_receipt("--user-units", "--restart-unit", "api.service",
                                            "--restart-unit", "web.service", "--", "./activate")
        self.assertEqual(code, 1)
        self.assertEqual(command.call_args_list[0].args[0], ["./activate"])
        self.assertEqual(command.call_args_list[1].args[0], ["systemctl", "--user", "restart", "api.service"])
        self.assertEqual(result["stages"]["restart:api.service"], {"status": "failed", "exit_code": 5})
        self.assertEqual(result["stages"]["restart:web.service"]["status"], "skipped")
        self.assertEqual(result["stages"]["lan_https"]["status"], "skipped")
        probe.assert_not_called()

    def test_reports_tls_failure_as_host_certificate_failure(self) -> None:
        with patch.object(receipt, "open_direct", side_effect=[
            ConnectionRefusedError(), urllib.error.URLError(ssl.SSLCertVerificationError("untrusted")),
        ]):
            code, result = self.run_receipt()
        self.assertEqual(code, 1)
        self.assertEqual(result["stages"]["loopback"]["issue"], "connection_refused")
        self.assertEqual(result["stages"]["lan_https"]["issue"], "certificate_untrusted")
        self.assertEqual(result["stages"]["lan_https"]["network_origin"], "command_host")
        self.assertEqual(result["stages"]["lan_https_revision"]["status"], "skipped")

    def test_retries_transient_health_failure_after_activation(self) -> None:
        with (
            patch.object(receipt, "_command_stage", return_value={"status": "passed"}),
            patch.object(receipt.time, "sleep") as sleep,
            patch.object(receipt, "_probe", side_effect=[
                ({"status": "failed", "issue": "connection_refused"}, None),
                ({"status": "passed", "http_status": 200}, self.revision),
                ({"status": "passed", "http_status": 200}, self.revision),
            ]),
        ):
            code, result = self.run_receipt("--", "./activate")
        self.assertEqual(code, 0)
        self.assertTrue(result["ok"])
        self.assertEqual(result["stages"]["loopback_revision"]["observed"], self.revision)
        sleep.assert_called_once()
