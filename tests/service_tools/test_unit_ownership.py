"""Explicit nested removals retain shared unit recovery exclusion."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from lib import unit_transaction as units
from lib.operation_state import OperationStateStore
from tests.unit_fakes import FakeSystemd


class UnitOwnershipTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.marker = self.root / ".basaltwater-unit-operation.json"
        self.systemd = FakeSystemd(self.root)
        self.enterContext(patch.object(units, "run", side_effect=self.systemd))
        self.enterContext(patch.object(units, "is_dry_run", return_value=False))

    def assert_owned(self) -> None:
        other = OperationStateStore(str(self.marker))
        try:
            with self.assertRaisesRegex(ValueError, "another process"):
                with other.ownership():
                    self.fail("Competing owner acquired the unit lock")
        finally:
            other.close()

    def test_outer_scope_refuses_existing_owner_or_retained_or_corrupt_marker(self) -> None:
        store = OperationStateStore(str(self.marker))
        with store.ownership(), self.assertRaisesRegex(ValueError, "another process"):
            with units.unit_ownership(str(self.root)):
                self.fail("Existing owner was bypassed")
        record = store.begin("unit-replacement", str(self.root), "replacing")
        for status in ("in_progress", "recovery_required"):
            store.transition(record.operation_id, "rollback-failed", status=status)
            store.close()
            before = self.marker.read_bytes()
            with self.assertRaisesRegex(ValueError, "Unfinished"):
                with units.unit_ownership(str(self.root)):
                    self.fail("Retained recovery was bypassed")
            self.assertEqual(self.marker.read_bytes(), before)
        for content in ("{", '{"schema_version":999}'):
            self.marker.write_text(content)
            with self.assertRaises(ValueError):
                with units.unit_ownership(str(self.root)):
                    self.fail("Corrupt recovery was bypassed")
            self.assertEqual(self.marker.read_text(), content)
        self.assertEqual(self.systemd.commands, [])

    def test_success_noop_and_rollback_keep_outer_lock_until_scope_exit(self) -> None:
        for mode in ("success", "absent", "rollback"):
            with self.subTest(mode=mode):
                path = self.root / "job.service"
                if mode != "absent":
                    path.write_text("previous definition")
                with units.unit_ownership(str(self.root)) as owner:
                    self.assert_owned()
                    def prepare():
                        self.assert_owned()
                        if mode == "rollback":
                            raise RuntimeError("preparation refused")
                    if mode == "rollback":
                        with self.assertRaisesRegex(RuntimeError, "preparation refused"):
                            units.remove_units((path.name,), owner=owner, prepare=prepare)
                        self.assertEqual(path.read_text(), "previous definition")
                    else:
                        units.remove_units((path.name,), owner=owner, prepare=prepare)
                        self.assertFalse(path.exists())
                    self.assert_owned()
                    self.assertFalse(self.marker.exists())
                store = OperationStateStore(str(self.marker))
                with store.ownership():
                    pass
                path.unlink(missing_ok=True)

    def test_failed_nested_quiescence_retains_marker_and_outer_lock(self) -> None:
        path = self.root / "job.service"
        path.write_text("previous definition")
        self.systemd.states[path.name] = ["active", "enabled"]
        def before(command, **kwargs):
            if command == ["systemctl", "stop", path.name]:
                raise subprocess.CalledProcessError(1, command)
        self.systemd.before = before
        with units.unit_ownership(str(self.root)) as owner:
            with self.assertRaises(units.UnitRecoveryError):
                units.remove_units((path.name,), owner=owner)
            self.assert_owned()
            self.assertEqual(path.read_text(), "previous definition")
            self.assertEqual(json.loads(self.marker.read_text())["status"], "recovery_required")
            commands = len(self.systemd.commands)
            with self.assertRaisesRegex(ValueError, "Unfinished"):
                units.remove_units((path.name,), owner=owner)
            self.assertEqual(len(self.systemd.commands), commands)
            self.assert_owned()
        with self.assertRaisesRegex(ValueError, "Unfinished"):
            with units.unit_ownership(str(self.root)):
                self.fail("Failed removal guard was bypassed")

    def test_owner_cannot_escape_its_scope_or_directory_or_implicitly_authorize_replacement(self) -> None:
        with units.unit_ownership(str(self.root)) as owner:
            with self.assertRaisesRegex(ValueError, "another directory"):
                units.remove_units(("job.service",), unit_dir=str(self.root / "other"), owner=owner)
            with self.assertRaisesRegex(ValueError, "another process"):
                units.replace_units({"job.service": "candidate"}, activate=("job.service",), unit_dir=str(self.root))
            self.assert_owned()
        with self.assertRaisesRegex(ValueError, "inactive"):
            units.remove_units(("job.service",), owner=owner)
        self.assertEqual(self.systemd.commands, [])


if __name__ == "__main__":
    unittest.main()
