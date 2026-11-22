"""Tests for concise end-of-run setup notes."""

from __future__ import annotations

import io
import os
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

from lib.managed_storage import BUILD_HOMES
from lib.setup_report import SetupReport, get_setup_report


class SetupReportTest(unittest.TestCase):
    def test_disk_usage_is_compact_deduplicated_and_includes_available_capacity(self) -> None:
        report = SetupReport()
        report.include_disk_usage("fixture")
        output = io.StringIO()
        gib = 1024**3
        usage = SimpleNamespace(f_frsize=1, f_blocks=8 * gib, f_bfree=2 * gib,
                                f_bavail=3 * gib // 2, f_files=200000, f_favail=123456)
        temporary = SimpleNamespace(f_frsize=1, f_blocks=512 * 1024**2, f_bfree=256 * 1024**2,
                                    f_bavail=256 * 1024**2, f_files=0, f_favail=0)
        def filesystem(path):
            return {"mount": {"/tmp": "/tmp", "/scratch": "/scratch", BUILD_HOMES: "/build"}.get(path, "/")}
        with patch("lib.managed_storage.filesystem", side_effect=filesystem), patch("pwd.getpwnam", return_value=SimpleNamespace(
                pw_dir="/home/fixture")), patch("os.statvfs", side_effect=lambda path: temporary if path == "/tmp" else usage) as statvfs, \
                patch.dict(os.environ, {"TMPDIR": "/scratch"}), redirect_stdout(output):
            report.render()
        rendered = output.getvalue()
        self.assertIn("Disk usage:", rendered)
        self.assertIn("/: 6.00 GB / 8.00 GB used (75%); 1.50 GB available; 123,456 inodes free", rendered)
        self.assertIn("/tmp: 256.00 MB / 512.00 MB used (50%); 256.00 MB available\n", rendered)
        self.assertEqual([call.args[0] for call in statvfs.call_args_list], ["/", "/tmp", "/scratch", "/build"])
        self.assertNotIn("Run notes:", rendered)

    def test_disk_usage_failure_preserves_run_notes_and_does_not_raise(self) -> None:
        report = SetupReport()
        report.include_disk_usage("fixture")
        report.warning("existing setup warning")
        output = io.StringIO()
        with patch("lib.managed_storage.filesystem", side_effect=OSError("filesystem inaccessible")), patch(
                "pwd.getpwnam", side_effect=KeyError), patch("os.statvfs") as statvfs, redirect_stdout(output):
            report.render()
        self.assertIn("Disk usage:\n  Unavailable", output.getvalue())
        self.assertIn("existing setup warning", output.getvalue())
        statvfs.assert_not_called()

    def test_observes_and_deduplicates_marked_lines(self) -> None:
        report = SetupReport()
        report.set_step("Configure web")
        report.observe("  ⚠ certificate check was skipped")
        report.observe("  ⚠ certificate check was skipped")
        report.observe("  ✗ nginx reload failed")
        report.observe("normal progress output")

        self.assertEqual(len(report.notes), 2)
        self.assertEqual(report.notes[0].severity, "warning")
        self.assertEqual(report.notes[0].step, "Configure web")
        self.assertEqual(report.notes[1].severity, "error")
        self.assertNotIn("✗", report.notes[1].message)

    def test_capture_forwards_output_and_records_complete_lines(self) -> None:
        report = SetupReport()
        output = io.StringIO()
        with redirect_stdout(output):
            with report.capture():
                print("  ⚠ first half", end="")
                print(" of warning")

        self.assertIn("first half of warning", output.getvalue())
        self.assertEqual(len(report.notes), 1)
        self.assertIn("first half of warning", report.notes[0].message)

    def test_success_line_containing_failure_language_is_not_an_error(self) -> None:
        report = SetupReport()

        report.observe("  ✓ fail2ban configured for Samba (5 failed attempts = 1 hour ban)")

        self.assertEqual(report.notes, ())

    def test_render_groups_notes_for_skimming(self) -> None:
        report = SetupReport()
        report.error("service failed", step="Web")
        report.warning("certificate is local", step="Web")
        output = io.StringIO()
        with redirect_stdout(output):
            report.render()

        rendered = output.getvalue()
        self.assertIn("Run notes:", rendered)
        self.assertIn("Errors (1):", rendered)
        self.assertIn("Warnings (1):", rendered)
        self.assertLess(rendered.index("Errors"), rendered.index("Warnings"))

    def test_active_report_is_scoped(self) -> None:
        self.assertIsNone(get_setup_report())
        report = SetupReport()
        with report.activate():
            self.assertIs(get_setup_report(), report)
        self.assertIsNone(get_setup_report())


if __name__ == "__main__":
    unittest.main()
