"""Integrity reports and recovery never mutate live data before verification."""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from lib import scrub_cli
from lib.runtime_config import RuntimeConfig
from sync.service_tools import scrub_findings as findings, scrub_manage, scrub_par2, storage_ops


class TestFindings(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "source"
        self.database = self.root / "parity"
        self.source.mkdir()
        self.database.mkdir()
        self.file = self.source / "data.bin"
        self.parity = self.database / "data.bin.par2"
        self.file.write_bytes(b"damaged")
        self.parity.write_bytes(b"old-parity")
        self.args = (str(self.file), str(self.source), str(self.database))
        self.addCleanup(patch.stopall)
        patch.object(scrub_par2, "log").start()
        patch.object(scrub_par2, "create_operation_logger").start()
        patch.object(findings.os, "chown").start()
        patch.object(findings.shutil, "disk_usage", return_value=Mock(free=10**12)).start()

    def report(self):
        return findings.Findings(str(self.source), str(self.database))

    def run_par2(self, code=0, evidence=b"PAR2 evidence"):
        def run(command, **kwargs):
            kwargs["stdout"].write(evidence)
            return subprocess.CompletedProcess(command, code)
        return patch.object(findings.subprocess, "run", side_effect=run)

    def test_exit_codes_distinguish_damage_from_execution_errors(self):
        for code, category in ((0, "healthy"), (1, "repairable"), (2, "unrepairable"),
                               (4, "invalid_parity"), (5, "repair_failed"), (6, "io_error"), (8, "tool_error")):
            with self.subTest(code=code), self.run_par2(code, b"x" * 9000):
                outcome = findings.examine(*self.args)
            self.assertEqual(outcome["category"], category)
            self.assertEqual(outcome["returncode"], code)
            self.assertEqual(len(outcome["evidence"]), 8192)

    def test_missing_parity_is_not_verified_health(self):
        self.parity.unlink()
        with patch.object(findings.subprocess, "run") as run:
            self.assertEqual(findings.examine(*self.args)["category"], "missing_parity")
        run.assert_not_called()

    def test_missing_file_is_classified_and_can_be_restored(self):
        self.file.unlink()
        with self.run_par2(1):
            self.assertEqual(findings.examine(*self.args)["category"], "missing_file")
        backup = self.root / "backup"
        backup.write_bytes(b"restored")
        with self.run_par2(0):
            findings.remediate(*self.args, 10, "restore", backup=str(backup))
        self.assertEqual(self.file.read_bytes(), b"restored")

    def test_io_error_is_recorded_as_operational_failure(self):
        with self.run_par2(6):
            with self.assertRaises(OSError):
                scrub_par2.verify_repair(*self.args, "unused")
        self.assertEqual(self.report().data["files"]["data.bin"]["category"], "io_error")

    def test_concurrent_change_invalidates_successful_scan(self):
        def run(command, **kwargs):
            self.file.write_bytes(b"new contents")
            return subprocess.CompletedProcess(command, 0)
        with patch.object(findings.subprocess, "run", side_effect=run):
            self.assertEqual(findings.examine(*self.args)["category"], "changed_during_scan")

    def test_first_seen_persists_and_only_verified_result_resolves(self):
        self.report().record("data.bin", {"category": "unrepairable", "evidence": "bad blocks"})
        initial = self.report().data["files"]["data.bin"]
        self.report().record("data.bin", {"category": "io_error", "evidence": "offline"})
        updated = self.report().data["files"]["data.bin"]
        self.assertEqual(updated["first_seen"], initial["first_seen"])
        self.assertEqual(updated["state"], "open")
        self.report().record("data.bin", {"category": "healthy", "evidence": "verified"})
        resolved = self.report().data["files"]["data.bin"]
        self.assertEqual(resolved["state"], "resolved")
        self.assertEqual(resolved["resolution"], "verified")
        self.assertEqual(resolved["history"][0]["category"], "unrepairable")
        self.assertEqual(os.stat(self.report().path).st_mode & 0o777, 0o600)

    def test_daily_update_preserves_open_findings_and_original_parity(self):
        self.report().record("data.bin", {"category": "unrepairable", "evidence": "bad blocks"})
        with patch.object(findings.subprocess, "run") as run:
            result = scrub_par2.scrub_directory(str(self.source), str(self.database), 10, "unused",
                                                verify=False, suppress_notifications=True)
        run.assert_not_called()
        self.assertTrue(result["completed"])
        self.assertFalse(result["ok"])
        self.assertTrue(self.report().active("data.bin"))
        self.assertEqual(self.parity.read_bytes(), b"old-parity")

    def test_clean_full_scan_is_recorded_separately_from_fast_maintenance(self):
        with self.run_par2(0):
            result = scrub_par2.scrub_directory(str(self.source), str(self.database), 10, "unused", suppress_notifications=True)
        self.assertTrue(result["ok"])
        first_scan = self.report().data["last_full_scan"]
        self.assertEqual(first_scan["open_findings"], 0)
        with patch.object(findings.subprocess, "run") as run:
            scrub_par2.scrub_directory(str(self.source), str(self.database), 10, "unused", verify=False, suppress_notifications=True)
        run.assert_not_called()
        self.assertEqual(self.report().data["last_full_scan"], first_scan)
        self.assertIn("last_parity_update", self.report().data)

    def test_known_suspect_file_cannot_gain_a_silent_new_baseline(self):
        self.report().record("data.bin", {"category": "missing_parity", "evidence": "missing"})
        self.parity.unlink()
        with patch.object(findings.subprocess, "run") as run:
            scrub_par2.create_par2(*self.args, 10, "unused")
        run.assert_not_called()
        self.assertTrue(self.report().active("data.bin"))

    def test_full_scan_preserves_newer_content_instead_of_reverting_edit(self):
        os.utime(self.parity, (10, 10))
        os.utime(self.file, (30, 30))
        with self.run_par2(1) as run:
            result = scrub_par2.scrub_directory(str(self.source), str(self.database), 10, "unused",
                                                suppress_notifications=True)
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0][1], "verify")
        self.assertFalse(result["ok"])
        self.assertTrue(result["completed"])
        self.assertEqual(self.file.read_bytes(), b"damaged")
        self.assertTrue(self.report().data["files"]["data.bin"]["content_change_uncertain"])

    def test_corrupt_report_fails_closed_before_parity_change(self):
        report = self.report()
        Path(report.root).mkdir()
        Path(report.path).write_text("broken JSON")
        with patch.object(findings.subprocess, "run") as run:
            with self.assertRaises(ValueError):
                scrub_par2.create_par2(*self.args, 10, "unused")
        run.assert_not_called()
        self.assertEqual(self.parity.read_bytes(), b"old-parity")

    def test_failed_restore_preserves_live_file_and_parity(self):
        backup = self.root / "backup"
        backup.write_bytes(b"incorrect backup")
        with self.run_par2(2):
            outcome = findings.remediate(*self.args, 10, "restore", backup=str(backup))
        self.assertEqual(outcome["category"], "unrepairable")
        self.assertEqual(self.file.read_bytes(), b"damaged")
        self.assertEqual(self.parity.read_bytes(), b"old-parity")
        recovery = Path(outcome["recovery"])
        self.assertEqual((recovery / "original/data.bin").read_bytes(), b"damaged")
        self.assertEqual((recovery / "parity/data.bin.par2").read_bytes(), b"old-parity")

    def test_verified_restore_retains_evidence_and_resolves(self):
        backup = self.root / "backup"
        backup.write_bytes(b"restored content")
        with self.run_par2(0):
            result = findings.remediate(*self.args, 10, "restore", backup=str(backup))
        self.assertEqual(self.file.read_bytes(), b"restored content")
        self.assertEqual(self.parity.read_bytes(), b"old-parity")
        self.assertEqual(self.report().data["files"]["data.bin"]["resolution"], "restore")
        # Orphan cleanup must never consume the retained recovery copy.
        scrub_par2._record_missing_files(str(self.source), str(self.database), {"data.bin"}, "unused")
        self.assertTrue((Path(result["recovery"]) / "parity/data.bin.par2").exists())

    def test_live_change_during_restore_prevents_publication(self):
        backup = self.root / "backup"
        backup.write_bytes(b"backup")
        def run(command, **kwargs):
            self.file.write_bytes(b"concurrent edit")
            return subprocess.CompletedProcess(command, 0)
        with patch.object(findings.subprocess, "run", side_effect=run):
            with self.assertRaisesRegex(RuntimeError, "changed during remediation"):
                findings.remediate(*self.args, 10, "restore", backup=str(backup))
        self.assertEqual(self.file.read_bytes(), b"concurrent edit")

    def test_repair_uses_staging_and_reverifies_before_publication(self):
        def run(command, **kwargs):
            self.assertEqual(self.file.read_bytes(), b"damaged")
            candidate = Path(kwargs["cwd"]) / "data.bin"
            self.assertNotEqual(candidate, self.file)
            if command[1] == "repair":
                candidate.write_bytes(b"repaired")
            return subprocess.CompletedProcess(command, 0)
        with patch.object(findings.subprocess, "run", side_effect=run) as run:
            result = findings.remediate(*self.args, 10, "repair")
        self.assertEqual([call.args[0][1] for call in run.call_args_list], ["repair", "verify"])
        self.assertEqual(self.file.read_bytes(), b"repaired")
        self.assertEqual(result["category"], "healthy")

    def test_accept_generates_and_verifies_new_parity_before_replacing_old(self):
        def create(file_path, directory, database, *args):
            self.assertEqual(self.parity.read_bytes(), b"old-parity")
            Path(database, "data.bin.vol00+01.par2").write_bytes(b"new-parity")
            return True
        with patch.object(scrub_par2, "create_par2", side_effect=create), self.run_par2(0):
            result = findings.remediate(*self.args, 10, "accept")
        self.assertEqual(self.file.read_bytes(), b"damaged")
        self.assertEqual(self.parity.read_bytes(), b"old-parity")
        self.assertEqual(Path(scrub_par2.locate(*self.args)[1][0]).read_bytes(), b"new-parity")
        self.assertEqual((Path(result["recovery"]) / "parity/data.bin.par2").read_bytes(), b"old-parity")
        self.assertEqual(self.report().data["files"]["data.bin"]["resolution"], "accepted")

    def test_failed_accept_does_not_replace_original_parity(self):
        with patch.object(scrub_par2, "create_par2", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "Parity creation failed"):
                findings.remediate(*self.args, 10, "accept")
        self.assertEqual(self.parity.read_bytes(), b"old-parity")
        self.assertTrue(self.report().active("data.bin"))

    def test_symlinks_and_outside_paths_are_rejected(self):
        outside = self.root / "outside"
        outside.write_bytes(b"outside")
        link = self.source / "link"
        link.symlink_to(outside)
        for target in (link, outside):
            with self.subTest(target=target), patch.object(findings.subprocess, "run") as run:
                with self.assertRaises(ValueError):
                    findings.remediate(str(target), str(self.source), str(self.database), 10, "accept")
                run.assert_not_called()

    def test_unknown_action_and_restore_without_baseline_fail_before_mutation(self):
        with self.assertRaises(ValueError):
            findings.remediate(*self.args, 10, "delete")
        self.parity.unlink()
        with self.assertRaisesRegex(ValueError, "Existing parity"):
            findings.remediate(*self.args, 10, "restore", backup=str(self.file))

    def test_space_preflight_and_hard_links_prevent_recovery_mutation(self):
        with patch.object(findings.shutil, "disk_usage", return_value=Mock(free=0)), patch.object(findings.subprocess, "run") as run:
            with self.assertRaisesRegex(OSError, "Insufficient free space"):
                findings.remediate(*self.args, 10, "repair")
        run.assert_not_called()
        os.link(self.file, self.source / 'second-name')
        with self.assertRaisesRegex(ValueError, 'hard-linked'):
            findings.remediate(*self.args, 10, 'repair')
        self.assertEqual(self.file.read_bytes(), b'damaged')
        self.assertEqual(list(Path(self.report().root).glob('recovery-*')), [])

    def test_cleanup_preserves_missing_file_with_open_finding(self):
        self.report().record("data.bin", {"category": "unrepairable", "evidence": "damage"})
        self.file.unlink()
        scrub_par2._record_missing_files(str(self.source), str(self.database), set(), "unused")
        self.assertTrue(self.parity.exists())
        result = scrub_par2.scrub_directory(str(self.source), str(self.database), 10, "unused", suppress_notifications=True)
        self.assertTrue(result["completed"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["files_unrepairable"], ["data.bin"])

    def test_missing_unflagged_file_keeps_its_only_recovery_parity(self):
        self.file.unlink()
        with patch.object(findings.subprocess, "run") as run:
            result = scrub_par2.scrub_directory(str(self.source), str(self.database), 10, "unused",
                                                verify=False, suppress_notifications=True)
        run.assert_not_called()
        self.assertTrue(self.parity.exists())
        self.assertEqual(self.report().data["files"]["data.bin"]["category"], "missing_file")
        self.assertTrue(result["completed"])
        self.assertFalse(result["ok"])

    def test_sync_rejects_parity_destination_and_known_suspect_endpoints(self):
        config = RuntimeConfig.from_dict({"scrub_specs": [[str(self.source), str(self.database), "10%", "weekly"]]})
        unrelated = str(self.root / "other")
        with patch.object(storage_ops, "validate_mounts_for_operation", return_value=(True, "")):
            for destination in (str(self.root), str(self.database), str(self.database / "child")):
                valid, message = storage_ops.validate_sync_integrity(unrelated, destination, config)
                self.assertFalse(valid)
                self.assertIn("overlaps scrub database", message)
            alias = self.root / "alias"
            alias.symlink_to(self.database, target_is_directory=True)
            self.assertFalse(storage_ops.validate_sync_integrity(unrelated, str(alias), config)[0])
            self.assertTrue(storage_ops.validate_sync_integrity(str(self.source), unrelated, config)[0])
            self.report().record("data.bin", {"category": "unrepairable"})
            for first, second in ((str(self.source), unrelated), (unrelated, str(self.source))):
                valid, message = storage_ops.validate_sync_integrity(first, second, config)
                self.assertFalse(valid)
                self.assertIn("unresolved integrity finding", message)
            self.assertTrue(storage_ops.validate_sync_integrity(str(self.source / "unaffected"), unrelated, config)[0])
            self.report().record("data.bin", {"category": "healthy"})
            self.assertTrue(storage_ops.validate_sync_integrity(str(self.source), unrelated, config)[0])
            Path(self.report().path).write_text("broken")
            self.assertFalse(storage_ops.validate_sync_integrity(str(self.source), unrelated, config)[0])

    def test_sync_guard_is_enforced_before_rsync(self):
        config = {"sync_specs": [[str(self.source), str(self.database), "daily"]],
                  "scrub_specs": [[str(self.source), str(self.database), "10%", "weekly"]]}
        with patch.object(storage_ops, "load_setup_config", return_value=config), patch.object(storage_ops, "load_last_run", return_value={}), patch.object(storage_ops, "save_last_run"), patch.object(storage_ops, "parse_notification_args", return_value=[]), patch.object(storage_ops, "get_service_logger"), patch.object(storage_ops, "validate_mounts_for_operation", return_value=(True, "")), patch.object(storage_ops, "run_sync") as run, patch.object(storage_ops, "run_scrub", return_value=(True, "done", True)):
            result = storage_ops.execute_storage_operations()
        run.assert_not_called()
        self.assertFalse(result["success"])
        self.assertIn("overlaps scrub database", result["syncs"][0]["error"])

    def test_completed_operation_is_checkpointed_before_later_job_crashes(self):
        config = {"sync_specs": [["/source", "/destination", "daily"], ["/other", "/another", "daily"]]}
        with patch.object(storage_ops, "STATE_FILE", str(self.root / "state.json")), patch.object(storage_ops, "load_setup_config", return_value=config), patch.object(storage_ops, "parse_notification_args", return_value=[]), patch.object(storage_ops, "get_service_logger"), patch.object(storage_ops, "validate_mounts_for_operation", return_value=(True, "")), patch.object(storage_ops, "run_sync", side_effect=[(True, "done"), RuntimeError("interrupted")]):
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                storage_ops.execute_storage_operations()
            saved = storage_ops.load_last_run()
        self.assertIn("sync:/source:/destination", saved)
        self.assertNotIn("sync:/other:/another", saved)

    def test_status_and_inspection_do_not_verify_or_change_findings(self):
        self.report().record("data.bin", {"category": "unrepairable", "evidence": "bad blocks"})
        before = Path(self.report().path).read_bytes()
        config = RuntimeConfig.from_dict({"scrub_specs": [[str(self.source), str(self.database), "10%", "weekly"]]})
        with patch.object(scrub_manage, "validate_mounts_for_operation", return_value=(True, "")), patch.object(findings.subprocess, "run") as run:
            args = argparse.Namespace(action="status", directory=None, database=None, all=False)
            result, code = scrub_manage.execute(args, config)
            self.assertEqual(code, 0)
            self.assertEqual(result["jobs"][0]["files"]["data.bin"]["category"], "unrepairable")
            args.action = "inspect"
            args.file = str(self.file)
            inspected, _ = scrub_manage.execute(args, config)
            self.assertEqual(inspected["finding"]["evidence"], "bad blocks")
            run.assert_not_called()
        self.assertEqual(Path(self.report().path).read_bytes(), before)

    def test_target_json_output_and_unresolved_exit_status(self):
        config = {"scrub_specs": [[str(self.source), str(self.database), "10%", "weekly"]]}
        output = io.StringIO()
        with patch.object(scrub_manage.os, "geteuid", return_value=0), patch.object(scrub_manage, "load_setup_config", return_value=config), patch.object(scrub_manage, "OperationLock") as lock, patch.object(scrub_manage, "validate_mounts_for_operation", return_value=(True, "")), self.run_par2(2), redirect_stdout(output):
            lock.return_value.__enter__.return_value.acquire.return_value = True
            self.assertEqual(scrub_manage.main(["verify", "--file", str(self.file), "--json"]), 1)
        self.assertEqual(json.loads(output.getvalue())["category"], "unrepairable")

    def test_target_selection_and_confirmation(self):
        config = RuntimeConfig.from_dict({"scrub_specs": [[str(self.source), str(self.database), "10%", "weekly"]]})
        args = argparse.Namespace(action="repair", file=str(self.file), directory=None, database=None, yes=False)
        with patch.object(scrub_manage, "validate_mounts_for_operation", return_value=(True, "")):
            with self.assertRaisesRegex(ValueError, "confirmation"):
                scrub_manage.execute(args, config)
            config.scrub_specs.append([str(self.source), str(self.root / "other-db"), "10%", "weekly"])
            with self.assertRaisesRegex(ValueError, "multiple"):
                scrub_manage.selected_jobs(config, args)
            args.database = str(self.database)
            self.assertEqual(len(scrub_manage.selected_jobs(config, args)), 1)
        with patch.object(scrub_manage, "validate_mounts_for_operation", return_value=(False, "mount offline")):
            with self.assertRaisesRegex(ValueError, "mount offline"):
                scrub_manage.selected_jobs(config, args)

    def test_management_refuses_busy_storage_lock(self):
        lock = Mock()
        lock.acquire.return_value = False
        with patch.object(scrub_manage.os, "geteuid", return_value=0), patch.object(scrub_manage, "load_setup_config", return_value={}), patch.object(scrub_manage, "OperationLock") as factory, patch.object(scrub_manage, "execute") as execute:
            factory.return_value.__enter__.return_value = lock
            self.assertEqual(scrub_manage.main(["status"]), 1)
            execute.assert_not_called()


class TestScrubCLI(unittest.TestCase):
    def parser(self):
        from basaltwater import create_basaltwater_parser
        return create_basaltwater_parser()[0]

    def test_all_commands_parse(self):
        for action in scrub_cli.ACTIONS:
            argv = ["scrub", action, "nas"]
            if action != "status":
                argv += ["--file", "/data/file"]
            if action == "restore":
                argv += ["--from", "/backup/file"]
            self.assertEqual(self.parser().parse_args(argv).scrub_action, action)

    def test_remote_arguments_quoted_and_cached_key_used(self):
        path = "/data/file with spaces"
        args = self.parser().parse_args(["scrub", "verify", "nas", "--file", path, "--json"])
        with patch.object(scrub_cli, "load_setup_command", return_value=Mock(ssh_key="/keys/key")), patch("lib.ssh_utils.get_workspace_known_hosts_path", return_value="/tmp/known_hosts"), patch.object(scrub_cli.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)) as run:
            self.assertEqual(scrub_cli.run_scrub_command(args), 1)
        command = run.call_args.args[0]
        self.assertIn("root@nas", command)
        self.assertIn("/keys/key", command)
        remote = shlex.split(command[-1])
        self.assertEqual(remote[remote.index("--file") + 1], path)
        self.assertIn("--json", remote)

    def test_noninteractive_mutation_requires_yes(self):
        args = self.parser().parse_args(["scrub", "accept", "nas", "--file", "/data/file"])
        with patch.object(scrub_cli.sys.stdin, "isatty", return_value=False), patch.object(scrub_cli.subprocess, "run") as run:
            self.assertEqual(scrub_cli.run_scrub_command(args), 1)
        run.assert_not_called()

    def test_main_dispatches_scrub(self):
        import basaltwater
        with patch.object(basaltwater.sys, "argv", ["basaltw", "scrub", "status", "nas"]), patch.object(basaltwater, "run_scrub_command", return_value=0) as run:
            self.assertEqual(basaltwater.main(), 0)
        self.assertEqual(run.call_args.args[0].scrub_action, "status")
