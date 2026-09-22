"""Storage snapshots expose bounded health metadata without privileged reads in HTTP."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from common.service_tools import web_panel_storage as storage, web_panel_jobs as jobs, web_panel_audit_export as exporter
from sync.service_tools.scrub_findings import Findings


class StorageSnapshotTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.data = self.root / "data"
        self.db = self.root / "parity"
        self.data.mkdir()
        self.db.mkdir()
        self.path = self.root / "snapshot.json"
        self.config = {"scrub_specs": [[str(self.data), str(self.db), "10%", "weekly"]]}
        self.report = Findings(str(self.data), str(self.db))

    def collect(self):
        with patch("lib.machine_state.load_setup_config", return_value=self.config), patch("sync.service_tools.storage_ops.validate_mounts_for_operation", return_value=(True, "")), patch("subprocess.run") as run:
            result = storage.collect_storage_snapshot()
        run.assert_not_called()
        return result

    def load(self, data):
        self.path.write_text(json.dumps(data))
        return storage.load_storage_snapshot(str(self.path))

    def test_unknown_and_completed_scan_are_distinct(self):
        self.assertEqual(self.collect()["jobs"][0]["status"], "never_scanned")
        self.report.finish_scan(verify=True, completed=False)
        self.assertEqual(self.collect()["jobs"][0]["status"], "incomplete")
        self.report.finish_scan(verify=True, completed=True)
        data = self.collect()
        self.assertEqual(data["jobs"][0]["status"], "verified")
        self.assertTrue(self.load(data)["available"])

    def test_export_omits_private_evidence_and_bounds_findings(self):
        for index in range(storage.MAX_FINDINGS + 3):
            self.report.record(f"file{index}", {"category": "unrepairable", "evidence": "SECRET TOOL OUTPUT", "recovery": "PRIVATE COPY"})
        result = self.collect()
        job = result["jobs"][0]
        self.assertEqual(job["open_count"], storage.MAX_FINDINGS + 3)
        self.assertEqual(job["omitted_findings"], 3)
        self.assertEqual(job["status"], "findings")
        self.assertNotIn("SECRET", json.dumps(result))
        self.assertNotIn("PRIVATE", json.dumps(result))
        self.assertTrue(self.load(result)["available"])

    def test_unreadable_report_and_missing_mount_never_claim_health(self):
        Path(self.report.root).mkdir()
        Path(self.report.path).write_text("invalid")
        self.assertEqual(self.collect()["jobs"][0]["status"], "unavailable")
        with patch("lib.machine_state.load_setup_config", return_value=self.config), patch("sync.service_tools.storage_ops.validate_mounts_for_operation", return_value=(False, "private mount error")):
            snapshot = storage.collect_storage_snapshot()
        self.assertEqual(snapshot["jobs"][0]["status"], "unavailable")
        self.assertNotIn("private mount error", json.dumps(snapshot))

    def test_stale_future_malformed_and_oversized_snapshots_fail_closed(self):
        data = self.collect()
        for delta in (-901, 120):
            data["generated_at"] = (datetime.now(timezone.utc) + timedelta(seconds=delta)).isoformat()
            self.assertFalse(self.load(data)["available"])
        data = self.collect()
        data["jobs"][0]["status"] = "verified"
        self.assertFalse(self.load(data)["available"])
        data["jobs"][0]["status"] = []
        self.assertFalse(self.load(data)["available"])
        self.path.write_bytes(b"x" * (storage.MAX_BYTES + 1))
        self.assertFalse(storage.load_storage_snapshot(str(self.path))["available"])

    def test_html_escapes_paths_and_does_not_run_commands(self):
        self.report.record("<img src=x>.bin", {"category": "unrepairable"})
        data = self.collect()
        with patch.object(storage, "load_storage_snapshot", return_value=data), patch("subprocess.run") as run:
            page = storage.render_storage()
        run.assert_not_called()
        self.assertNotIn("<img", page)
        self.assertIn("&lt;img", page)
        self.assertIn("Unresolved findings", page)

    def test_jobs_page_load_is_explicit(self):
        with patch.object(jobs, "render_storage", return_value="STORAGE SNAPSHOT") as render, patch.object(jobs, "collect_jobs", return_value=jobs.JobSnapshot([], [])):
            self.assertNotIn("STORAGE SNAPSHOT", jobs.render_jobs(False, "", "nas"))
            render.assert_not_called()
            self.assertIn("STORAGE SNAPSHOT", jobs.render_jobs(True, "", "nas"))
            render.assert_called_once()

    def test_exporter_replaces_old_snapshot_when_collection_times_out(self):
        self.path.write_text('{"available": true}')
        args = SimpleNamespace(output=str(self.root / "audit.json"))
        with patch.object(exporter, "_parser") as parser, patch.object(exporter, "_run_bounded", return_value=None) as run, patch.object(exporter, "collect_audit_snapshot", return_value={}):
            parser.return_value.parse_args.return_value = args
            self.assertEqual(exporter.main(), 0)
        run.assert_called_once()
        self.assertEqual(run.call_args.kwargs["timeout"], 20)
        exported = self.root / "storage.json"
        self.assertEqual(json.loads(exported.read_text()), {"available": False})
        self.assertEqual(exported.stat().st_mode & 0o777, 0o640)
