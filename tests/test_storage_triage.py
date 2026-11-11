"""Durable reviewed batches and persistent disposition protections."""

from __future__ import annotations

from contextlib import nullcontext
import copy
import errno
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
from sync.service_tools import scrub_findings as findings, scrub_par2, storage_ops, sync_rsync


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

    def archive(self, job_id=None):
        return Path(data_job._payload_path(job_id or self.value["job_id"]) + ".claimed")

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

    def test_overlapping_scrubs_reject_disposition_before_any_effects(self):
        for index, directory in enumerate((self.source, self.root, self.source / "nested")):
            if index == 2:
                directory.mkdir()
                self.paths[1] = self.paths[1].rename(directory / "two")
                self.report().record("nested/two", {"category": "unrepairable", "file_identity": findings.file_identity(str(self.paths[1]))})
                self.value["items"][1] = {"file": str(self.paths[1]), "finding": finding_revision(self.report().data["files"]["nested/two"])}
            self.saved["scrub_specs"] = [self.saved["scrub_specs"][0],
                                          [str(directory), str(self.root / "other-parity"), "10%", "weekly"]]
            for action in ("quarantine", "delete"):
                with self.subTest(directory=directory, action=action):
                    self.value.update(action=action, job_id=f"{index * 2 + (action == 'delete'):032x}")
                    with patch.object(data_job, "dispose", return_value={"category": action}) as dispose:
                        self.assertEqual(data_job.run_action(self.value), 1)
                        dispose.assert_not_called()
                    self.assertEqual(data_job._record()["failure"], "overlapping_scrubs")
                    self.assertEqual([item["status"] for item in self.result()["items"]], ["skipped"] * 2)
                    self.assertTrue(all(path.exists() for path in self.paths))

    def test_overlapping_scrubs_still_allow_verification(self):
        self.saved["scrub_specs"].append([str(self.source), str(self.root / "other-parity"), "10%", "weekly"])
        self.value["action"] = "verify"
        with patch.object(data_job, "examine", return_value={"category": "healthy"}) as examine:
            self.assertEqual(data_job.run_action(self.value), 0)
        self.assertEqual(examine.call_count, 2)

    def test_disposition_cannot_remove_another_tasks_parity(self):
        self.saved["scrub_specs"].append([str(self.root / "other-source"), str(self.source), "10%", "weekly"])
        with patch.object(data_job, "dispose", return_value={"category": "quarantine"}) as dispose:
            self.assertEqual(data_job.run_action(self.value), 1)
            dispose.assert_not_called()
        self.assertEqual(data_job._record()["failure"], "reserved_path")
        self.assertTrue(all(path.exists() for path in self.paths))

    def test_disjoint_scrub_does_not_block_disposition(self):
        self.saved["scrub_specs"].append([str(self.root / "other-source"), str(self.root / "other-parity"), "10%", "weekly"])
        self.assertEqual(data_job.run_action(self.value), 0)

    def test_disposition_rejects_reserved_scrub_metadata(self):
        metadata = self.source / findings.METADATA_DIR
        metadata.mkdir()
        path = metadata / "manifest.json"
        path.write_bytes(b"metadata")
        with self.assertRaisesRegex(ValueError, "Reserved disposition path"):
            findings.disposition_preflight(str(path), str(self.source), str(self.database))
        self.assertEqual(path.read_bytes(), b"metadata")

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
        self.assertEqual(json.loads(self.archive().read_text()), self.result())
        with self.assertRaises(ValueError):
            data_job.run_action(self.value)
        # Even after another job occupies the latest slot, the durable claim remains.
        self.state.unlink()
        with self.assertRaises(FileExistsError):
            data_job.run_action(self.value)

    def test_batch_results_survive_another_job_in_the_latest_slot(self):
        self.assertEqual(data_job.run_action(self.value), 0)
        archived = self.archive()
        original = self.result()
        self.assertEqual(json.loads(archived.read_text()), original)
        self.assertEqual(archived.stat().st_mode & 0o777, 0o600)
        self.paths[0].write_bytes(b"replacement")
        self.report().record("one", {"category": "unrepairable", "file_identity": findings.file_identity(str(self.paths[0]))})
        self.value.update(action="verify", job_id="b" * 32,
                          items=[{"file": str(self.paths[0]), "finding": finding_revision(self.report().data["files"]["one"])}])
        with patch.object(data_job, "examine", return_value={"category": "healthy"}):
            self.assertEqual(data_job.run_action(self.value), 0)
        self.assertEqual(self.result()["parameters"]["job_id"], "b" * 32)
        self.assertEqual(json.loads(archived.read_text()), original)
        self.assertTrue(all(Path(item["destination"]).is_file() for item in original["items"]))

    def test_failed_initial_archive_write_prevents_effects_and_replay(self):
        write = data_job.write_json_atomic
        def fail_archive(path, *args, **kwargs):
            if str(path).endswith(".claimed"):
                raise OSError("evidence unavailable")
            return write(path, *args, **kwargs)
        with patch.object(data_job, "write_json_atomic", side_effect=fail_archive), patch.object(data_job, "dispose") as dispose:
            with self.assertRaises(OSError):
                data_job.run_action(self.value)
            dispose.assert_not_called()
        self.assertTrue(all(path.exists() for path in self.paths))
        self.assertFalse(self.state.exists())
        with self.assertRaises(FileExistsError):
            data_job.run_action(self.value)

    def test_archive_failure_after_move_stops_remaining_items(self):
        write = data_job.write_json_atomic
        failed = False
        def fail_once(path, value, **kwargs):
            nonlocal failed
            if str(path).endswith(".claimed") and value["items"][0]["status"] == "completed" and not failed:
                failed = True
                raise OSError("evidence write interrupted")
            return write(path, value, **kwargs)
        with patch.object(data_job, "write_json_atomic", side_effect=fail_once):
            self.assertEqual(data_job.run_action(self.value), 1)
        self.assertTrue(failed)
        self.assertFalse(self.paths[0].exists())
        self.assertTrue(self.paths[1].exists())
        self.assertEqual([item["status"] for item in self.result()["items"]], ["completed", "skipped"])
        self.assertEqual(json.loads(self.archive().read_text()), self.result())

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

    def test_quarantine_stays_inside_nested_subvolume_and_mirrors_exclude_it(self):
        nested = self.source / "snapshots" / "sample"
        nested.mkdir(parents=True)
        path = nested / "one"
        self.paths[0].rename(path)
        self.report().record("snapshots/sample/one", {"category": "unrepairable", "file_identity": findings.file_identity(str(path))})
        self.value["items"] = [{"file": str(path), "finding": finding_revision(self.report().data["files"]["snapshots/sample/one"])}]
        real_stat = os.stat
        # Model a Btrfs subvolume's distinct device number, retaining real
        # temporary-directory rename/fsync/report behavior.
        def subvolume_stat(candidate, *args, **kwargs):
            info = real_stat(candidate, *args, **kwargs)
            if kwargs.get("dir_fd") is None and Path(candidate).is_relative_to(nested):
                fields = list(info)
                fields[2] = real_stat(self.source).st_dev + 1
                return os.stat_result(fields)
            return info
        with patch.object(findings.os, "stat", side_effect=subvolume_stat):
            self.assertEqual(findings.quarantine_root(str(path), str(self.source)), str(nested))
        with patch.object(findings, "quarantine_root", return_value=str(nested)):
            self.assertEqual(data_job.run_action(self.value), 0)
        destination = Path(self.result()["items"][0]["destination"])
        self.assertTrue(destination.is_relative_to(nested / findings.QUARANTINE_DIR))
        self.assertEqual(destination.read_bytes(), b"damaged bytes")
        inventory = scrub_par2._scan_tree(str(self.source))
        self.assertFalse(any(findings.QUARANTINE_DIR in root for root, _, _ in inventory))
        for conservative in (False, True):
            with sync_rsync._rsync_command(str(self.source), str(self.root / "mirror"), (), conservative) as (command, _):
                self.assertIn('--exclude=' + findings.QUARANTINE_DIR, command)

    def test_filesystem_failure_is_actionable_and_exception_text_stays_private(self):
        for index, (code, label) in enumerate(((errno.EROFS, "read_only"), (errno.EACCES, "permission_denied"),
                                              (errno.ENOSPC, "no_space"), (errno.EXDEV, "cross_device"))):
            self.value["job_id"] = f"{index:032x}"
            with patch.object(data_job, "disposition_preflight", side_effect=OSError(code, "PRIVATE host evidence")), \
                 patch.object(data_job, "dispose") as dispose:
                self.assertEqual(data_job.run_action(self.value), 1)
                dispose.assert_not_called()
            self.assertEqual(data_job._record()["failure"], label)
            self.assertTrue(all(item["failure"] == label for item in self.result()["items"]))
            self.assertNotIn("PRIVATE", self.state.read_text())

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

    def test_fresh_verification_of_replacement_survives_daily_maintenance(self):
        self.assertEqual(data_job.run_action(self.value), 0)
        self.paths[0].write_bytes(b"verified replacement")
        healthy = {"category": "healthy", "file_identity": findings.file_identity(str(self.paths[0]))}
        with patch.object(scrub_par2, "examine", return_value=healthy):
            self.assertEqual(scrub_par2.verify_repair(str(self.paths[0]), str(self.source), str(self.database),
                                                    str(self.root / "log")), scrub_par2.VERIFY_OK)
        with patch.object(scrub_par2.subprocess, "run") as run:
            self.assertTrue(scrub_par2.create_par2(str(self.paths[0]), str(self.source), str(self.database),
                                                  10, str(self.root / "log")))
            self.assertFalse(self.report().active("one"))
            self.assertTrue(self.report().disposed("one"))
            self.paths[0].write_bytes(b"another unverified replacement")
            self.assertTrue(scrub_par2.create_par2(str(self.paths[0]), str(self.source), str(self.database),
                                                  10, str(self.root / "log")))
            self.assertTrue(self.report().active("one"))
            run.assert_not_called()

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
