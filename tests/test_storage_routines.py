"""Approved routine changes preserve guards, files and independent history."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from common.service_tools import privilege_broker
from lib.atomic_io import read_json_file, write_json_atomic
from lib.operation_state import OperationStateStore
from lib.privilege_execution import ExecutionResult
from lib.privilege_policy import MAX_MESSAGE, canonical, operation_plan
from lib import storage_controls
from lib.storage_routines import (
    MAX_ROUTINES, ROUTINE_HELPER, ROUTINE_UNIT, configured_routines,
    routine_dispatch_argv, routine_identity, validate_routine_action,
    validate_routine_snapshot,
)
from sync import routine_job, routine_management
from sync.service_tools import storage_ops
from sync.service_tools.storage_ops import run_scrub


def row(kind="sync", *, paused=False):
    spec = ["/source", "/destination", "daily"]
    if kind == "scrub":
        spec = ["/source", "/source/.parity", "10%", "weekly"]
    return {"kind": kind, "spec": spec, "paused": paused}


def action(before=None, after=None, *, run=False, revision="a" * 64, job_id="b" * 32):
    return {"action": "run" if run else "change", "before": before, "after": after,
            "revision": revision, "job_id": job_id}


def policy():
    return {"version": 1, "machine": "a" * 32, "origin": "https://host.example:9444",
            "requester_uid": 1000, "ttl_seconds": 300, "services": {},
            "reboot": "approve", "commands": "approve"}


class RoutineContractTests(unittest.TestCase):
    def test_complete_inventory_is_lexical_and_canonicalizes_relative_parity(self):
        saved = {"scrub_specs": [["/source", ".parity", "10%", "weekly"]]}
        identifier = routine_identity("scrub", saved["scrub_specs"][0])
        with patch("os.stat", side_effect=AssertionError("No NAS IO")):
            rows = configured_routines(saved, {identifier})
        self.assertEqual(rows, [row("scrub", paused=True)])
        self.assertEqual(saved["scrub_specs"][0][1], ".parity")

    def test_rejects_incomplete_invalid_overlapping_and_ambiguous_inventory(self):
        bad = [
            {"sync_specs": False}, {"sync_specs": [["/a", "/b"]]},
            {"sync_specs": [["/a", "/b", "daily"]] * (MAX_ROUTINES + 1)},
            {"sync_specs": [["/a", "/b", "daily"]], "backup_specs": [["/a", "/b", "weekly"]]},
            {"sync_specs": [["/a:/b", "/c", "daily"], ["/a", "/b:/c", "daily"]]},
            {"scrub_specs": [["/a:b", "c", "10%", "weekly"], ["/a", "b:c", "10%", "weekly"]]},
            {"scrub_specs": [["/a", "/parity", "10%", "weekly"], ["/b", "/parity/sub", "10%", "weekly"]]},
            {"scrub_specs": [["/a", "/parity", "10%", "weekly"]], "backup_specs": [["/source", "/parity/sub", "daily"]]},
            {"sync_specs": [["/a/../b", "/c", "daily"]]},
            {"scrub_specs": [["/a", "/", "10%", "weekly"]]},
        ]
        for saved in bad:
            with self.subTest(saved=saved), self.assertRaises(ValueError):
                configured_routines(saved, set())

    def test_public_actions_are_exact_and_only_job_identity_reaches_argv(self):
        value = action(after=row())
        value["after"]["spec"][:2] = ["/source/$HOME %n", "/destination/$(keep)"]
        argv = routine_dispatch_argv(value)
        self.assertEqual(argv[-2:], [ROUTINE_HELPER, value["job_id"]])
        self.assertIn("--unit=" + ROUTINE_UNIT, argv)
        self.assertNotIn(value["after"]["spec"][0], argv)
        plan = operation_plan(policy(), 1000, "data.routine", value)
        self.assertEqual(plan["mode"], "approve")
        self.assertIn("replacement and deletion", operation_plan(policy(), 1000, "data.routine", action(row(), run=True))["effect"])
        for invalid in (
            {**value, "argv": ["sh"]}, action(), action(row(), row()),
            action(row(), row("backup")), action(after=row(), run=True),
            {**value, "revision": "bad"}, {**value, "job_id": "--help"},
            action(after={**row(), "paused": 1}),
        ):
            with self.subTest(value=invalid), self.assertRaises(ValueError):
                validate_routine_action(invalid)

    def test_snapshot_must_describe_all_unique_supported_rows(self):
        snapshot = {"available": True, "revision": "a" * 64, "routines": [row()]}
        self.assertEqual(validate_routine_snapshot(snapshot), snapshot)
        self.assertEqual(validate_routine_snapshot(None), {"available": False})
        with self.assertRaises(ValueError):
            validate_routine_snapshot({**snapshot, "routines": [row(), row()]})


class RoutineStateFixture(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = directory.name
        self.setup = os.path.join(self.root, "setup.json")
        self.controls = os.path.join(self.root, "controls.json")
        self.marker = os.path.join(self.root, "setup-operation.json")
        with open(self.marker + ".lock", "xb"):
            pass
        self.lock = os.path.join(self.root, "storage.lock")
        self.result_path = os.path.join(self.root, "storage-routine.json")
        self.history = os.path.join(self.root, "last_run.json")
        self.saved = {"username": "operator", "friendly_name": "keep fresh name",
                      "notify_specs": [["mailbox", "ops@example.com"]],
                      "storage_mounts": [["data", "/source"]],
                      "sync_specs": [row()["spec"]],
                      "scrub_specs": [["/source", ".parity", "10%", "weekly"],
                                      ["/elsewhere", ".db", "5%", "monthly"]],
                      "private_unrelated": {"keep": "unchanged"}}
        write_json_atomic(self.setup, self.saved)
        self.units = Mock()
        def public_write(path, value, **kwargs):
            kwargs.pop("uid", None)
            kwargs.pop("gid", None)
            write_json_atomic(path, value, **kwargs)
        patches = (
            patch.object(routine_management, "STATE_DIR", self.root),
            patch.object(routine_management, "SETUP_CONFIG_FILE", self.setup),
            patch.object(routine_management, "CONTROLS_FILE", self.controls),
            patch.object(routine_management, "LOCK_FILE", self.lock),
            patch.object(routine_management, "STATE_FILE", self.history),
            patch.object(storage_ops, "STATE_FILE", self.history),
            patch.object(routine_management, "protected_path"),
            patch.object(routine_management, "can_manage_system_services", return_value=True),
            patch.object(routine_management, "load_setup_config", side_effect=lambda: read_json_file(self.setup)),
            patch.object(routine_management, "write_json_atomic", side_effect=public_write),
            patch.object(routine_management.shutil, "which", return_value="/usr/bin/tool"),
            patch.object(routine_management, "create_storage_ops_service", self.units),
            patch.object(storage_controls, "CONTROLS_FILE", self.controls),
            patch.object(storage_controls, "STATE_DIR", self.root),
            patch.object(storage_controls, "protected_path"),
            patch.object(routine_job, "STATE_DIR", self.root),
            patch.object(routine_job, "ROUTINE_STATE", self.result_path),
            patch.object(routine_job, "protected_path"),
            patch.object(routine_job, "can_manage_system_services", return_value=True),
        )
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
    def run_root(self, function, *args):
        original_uid = os.geteuid()
        original_fstat = os.fstat
        def owned(descriptor):
            info = original_fstat(descriptor)
            if original_uid == 0:
                return info
            fields = list(info)
            fields[4] = 0
            return os.stat_result(fields)
        with patch("os.geteuid", return_value=0), patch("os.fstat", side_effect=owned):
            return function(*args)

    def review(self, before=None, after=None, *, run=False):
        snapshot = routine_management.collect_routine_snapshot(read_json_file(self.setup))
        self.assertTrue(snapshot["available"])
        return action(before, after, run=run, revision=snapshot["revision"])


class RoutineManagementTests(RoutineStateFixture):
    def test_create_edit_pause_resume_remove_preserve_other_declarations_and_files(self):
        sentinel = os.path.join(self.root, "retained.par2")
        with open(sentinel, "w") as stream:
            stream.write("keep evidence")
        backup = {"kind": "backup", "spec": ["/another", "/mirror", "weekly"], "paused": True}
        self.assertEqual(self.run_root(routine_management.apply_routine_action, self.review(after=backup)), "succeeded")
        self.assertIn(routine_identity("backup", backup["spec"]), storage_controls.load_paused())
        saved = read_json_file(self.setup)
        self.assertEqual(saved["scrub_specs"], self.saved["scrub_specs"])
        self.assertEqual(saved["private_unrelated"], self.saved["private_unrelated"])
        edited = {**backup, "spec": ["/another", "/new-mirror", "daily"]}
        self.run_root(routine_management.apply_routine_action, self.review(backup, edited))
        self.assertNotIn(routine_identity("backup", backup["spec"]), storage_controls.load_paused())
        self.assertIn(routine_identity("backup", edited["spec"]), storage_controls.load_paused())
        resumed = {**edited, "paused": False}
        self.run_root(routine_management.apply_routine_action, self.review(edited, resumed))
        self.assertEqual(storage_controls.load_paused(), set())
        self.run_root(routine_management.apply_routine_action, self.review(resumed, edited))
        self.run_root(routine_management.apply_routine_action, self.review(edited, None))
        saved = read_json_file(self.setup)
        self.assertIsNone(saved["backup_specs"])
        self.assertEqual(saved["scrub_specs"], self.saved["scrub_specs"])
        self.assertEqual(saved["notify_specs"], self.saved["notify_specs"])
        self.assertEqual(storage_controls.load_paused(), set())
        with open(sentinel) as stream:
            self.assertEqual(stream.read(), "keep evidence")
        self.assertEqual(self.units.call_count, 5)
        self.assertFalse(os.path.exists(self.marker))
        self.assertEqual(os.stat(self.controls).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(self.setup).st_mode & 0o777, 0o600)

    def test_pause_keeps_raw_relative_cadence_keys_and_never_runs_workers(self):
        current = row("scrub")
        with patch.object(routine_management, "execute_storage_operations") as execute:
            self.run_root(routine_management.apply_routine_action, self.review(current, {**current, "paused": True}))
        execute.assert_not_called()
        self.assertEqual(read_json_file(self.setup), self.saved)
        self.assertEqual(storage_controls.load_paused(), {routine_identity("scrub", current["spec"])})

    def test_stale_configuration_pause_or_before_row_cannot_mutate(self):
        for changed in ("setup", "controls", "before"):
            value = self.review(row(), {**row(), "paused": True})
            if changed == "setup":
                write_json_atomic(self.setup, {**read_json_file(self.setup), "friendly_name": "new"})
            elif changed == "controls":
                write_json_atomic(self.controls, {"version": 1, "paused": []})
            else:
                value["before"]["spec"][-1] = "weekly"
            before = read_json_file(self.setup)
            with self.subTest(changed=changed), self.assertRaisesRegex(ValueError, "review_changed"):
                self.run_root(routine_management.apply_routine_action, value)
            self.assertEqual(read_json_file(self.setup), before)
            self.assertFalse(os.path.exists(self.marker))
        self.units.assert_not_called()

    def test_shared_setup_and_storage_owners_refuse_without_changes(self):
        value = self.review(row(), {**row(), "paused": True})
        with OperationStateStore(self.marker).ownership():
            with self.assertRaisesRegex(ValueError, "owned"):
                self.run_root(routine_management.apply_routine_action, value)
        with storage_ops.OperationLock(self.lock) as lock:
            self.assertTrue(lock.acquire())
            with self.assertRaisesRegex(ValueError, "storage_busy"):
                self.run_root(routine_management.apply_routine_action, value)
        self.assertEqual(read_json_file(self.setup), self.saved)
        self.units.assert_not_called()

    def test_incomplete_save_keeps_recovery_guard_and_blocks_automatic_work(self):
        self.units.side_effect = RuntimeError("unit activation failed")
        value = self.review(row(), {**row(), "paused": True})
        with self.assertRaises(RuntimeError):
            self.run_root(routine_management.apply_routine_action, value)
        self.assertEqual(OperationStateStore(self.marker).load().status, "recovery_required")
        with self.assertRaisesRegex(ValueError, "recovery"):
            storage_controls.require_no_setup_recovery()
        self.assertFalse(routine_management.collect_routine_snapshot(read_json_file(self.setup))["available"])
        with self.assertRaisesRegex(ValueError, "setup_recovery_required"):
            self.run_root(routine_management.apply_routine_action, value)

    def test_missing_tools_refuse_new_active_routine_but_allow_pausing(self):
        backup = {"kind": "backup", "spec": ["/other", "/mirror", "daily"], "paused": False}
        with patch.object(routine_management.shutil, "which", return_value=None):
            with self.assertRaisesRegex(ValueError, "tools_unavailable"):
                self.run_root(routine_management.apply_routine_action, self.review(after=backup))
            self.assertFalse(os.path.exists(self.marker))
            self.run_root(routine_management.apply_routine_action, self.review(after={**backup, "paused": True}))
            self.run_root(routine_management.apply_routine_action, self.review(row(), {**row(), "paused": True}))
            with patch.object(routine_management, "execute_storage_operations") as execute:
                with self.assertRaisesRegex(ValueError, "tools_unavailable"):
                    self.run_root(routine_management.apply_routine_action, self.review(row(paused=True), run=True))
                execute.assert_not_called()

    def test_selected_run_reports_findings_warnings_and_incomplete_failure(self):
        for kind, success, completed, warning, expected in (
            ("scrub", False, True, None, "findings"),
            ("sync", True, True, "retained excluded files", "warnings"),
            ("sync", False, False, None, "routine_failed"),
        ):
            result = {"success": success, "syncs": [], "scrubs": [], "parity_updates": []}
            result["scrubs" if kind == "scrub" else "syncs"].append({"completed": completed, "warning": warning})
            value = self.review(row(kind), run=True)
            with self.subTest(kind=kind, expected=expected), patch.object(routine_management, "execute_storage_operations", return_value=result) as execute:
                if expected == "routine_failed":
                    with self.assertRaisesRegex(ValueError, expected):
                        self.run_root(routine_management.apply_routine_action, value)
                else:
                    self.assertEqual(self.run_root(routine_management.apply_routine_action, value), expected)
                execute.assert_called_once_with(selection=value["before"])
        self.units.assert_not_called()

    def test_pause_file_is_strict_and_corruption_never_resumes_work(self):
        invalid = [None, {}, {"version": True, "paused": []}, {"version": 1, "paused": ["bad"]},
                   {"version": 1, "paused": ["a" * 64] * 2}, {"version": 1, "paused": [], "extra": 1}]
        for value in invalid:
            write_json_atomic(self.controls, value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                storage_controls.load_paused()
            self.assertFalse(routine_management.collect_routine_snapshot(self.saved)["available"])
        os.unlink(self.controls)
        os.symlink(self.setup, self.controls)
        with self.assertRaises(OSError):
            storage_controls.load_paused()

    def test_exported_history_is_scoped_and_invalid_history_does_not_hide_configuration(self):
        state = {"sync:/source:/destination": 123.0, "scrub:/source:.parity": 456.0,
                 "parity:/source:.parity": 789.0, "sync:/private:/unconfigured": 12.0,
                 "_attempts": {"scrub:/source:.parity": {"failed_at": 800.0, "retry_after": 4400.0, "failures": 1,
                                                         "private": "PRIVATE_TOOL_OUTPUT"}}}
        write_json_atomic(self.history, state)
        snapshot = routine_management.collect_routine_snapshot(self.saved)
        self.assertTrue(snapshot["history"]["available"])
        exported = snapshot["history"]["routines"]
        scrub = exported[routine_identity("scrub", row("scrub")["spec"])]
        self.assertEqual(scrub["cadence_at"], 456.0)
        self.assertEqual(scrub["parity_at"], 789.0)
        self.assertEqual(scrub["retry"]["failures"], 1)
        self.assertNotIn("private", json.dumps(snapshot))
        self.assertNotIn("PRIVATE_TOOL_OUTPUT", json.dumps(snapshot))
        self.assertEqual(validate_routine_snapshot(snapshot), snapshot)
        malformed = {**snapshot, "history": {"available": True, "routines": {}}}
        self.assertFalse(validate_routine_snapshot(malformed)["history"]["available"])
        with open(self.history, "w") as stream:
            stream.write("{broken")
        snapshot = routine_management.collect_routine_snapshot(self.saved)
        self.assertTrue(snapshot["available"])
        self.assertFalse(snapshot["history"]["available"])

    def test_unreadable_guard_or_controls_never_become_absence(self):
        with patch.object(storage_controls.os, "lstat", side_effect=PermissionError("unreadable")):
            with self.assertRaises(PermissionError):
                storage_controls.require_no_setup_recovery()
            with self.assertRaises(PermissionError):
                storage_controls.load_paused()


class SelectedRuntimeTests(RoutineStateFixture):
    def setUp(self):
        super().setUp()
        self.sync = Mock(return_value=(True, "OK"))
        self.scrub = Mock(return_value=(True, "OK", True))
        patches = (
            patch.object(storage_ops, "get_service_logger"),
            patch.object(storage_ops, "load_setup_config", side_effect=lambda: read_json_file(self.setup)),
            patch.object(storage_ops, "parse_notification_args", return_value=[]),
            patch.object(storage_ops, "run_sync", self.sync),
            patch.object(storage_ops, "run_scrub", self.scrub),
            patch.object(storage_ops.time, "time", return_value=1_000_000.0),
        )
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def test_paused_scrubs_skip_full_and_parity_but_retain_mirror_guards(self):
        paused = [routine_identity("scrub", spec) for spec in self.saved["scrub_specs"]]
        write_json_atomic(self.controls, {"version": 1, "paused": paused})
        result = storage_ops.execute_storage_operations()
        self.assertTrue(result["success"])
        self.sync.assert_called_once()
        self.scrub.assert_not_called()
        self.assertEqual(self.sync.call_args.kwargs["config"].scrub_specs, self.saved["scrub_specs"])
        self.assertFalse(any(key.startswith(("scrub:", "parity:")) for key in read_json_file(self.history)))

    def test_explicit_paused_scrub_ignores_cadence_retry_and_keeps_unrelated_history(self):
        identifier = routine_identity("scrub", row("scrub")["spec"])
        write_json_atomic(self.controls, {"version": 1, "paused": [identifier]})
        history = {"scrub:/source:.parity": 999_999.0, "sync:/else:/other": 123.0,
                   "_attempts": {"scrub:/source:.parity": {"failed_at": 999_999.0, "retry_after": 1_020_000.0, "failures": 4}}}
        write_json_atomic(self.history, history)
        result = storage_ops.execute_storage_operations(selection=row("scrub", paused=True))
        self.assertTrue(result["success"])
        self.sync.assert_not_called()
        self.scrub.assert_called_once()
        self.assertTrue(self.scrub.call_args.kwargs["verify"])
        self.assertEqual(self.scrub.call_args.args[:2], ("/source", "/source/.parity"))
        saved = read_json_file(self.history)
        self.assertEqual(saved["scrub:/source:.parity"], 1_000_000.0)
        self.assertEqual(saved["sync:/else:/other"], 123.0)
        self.assertNotIn("scrub:/elsewhere:.db", saved)
        self.assertNotIn("scrub:/source:.parity", saved.get("_attempts", {}))
        self.assertEqual(storage_controls.load_paused(), {identifier})

    def test_selected_mirror_preserves_all_scrub_guards_and_updates_only_its_history(self):
        write_json_atomic(self.history, {"sync:/source:/destination": 999_999.0, "scrub:/elsewhere:.db": 42.0})
        result = storage_ops.execute_storage_operations(selection=row())
        self.assertTrue(result["success"])
        self.sync.assert_called_once()
        self.scrub.assert_not_called()
        self.assertEqual(self.sync.call_args.kwargs["config"].scrub_specs, self.saved["scrub_specs"])
        self.assertEqual(read_json_file(self.history)["scrub:/elsewhere:.db"], 42.0)

    def test_stale_selection_or_invalid_controls_or_recovery_guard_stop_before_effects(self):
        with self.subTest(reason="selection"):
            self.assertFalse(storage_ops.execute_storage_operations(selection=row(paused=True))["success"])
        write_json_atomic(self.controls, {"version": 1, "paused": "bad"})
        with self.subTest(reason="controls"):
            self.assertFalse(storage_ops.execute_storage_operations()["success"])
        os.unlink(self.controls)
        with OperationStateStore(self.marker).ownership() as owner:
            owner.begin("storage_routines", "localhost", "saving")
        with self.subTest(reason="recovery"):
            self.assertFalse(storage_ops.execute_storage_operations()["success"])
        self.sync.assert_not_called()
        self.scrub.assert_not_called()
        self.assertFalse(os.path.exists(self.history))

    def test_selected_incomplete_scrub_records_retry_without_fast_parity_fallback(self):
        self.scrub.return_value = (False, "mount unavailable", False)
        result = storage_ops.execute_storage_operations(selection=row("scrub"))
        self.assertFalse(result["success"])
        self.scrub.assert_called_once()
        history = read_json_file(self.history)
        self.assertEqual(history["_attempts"]["scrub:/source:.parity"]["failures"], 1)
        self.assertNotIn("parity:/source:.parity", history)

    def test_repaired_scrub_has_warning_in_result_and_notification(self):
        # Use the real report adapter rather than inventing a warning in the
        # orchestrator mock; the supervised worker count is the source of truth.
        report = {"ok": True, "completed": True, "files_repaired": 1}
        with patch.object(storage_ops, "run_scrub", run_scrub), patch.object(storage_ops, "run_scrub_supervised", return_value=report):
            result = storage_ops.execute_storage_operations(selection=row("scrub"))
        self.assertTrue(result["success"])
        self.assertIn("Repaired 1", result["scrubs"][0]["warning"])
        with patch.object(storage_ops, "send_notification_safe") as send:
            storage_ops.send_operation_notification(result, ["cfg"], Mock())
        self.assertEqual(send.call_args.kwargs["status"], "warning")


class RoutineWorkerTests(RoutineStateFixture):
    def test_private_payload_is_bounded_one_use_and_never_enters_argv(self):
        value = self.review(row(), run=True)
        routine_job.stage_routine_job(value)
        path = routine_job._payload(value["job_id"])
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        with self.assertRaises(ValueError):
            routine_job.stage_routine_job(value)
        self.assertEqual(routine_job.consume_routine_job(value["job_id"]), value)
        with self.assertRaises(OSError):
            routine_job.consume_routine_job(value["job_id"])

    def test_worker_records_correlated_completion_and_refuses_replay(self):
        value = self.review(row(), run=True)
        with patch.object(routine_job, "apply_routine_action", return_value="findings") as execute:
            self.assertEqual(self.run_root(routine_job.run_action, value), 1)
            with self.assertRaises(ValueError):
                self.run_root(routine_job.run_action, value)
        execute.assert_called_once()
        result = read_json_file(self.result_path)
        self.assertEqual(result["job_id"], value["job_id"])
        self.assertEqual(result["status"], "findings")
        self.assertIsNotNone(result["finished"])
        self.assertNotIn("spec", result)

    def test_unfinished_worker_is_reported_interrupted_and_errors_are_fixed(self):
        value = self.review(row(), run=True)
        with patch.object(routine_job, "apply_routine_action", side_effect=ValueError("private tool error")):
            self.assertEqual(self.run_root(routine_job.run_action, value), 1)
        record = read_json_file(self.result_path)
        self.assertEqual(record["failure"], "execution_rejected")
        record.update(status="running", failure="", finished=None)
        write_json_atomic(self.result_path, record)
        with patch.object(routine_job, "_unit_state", return_value="inactive"):
            status = routine_job.job_status()
        self.assertEqual(status["latest"]["status"], "interrupted")
        self.assertNotIn("private", json.dumps(status))


class RoutineBrokerTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.runner = Mock(return_value=ExecutionResult("dispatched", exit_code=0))
        self.broker = privilege_broker.Broker(os.path.join(directory.name, "state.db"), policy, self.runner)
        self.addCleanup(self.broker.db.close)

    def request(self, value=None):
        return self.broker.request(1000, "data.routine", value or action(after=row()), "Reviewed routine change")

    def test_pending_denied_cancelled_never_execute_then_approval_claims_once(self):
        request = self.request()
        self.assertEqual(request["state"], "pending")
        self.assertFalse(self.broker.execute_next())
        self.broker.decide(request["id"], request["digest"], False, "operator")
        self.assertFalse(self.broker.execute_next())
        request = self.request()
        self.broker.cancel(request["id"], 1000)
        self.assertFalse(self.broker.execute_next())
        self.runner.assert_not_called()
        request = self.request()
        self.broker.decide(request["id"], request["digest"], True, "operator")
        self.assertTrue(self.broker.execute_next())
        self.assertFalse(self.broker.execute_next())
        self.runner.assert_called_once_with(request["plan"])
        self.assertEqual(self.broker.status(request["id"])["state"], "dispatched")

    def test_outstanding_data_request_blocks_other_data_action_and_status_is_owned(self):
        self.request()
        recovery = {"action": "verify", "directory": "/source", "database": "/source/.db", "file": "/source/file",
                    "backup": "", "finding": "a" * 64, "job_id": "c" * 32}
        with self.assertRaisesRegex(ValueError, "outstanding"):
            self.broker.request(1000, "data.run", recovery, "review")
        with patch.object(privilege_broker, "routine_job_status", return_value={"available": True, "latest": {}}), patch.object(privilege_broker, "data_job_status", return_value={"unit_state": "active"}), patch.object(privilege_broker, "protected_path"):
            status = self.broker.dispatch({"action": "routine-status"}, 1000)
            self.assertTrue(status["outstanding"])
            self.assertTrue(status["other_active"])
            self.assertEqual(status["requests"][0]["parameters"], action(after=row()))
            with self.assertRaises(PermissionError):
                self.broker.dispatch({"action": "routine-status"}, 1001)
            with self.assertRaises(PermissionError):
                self.broker.dispatch({"action": "routine-status"}, 1000, approval=True)

    def test_maximum_unicode_review_and_trimmed_owned_history_fit_transport(self):
        before = row()
        before["spec"] = ["/s/" + "界" * 509, "/d/" + "語" * 509, "daily"]
        value = action(before, {**before, "paused": True})
        for _ in range(10):
            request = self.request(value)
            self.assertLessEqual(len(canonical({"ok": True, "result": request}).encode()), MAX_MESSAGE)
            self.broker.cancel(request["id"], 1000)
        with patch.object(privilege_broker, "routine_job_status", return_value={"available": True, "latest": {}}), patch.object(privilege_broker, "data_job_status", return_value={}), patch.object(privilege_broker, "protected_path"):
            status = self.broker.dispatch({"action": "routine-status"}, 1000)
        self.assertTrue(status["requests"])
        self.assertLess(len(status["requests"]), 10)
        self.assertLessEqual(len(canonical({"ok": True, "result": status}).encode()), MAX_MESSAGE)

    def test_dispatch_stages_only_reviewed_parameters_and_reports_dispatch(self):
        plan = operation_plan(policy(), 1000, "data.routine", action(after=row()))
        with (
            patch.object(privilege_broker, "protected_path"),
            patch.object(privilege_broker, "routine_job_status", return_value={"available": True, "blocked": False, "unit_state": "inactive"}),
            patch.object(privilege_broker, "data_job_status", return_value={"unit_state": "inactive"}),
            patch.object(privilege_broker, "stage_routine_job") as stage,
            patch.object(privilege_broker.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as run,
        ):
            self.assertEqual(privilege_broker.execute(plan).state, "dispatched")
            stage.assert_called_once_with(plan["parameters"])
            self.assertEqual(run.call_args.args[0], plan["argv"])
        for status in ({"available": False}, {"available": True, "blocked": True},
                       {"available": True, "blocked": False, "unit_state": "active"}):
            with patch.object(privilege_broker, "protected_path"), patch.object(privilege_broker, "routine_job_status", return_value=status), patch.object(privilege_broker, "stage_routine_job") as stage:
                self.assertEqual(privilege_broker.execute(plan).state, "failed")
                stage.assert_not_called()
