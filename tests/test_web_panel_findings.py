"""Complete finding browsing stays bounded and independent of NAS availability."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from common.service_tools import web_panel_audit_export as exporter, web_panel_findings as catalog
from common.service_tools import web_panel_shares_data as page, web_panel_storage as storage
from sync.service_tools.scrub_findings import Findings
from lib.shares_data_inventory import MAX_ROUTINES, MAX_SHARES


class CompleteFindingBrowserTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.catalog_path = self.root / "catalog.sqlite3"
        self.config = {"scrub_specs": []}
        for index in range(storage.MAX_JOBS + 1):
            directory, database = self.root / f"data{index}", self.root / f"parity{index}"
            directory.mkdir()
            database.mkdir()
            self.config["scrub_specs"].append([str(directory), str(database), "10%", "weekly"])
            report = Findings(str(directory), str(database))
            report.data["files"] = {f"file{number:03d}.bin": {
                "state": "open", "category": "repairable", "first_seen": "2026-01-01T00:00:00+00:00",
                "last_checked": "2026-01-02T00:00:00+00:00", "file_identity": None,
                "evidence": "PRIVATE EVIDENCE", "recovery": "PRIVATE COPY"}
                for number in range(63 if index == 0 else 1)}
            if index == storage.MAX_JOBS:
                report.data["files"]["Straße-100%_[quoted].bin"] = report.data["files"].pop("file000.bin")
            report.save()

    def collect(self):
        with patch("lib.machine_state.load_setup_config", return_value=self.config), patch("sync.service_tools.storage_ops.validate_mounts_for_operation", return_value=(True, "")), patch("subprocess.run") as run:
            snapshot = storage.collect_storage_snapshot(catalog_path=str(self.catalog_path))
        run.assert_not_called()
        return snapshot

    def load(self, snapshot, query=None):
        return catalog.load_findings(snapshot, query or {}, str(self.catalog_path))

    def test_every_finding_and_task_is_reachable_without_reading_private_reports(self):
        snapshot = self.collect()
        self.assertEqual(snapshot["omitted_jobs"], 1)
        self.assertEqual(snapshot["jobs"][0]["omitted_findings"], 43)
        seen = []
        with patch("subprocess.run") as run, patch("sync.service_tools.scrub_findings.Findings") as private:
            for number in range(1, 5):
                result = self.load(snapshot, {"page": str(number)})
                self.assertTrue(result["available"])
                self.assertLessEqual(len(result["findings"]), catalog.PAGE_SIZE)
                self.assertEqual(result["total"], 71)
                self.assertEqual(result["tasks"], 9)
                self.assertEqual(result["unavailable_jobs"], 0)
                seen.extend(finding["path"] for _job, finding in result["findings"])
        run.assert_not_called()
        private.assert_not_called()
        self.assertEqual(len(seen), len(set(seen)))
        self.assertEqual(len(seen), 71)
        self.assertTrue(any("data8" in path for path in seen))
        with sqlite3.connect(self.catalog_path) as connection:
            self.assertNotIn("PRIVATE", " ".join(connection.iterdump()))

    def test_literal_casefolded_search_and_out_of_range_page(self):
        snapshot = self.collect()
        result = self.load(snapshot, {"q": "STRASSE-100%_[", "category": "repairable", "page": "1000000"})
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["page"], 1)
        self.assertIn("Straße-100%_[", result["findings"][0][1]["path"])
        self.assertEqual(self.load(snapshot, {"q": "' OR 1=1 --"})["total"], 0)
        self.assertEqual(self.load(snapshot, {"category": "unrepairable"})["total"], 0)
        self.assertEqual(page.parse_shares_data_query("view=scrubs&page=1000000")["page"], "1000000")
        for invalid in ("1000001", "9" * 100, "-1", "٠١"):
            with self.assertRaises(ValueError):
                page.parse_shares_data_query("view=scrubs&page=" + invalid)

    def test_large_valid_inventory_does_not_hide_the_complete_finding_catalog(self):
        suffix = "/".join(["\U0001f600" * 60] * 12)
        self.config.update(enable_samba=True,
            samba_shares=[["read", f"share{index}", f"/srv/share{index}/" + suffix, "alice"] for index in range(MAX_SHARES)],
            backup_specs=[["/srv/source/" + suffix, f"/srv/backup{index}/" + suffix, "daily"] for index in range(MAX_ROUTINES)])
        snapshot = self.collect()
        encoded = json.dumps(snapshot).encode()
        self.assertGreater(len(encoded), 2 * 1024 * 1024)
        path = self.root / "storage.json"
        path.write_bytes(encoded)
        loaded = storage.load_storage_snapshot(str(path))
        self.assertTrue(loaded["available"])
        self.assertTrue(loaded["inventory"]["available"])
        self.assertEqual(self.load(loaded)["total"], 71)

    def test_catalog_mismatch_corruption_oversize_and_symlink_are_unavailable(self):
        snapshot = self.collect()
        self.assertFalse(self.load({**snapshot, "generated_at": "2026-01-03T00:00:00+00:00"})["available"])
        with patch.object(catalog, "MAX_CATALOG_BYTES", 1):
            self.assertFalse(self.load(snapshot)["available"])
        destination = self.root / "original.sqlite3"
        self.catalog_path.rename(destination)
        self.catalog_path.symlink_to(destination)
        self.assertFalse(self.load(snapshot)["available"])
        self.catalog_path.unlink()
        self.catalog_path.write_bytes(b"malformed database")
        self.assertFalse(self.load(snapshot)["available"])
        self.assertEqual(self.load({"generated_at": "old"}), {"available": False, "legacy": True})

    def test_missing_report_is_counted_and_never_appears_healthy(self):
        report = Path(self.config["scrub_specs"][-1][1]) / ".basaltwater-scrub" / "findings.json"
        report.write_text("invalid")
        snapshot = self.collect()
        result = self.load(snapshot)
        self.assertTrue(result["available"])
        self.assertEqual(result["unavailable_jobs"], 1)
        self.assertEqual(result["total"], 70)

    def test_query_budget_exhaustion_is_unavailable_instead_of_an_empty_result(self):
        snapshot = self.collect()
        with sqlite3.connect(self.catalog_path) as connection:
            for _ in range(7):
                connection.execute("INSERT INTO findings(job,path,search_path,category,first_seen,last_checked,revision) "
                    "SELECT job,path,search_path,category,first_seen,last_checked,revision FROM findings")
        calls = 0
        def clock():
            nonlocal calls
            calls += 1
            return 0 if calls == 1 else 1
        with patch.object(catalog.time, "monotonic", side_effect=clock):
            result = self.load(snapshot, {"q": "file"})
        self.assertFalse(result["available"])
        self.assertFalse(result["legacy"])

    def test_unsupported_metadata_marks_the_report_incomplete(self):
        report_path = Path(self.config["scrub_specs"][0][1]) / ".basaltwater-scrub" / "findings.json"
        report = json.loads(report_path.read_text())
        report["files"]["file000.bin"]["last_checked"] = "invalid date"
        report_path.write_text(json.dumps(report))
        result = self.load(self.collect())
        self.assertEqual(result["unavailable_jobs"], 1)
        self.assertEqual(result["total"], 70)

    def test_page_uses_complete_catalog_and_does_not_hide_a_failed_generation(self):
        snapshot = self.collect()
        state = SimpleNamespace(manifest={"host": "nas"}, navigation=Mock(return_value=()))
        loader = lambda snapshot, query: self.load(snapshot, query)
        with patch.object(storage, "load_storage_snapshot", return_value=snapshot), patch.object(page, "load_findings", side_effect=loader):
            document = page.render_shares_data(state, "", {"view": "scrubs", "page": "4"})
        self.assertIn("Showing 61–71 of 71", document)
        self.assertIn("data8", document)
        self.assertIn("includes all 9 configured", document)
        self.assertNotIn("outside this snapshot", document)
        self.assertNotIn("PRIVATE", document)
        with patch.object(storage, "load_storage_snapshot", return_value=snapshot), patch.object(page, "load_findings", return_value={"available": False, "legacy": False}):
            document = page.render_shares_data(state, "", {"view": "scrubs"})
        self.assertIn("catalog is unavailable", document)
        self.assertNotIn("No recorded findings match", document)
        self.assertNotIn("Review recovery", document)

    def test_supervisor_publishes_matching_generation_and_cleans_temporary_files(self):
        snapshot = self.collect()
        def worker(command, **_kwargs):
            destination = Path(command[command.index("--catalog") + 1])
            destination.write_bytes(self.catalog_path.read_bytes())
            return 0, json.dumps(snapshot)
        args = SimpleNamespace(output=str(self.root / "audit.json"))
        with patch.object(exporter, "_parser") as parser, patch.object(exporter, "_run_bounded", side_effect=worker) as run, patch.object(exporter, "collect_audit_snapshot", return_value={}), patch.object(exporter.os, "chown"):
            parser.return_value.parse_args.return_value = args
            self.assertEqual(exporter.main(), 0)
        self.assertEqual(run.call_args.kwargs["file_size_limit"], catalog.MAX_CATALOG_BYTES)
        self.assertEqual(run.call_args.kwargs["output_size_limit"], storage.MAX_BYTES)
        published = self.root / "storage-findings.sqlite3"
        self.assertEqual(published.stat().st_mode & 0o777, 0o640)
        self.assertTrue(catalog.load_findings(snapshot, {}, str(published))["available"])
        self.assertEqual(json.loads((self.root / "storage.json").read_text())["catalog"], snapshot["catalog"])
        self.assertEqual(list(self.root.glob(".storage-export-*")), [])
