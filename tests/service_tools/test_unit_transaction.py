"""Fault injection at systemd replacement boundaries, without system mutation."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from lib import unit_transaction as units


class TestUnitTransaction(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "demo.timer"
        self.path.write_text("old unit")
        self.path.chmod(0o640)
        self.active = "active"
        self.enabled = "enabled"
        self.failure = None
        self.commands = []
        self.runner = patch.object(units, "run", side_effect=self.run_command)
        self.runner.start()
        self.addCleanup(self.runner.stop)
        self.dry = patch.object(units, "is_dry_run", return_value=False)
        self.dry.start()
        self.addCleanup(self.dry.stop)

    def run_command(self, command, **kwargs):
        self.commands.append(command)
        if command[0] == "systemd-analyze":
            self.assertEqual(self.path.read_text(), "old unit")
        if command[:2] == self.failure:
            self.failure = None
            raise subprocess.CalledProcessError(1, command)
        output = ""
        if command[:2] == ["systemctl", "show"]:
            output = f"LoadState=loaded\nActiveState={self.active}\nUnitFileState={self.enabled}\n"
        elif command[:2] == ["systemctl", "enable"]:
            self.enabled = "enabled-runtime" if "--runtime" in command else "enabled"
        elif command[:2] == ["systemctl", "disable"]:
            self.enabled = "disabled"
        elif command[:2] == ["systemctl", "restart"]:
            self.active = "active"
        elif command[:2] == ["systemctl", "stop"]:
            self.active = "inactive"
        return subprocess.CompletedProcess(command, 0, output, "")

    def replace(self):
        units.replace_units({"demo.timer": "new unit"}, activate=("demo.timer",), unit_dir=str(self.root))

    def test_success_replaces_without_deleting_or_stopping_old_unit(self):
        self.replace()
        self.assertEqual(self.path.read_text(), "new unit")
        self.assertNotIn(["systemctl", "stop", "demo.timer"], self.commands)
        self.assertFalse((self.root / ".basaltwater-unit-operation.json").exists())

    def test_command_failures_restore_content_mode_and_state(self):
        for failure in (["systemd-analyze", "verify"], ["systemctl", "daemon-reload"], ["systemctl", "enable"], ["systemctl", "restart"]):
            for active, enabled in (("active", "enabled"), ("inactive", "disabled"), ("active", "enabled-runtime")):
                with self.subTest(failure=failure, active=active, enabled=enabled):
                    self.failure = failure
                    self.active, self.enabled = active, enabled
                    with self.assertRaises(subprocess.CalledProcessError):
                        self.replace()
                    self.assertEqual(self.path.read_text(), "old unit")
                    self.assertEqual(self.path.stat().st_mode & 0o777, 0o640)
                    self.assertEqual((self.active, self.enabled), (active, enabled))

    def test_live_write_failure_after_rename_restores_old_bytes(self):
        original = units.write_text_atomic
        failed = False

        def write(path, content, **kwargs):
            nonlocal failed
            original(path, content, **kwargs)
            if path == str(self.path) and not failed:
                failed = True
                raise OSError("directory fsync failed")

        with patch.object(units, "write_text_atomic", side_effect=write), self.assertRaises(OSError):
            self.replace()
        self.assertEqual(self.path.read_text(), "old unit")

    def test_failed_rollback_retains_backup_and_blocks_retry(self):
        with patch.object(units, "_command", side_effect=lambda *args: (_ for _ in ()).throw(OSError()) if args[:2] == ("systemctl", "daemon-reload") else self.run_command(list(args)).stdout):
            with self.assertRaisesRegex(RuntimeError, "needs recovery"):
                self.replace()
        marker = json.loads((self.root / ".basaltwater-unit-operation.json").read_text())
        backup = Path(marker["context"]["backup_dir"]) / "previous.json"
        self.assertEqual(json.loads(backup.read_text())["units"]["demo.timer"]["content"], "old unit")
        with self.assertRaisesRegex(ValueError, "Unfinished"):
            self.replace()

    def test_staging_failure_does_not_change_live_unit(self):
        with patch.object(units, "write_text_atomic", side_effect=OSError("disk full")), self.assertRaises(OSError):
            self.replace()
        self.assertEqual(self.path.read_text(), "old unit")
        self.assertFalse(any(command[1] in {"enable", "restart", "daemon-reload"} for command in self.commands))

    def test_post_activation_verification_failure_restores_old_state(self):
        original = units.inspect_unit_state
        calls = 0

        def state(name):
            nonlocal calls
            calls += 1
            if calls == 2:
                return {"LoadState": "loaded", "ActiveState": "failed", "UnitFileState": "enabled"}
            return original(name)

        with patch.object(units, "inspect_unit_state", side_effect=state), self.assertRaisesRegex(RuntimeError, "verification"):
            self.replace()
        self.assertEqual(self.path.read_text(), "old unit")
        self.assertEqual((self.active, self.enabled), ("active", "enabled"))

    def test_multi_unit_write_failure_restores_pair_without_restarting_oneshot(self):
        service = self.root / "demo.service"
        service.write_text("old service")
        original = units.write_text_atomic
        failed = False

        def write(path, content, **kwargs):
            nonlocal failed
            if path == str(self.path) and not failed:
                failed = True
                raise OSError("disk full")
            original(path, content, **kwargs)

        with patch.object(units, "write_text_atomic", side_effect=write), self.assertRaises(OSError):
            units.replace_units({"demo.service": "new service", "demo.timer": "new timer"}, activate=("demo.timer",), unit_dir=str(self.root))
        self.assertEqual(service.read_text(), "old service")
        self.assertEqual(self.path.read_text(), "old unit")
        self.assertFalse(any("demo.service" in command for command in self.commands))

    def test_symlink_old_unit_is_rejected_without_following(self):
        self.path.unlink()
        self.path.symlink_to(self.root / "unrelated")
        with self.assertRaises(OSError):
            self.replace()
        self.assertFalse((self.root / "unrelated").exists())
        self.assertEqual(self.commands, [])

    def test_cleanup_failure_does_not_reject_successful_activation(self):
        with patch.object(units.shutil, "rmtree", side_effect=OSError("busy")):
            self.replace()
        self.assertEqual(self.path.read_text(), "new unit")
        self.assertFalse((self.root / ".basaltwater-unit-operation.json").exists())
        self.assertEqual(len(list(self.root.glob(".basaltwater-units-*"))), 1)

    def test_cleanup_failure_does_not_mask_validation_failure(self):
        self.failure = ["systemd-analyze", "verify"]
        with patch.object(units.shutil, "rmtree", side_effect=OSError("busy")):
            with self.assertRaises(subprocess.CalledProcessError):
                self.replace()
        self.assertEqual(self.path.read_text(), "old unit")

    def test_failed_marker_completion_retains_recovery_snapshot(self):
        for validation_failure in (False, True):
            with self.subTest(validation_failure=validation_failure):
                if validation_failure:
                    self.failure = ["systemd-analyze", "verify"]
                with patch.object(units.OperationStateStore, "complete", side_effect=OSError("result storage failed")):
                    with self.assertRaises(units.UnitRecoveryError) as error:
                        self.replace()
                self.assertIsInstance(error.exception.__cause__, OSError)
                self.assertEqual(str(error.exception.__cause__), "result storage failed")
                store = units.OperationStateStore(str(self.root / ".basaltwater-unit-operation.json"))
                record = store.load()
                snapshot = Path(record.context["backup_dir"]) / "previous.json"
                self.assertTrue(snapshot.is_file())
                self.assertEqual(json.loads(snapshot.read_text())["units"]["demo.timer"]["content"], "old unit")
                with self.assertRaisesRegex(ValueError, "Unfinished"):
                    self.replace()
                store.complete(record.operation_id, outcome="rolled_back")

    def test_failed_rollback_reload_cannot_restart_cached_new_unit(self):
        self.failure = ["systemctl", "restart"]
        reloads = 0

        def command(*args):
            nonlocal reloads
            if args == ("systemctl", "daemon-reload"):
                reloads += 1
                if reloads == 2:
                    raise OSError("restore reload failed")
            return self.run_command(list(args)).stdout

        with patch.object(units, "_command", side_effect=command):
            with self.assertRaisesRegex(RuntimeError, "needs recovery"):
                self.replace()
        self.assertEqual(self.path.read_text(), "old unit")
        self.assertEqual(self.commands.count(["systemctl", "restart", "demo.timer"]), 1)
        self.assertEqual(self.active, "inactive")

    def test_failed_file_restoration_cannot_restart_unrestored_unit(self):
        self.failure = ["systemctl", "restart"]
        original = units.write_text_atomic

        def write(path, content, **kwargs):
            if path == str(self.path) and content == "old unit":
                raise OSError("restore write failed")
            original(path, content, **kwargs)

        with patch.object(units, "write_text_atomic", side_effect=write):
            with self.assertRaisesRegex(RuntimeError, "needs recovery"):
                self.replace()
        self.assertEqual(self.path.read_text(), "new unit")
        self.assertEqual(self.commands.count(["systemctl", "restart", "demo.timer"]), 1)
        self.assertEqual(self.active, "inactive")
