"""Fault injection at systemd transaction boundaries, without system mutation."""

from __future__ import annotations

import json
import os
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

    def test_failed_oneshot_restoration_keeps_its_timer_stopped(self):
        service = self.root / "demo.service"
        service.write_text("old service")
        self.failure = ["systemctl", "restart"]
        original = units.write_text_atomic

        def write(path, content, **kwargs):
            if path == str(service) and content == "old service":
                raise OSError("service restoration failed")
            original(path, content, **kwargs)

        with patch.object(units, "write_text_atomic", side_effect=write):
            with self.assertRaises(units.UnitRecoveryError):
                units.replace_units(
                    {"demo.service": "new service", "demo.timer": "new timer"},
                    activate=("demo.timer",), unit_dir=str(self.root),
                )
        self.assertEqual(self.path.read_text(), "old unit")
        self.assertEqual(service.read_text(), "new service")
        self.assertEqual(self.commands.count(["systemctl", "restart", "demo.timer"]), 1)
        self.assertEqual(self.active, "inactive")


class TestUnitRemoval(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.names = ("demo.timer", "demo.path", "demo.service")
        self.previous = {
            "demo.timer": ("active", "enabled-runtime"),
            "demo.path": ("inactive", "disabled"),
            "demo.service": ("active", "static"),
        }
        self.states = {name: list(state) for name, state in self.previous.items()}
        for name in self.names:
            (self.root / name).write_text(f"old {name}")
            (self.root / name).chmod(0o640)
        self.commands = []
        self.failure = None
        runner = patch.object(units, "run", side_effect=self.run_command)
        runner.start()
        self.addCleanup(runner.stop)
        dry = patch.object(units, "is_dry_run", return_value=False)
        dry.start()
        self.addCleanup(dry.stop)

    def run_command(self, command, **kwargs):
        self.commands.append(command)
        if command == self.failure:
            self.failure = None
            raise subprocess.CalledProcessError(1, command)
        output = ""
        if command[0] == "systemctl" and command[1] != "daemon-reload":
            name = command[2] if command[1] == "show" else command[-1]
            active, enabled = self.states[name]
            if command[1] == "show":
                present = (self.root / name).exists()
                load = "loaded" if present else "not-found"
                output = f"LoadState={load}\nActiveState={active}\nUnitFileState={enabled if present else ''}\n"
            elif command[1] == "stop":
                self.states[name][0] = "inactive"
            elif command[1] == "restart":
                self.states[name][0] = "active"
            elif command[1] == "enable":
                self.states[name][1] = "enabled-runtime" if "--runtime" in command else "enabled"
            elif command[1] == "disable":
                self.states[name][1] = "disabled"
        return subprocess.CompletedProcess(command, 0, output, "")

    def remove(self):
        units.remove_units(self.names, unit_dir=str(self.root))

    def assert_restored(self):
        for name in self.names:
            self.assertEqual((self.root / name).read_text(), f"old {name}")
            self.assertEqual((self.root / name).stat().st_mode & 0o777, 0o640)
            self.assertEqual(tuple(self.states[name]), self.previous[name])
        self.assertFalse((self.root / ".basaltwater-unit-operation.json").exists())

    def test_removal_stops_group_before_deleting_and_skips_static_disable(self):
        original = units.remove_file_durable

        def remove(path):
            if path == str(self.root / self.names[0]):
                self.assertTrue(all(state[0] == "inactive" for state in self.states.values()))
                self.assertTrue(all((self.root / name).exists() for name in self.names))
            original(path)

        with patch.object(units, "remove_file_durable", side_effect=remove):
            self.remove()
        self.assertTrue(all(not (self.root / name).exists() for name in self.names))
        self.assertIn(["systemctl", "disable", "--runtime", "demo.timer"], self.commands)
        self.assertFalse(any(command[1] == "disable" and command[-1] in {"demo.service", "demo.path"} for command in self.commands))
        result = json.loads((self.root / ".basaltwater-unit-operation.json.last.json").read_text())
        self.assertEqual(result["outcome"], "succeeded")
        self.assertEqual(result["operation"]["operation_type"], "unit-removal")
        self.assertIn("removing", result["operation"]["phases"])

    def test_stop_disable_and_reload_failures_restore_entire_group(self):
        for failure in (
            ["systemctl", "stop", "demo.service"],
            ["systemctl", "disable", "--runtime", "demo.timer"],
            ["systemctl", "daemon-reload"],
        ):
            with self.subTest(failure=failure):
                self.commands.clear()
                self.failure = failure
                with self.assertRaises(subprocess.CalledProcessError):
                    self.remove()
                self.assert_restored()
                if failure[1] != "disable":
                    self.assertLess(
                        self.commands.index(["systemctl", "restart", "demo.service"]),
                        self.commands.index(["systemctl", "restart", "demo.timer"]),
                    )

    def test_durable_unlink_error_after_deletion_restores_group(self):
        original = units.remove_file_durable

        def remove(path):
            original(path)
            if path == str(self.root / "demo.path"):
                raise OSError("directory sync failed after unlink")

        with patch.object(units, "remove_file_durable", side_effect=remove):
            with self.assertRaisesRegex(OSError, "sync failed"):
                self.remove()
        self.assert_restored()

    def test_stop_timeout_after_effect_restores_running_state(self):
        original = self.run_command
        timed_out = False

        def command(args, **kwargs):
            nonlocal timed_out
            result = original(args, **kwargs)
            if args == ["systemctl", "stop", "demo.service"] and not timed_out:
                timed_out = True
                raise subprocess.TimeoutExpired(args, 120)
            return result

        with patch.object(units, "run", side_effect=command):
            with self.assertRaises(subprocess.TimeoutExpired):
                self.remove()
        self.assert_restored()

    def test_failed_service_restore_keeps_activators_stopped(self):
        self.failure = ["systemctl", "daemon-reload"]
        original = units.write_text_atomic

        def write(path, content, **kwargs):
            if path == str(self.root / "demo.service"):
                raise OSError("service restoration failed")
            original(path, content, **kwargs)

        with patch.object(units, "write_text_atomic", side_effect=write):
            with self.assertRaises(units.UnitRecoveryError):
                self.remove()
        self.assertEqual((self.root / "demo.timer").read_text(), "old demo.timer")
        self.assertFalse((self.root / "demo.service").exists())
        self.assertTrue(all(state[0] == "inactive" for state in self.states.values()))
        self.assertFalse(any(command[:2] == ["systemctl", "restart"] for command in self.commands))

    def test_ineffective_stop_or_disable_fails_before_any_file_deletion(self):
        original = self.run_command
        for ineffective in ("stop", "disable"):
            with self.subTest(ineffective=ineffective):
                ignored = False

                def once(args, **kwargs):
                    nonlocal ignored
                    if not ignored and args[1] == ineffective and args[-1] == "demo.timer":
                        ignored = True
                        return subprocess.CompletedProcess(args, 0, "", "")
                    return original(args, **kwargs)

                with patch.object(units, "run", side_effect=once), patch.object(
                    units, "remove_file_durable", wraps=units.remove_file_durable,
                ) as unlink:
                    with self.assertRaisesRegex(RuntimeError, "verification"):
                        self.remove()
                self.assertFalse(any(
                    call.args[0] in {str(self.root / name) for name in self.names}
                    for call in unlink.call_args_list
                ))
                self.assert_restored()

    def test_missing_managed_files_leave_vendor_units_alone(self):
        for name in self.names:
            (self.root / name).unlink()
        self.remove()
        self.assertEqual(self.commands, [])

    def test_recovery_guard_blocks_removal_and_replacement(self):
        self.failure = ["systemctl", "stop", "demo.service"]
        with patch.object(units, "write_text_atomic", side_effect=OSError("restore write failed")):
            with self.assertRaises(units.UnitRecoveryError) as error:
                self.remove()
        snapshot = Path(error.exception.backup_dir) / "previous.json"
        self.assertEqual(json.loads(snapshot.read_text())["states"]["demo.timer"]["UnitFileState"], "enabled-runtime")
        self.assertFalse(any(command[:2] == ["systemctl", "restart"] for command in self.commands))
        for operation in (self.remove, lambda: units.replace_units({"demo.timer": "new"}, activate=(), unit_dir=str(self.root))):
            with self.assertRaisesRegex(ValueError, "Unfinished"):
                operation()

    def test_failed_finalization_retains_snapshot_even_after_restoration(self):
        with patch.object(units.OperationStateStore, "complete", side_effect=OSError("result write failed")):
            with self.assertRaises(units.UnitRecoveryError) as error:
                self.remove()
        self.assertTrue((Path(error.exception.backup_dir) / "previous.json").is_file())
        for name in self.names:
            self.assertEqual((self.root / name).read_text(), f"old {name}")
            self.assertEqual(tuple(self.states[name]), self.previous[name])
        self.assertTrue(Path(error.exception.marker_path).exists())

    def test_unsafe_files_are_rejected_before_stopping_any_unit(self):
        path = self.root / "demo.path"
        for kind in ("symlink", "fifo", "hardlink"):
            with self.subTest(kind=kind):
                path.unlink()
                if kind == "symlink":
                    path.symlink_to(self.root / "demo.service")
                elif kind == "fifo":
                    os.mkfifo(path)
                else:
                    os.link(self.root / "demo.service", path)
                with self.assertRaises((OSError, ValueError)):
                    self.remove()
                self.assertEqual(self.commands, [])

    def test_dry_run_creates_no_marker_and_changes_no_units(self):
        with patch.object(units, "is_dry_run", return_value=True):
            self.remove()
        self.assert_restored()
        self.assertEqual(self.commands, [])
        self.assertFalse(list(self.root.glob(".basaltwater-*")))
