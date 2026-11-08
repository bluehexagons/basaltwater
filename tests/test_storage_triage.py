"""Durable reviewed batches and persistent disposition protections."""

from __future__ import annotations

from contextlib import nullcontext
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from common.service_tools import data_job
from lib.data_actions import finding_revision, validate_data_action
from lib.runtime_config import RuntimeConfig
from sync.service_tools import scrub_findings as findings, scrub_par2, storage_ops


class TriageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source, self.database = self.root / "source", self.root / "parity"
        self.source.mkdir()
        self.database.mkdir()
        self.paths = [self.source / "one", self.source / "two"]
        for path in self.paths:
            path.write_bytes(b"damaged bytes")
            self.report().record(path.name, {"category": "unrepairable", "file_identity": findings.file_identity(str(path))})
        self.saved = {"scrub_specs": [[str(self.source), str(self.database), "10%", "weekly"]]}
        self.value = {"action": "quarantine", "directory": str(self.source), "database": str(self.database),
                      "job_id": "a" * 32, "items": [{"file": str(path), "finding": finding_revision(self.report().data["files"][path.name])} for path in self.paths]}
        self.state = self.root / "job.json"
        self.enterContext(patch.multiple(data_job, DATA_STATE=str(self.state), LOCK_FILE=str(self.root / "storage.lock")))
        self.enterContext(patch.object(data_job, "protected_path"))
        self.enterContext(patch.object(data_job, "_setup_owner", side_effect=lambda: nullcontext()))
        self.enterContext(patch.object(data_job, "can_manage_system_services", return_value=True))
        self.enterContext(patch.object(data_job, "load_setup_config", return_value=self.saved))
        original = os.fstat
        def owned(fd):
            fields = list(original(fd))
            fields[4] = 0
            return os.stat_result(fields)
        self.enterContext(patch.object(os, "fstat", side_effect=owned))
        self.enterContext(patch.object(os, "geteuid", return_value=0))

    def report(self):
        return findings.Findings(str(self.source), str(self.database))

    def result(self):
        return json.loads(self.state.read_text())

    def test_contract_exact_bounded_and_detached(self):
        validated = validate_data_action(self.value)
        validated["items"][0]["file"] = "changed"
        self.assertEqual(self.value["items"][0]["file"], str(self.paths[0]))
        for invalid in ({**self.value, "extra": 1}, {**self.value, "action": "accept"},
                        {**self.value, "items": []}, {**self.value, "items": self.value["items"] * 6},
                        {**self.value, "items": [self.value["items"][0]] * 2},
                        {**self.value, "items": [{**self.value["items"][0], "backup": ""}]}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_data_action(invalid)
        large = copy.deepcopy(self.value)
        large["items"] = [{"file": str(self.source / (str(i) + "x" * 950)), "finding": "a" * 64} for i in range(10)]
        with self.assertRaisesRegex(ValueError, "10000 bytes"):
            validate_data_action(large)

    def test_stale_whole_preflight_has_no_effects(self):
        self.paths[1].write_bytes(b"replacement")
        self.assertEqual(data_job.run_action(self.value), 1)
        self.assertTrue(all(path.exists() for path in self.paths))
        self.assertEqual([item["status"] for item in self.result()["items"]], ["skipped"] * 2)
        self.assertFalse(self.report().data.get("dispositions"))

    def test_verify_batch_rejects_changed_identity(self):
        self.value["action"] = "verify"
        self.paths[1].write_bytes(b"replacement")
        with patch.object(data_job, "examine") as examine:
            self.assertEqual(data_job.run_action(self.value), 1)
            examine.assert_not_called()

    def test_changed_finding_rejects_entire_batch(self):
        self.report().record("two", {"category": "repairable"})
        with patch.object(data_job, "dispose") as dispose:
            self.assertEqual(data_job.run_action(self.value), 1)
            dispose.assert_not_called()
        self.assertTrue(all(path.exists() for path in self.paths))
        self.assertEqual(self.result()["failure"], "finding_changed")

    def test_per_item_race_preserves_partial_results(self):
        dispose = data_job.dispose
        def race(*args):
            result = dispose(*args)
            self.paths[1].write_bytes(b"new bytes after preflight")
            return result
        with patch.object(data_job, "dispose", side_effect=race):
            self.assertEqual(data_job.run_action(self.value), 1)
        self.assertEqual([item["status"] for item in self.result()["items"]], ["completed", "skipped"])
        self.assertTrue(self.paths[1].exists())

    def test_interruption_never_replays_and_reports_uncertainty(self):
        dispose = data_job.dispose
        def stop(*args):
            dispose(*args)
            raise KeyboardInterrupt()
        with patch.object(data_job, "dispose", side_effect=stop), self.assertRaises(KeyboardInterrupt):
            data_job.run_action(self.value)
        self.assertEqual([item["status"] for item in self.result()["items"]], ["unresolved", "skipped"])
        with self.assertRaises(ValueError):
            data_job.run_action(self.value)
        # Even after another job occupies the latest slot, the durable claim remains.
        self.state.unlink()
        with self.assertRaises(FileExistsError):
            data_job.run_action(self.value)

    def test_quarantine_retains_relative_path_parity_and_excludes_inventory(self):
        (self.database / "one.par2").write_bytes(b"parity evidence")
        self.assertEqual(data_job.run_action(self.value), 0)
        self.assertEqual([item["status"] for item in self.result()["items"]], ["completed"] * 2)
        self.assertFalse(any(path.exists() for path in self.paths))
        for item in self.result()["items"]:
            self.assertEqual(Path(item["destination"]).read_bytes(), b"damaged bytes")
            self.assertTrue(item["destination"].endswith(Path(item["file"]).name + ".corrupt"))
        self.assertEqual((self.source / findings.QUARANTINE_DIR).stat().st_mode & 0o777, 0o700)
        self.assertTrue((self.database / "one.par2").exists())
        inventory = scrub_par2._scan_tree(str(self.source))
        self.assertFalse(any(files for _, _, files in inventory))
        scrub_par2._record_missing_files(str(self.source), str(self.database), set(), str(self.root / "log"))
        self.assertEqual(self.report().data["files"]["one"]["category"], "unrepairable")

    def test_delete_retains_parity_and_intent_on_ambiguous_failure(self):
        self.value["action"] = "delete"
        (self.database / "one.par2").write_bytes(b"parity")
        unlink = os.unlink
        def uncertain(path, *args, **kwargs):
            if str(path).endswith(".corrupt"):
                raise OSError("uncertain")
            return unlink(path, *args, **kwargs)
        with patch.object(findings.os, "unlink", side_effect=uncertain):
            self.assertEqual(data_job.run_action(self.value), 1)
        self.assertTrue(all(entry["state"] == "intent" for entry in self.report().data["dispositions"].values()))
        self.assertTrue((self.database / "one.par2").exists())
        self.assertTrue(all(item["status"] == "unresolved" for item in self.result()["items"]))

    def test_successful_delete_keeps_disposition(self):
        self.value["action"] = "delete"
        self.assertEqual(data_job.run_action(self.value), 0)
        self.assertFalse(any(path.exists() for path in self.paths))
        self.assertTrue(all(entry["state"] == "completed" for entry in self.report().data["dispositions"].values()))

    def test_symlink_hardlink_and_unrecognized_quarantine_rejected_before_effects(self):
        for case in ("symlink", "hardlink", "directory", "collision"):
            with self.subTest(case=case):
                if case == "symlink":
                    self.paths[1].unlink()
                    self.paths[1].symlink_to(self.paths[0])
                elif case == "hardlink":
                    self.paths[1].unlink()
                    os.link(self.paths[0], self.paths[1])
                elif case == "directory":
                    self.paths[1].unlink()
                    self.paths[1].write_bytes(b"damaged bytes")
                    (self.source / findings.QUARANTINE_DIR).mkdir(mode=0o700)
                else:
                    quarantine = self.source / findings.QUARANTINE_DIR
                    (quarantine / ".owner").write_bytes(b"basaltwater-quarantine-v1\n")
                    (quarantine / ".owner").chmod(0o600)
                    # Ownership mocked at descriptor checks, never host state.
                    key = "a" * 32 + "-" + hashlib.sha256(b"one").hexdigest()[:16]
                    (quarantine / key).mkdir()
                self.value["job_id"] = "a" * 32
                for index, path in enumerate(self.paths):
                    if case != "symlink":
                        self.report().record(path.name, {"category": "unrepairable", "file_identity": findings.file_identity(str(path))})
                        self.value["items"][index]["finding"] = finding_revision(self.report().data["files"][path.name])
                with patch.object(data_job, "dispose") as dispose:
                    if self.state.exists():
                        self.state.unlink()
                    for claim in self.root.glob("*.claimed"):
                        claim.unlink()
                    self.assertEqual(data_job.run_action(self.value), 1)
                    dispose.assert_not_called()
                self.assertTrue(self.paths[0].exists())

    def test_reappearing_path_requires_verification_and_mirror_protection_survives_health(self):
        self.assertEqual(data_job.run_action(self.value), 0)
        self.paths[0].write_bytes(b"new appearance")
        report = self.report()
        report.record("one", {"category": "healthy"})
        with patch.object(scrub_par2.subprocess, "run") as run:
            self.assertTrue(scrub_par2.create_par2(str(self.paths[0]), str(self.source), str(self.database), 10, str(self.root / "log")))
            run.assert_not_called()
        self.assertEqual(self.report().data["files"]["one"]["category"], "changed_unverified")
        report = self.report()
        report.record("one", {"category": "healthy"})
        report.record("two", {"category": "healthy"})
        mirror = self.root / "mirror"
        mirror.mkdir()
        result = storage_ops.validate_sync_integrity(str(self.source), str(mirror), RuntimeConfig.from_dict(self.saved))
        self.assertIn("one", result.excluded_paths)
        self.assertIn("two", result.excluded_paths)
        self.assertIn(findings.QUARANTINE_DIR, result.excluded_paths)

    def test_inactive_worker_maps_running_to_unresolved_without_replay(self):
        self.value["action"] = "verify"
        with patch.object(data_job, "examine", return_value={"category": "healthy"}):
            self.assertEqual(data_job.run_action(self.value), 0)
        record = self.result()
        record["status"] = "running"
        record["items"][0]["status"] = "running"
        record["items"][1]["status"] = "pending"
        self.state.write_text(json.dumps(record))
        with patch.object(data_job, "_unit_state", return_value="inactive"), patch.object(data_job, "_setup_blocked", return_value=False):
            observed = data_job.job_status()["latest"]
        self.assertEqual([item["status"] for item in observed["items"]], ["unresolved", "skipped"])

    def test_full_scan_does_not_reopen_absence_or_create_reinserted_baseline(self):
        self.assertEqual(data_job.run_action(self.value), 0)
        with patch.object(scrub_par2, "create_operation_logger", return_value=Mock()), patch.object(scrub_par2, "log"), patch.object(scrub_par2.subprocess, "run") as run:
            result = scrub_par2.scrub_directory(str(self.source), str(self.database), 10,
                                                str(self.root / "log"), verify=True, suppress_notifications=True)
            self.assertTrue(result["ok"])
            self.paths[0].write_bytes(b"unreviewed replacement")
            result = scrub_par2.scrub_directory(str(self.source), str(self.database), 10,
                                                str(self.root / "log"), verify=True, suppress_notifications=True)
            self.assertFalse(result["ok"])
            run.assert_not_called()
        self.assertEqual(self.report().data["files"]["one"]["category"], "missing_parity")
        self.assertTrue(self.report().disposed("one"))

    def test_rename_collision_after_intent_does_not_clobber(self):
        save = findings.Findings.save
        def collide(report):
            save(report)
            for entry in report.data.get("dispositions", {}).values():
                if entry["state"] == "intent":
                    Path(entry["destination"]).write_bytes(b"collision evidence")
        with patch.object(findings.Findings, "save", collide):
            self.assertEqual(data_job.run_action(self.value), 1)
        self.assertTrue(all(path.exists() for path in self.paths))
        self.assertTrue(all(item["status"] == "unresolved" for item in self.result()["items"]))
        for entry in self.report().data["dispositions"].values():
            self.assertEqual(Path(entry["destination"]).read_bytes(), b"collision evidence")

    def test_nested_path_is_retained_and_new_review_can_dispose_reappearance(self):
        nested = self.source / "nested"
        nested.mkdir()
        path = nested / "one"
        self.paths[0].rename(path)
        self.report().record("nested/one", {"category": "unrepairable", "file_identity": findings.file_identity(str(path))})
        self.value["items"] = [{"file": str(path), "finding": finding_revision(self.report().data["files"]["nested/one"])}]
        self.assertEqual(data_job.run_action(self.value), 0)
        self.assertTrue(self.result()["items"][0]["destination"].endswith("nested/one.corrupt"))
        path.write_bytes(b"different damaged content")
        self.report().record("nested/one", {"category": "unrepairable", "file_identity": findings.file_identity(str(path))})
        self.value["job_id"] = "b" * 32
        self.value["items"][0]["finding"] = finding_revision(self.report().data["files"]["nested/one"])
        self.assertEqual(data_job.run_action(self.value), 0)
        self.assertEqual(len(self.report().data["dispositions"]["nested/one"]["history"]), 1)
