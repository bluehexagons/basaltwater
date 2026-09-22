"""Tests for storage_ops runtime helpers."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, mock_open, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))

from lib.runtime_config import RuntimeConfig
from sync.service_tools.storage_ops import (
    FREQUENCY_SECONDS,
    OperationLock,
    get_parity_op_id,
    get_scrub_op_id,
    get_sync_op_id,
    is_operation_due,
    load_last_run,
    resolve_scrub_database_path,
    run_sync,
    run_scrub,
    save_last_run,
    send_operation_notification,
    validate_mounts_for_operation,
)


class TestOperationLock(unittest.TestCase):
    @patch("sync.service_tools.storage_ops.os.makedirs")
    @patch("sync.service_tools.storage_ops.fcntl.flock", side_effect=OSError("busy"))
    @patch("sync.service_tools.storage_ops.open", new_callable=mock_open)
    def test_nonblocking_acquire_failure_closes_file(self, mock_file_open, _flock, _makedirs):
        lock = OperationLock("/run/lock/storage-ops.lock")
        acquired = lock.acquire(blocking=False)
        self.assertFalse(acquired)
        mock_file_open.assert_called_once_with("/run/lock/storage-ops.lock", "a+")
        mock_file_open.return_value.close.assert_called_once()
        self.assertIsNone(lock.lock_file)

    @patch("sync.service_tools.storage_ops.os.unlink")
    @patch("sync.service_tools.storage_ops.fcntl.flock")
    def test_release_keeps_stable_lock_file(self, mock_flock, mock_unlink):
        lock = OperationLock("/run/lock/storage-ops.lock")
        lock.lock_file = MagicMock()
        lock.acquired = True

        lock.release()

        mock_flock.assert_called_once()
        lock.lock_file = None
        mock_unlink.assert_not_called()


class TestValidateMountsForOperation(unittest.TestCase):
    @patch("sync.service_tools.storage_ops.os.path.ismount", side_effect=lambda path: path == "/srv")
    @patch("sync.service_tools.storage_ops.get_mount_ancestor", return_value="/srv")
    def test_declared_mount_cannot_fall_back_to_mounted_parent(self, _ancestor, _ismount):
        for declaration in ({"storage_mounts": [["data", "/srv/data"]]}, {"smb_mounts": [["/srv/data", "host", "creds", "share"]]}):
            with self.subTest(declaration=declaration):
                config = RuntimeConfig.from_dict(declaration)
                valid, message = validate_mounts_for_operation(["/srv/data/files"], config, "sync")
                self.assertFalse(valid)
                self.assertIn("Required configured mount", message)

    @patch("sync.service_tools.storage_ops.get_mount_ancestor", return_value=None)
    @patch("sync.service_tools.storage_ops.os.path.exists", return_value=True)
    def test_rejects_missing_mount_ancestor(self, _exists, _ancestor):
        config = RuntimeConfig(username="test", sync_specs=[], scrub_specs=[], notify_specs=[])
        valid, message = validate_mounts_for_operation(["/mnt/data/source"], config, "sync")
        self.assertFalse(valid)
        self.assertIn("No mounted filesystem found", message)
    
    @patch("sync.service_tools.storage_ops.os.path.ismount", return_value=True)
    @patch("sync.service_tools.storage_ops.get_mount_ancestor", return_value="/mnt/scrap_100_1")
    @patch("sync.service_tools.storage_ops.os.path.exists")
    def test_accepts_nonexistent_destination_on_mounted_parent(self, mock_exists, mock_ancestor, mock_ismount):
        """Test that sync destinations can be created on mounted filesystems.
        
        Simulates the case where /mnt/scrap_100_1 is mounted but
        /mnt/scrap_100_1/incoming/scrap_100_2 doesn't exist yet.
        """
        def exists_side_effect(path):
            # Mount point exists, but destination subdirectory doesn't
            if path == "/mnt/scrap_100_1":
                return True
            if path == "/mnt/scrap_100_1/incoming":
                return False
            if path == "/mnt/scrap_100_1/incoming/scrap_100_2":
                return False
            return False
        
        mock_exists.side_effect = exists_side_effect
        
        config = RuntimeConfig(username="test", sync_specs=[], scrub_specs=[], notify_specs=[])
        valid, message = validate_mounts_for_operation(
            ["/mnt/scrap_100_1/incoming/scrap_100_2"], 
            config, 
            "sync"
        )
        self.assertTrue(valid, f"Should accept non-existent path on mounted filesystem: {message}")
    
    @patch("sync.service_tools.storage_ops.os.path.exists", return_value=False)
    def test_rejects_nonexistent_path_with_no_mounted_parent(self, mock_exists):
        """Test that paths with no mounted parent are rejected."""
        config = RuntimeConfig(username="test", sync_specs=[], scrub_specs=[], notify_specs=[])
        valid, message = validate_mounts_for_operation(
            ["/mnt/nonexistent/path"], 
            config, 
            "sync"
        )
        self.assertFalse(valid)
        self.assertIn("not available", message)



class TestRunScrub(unittest.TestCase):
    @patch("sync.service_tools.storage_ops.os.makedirs")
    def test_completion_is_independent_of_integrity_and_execution_failures(self, _makedirs):
        cases = [
            ({"ok": True, "completed": True}, True, True),
            ({"ok": False, "completed": True, "files_unrepairable": ["damaged.bin"]}, False, True),
            ({"ok": False, "completed": False, "files_unrepairable": ["damaged.bin"],
              "files_failed": ["unreadable.bin"]}, False, False),
            ({"ok": False, "completed": False}, False, False),
            (None, False, False),
        ]
        for result, expected_success, expected_completed in cases:
            with self.subTest(result=result), patch(
                "sync.service_tools.scrub_par2.scrub_directory", return_value=result
            ):
                success, message, completed = run_scrub("/data", "/db", "10%", True, MagicMock())
                self.assertEqual((success, completed), (expected_success, expected_completed))
                if isinstance(result, dict) and result.get("files_unrepairable"):
                    self.assertIn("damaged.bin", message)

        with patch("sync.service_tools.scrub_par2.scrub_directory", side_effect=OSError("read failed")):
            self.assertEqual(
                run_scrub("/data", "/db", "10%", True, MagicMock()),
                (False, "read failed", False),
            )

    @patch("sync.service_tools.storage_ops.os.makedirs")
    def test_invalid_redundancy_returns_failure_tuple(self, _makedirs):
        logger = MagicMock()
        success, message, completed = run_scrub("/mnt/data", "/mnt/data/.pardb", "abc%", True, logger)
        self.assertFalse(success)
        self.assertFalse(completed)
        self.assertIn("Invalid redundancy value", message)
        log_calls = [call.args[1] for call in logger.log.call_args_list]
        self.assertTrue(
            any("Starting scrub | directory='/mnt/data' mode='full verify+repair'" in msg for msg in log_calls),
            log_calls,
        )
        self.assertTrue(
            any("Invalid scrub redundancy | directory='/mnt/data' error=" in msg for msg in log_calls),
            log_calls,
        )


class TestRunSync(unittest.TestCase):
    @patch("sync.service_tools.sync_rsync.run_rsync_with_notifications", side_effect=RuntimeError("boom"))
    def test_sync_exception_logs_structured_failure(self, _mock_sync):
        logger = MagicMock()

        success, message = run_sync("/src", "/dst", logger)

        self.assertFalse(success)
        self.assertEqual(message, "boom")
        log_calls = [call.args[1] for call in logger.log.call_args_list]
        self.assertIn("Sync failed | destination='/dst' error='boom' source='/src'", log_calls)


class TestGetSyncOpId(unittest.TestCase):
    def test_basic_format(self):
        op_id = get_sync_op_id("/mnt/source", "/mnt/dest")
        self.assertEqual(op_id, "sync:/mnt/source:/mnt/dest")

    def test_different_paths_produce_different_ids(self):
        id1 = get_sync_op_id("/a", "/b")
        id2 = get_sync_op_id("/c", "/d")
        self.assertNotEqual(id1, id2)

    def test_same_paths_produce_same_ids(self):
        id1 = get_sync_op_id("/mnt/data", "/backup/data")
        id2 = get_sync_op_id("/mnt/data", "/backup/data")
        self.assertEqual(id1, id2)


class TestGetScrubOpId(unittest.TestCase):
    def test_basic_format(self):
        op_id = get_scrub_op_id("/mnt/data", ".pardatabase")
        self.assertEqual(op_id, "scrub:/mnt/data:.pardatabase")

    def test_absolute_database_path(self):
        op_id = get_scrub_op_id("/mnt/data", "/mnt/data/.pardatabase")
        self.assertEqual(op_id, "scrub:/mnt/data:/mnt/data/.pardatabase")

    def test_relative_database_preserves_raw_value(self):
        """Op IDs must use the raw spec value, not a resolved path."""
        op_id = get_scrub_op_id("/mnt/data", ".pardatabase")
        self.assertIn(".pardatabase", op_id)
        self.assertNotIn("/mnt/data/.pardatabase", op_id)

    def test_different_directories_produce_different_ids(self):
        id1 = get_scrub_op_id("/mnt/data1", ".pardb")
        id2 = get_scrub_op_id("/mnt/data2", ".pardb")
        self.assertNotEqual(id1, id2)


class TestGetParityOpId(unittest.TestCase):
    def test_basic_format(self):
        op_id = get_parity_op_id("/mnt/data", ".pardatabase")
        self.assertEqual(op_id, "parity:/mnt/data:.pardatabase")


class TestResolveScrubDatabasePath(unittest.TestCase):
    def test_relative_path_resolved_against_directory(self):
        result = resolve_scrub_database_path("/mnt/data", ".pardatabase")
        self.assertEqual(result, "/mnt/data/.pardatabase")

    def test_absolute_path_unchanged(self):
        result = resolve_scrub_database_path("/mnt/data", "/var/lib/pardb")
        self.assertEqual(result, "/var/lib/pardb")

    def test_relative_path_with_subdirectory(self):
        result = resolve_scrub_database_path("/mnt/data", "parity/db")
        self.assertEqual(result, "/mnt/data/parity/db")

    def test_normpath_applied(self):
        """Redundant separators and . components are normalized."""
        result = resolve_scrub_database_path("/mnt/data/", "./pardb")
        self.assertEqual(result, "/mnt/data/pardb")

    def test_dotdot_normalized(self):
        result = resolve_scrub_database_path("/mnt/data/subdir", "../pardb")
        self.assertEqual(result, "/mnt/data/pardb")


class TestIsOperationDue(unittest.TestCase):
    def test_never_run_is_due(self):
        self.assertTrue(is_operation_due({}, "sync:/a:/b", "hourly"))

    def test_recently_run_not_due(self):
        last_run = {"sync:/a:/b": time.time()}
        self.assertFalse(is_operation_due(last_run, "sync:/a:/b", "hourly"))

    def test_old_run_is_due(self):
        one_week_ago = time.time() - 604800 - 1
        last_run = {"sync:/a:/b": one_week_ago}
        self.assertTrue(is_operation_due(last_run, "sync:/a:/b", "daily"))

    def test_unknown_interval_defaults_to_hourly(self):
        """Unknown intervals fall back to hourly (3600s)."""
        two_hours_ago = time.time() - 7200
        last_run = {"op1": two_hours_ago}
        self.assertTrue(is_operation_due(last_run, "op1", "nonexistent_interval"))

    def test_unknown_op_id_is_due(self):
        last_run = {"sync:/a:/b": time.time()}
        self.assertTrue(is_operation_due(last_run, "sync:/c:/d", "hourly"))

    def test_invalid_or_future_timestamp_is_due(self):
        self.assertTrue(is_operation_due({"op": "invalid"}, "op", "daily"))
        self.assertTrue(is_operation_due({"op": time.time() + 3600}, "op", "daily"))

    def test_all_known_intervals(self):
        """All named intervals are recognized and have positive values."""
        for name, seconds in FREQUENCY_SECONDS.items():
            self.assertGreater(seconds, 0, f"Interval {name} should be positive")
            # Just-expired should be due
            last_run = {"op": time.time() - seconds - 1}
            self.assertTrue(
                is_operation_due(last_run, "op", name),
                f"Operation should be due after {name} interval expires"
            )
            # Just-before-expiry should not be due
            last_run = {"op": time.time() - seconds + 60}
            self.assertFalse(
                is_operation_due(last_run, "op", name),
                f"Operation should not be due before {name} interval expires"
            )


class TestLoadLastRun(unittest.TestCase):
    def test_returns_empty_dict_when_file_missing(self):
        with patch("sync.service_tools.storage_ops.STATE_FILE", "/nonexistent/path.json"):
            result = load_last_run()
        self.assertEqual(result, {})

    def test_loads_valid_json(self):
        data = {"sync:/a:/b": 1234567890.0}
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(data, f)
            f.flush()
            temp_path = f.name
        try:
            with patch("sync.service_tools.storage_ops.STATE_FILE", temp_path):
                result = load_last_run()
            self.assertEqual(result, data)
        finally:
            os.unlink(temp_path)

    def test_returns_empty_dict_on_corrupt_json(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            f.write("not valid json{{{")
            f.flush()
            temp_path = f.name
        try:
            with patch("sync.service_tools.storage_ops.STATE_FILE", temp_path):
                result = load_last_run()
            self.assertEqual(result, {})
        finally:
            os.unlink(temp_path)

    def test_returns_empty_dict_for_non_mapping_json(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump([123], f)
            temp_path = f.name
        try:
            with patch("sync.service_tools.storage_ops.STATE_FILE", temp_path):
                self.assertEqual(load_last_run(), {})
        finally:
            os.unlink(temp_path)


class TestSaveLastRun(unittest.TestCase):
    def test_saves_and_roundtrips(self):
        data = {"scrub:/data:.pardb": 1700000000.0, "sync:/a:/b": 1700001000.0}
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = os.path.join(tmpdir, "subdir", "last_run.json")
            with patch("sync.service_tools.storage_ops.STATE_FILE", state_path):
                save_last_run(data)
                # Verify the file exists and contains correct data
                with open(state_path, 'r') as f:
                    loaded = json.load(f)
                self.assertEqual(loaded, data)
                self.assertEqual(os.stat(state_path).st_mode & 0o777, 0o600)

    def test_creates_parent_directories(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = os.path.join(tmpdir, "a", "b", "c", "state.json")
            with patch("sync.service_tools.storage_ops.STATE_FILE", state_path):
                save_last_run({"op": 123.0})
            self.assertTrue(os.path.exists(state_path))
            os.unlink(state_path)


class TestOpIdStability(unittest.TestCase):
    """Verify that op_id uses the raw database path, not the resolved one.

    This is critical: if the spec stores '.pardatabase' as a relative path,
    the op_id must be 'scrub:/data:.pardatabase' not 'scrub:/data:/data/.pardatabase'.
    Otherwise last_run.json state tracking breaks (previously completed operations
    appear as never-run because their IDs changed).
    """

    @patch("sync.service_tools.storage_ops.send_operation_notification")
    @patch("sync.service_tools.storage_ops.run_scrub", return_value=(True, "OK", True))
    @patch("sync.service_tools.storage_ops.validate_mounts_for_operation", return_value=(True, ""))
    @patch("sync.service_tools.storage_ops.save_last_run")
    @patch("sync.service_tools.storage_ops.load_last_run")
    @patch("sync.service_tools.storage_ops.parse_notification_args", return_value=[])
    @patch("sync.service_tools.storage_ops.load_setup_config")
    @patch("sync.service_tools.storage_ops.get_service_logger")
    def test_scrub_op_id_uses_raw_database_path(
        self, mock_logger, mock_load_config, _notif_args,
        mock_load_last, mock_save_last, _validate, mock_run_scrub, _send_notif
    ):
        """Op IDs saved to last_run.json must use raw (unresolved) database paths."""
        from sync.service_tools.storage_ops import execute_storage_operations
        import time

        # Mock an old timestamp so the scrub is due (weekly interval = 7 days)
        old_time = time.time() - (8 * 24 * 3600)  # 8 days ago
        mock_load_last.return_value = {"scrub:/data:.pardatabase": old_time}
        
        mock_logger.return_value = MagicMock()
        mock_load_config.return_value = {
            "username": "test",
            "sync_specs": [],
            "scrub_specs": [["/data", ".pardatabase", "5%", "weekly"]],
            "notify_specs": [],
        }

        execute_storage_operations()

        # Completion is checkpointed immediately and again at run completion.
        self.assertEqual(mock_save_last.call_count, 2)
        saved_state = mock_save_last.call_args[0][0]

        # The op_id must use the raw relative database path
        expected_op_id = "scrub:/data:.pardatabase"
        self.assertIn(expected_op_id, saved_state,
                      f"Expected op_id '{expected_op_id}' in saved state, got keys: {list(saved_state.keys())}")

        # The resolved path should NOT appear as an op_id
        wrong_op_id = "scrub:/data:/data/.pardatabase"
        self.assertNotIn(wrong_op_id, saved_state,
                         "Resolved path should not be used in op_id")

        # But run_scrub must receive the resolved path
        mock_run_scrub.assert_called()
        # First call is full scrub (verify=True), second is parity update (verify=False)
        for call in mock_run_scrub.call_args_list:
            database_arg = call[0][1]  # second positional arg is database
            self.assertEqual(database_arg, "/data/.pardatabase",
                            f"run_scrub should receive resolved path, got: {database_arg}")


class TestParityCadence(unittest.TestCase):
    def test_full_scrub_is_not_followed_by_fast_parity_run(self):
        from sync.service_tools import storage_ops

        for success in (True, False):
            with self.subTest(success=success), patch.object(storage_ops, "get_service_logger"), patch.object(storage_ops, "load_setup_config", return_value={"scrub_specs": [["/data", ".db", "10%", "weekly"]]}), patch.object(storage_ops, "parse_notification_args", return_value=[]), patch.object(storage_ops, "load_last_run", return_value={"scrub:/data:.db": 1}), patch.object(storage_ops, "save_last_run") as save, patch.object(storage_ops, "validate_mounts_for_operation", return_value=(True, "")), patch.object(storage_ops, "run_scrub", return_value=(success, "result", success)) as scrub:
                result = storage_ops.execute_storage_operations()
                scrub.assert_called_once()
                self.assertTrue(scrub.call_args.kwargs["verify"])
                self.assertEqual(result["success"], success)
                self.assertEqual(result["parity_updates"], [])
                self.assertEqual("parity:/data:.db" in save.call_args.args[0], success)

    def test_damage_observes_interval_but_incomplete_scans_retry(self):
        from sync.service_tools import storage_ops

        for completed in (True, False):
            with (
                self.subTest(completed=completed),
                tempfile.TemporaryDirectory() as root,
                patch.object(storage_ops, "STATE_FILE", os.path.join(root, "last_run.json")),
                patch.object(storage_ops, "LOG_DIR", root),
                patch.object(storage_ops, "get_service_logger"),
                patch.object(storage_ops, "load_setup_config", return_value={
                    "scrub_specs": [["/data", ".db", "10%", "weekly"]],
                }),
                patch.object(storage_ops, "parse_notification_args", return_value=["cfg"]),
                patch.object(storage_ops, "validate_mounts_for_operation", return_value=(True, "")),
                patch.object(storage_ops, "send_notification_safe") as notify,
                patch.object(storage_ops.time, "time", return_value=1_000_000.0) as clock,
                patch("sync.service_tools.scrub_par2.scrub_directory", return_value={
                    "ok": False, "completed": completed,
                    "files_unrepairable": ["damaged.bin"],
                    "files_failed": [] if completed else ["unreadable.bin"],
                }) as scrub,
            ):
                storage_ops.save_last_run({"scrub:/data:.db": 1.0})
                first = storage_ops.execute_storage_operations()
                self.assertFalse(first["success"])
                self.assertEqual(notify.call_args.kwargs["status"], "error")
                self.assertIn("damaged.bin", notify.call_args.kwargs["details"])
                self.assertEqual(first["parity_updates"], [])

                clock.return_value += 3600
                second = storage_ops.execute_storage_operations()
                self.assertEqual(len(second["scrubs"]), 0 if completed else 1)
                self.assertEqual(scrub.call_count, 1 if completed else 2)
                self.assertEqual(notify.call_count, 1 if completed else 2)
                self.assertEqual(second["parity_updates"], [])
                state = storage_ops.load_last_run()
                self.assertEqual(state["scrub:/data:.db"], 1_000_000.0 if completed else 1.0)

                if completed:
                    # Daily parity maintenance continues without full verification.
                    clock.return_value = 1_000_000.0 + FREQUENCY_SECONDS["daily"]
                    scrub.return_value = {"ok": True, "completed": True}
                    daily = storage_ops.execute_storage_operations()
                    self.assertEqual(daily["scrubs"], [])
                    self.assertFalse(scrub.call_args.args[4])
                    self.assertEqual(storage_ops.load_last_run()["scrub:/data:.db"], 1_000_000.0)

                    clock.return_value = 1_000_000.0 + FREQUENCY_SECONDS["weekly"]
                    weekly = storage_ops.execute_storage_operations()
                    self.assertEqual(len(weekly["scrubs"]), 1)
                    self.assertTrue(scrub.call_args.args[4])
                    self.assertEqual(weekly["parity_updates"], [])

    @patch("sync.service_tools.storage_ops.send_operation_notification")
    @patch("sync.service_tools.storage_ops.run_scrub", return_value=(True, "OK", True))
    @patch("sync.service_tools.storage_ops.validate_mounts_for_operation", return_value=(True, ""))
    @patch("sync.service_tools.storage_ops.save_last_run")
    @patch("sync.service_tools.storage_ops.load_last_run", return_value={})
    @patch("sync.service_tools.storage_ops.parse_notification_args", return_value=[])
    @patch("sync.service_tools.storage_ops.load_setup_config")
    @patch("sync.service_tools.storage_ops.get_service_logger")
    def test_first_run_establishes_full_scrub_baseline(
        self, mock_logger, mock_load_config, _notif_args,
        _load_last, mock_save_last, _validate, mock_run_scrub, _send_notif
    ):
        from sync.service_tools.storage_ops import execute_storage_operations

        mock_logger.return_value = MagicMock()
        mock_load_config.return_value = {
            "username": "test",
            "sync_specs": [],
            "scrub_specs": [["/data", ".pardatabase", "5%", "weekly"]],
            "notify_specs": [],
        }

        execute_storage_operations()

        self.assertEqual(mock_run_scrub.call_count, 1)
        self.assertFalse(mock_run_scrub.call_args.kwargs["verify"])
        saved_state = mock_save_last.call_args.args[0]
        self.assertIn("scrub:/data:.pardatabase", saved_state)
        self.assertIn("parity:/data:.pardatabase", saved_state)

    @patch("sync.service_tools.storage_ops.send_operation_notification")
    @patch("sync.service_tools.storage_ops.run_scrub", return_value=(True, "OK", True))
    @patch("sync.service_tools.storage_ops.validate_mounts_for_operation", return_value=(True, ""))
    @patch("sync.service_tools.storage_ops.save_last_run")
    @patch("sync.service_tools.storage_ops.load_last_run")
    @patch("sync.service_tools.storage_ops.parse_notification_args", return_value=[])
    @patch("sync.service_tools.storage_ops.load_setup_config")
    @patch("sync.service_tools.storage_ops.get_service_logger")
    def test_skips_parity_update_when_last_run_is_recent(
        self, mock_logger, mock_load_config, _notif_args,
        mock_load_last, _mock_save_last, _validate, mock_run_scrub, _send_notif
    ):
        from sync.service_tools.storage_ops import execute_storage_operations

        EIGHT_DAYS_IN_SECONDS = 8 * 24 * 3600
        ONE_HOUR_IN_SECONDS = 3600
        now = time.time()
        mock_load_last.return_value = {
            "scrub:/data:.pardatabase": now - EIGHT_DAYS_IN_SECONDS,  # full scrub due
            "parity:/data:.pardatabase": now - ONE_HOUR_IN_SECONDS,  # parity not due for daily cadence
        }

        mock_logger.return_value = MagicMock()
        mock_load_config.return_value = {
            "username": "test",
            "sync_specs": [],
            "scrub_specs": [["/data", ".pardatabase", "5%", "weekly"]],
            "notify_specs": [],
        }

        execute_storage_operations()

        self.assertEqual(mock_run_scrub.call_count, 1)
        self.assertTrue(mock_run_scrub.call_args[1]["verify"])


class TestOperationLogging(unittest.TestCase):
    """Test visibility into operation scheduling and execution."""
    
    @patch("sync.service_tools.storage_ops.run_sync")
    @patch("sync.service_tools.storage_ops.validate_mounts_for_operation", return_value=(True, ""))
    @patch("sync.service_tools.storage_ops.load_last_run")
    @patch("sync.service_tools.storage_ops.save_last_run")
    @patch("sync.service_tools.storage_ops.get_service_logger")
    @patch("sync.service_tools.storage_ops.load_setup_config")
    @patch("sync.service_tools.storage_ops.parse_notification_args", return_value=[])
    def test_logs_no_operations_due_message(
        self, _parse_notify, mock_load_config, mock_logger, mock_save, mock_load_last, _validate, _run_sync
    ):
        """Test that when no operations are due, appropriate log message is shown."""
        from sync.service_tools.storage_ops import execute_storage_operations
        
        # Create a mock logger to capture log calls
        logger_mock = MagicMock()
        mock_logger.return_value = logger_mock
        
        # Set up configuration with syncs and scrubs
        mock_load_config.return_value = {
            "username": "test",
            "sync_specs": [["/src1", "/dst1", "daily"], ["/src2", "/dst2", "hourly"]],
            "scrub_specs": [["/data", ".pardatabase", "5%", "monthly"]],
            "notify_specs": [],
        }
        
        # Mock last_run to indicate all operations ran recently (not due)
        current_time = time.time()
        mock_load_last.return_value = {
            "sync:/src1:/dst1": current_time - 100,  # Ran 100s ago (daily not due)
            "sync:/src2:/dst2": current_time - 100,  # Ran 100s ago (hourly not due)
            "scrub:/data:.pardatabase": current_time - 100,  # Ran 100s ago (monthly not due)
            "parity:/data:.pardatabase": current_time - 100,  # Ran 100s ago (daily not due)
        }
        
        results = execute_storage_operations()
        
        # Verify the structured "No operations due at this time" message was logged
        log_calls = [call.args[1] for call in logger_mock.log.call_args_list]
        self.assertTrue(
            any("No operations due at this time" in msg for msg in log_calls),
            f"Expected 'No operations due at this time' log message. Got: {log_calls}"
        )
        
        # Verify the final structured summary message about skipped tasks
        self.assertTrue(
            any(
                "No operations executed - all tasks skipped | reason='not_due_or_validation_failed'" in msg
                for msg in log_calls
            ),
            f"Expected structured skipped-task message. Got: {log_calls}"
        )
        
        # Verify no operations were executed
        self.assertEqual(len(results["syncs"]), 0)
        self.assertEqual(len(results["scrubs"]), 0)
        self.assertEqual(len(results["parity_updates"]), 0)
    
    @patch("sync.service_tools.storage_ops.send_operation_notification")
    @patch("sync.service_tools.storage_ops.run_sync", return_value=(True, "Success"))
    @patch("sync.service_tools.storage_ops.validate_mounts_for_operation", return_value=(True, ""))
    @patch("sync.service_tools.storage_ops.save_last_run")
    @patch("sync.service_tools.storage_ops.load_last_run", return_value={})
    @patch("sync.service_tools.storage_ops.parse_notification_args", return_value=[])
    @patch("sync.service_tools.storage_ops.load_setup_config")
    @patch("sync.service_tools.storage_ops.get_service_logger")
    def test_logs_operations_due_summary(
        self, mock_logger, mock_load_config, _parse_notify, _load_last, mock_save, _validate, _run_sync, _send_notif
    ):
        """Test that when operations are due, appropriate summary is logged."""
        from sync.service_tools.storage_ops import execute_storage_operations
        
        # Create a mock logger to capture log calls
        logger_mock = MagicMock()
        mock_logger.return_value = logger_mock
        
        # Set up configuration with syncs (first run, should execute)
        mock_load_config.return_value = {
            "username": "test",
            "sync_specs": [["/src1", "/dst1", "daily"]],
            "scrub_specs": [],
            "notify_specs": [],
        }
        
        results = execute_storage_operations()
        
        # Verify the structured "Operations due" message was logged
        log_calls = [call.args[1] for call in logger_mock.log.call_args_list]
        self.assertTrue(
            any(
                "Operations due | parity_updates_due=0 scrubs_due=0 syncs_due=1" in msg
                for msg in log_calls
            ),
            f"Expected structured operations-due log message. Got: {log_calls}"
        )
        
        # Verify sync was executed
        self.assertEqual(len(results["syncs"]), 1)

    @patch("sync.service_tools.storage_ops.send_operation_notification")
    @patch("sync.service_tools.storage_ops.run_sync")
    @patch("sync.service_tools.storage_ops.validate_mounts_for_operation", return_value=(True, ""))
    @patch("sync.service_tools.storage_ops.save_last_run")
    @patch("sync.service_tools.storage_ops.load_last_run", return_value={})
    @patch("sync.service_tools.storage_ops.parse_notification_args", return_value=[])
    @patch("sync.service_tools.storage_ops.load_setup_config")
    @patch("sync.service_tools.storage_ops.get_service_logger")
    def test_logs_invalid_sync_spec(
        self, mock_logger, mock_load_config, _parse_notify, _load_last, _save, _validate, mock_run_sync, _send_notif
    ):
        from sync.service_tools.storage_ops import execute_storage_operations

        logger_mock = MagicMock()
        mock_logger.return_value = logger_mock
        mock_load_config.return_value = {
            "username": "test",
            "sync_specs": [["/src1", "/dst1"]],
            "scrub_specs": [],
            "notify_specs": [],
        }

        results = execute_storage_operations()

        mock_run_sync.assert_not_called()
        log_calls = [call.args[1] for call in logger_mock.log.call_args_list]
        self.assertIn("Invalid sync spec | spec=['/src1', '/dst1']", log_calls)
        self.assertEqual(results["syncs"][0]["error"], "Invalid spec")
        self.assertFalse(results["success"])

    @patch("sync.service_tools.storage_ops.send_operation_notification")
    @patch("sync.service_tools.storage_ops.run_sync")
    @patch("sync.service_tools.storage_ops.validate_mounts_for_operation", return_value=(False, "Mount /mnt/data not available"))
    @patch("sync.service_tools.storage_ops.save_last_run")
    @patch("sync.service_tools.storage_ops.load_last_run", return_value={})
    @patch("sync.service_tools.storage_ops.parse_notification_args", return_value=[])
    @patch("sync.service_tools.storage_ops.load_setup_config")
    @patch("sync.service_tools.storage_ops.get_service_logger")
    def test_logs_skipped_sync_with_context(
        self, mock_logger, mock_load_config, _parse_notify, _load_last, _save, _validate, mock_run_sync, _send_notif
    ):
        from sync.service_tools.storage_ops import execute_storage_operations

        logger_mock = MagicMock()
        mock_logger.return_value = logger_mock
        mock_load_config.return_value = {
            "username": "test",
            "sync_specs": [["/src1", "/dst1", "daily"]],
            "scrub_specs": [],
            "notify_specs": [],
        }

        results = execute_storage_operations()

        mock_run_sync.assert_not_called()
        log_calls = [call.args[1] for call in logger_mock.log.call_args_list]
        self.assertIn(
            "Skipping sync | destination='/dst1' error='Mount /mnt/data not available' source='/src1'",
            log_calls,
        )
        self.assertTrue(results["syncs"][0]["skipped"])
        self.assertFalse(results["success"])

    @patch("sync.service_tools.storage_ops.send_operation_notification")
    @patch("sync.service_tools.storage_ops.run_scrub")
    @patch("sync.service_tools.storage_ops.validate_mounts_for_operation", return_value=(False, "Mount /mnt/data not available"))
    @patch("sync.service_tools.storage_ops.save_last_run")
    @patch("sync.service_tools.storage_ops.load_last_run")
    @patch("sync.service_tools.storage_ops.parse_notification_args", return_value=[])
    @patch("sync.service_tools.storage_ops.load_setup_config")
    @patch("sync.service_tools.storage_ops.get_service_logger")
    def test_logs_skipped_scrub_and_parity_with_context(
        self, mock_logger, mock_load_config, _parse_notify, mock_load_last, _save, _validate, mock_run_scrub, _send_notif
    ):
        from sync.service_tools.storage_ops import execute_storage_operations

        logger_mock = MagicMock()
        mock_logger.return_value = logger_mock
        mock_load_last.return_value = {
            "scrub:/data:.pardatabase": time.time() - (8 * 24 * 3600),
        }
        mock_load_config.return_value = {
            "username": "test",
            "sync_specs": [],
            "scrub_specs": [["/data", ".pardatabase", "5%", "weekly"]],
            "notify_specs": [],
        }

        results = execute_storage_operations()

        mock_run_scrub.assert_not_called()
        log_calls = [call.args[1] for call in logger_mock.log.call_args_list]
        self.assertIn("Skipping scrub | directory='/data' error='Mount /mnt/data not available'", log_calls)
        self.assertIn("Skipping parity update | directory='/data' error='Mount /mnt/data not available'", log_calls)
        self.assertTrue(results["scrubs"][0]["skipped"])
        self.assertTrue(results["parity_updates"][0]["skipped"])
        self.assertFalse(results["success"])


class TestOperationNotifications(unittest.TestCase):
    @patch("sync.service_tools.storage_ops.send_notification_safe", side_effect=RuntimeError("notify boom"))
    def test_logs_structured_operation_notification_failure(self, _mock_send):
        logger = MagicMock()
        results = {
            "syncs": [],
            "scrubs": [],
            "parity_updates": [],
            "start_time": "2026-01-01T00:00:00",
            "end_time": "2026-01-01T00:01:00",
            "success": True,
        }

        send_operation_notification(results, ["cfg"], logger, friendly_name="testbox")

        log_calls = [call.args[1] for call in logger.log.call_args_list]
        self.assertIn("Failed to send operation notification | error='notify boom'", log_calls)


class TestStorageOpsMain(unittest.TestCase):
    @patch("sync.service_tools.storage_ops.send_notification_safe", side_effect=RuntimeError("notify boom"))
    @patch("sync.service_tools.storage_ops.parse_notification_args", return_value=["cfg"])
    @patch("sync.service_tools.storage_ops.load_setup_config", return_value={"notify_specs": [["mailbox", "ops@example.com"]]})
    @patch("sync.service_tools.storage_ops.get_service_logger")
    def test_logs_structured_lock_notification_failure(
        self, mock_get_logger, _load_config, _parse_notify, _send_notification
    ):
        from sync.service_tools.storage_ops import main

        logger_mock = MagicMock()
        mock_get_logger.return_value = logger_mock
        lock = MagicMock()
        lock.acquire.return_value = False
        lock.__enter__.return_value = lock
        lock.__exit__.return_value = None

        with patch("sync.service_tools.storage_ops.OperationLock", return_value=lock):
            result = main()

        self.assertEqual(result, 0)
        log_calls = [call.args[1] for call in logger_mock.log.call_args_list]
        self.assertIn("Another storage-ops instance is already running, skipping this run", log_calls)
        self.assertIn("Failed to send lock failure notification | error='notify boom'", log_calls)


if __name__ == "__main__":
    unittest.main()
