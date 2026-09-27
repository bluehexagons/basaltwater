"""Certificate installers must stop before trust changes when TLS fails."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from common.service_tools.basaltwater_web import _certificate_trust
from common.service_tools.web_panel_service import (
    _linux_trust_script, _macos_trust_script, _render_certificate_trust,
    discover_certificate_trust,
)


class TestCertificateDownloadTrust(unittest.TestCase):
    def test_missing_ca_never_means_public_trust(self):
        with patch("common.service_tools.basaltwater_web.os.path.isfile") as exists:
            exists.side_effect = lambda path: path == "/tmp/server.crt"
            result = _certificate_trust({
                "certificate": "/tmp/server.crt",
                "ca_certificate": "/tmp/missing-ca.crt",
                "base_url": "https://example.test:8443",
            })
        self.assertEqual(result["status"], "unknown")
        self.assertNotIn("publicly_trusted", result)

    def test_self_signed_policy_without_ca_never_means_public_trust(self):
        with patch("common.service_tools.basaltwater_web.os.path.isfile", return_value=True):
            result = _certificate_trust({
                "certificate": "/tmp/self-signed.crt",
                "ca_certificate": None,
                "base_url": "https://example.test:8443",
            })
        self.assertEqual(result["status"], "unknown")

    def test_public_trust_requires_live_tls_verification(self):
        with (
            patch("common.service_tools.basaltwater_web.os.path.isfile", return_value=False),
            patch("common.service_tools.basaltwater_web._verify_live_tls") as verify,
        ):
            policy = {
                "certificate": "/etc/letsencrypt/live/example.test/fullchain.pem",
                "ca_certificate": None,
                "base_url": "https://example.test:8443",
            }
            result = _certificate_trust(policy)
            verify.assert_called_once_with("example.test", 8443, None)
            verify.side_effect = OSError("endpoint unavailable")
            failed = _certificate_trust(policy)
        self.assertEqual(result["status"], "public")
        self.assertEqual(failed["status"], "unknown")

    def test_live_vm_ca_cannot_be_reported_as_public(self):
        with (
            patch("common.service_tools.basaltwater_web.os.path.isfile", return_value=True),
            patch("common.service_tools.basaltwater_web._verify_live_tls") as verify,
        ):
            result = _certificate_trust({
                "certificate": "/etc/letsencrypt/live/example.test/fullchain.pem",
                "ca_certificate": None,
                "base_url": "https://example.test:8443",
            })
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(verify.call_count, 2)

    def test_local_ca_is_offered_after_live_verification(self):
        with tempfile.TemporaryDirectory() as temporary:
            ca = os.path.join(temporary, "ca.crt")
            with open(ca, "wb") as file_obj:
                file_obj.write(b"test CA")
            with patch("common.service_tools.basaltwater_web._verify_live_tls") as verify:
                result = _certificate_trust({
                    "certificate": os.path.join(temporary, "server.crt"),
                    "ca_certificate": ca,
                    "base_url": "https://example.test:8443",
                })
            verify.assert_called_once_with("example.test", 8443, ca)
        self.assertEqual(result["status"], "local_ca")
        self.assertEqual(len(result["sha256"]), 64)

    def test_panel_shows_unknown_trust_without_installation_instructions(self):
        with (
            patch("common.service_tools.web_panel_service.shutil.which", return_value="/usr/bin/basaltwater-web"),
            patch("common.service_tools.web_panel_service._run_json", return_value={"status": "unknown"}),
        ):
            trust = discover_certificate_trust()
        rendered = _render_certificate_trust(trust)
        self.assertIn("could not be verified", rendered)
        self.assertNotIn("No certificate installation required", rendered)
        self.assertNotIn("sudo install", rendered)

    def test_tls_failure_cannot_reach_installation(self):
        for script in (
            _linux_trust_script("a" * 64, "https://example.invalid/ca", "  sudo install"),
            _macos_trust_script("a" * 64, "https://example.invalid/ca"),
        ):
            with self.subTest(script=script), tempfile.TemporaryDirectory() as directory:
                for name, body in (
                    ("curl", "exit 60"),
                    ("sudo", "touch installed; exit 0"),
                ):
                    path = os.path.join(directory, name)
                    with open(path, "w") as file_obj:
                        file_obj.write("#!/bin/sh\n" + body + "\n")
                    os.chmod(path, 0o700)
                result = subprocess.run(
                    ["/bin/bash", "-c", script], cwd=directory,
                    env={"PATH": directory + ":/usr/bin:/bin", "TMPDIR": directory},
                    capture_output=True, text=True, timeout=10,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(sorted(os.listdir(directory)), ["curl", "sudo"])

    def test_all_platforms_retain_tls_verification_and_explain_independent_trust(self):
        rendered = _render_certificate_trust({
            "url": "https://example.invalid/basaltwater-ca.crt", "sha256": "a" * 64,
        })
        for bypass in ("--insecure", "--no-check-certificate", "SkipCertificateCheck", "ServerCertificateValidationCallback"):
            self.assertNotIn(bypass, rendered)
        self.assertIn("independently obtained fingerprint", rendered)
        self.assertIn("already trusts", rendered)
