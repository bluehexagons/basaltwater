from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts import collect_guacamole_diagnostics as diagnostics


class DiagnosticsTests(unittest.TestCase):
    def test_redacts_authorization_passwords_and_attachment_urls(self):
        sample = ("Authorization: Basic abcdef\npassword=hidden value\n"
                  "https://user:password@example.test/desktop/ws?attachment=secret&x=1")
        redacted = diagnostics.redact(sample)
        for secret in ("abcdef", "hidden value", "user:password", "=secret"):
            self.assertNotIn(secret, redacted)
        self.assertIn("example.test", redacted)

    def test_report_excludes_desktop_window_details_and_keeps_failed_commands(self):
        status = json.dumps(dict(state="running", geometry=[1600, 900],
                                 windows=[dict(title="private document")], token="secret"))
        with patch.object(diagnostics.os, "getuid", return_value=1000), \
                patch.object(diagnostics.shutil, "which", return_value="/usr/bin/basaltw"), \
                patch.object(diagnostics, "source_info", return_value={}), \
                patch.object(diagnostics, "capture", side_effect=[
                    dict(returncode=0, stdout=status), {}, dict(returncode=1, stderr="sudo unavailable"),
                    {}, {}, {},
                ]):
            report = diagnostics.collect()
        self.assertNotIn("private document", json.dumps(report))
        self.assertNotIn("secret", report["desktop"]["stdout"])
        self.assertEqual(json.loads(report["desktop"]["stdout"])["state"], "running")
        self.assertEqual(report["journal"]["returncode"], 1)

    def test_capture_bounds_output_and_reports_timeouts_without_partial_secrets(self):
        result = subprocess.CompletedProcess([], 0, "x" * (diagnostics.OUTPUT_LIMIT + 1), "")
        with patch.object(diagnostics.subprocess, "run", return_value=result):
            captured = diagnostics.capture(["test"])
        self.assertTrue(captured["truncated"])
        self.assertEqual(len(captured["stdout"]), diagnostics.OUTPUT_LIMIT)
        with patch.object(diagnostics.subprocess, "run",
                          side_effect=subprocess.TimeoutExpired(["test"], 20, output="secret")):
            self.assertNotIn("secret", json.dumps(diagnostics.capture(["test"])))

    def test_source_fingerprints_do_not_follow_links_or_include_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.write_text("private source")
            link = root / "link"
            link.symlink_to(source)
            self.assertIn("error", diagnostics.source_info(link))
            info = diagnostics.source_info(source)
            self.assertIn("sha256", info)
            self.assertNotIn("private source", json.dumps(info))


if __name__ == "__main__":
    unittest.main()
