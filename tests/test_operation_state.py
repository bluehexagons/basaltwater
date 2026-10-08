"""Tests for versioned durable operation markers."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
import subprocess
import sys
from unittest.mock import patch

from lib.operation_state import OperationStateError, OperationStateStore


class TestOperationStateStore(unittest.TestCase):
    def test_failed_completion_fsync_restores_marker_and_ownership(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "operation.json")
            store = OperationStateStore(path)
            self.addCleanup(store.close)
            record = store.begin("setup", "host", "finalizing")

            def remove(target):
                os.unlink(target)
                raise OSError("directory fsync failed")

            with patch("lib.operation_state.remove_file_durable", side_effect=remove):
                with self.assertRaisesRegex(OSError, "fsync failed"):
                    store.complete(record.operation_id)
            self.assertEqual(store.load(), record)
            other = OperationStateStore(path)
            with self.assertRaisesRegex(OperationStateError, "another process"):
                other.complete(record.operation_id)
            store.complete(record.operation_id)

    def test_invalid_marker_types_and_encoding_raise_domain_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'operation.json')
            for content in (b'{"schema_version":true}', b'{"schema_version":1,"status":[]}', b'\xff', b' ' * (1024 * 1024 + 1)):
                with self.subTest(content=content[:80]):
                    with open(path, 'wb') as stream:
                        stream.write(content)
                    with self.assertRaises(OperationStateError):
                        OperationStateStore(path).load()

    def test_live_owner_blocks_second_process_and_recovery_can_take_over(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'operation.json')
            store = OperationStateStore(path)
            record = store.begin('setup', 'host', 'applying')
            script = (
                'from lib.operation_state import OperationStateStore, OperationStateError\n'
                'import sys\n'
                'store = OperationStateStore(sys.argv[1])\n'
                'try:\n'
                '    store.complete(sys.argv[2])\n'
                'except OperationStateError:\n'
                '    sys.exit(7)\n'
            )
            command = [sys.executable, '-c', script, path, record.operation_id]
            blocked = subprocess.run(command, capture_output=True, timeout=10)
            self.assertEqual(blocked.returncode, 7, blocked.stderr)
            self.assertEqual(store.load(), record)
            store.close()
            recovered = subprocess.run(command, capture_output=True, timeout=10)
            self.assertEqual(recovered.returncode, 0, recovered.stderr)
            self.assertIsNone(store.load())

    def test_lifecycle_is_persistent_and_completed_marker_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "operation.json")
            store = OperationStateStore(path)
            started = store.begin(
                "manifest_deploy",
                "/var/www/example",
                "preparing",
                context={"release": "previous"},
            )

            self.assertEqual(store.load(), started)
            activating = store.transition(started.operation_id, "activating")
            self.assertEqual(activating.phase, "activating")
            self.assertEqual(activating.context, {"release": "previous"})
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)

            store.complete(started.operation_id)
            self.assertIsNone(store.load())
            with open(path + ".last.json") as stream:
                result = json.load(stream)
            self.assertEqual(result["outcome"], "succeeded")
            self.assertEqual(result["operation"]["operation_id"], started.operation_id)
            self.assertEqual(result["operation"]["phases"], ["preparing", "activating"])
            self.assertEqual(os.stat(path + ".last.json").st_mode & 0o777, 0o600)

    def test_existing_marker_blocks_new_operation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = OperationStateStore(os.path.join(tmpdir, "operation.json"))
            started = store.begin("setup", "host", "applying")

            with self.assertRaisesRegex(OperationStateError, started.operation_id):
                store.begin("setup", "host", "applying")

    def test_recovery_required_state_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = OperationStateStore(os.path.join(tmpdir, "operation.json"))
            started = store.begin("manifest_deploy", "app", "activating")

            failed = store.transition(
                started.operation_id,
                "recovery",
                status="recovery_required",
                context={"reason": "service restart failed"},
            )

            self.assertEqual(store.load(), failed)
            self.assertEqual(failed.status, "recovery_required")

    def test_corrupt_marker_names_path_and_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "operation.json")
            with open(path, "w", encoding="utf-8") as file_obj:
                file_obj.write("{broken")
            store = OperationStateStore(path)

            with self.assertRaisesRegex(OperationStateError, path):
                store.begin("setup", "host", "applying")

            with open(path, encoding="utf-8") as file_obj:
                self.assertEqual(file_obj.read(), "{broken")

    def test_unsupported_schema_has_remediation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "operation.json")
            with open(path, "w", encoding="utf-8") as file_obj:
                json.dump({"schema_version": 99}, file_obj)

            with self.assertRaisesRegex(OperationStateError, "verified recovery"):
                OperationStateStore(path).load()

    def test_symlink_marker_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            target = os.path.join(tmpdir, "target.json")
            marker = os.path.join(tmpdir, "operation.json")
            with open(target, "w", encoding="utf-8") as file_obj:
                file_obj.write("{}")
            os.symlink(target, marker)

            with self.assertRaisesRegex(OperationStateError, "must not be a symlink"):
                OperationStateStore(marker).load()

    def test_stale_operation_id_cannot_update_or_remove_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = OperationStateStore(os.path.join(tmpdir, "operation.json"))
            store.begin("setup", "host", "applying")

            for action in (
                lambda: store.transition("wrong-id", "verifying"),
                lambda: store.complete("wrong-id"),
            ):
                with self.subTest(action=action):
                    with self.assertRaisesRegex(OperationStateError, "wrong-id"):
                        action()

    def test_rejects_empty_labels_and_unknown_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = OperationStateStore(os.path.join(tmpdir, "operation.json"))
            with self.assertRaisesRegex(ValueError, "non-empty"):
                store.begin("", "host", "applying")
            started = store.begin("setup", "host", "applying")
            with self.assertRaisesRegex(ValueError, "Unsupported operation status"):
                store.transition(started.operation_id, "failed", status="failed")

    def test_invalid_marker_fields_preserve_evidence_and_do_not_echo_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "operation.json")
            store = OperationStateStore(path)
            record = store.begin("setup", "host", "applying").to_dict()
            store.close()
            for key, value in (
                ("schema_version", "private-data"), ("status", {"secret": "private-data"}),
                ("phase", "applying\nprivate-data"), ("operation_type", "\x1bprivate-data"),
                ("phases", ["\nprivate-data"]), ("phases", ["applying"] * 257),
            ):
                with self.subTest(key=key, value=value):
                    original = json.dumps({**record, key: value})
                    with open(path, "w", encoding="utf-8") as stream:
                        stream.write(original)
                    with self.assertRaises(OperationStateError) as error:
                        store.load()
                    self.assertNotIn("private-data", str(error.exception))
                    self.assertIn("verified recovery", str(error.exception))
                    with open(path, encoding="utf-8") as stream:
                        self.assertEqual(stream.read(), original)


if __name__ == "__main__":
    unittest.main()
