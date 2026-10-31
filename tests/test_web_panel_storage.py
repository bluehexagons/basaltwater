"""Storage snapshots expose bounded health metadata without privileged reads in HTTP."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from common.service_tools import web_panel_storage as storage, web_panel_jobs as jobs, web_panel_audit_export as exporter
from sync.service_tools.scrub_findings import Findings
from lib.shares_data_inventory import configuration_inventory, validate_inventory, MAX_ROUTINES, MAX_SHARES
from common.service_tools.web_panel_shares_data import render_shares_data, parse_shares_data_query
from common.service_tools.web_panel_templates import panel_navigation


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
        with patch("lib.machine_state.load_setup_config", return_value=self.config), patch("sync.service_tools.storage_ops.validate_mounts_for_operation", return_value=(True, "")), patch("smb.account_management.collect_account_snapshot", return_value={"available": False}), patch("smb.share_management.collect_share_snapshot", return_value={"available": False}), patch("sync.routine_management.collect_routine_snapshot", return_value={"available": False}), patch("subprocess.run") as run:
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

    def test_configuration_inventory_never_exports_credentials_or_mount_secrets(self):
        self.config.update(enable_samba=True, samba_shares=[["write", "archive", str(self.data), "alice:INLINE_SECRET,bob"]],
                           share_credentials=[["bob", "CREDENTIAL_SECRET"]],
                           smb_mounts=[["/mnt/remote", "192.0.2.1", "MOUNT_SECRET", "share", ""]],
                           backup_specs=[[str(self.data), str(self.root / "backup"), "daily"]],
                           sync_specs=[[str(self.db), str(self.root / "replica"), "weekly"]])
        snapshot = self.collect()
        inventory = snapshot["inventory"]
        self.assertEqual(inventory["shares"][0]["users"], ["alice", "bob"])
        self.assertEqual([item["kind"] for item in inventory["routines"]], ["backup", "sync"])
        self.assertNotIn("SECRET", json.dumps(snapshot))
        self.assertTrue(self.load(snapshot)["inventory"]["available"])

    def test_invalid_inventory_cannot_hide_valid_integrity_reports(self):
        snapshot = self.collect()
        snapshot["inventory"]["shares"] = [{"password": "PRIVATE"}]
        loaded = self.load(snapshot)
        self.assertTrue(loaded["available"])
        self.assertFalse(loaded["inventory"]["available"])
        self.assertNotIn("PRIVATE", json.dumps(loaded))
        del snapshot["inventory"]
        self.assertFalse(self.load(snapshot)["inventory"]["available"])

    def test_invalid_routine_controls_cannot_hide_valid_integrity_reports(self):
        snapshot = self.collect()
        snapshot["routine_management"] = {"available": True, "private": "PRIVATE"}
        loaded = self.load(snapshot)
        self.assertTrue(loaded["available"])
        self.assertFalse(loaded["routine_management"]["available"])
        self.assertNotIn("PRIVATE", json.dumps(loaded))

    def test_inventory_limits_and_malformed_configuration(self):
        saved = {"enable_samba": True,
                 "samba_shares": [["read", f"share{i}", f"/srv/share{i}", "alice"] for i in range(MAX_SHARES + 3)],
                 "backup_specs": [["/srv/source", f"/srv/backup{i}", "daily"] for i in range(MAX_ROUTINES + 2)]}
        inventory = configuration_inventory(saved)
        self.assertEqual(inventory["omitted_shares"], 3)
        self.assertEqual(inventory["omitted_routines"], 2)
        self.assertEqual(len(inventory["routines"]), MAX_ROUTINES)
        for invalid in ({"samba_shares": False}, {"samba_shares": [["write", "bad[section]", "/srv/share", "alice"]]},
                        {"samba_shares": [["write", "valid", "/srv/share", "bad username:PRIVATE"]]},
                        {"backup_specs": [["/srv", "/srv/backup", "daily"]]},
                        {"sync_specs": [["/srv/a", "/srv/b", "invalid"]]}):
            with self.subTest(invalid=invalid):
                self.assertEqual(configuration_inventory(invalid), {"available": False})
        inventory["shares"][0]["password"] = "PRIVATE"
        with self.assertRaises(ValueError):
            validate_inventory(inventory)

    def test_page_filters_escapes_and_preserves_snapshot_limits(self):
        self.report.record("<script>quoted 'file'.bin", {"category": "repairable", "evidence": "SECRET"})
        self.report.record("other.bin", {"category": "unrepairable"})
        snapshot = self.collect()
        state = SimpleNamespace(manifest={"host": "nas.example.test"},
            navigation=lambda current: panel_navigation(current=current, available=frozenset({"dashboard", "admin", "shares-data"})))
        query = parse_shares_data_query("view=scrubs&category=repairable&q=quoted")
        with patch.object(storage, "load_storage_snapshot", return_value=snapshot), patch("subprocess.run") as run:
            page = render_shares_data(state, "", query)
        run.assert_not_called()
        self.assertIn("&lt;script&gt;", page)
        self.assertNotIn("<script>", page)
        self.assertNotIn("SECRET", page)
        self.assertNotIn("other.bin", page)
        self.assertIn("Showing 1–1 of 1 matching", page)
        self.assertIn("--database", page)
        self.assertIn("--file", page)
        self.assertIn("new baseline", page)

    def test_finding_pagination_keeps_filters_and_unavailable_is_not_empty(self):
        snapshot = self.collect()
        job = snapshot["jobs"][0]
        finding = {"path": "/srv/match.bin", "category": "repairable", "first_seen": "now", "last_checked": "now"}
        job.update(findings=[finding.copy() for _ in range(20)], open_count=20)
        snapshot["jobs"].append(dict(job))
        state = SimpleNamespace(manifest={"host": "nas"}, navigation=Mock(return_value=()))
        with patch.object(storage, "load_storage_snapshot", return_value=snapshot):
            page = render_shares_data(state, "", {"view": "scrubs", "category": "repairable", "q": "match", "page": "2"})
        self.assertIn("Showing 21–40 of 40", page)
        self.assertIn("category=repairable&amp;q=match&amp;page=1", page)
        with patch.object(storage, "load_storage_snapshot", return_value={"available": False, "jobs": []}):
            page = render_shares_data(state, "", {})
        self.assertIn("unavailable or stale", page)
        self.assertNotIn("No backup or sync", page)

    def test_jobs_page_load_is_explicit(self):
        with patch.object(jobs, "render_storage", return_value="STORAGE SNAPSHOT") as render, patch.object(jobs, "collect_jobs", return_value=jobs.JobSnapshot([], [])) as collect:
            self.assertNotIn("STORAGE SNAPSHOT", jobs.render_jobs(False, "", "nas"))
            render.assert_not_called()
            collect.assert_not_called()
            self.assertNotIn("STORAGE SNAPSHOT", jobs.render_jobs(True, "", "nas"))
            render.assert_not_called()
            collect.return_value = jobs.JobSnapshot([{
                "service": "storage-ops.service", "label": "Storage operations", "tone": "info", "status": "No run recorded",
                **{key: "unknown" for key in ("timer", "enabled", "next", "triggered", "started", "finished", "result", "exit", "persistent")},
            }], [])
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
