"""Application readiness and failed quiescence inside unit replacement."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from lib import unit_transaction as units
from tests.unit_fakes import FakeSystemd


class UnitReadinessTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.name = "demo.service"
        self.path = self.root / self.name
        self.path.write_text("previous definition")
        self.path.chmod(0o640)
        self.systemd = FakeSystemd(self.root)
        self.systemd.states[self.name] = ["inactive", "disabled"]
        self.enterContext(patch.object(units, "run", side_effect=self.systemd))
        self.enterContext(patch.object(units, "is_dry_run", return_value=False))
        self.marker = self.root / ".basaltwater-unit-operation.json"

    def replace(self, verify) -> None:
        units.replace_units({self.name: "candidate definition"}, activate=(self.name,),
                            unit_dir=str(self.root), verify=verify)

    def test_readiness_runs_after_activation_before_completion_under_ownership(self) -> None:
        def verify():
            self.assertEqual(self.path.read_text(), "candidate definition")
            self.assertEqual(self.systemd.states[self.name], ["active", "enabled"])
            record = json.loads(self.marker.read_text())
            previous = json.loads((Path(record["context"]["backup_dir"]) / "previous.json").read_text())
            self.assertEqual(previous["units"][self.name]["content"], "previous definition")
            with self.assertRaisesRegex(ValueError, "another process"):
                self.replace(None)

        callback = Mock(side_effect=verify)
        self.replace(callback)
        callback.assert_called_once_with()
        self.assertFalse(self.marker.exists())
        self.assertFalse(list(self.root.glob(".basaltwater-units-*")))

    def test_readiness_failure_restores_prior_contents_metadata_and_activation(self) -> None:
        for active, enabled in (("inactive", "disabled"), ("active", "enabled"), ("active", "enabled-runtime")):
            with self.subTest(active=active, enabled=enabled):
                self.systemd.states[self.name] = [active, enabled]
                callback = Mock(side_effect=RuntimeError("application not ready"))
                with self.assertRaisesRegex(RuntimeError, "application not ready"):
                    self.replace(callback)
                self.assertEqual(self.path.read_text(), "previous definition")
                self.assertEqual(self.path.stat().st_mode & 0o777, 0o640)
                self.assertEqual(self.systemd.states[self.name], [active, enabled])
                self.assertFalse(self.marker.exists())

    def test_readiness_failure_removes_new_unit_after_confirmed_stop(self) -> None:
        self.path.unlink()
        self.systemd.states.clear()
        with self.assertRaisesRegex(RuntimeError, "application not ready"):
            self.replace(Mock(side_effect=RuntimeError("application not ready")))
        self.assertFalse(self.path.exists())
        self.assertFalse(self.marker.exists())
        self.assertIn(["systemctl", "stop", self.name], self.systemd.commands)

    def assert_recovery_retained(self, error: units.UnitRecoveryError) -> None:
        self.assertEqual(self.path.read_text(), "candidate definition")
        self.assertIsInstance(error.__cause__, RuntimeError)
        self.assertEqual(str(error.__cause__), "application not ready")
        record = json.loads(self.marker.read_text())
        self.assertEqual(record["status"], "recovery_required")
        backup = Path(error.backup_dir)
        self.assertEqual((backup / self.name).read_text(), "candidate definition")
        self.assertEqual(json.loads((backup / "previous.json").read_text())["units"][self.name]["content"],
                         "previous definition")
        self.assertEqual(self.systemd.commands.count(["systemctl", "daemon-reload"]), 1)
        self.assertEqual(self.systemd.commands.count(["systemctl", "restart", self.name]), 1)
        with self.assertRaisesRegex(ValueError, "Unfinished"):
            self.replace(None)

    def test_refused_stop_retains_live_candidate_original_snapshot_and_error(self) -> None:
        def before(command, **kwargs):
            if command[:2] == ["systemctl", "stop"]:
                raise subprocess.CalledProcessError(1, command)

        self.systemd.before = before
        with self.assertRaises(units.UnitRecoveryError) as error:
            self.replace(Mock(side_effect=RuntimeError("application not ready")))
        self.assert_recovery_retained(error.exception)

    def test_refused_disable_also_blocks_file_restoration(self) -> None:
        def before(command, **kwargs):
            if command[:2] == ["systemctl", "disable"]:
                raise subprocess.CalledProcessError(1, command)

        self.systemd.before = before
        with self.assertRaises(units.UnitRecoveryError) as error:
            self.replace(Mock(side_effect=RuntimeError("application not ready")))
        self.assert_recovery_retained(error.exception)

    def test_ineffective_stop_does_not_prove_quiescence(self) -> None:
        def run(command, **kwargs):
            if command[:2] == ["systemctl", "stop"]:
                self.systemd.commands.append(command)
                return subprocess.CompletedProcess(command, 0, "", "")
            return self.systemd(command, **kwargs)

        with patch.object(units, "run", side_effect=run), self.assertRaises(units.UnitRecoveryError) as error:
            self.replace(Mock(side_effect=RuntimeError("application not ready")))
        self.assert_recovery_retained(error.exception)

    def test_dry_run_skips_readiness_and_invalid_callback_shortcuts_are_rejected(self) -> None:
        callback = Mock()
        with patch.object(units, "is_dry_run", return_value=True):
            self.replace(callback)
        callback.assert_not_called()
        self.assertEqual(self.systemd.commands, [])
        self.assertEqual(self.path.read_text(), "previous definition")
        for settings in ({"verify": "yes"}, {"verify": callback, "preserve_unchanged": True}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                units.replace_units({"demo.timer": "timer"}, activate=("demo.timer",),
                                    unit_dir=str(self.root), **settings)
        self.assertFalse(self.marker.exists())

    def test_refusal_does_not_skip_stopping_other_activations_or_restore_any_files(self) -> None:
        other = "other.service"
        (self.root / other).write_text("previous other")

        def before(command, **kwargs):
            if command == ["systemctl", "stop", self.name]:
                raise subprocess.CalledProcessError(1, command)

        self.systemd.before = before
        with self.assertRaises(units.UnitRecoveryError):
            units.replace_units({self.name: "candidate definition", other: "candidate other"},
                                activate=(self.name, other), unit_dir=str(self.root),
                                verify=Mock(side_effect=RuntimeError("application not ready")))
        self.assertIn(["systemctl", "stop", other], self.systemd.commands)
        self.assertEqual(self.systemd.states[other][0], "inactive")
        self.assertEqual(self.path.read_text(), "candidate definition")
        self.assertEqual((self.root / other).read_text(), "candidate other")


if __name__ == "__main__":
    unittest.main()
