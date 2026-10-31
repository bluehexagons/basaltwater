"""Proxmox inventory setup includes initial collection in unit recovery."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from common import proxmox_web_panel_steps as steps
from lib import unit_transaction as units
from tests.unit_fakes import FakeSystemd


class ProxmoxSnapshotRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.service = steps.SNAPSHOT_SERVICE
        self.timer = steps.SNAPSHOT_TIMER
        self.marker = self.root / ".basaltwater-unit-operation.json"
        self.snapshot_dir = self.root / "inventory"
        self.systemd = FakeSystemd(self.root)
        self.enterContext(patch.object(units, "run", side_effect=self.systemd))
        self.enterContext(patch.object(units, "is_dry_run", return_value=False))
        self.enterContext(patch.object(steps, "run", side_effect=self.systemd))
        self.enterContext(patch.object(steps, "SNAPSHOT_DIR", str(self.snapshot_dir)))
        self.enterContext(patch.object(steps, "SNAPSHOT", str(self.snapshot_dir / "inventory.json")))
        self.enterContext(patch.object(steps.os, "fchown"))
        self.enterContext(patch.object(steps, "snapshot_unit_file", side_effect=lambda path:
                                      units.snapshot_unit_file(str(self.root / Path(path).name))))
        self.enterContext(patch.object(steps, "replace_units", side_effect=lambda definitions, **options:
                                      units.replace_units(definitions, unit_dir=str(self.root), **options)))

    def configure(self) -> None:
        steps.configure_proxmox_snapshot(991)

    def existing(self) -> dict[str, str]:
        old = {self.service: steps._MARKER + "\n[Service]\nType=oneshot\n# previous collector\n",
               self.timer: steps._MARKER + "\n# previous timer\n"}
        for name, content in old.items():
            (self.root / name).write_text(content)
            (self.root / name).chmod(0o640)
        self.systemd.states.update({self.service: ["inactive", "static"],
                                    self.timer: ["active", "enabled-runtime"]})
        return old

    def test_collection_failure_restores_previous_and_partial_installations(self) -> None:
        for missing in (None, self.service, self.timer, "both"):
            with self.subTest(missing=missing):
                old = self.existing()
                for name in tuple(old):
                    if missing in {name, "both"}:
                        (self.root / name).unlink()
                        old.pop(name)
                        self.systemd.states.pop(name)
                def before(command, **kwargs):
                    if command == ["systemctl", "start", self.service]:
                        raise subprocess.CalledProcessError(1, command)
                self.systemd.before = before
                with self.assertRaises(subprocess.CalledProcessError):
                    self.configure()
                for name in (self.service, self.timer):
                    path = self.root / name
                    self.assertEqual(path.read_text() if path.exists() else None, old.get(name))
                    if name in old:
                        self.assertEqual(path.stat().st_mode & 0o777, 0o640)
                if self.timer in old:
                    self.assertEqual(self.systemd.states[self.timer], ["active", "enabled-runtime"])
                self.assertFalse(self.marker.exists())

    def test_initial_and_unchanged_collection_finish_before_timer_activation(self) -> None:
        self.configure()
        commands = self.systemd.commands
        self.assertLess(commands.index(["systemctl", "start", self.service]),
                        commands.index(["systemctl", "restart", self.timer]))
        self.configure()
        self.assertEqual(commands.count(["systemctl", "start", self.service]), 2)
        self.assertFalse(self.marker.exists())

    def test_busy_collection_is_not_joined_or_stopped_during_timer_update(self) -> None:
        self.existing()
        self.systemd.states[self.service][0] = "activating"
        self.configure()
        for action in ("start", "stop", "restart"):
            self.assertNotIn(["systemctl", action, self.service], self.systemd.commands)
        self.assertEqual(self.systemd.states[self.service][0], "activating")
        self.assertFalse(self.marker.exists())

    def test_refused_timer_stop_preserves_definitions_and_blocks_retry(self) -> None:
        old = self.existing()
        def before(command, **kwargs):
            if command == ["systemctl", "stop", self.timer]:
                raise subprocess.CalledProcessError(1, command)
        self.systemd.before = before
        with self.assertRaises(units.UnitRecoveryError):
            self.configure()
        for name, content in old.items():
            self.assertEqual((self.root / name).read_text(), content)
        self.assertNotIn(["systemctl", "start", self.service], self.systemd.commands)
        with self.assertRaisesRegex(ValueError, "Unfinished"):
            self.configure()

    def test_lost_start_response_and_refused_job_stop_retain_candidates_and_evidence(self) -> None:
        def before(command, **kwargs):
            if command == ["systemctl", "start", self.service]:
                self.systemd.states.setdefault(self.service, ["inactive", "static"])[0] = "activating"
                raise subprocess.TimeoutExpired(command, 120)
            if command == ["systemctl", "stop", self.service]:
                raise subprocess.CalledProcessError(1, command)
        self.systemd.before = before
        with self.assertRaises(units.UnitRecoveryError) as error:
            self.configure()
        self.assertIsInstance(error.exception.__cause__, subprocess.TimeoutExpired)
        for name in (self.service, self.timer):
            self.assertTrue((self.root / name).is_file())
        previous = json.loads((Path(error.exception.backup_dir) / "previous.json").read_text())
        self.assertEqual(previous["units"], {self.service: None, self.timer: None})
        record = json.loads(self.marker.read_text())
        self.assertEqual(record["status"], "recovery_required")
        self.assertEqual(record["context"]["started_jobs"], [self.service])
        self.assertNotIn(["systemctl", "restart", self.timer], self.systemd.commands)
        with self.assertRaisesRegex(ValueError, "Unfinished"):
            self.configure()


if __name__ == "__main__":
    unittest.main()
