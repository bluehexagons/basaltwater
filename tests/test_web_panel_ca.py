"""Certificate installers must stop before trust changes when TLS fails."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest

from common.service_tools.web_panel_service import (
    _linux_trust_script, _macos_trust_script, _render_certificate_trust,
)


class TestCertificateDownloadTrust(unittest.TestCase):
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
