"""Audit oneshot setup owns recovery while preserving an existing collection."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from common import web_panel_steps as panel
from lib import unit_transaction as units
from tests.unit_fakes import FakeSystemd


class AuditRecoveryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.service = self.root / "audit.service"
        self.timer = self.root / "audit.timer"
        self.systemd = FakeSystemd(self.root)
        self.enterContext(patch.object(units, "run", side_effect=self.systemd))
        self.enterContext(patch.object(units, "is_dry_run", return_value=False))
        self.enterContext(patch.object(panel, "WEB_PANEL_AUDIT_SERVICE_FILE", str(self.service)))
        self.enterContext(patch.object(panel, "WEB_PANEL_AUDIT_TIMER_FILE", str(self.timer)))
        self.marker = self.root / ".basaltwater-unit-operation.json"

    def configure(self, gid=1002) -> bool:
        return panel._configure_audit_exporter(gid)

    def existing(self) -> dict[str, str]:
        old = {"audit.service": panel._SERVICE_MARKER + "\n[Service]\nType=oneshot\n# old service\n",
               "audit.timer": panel._SERVICE_MARKER + "\n# old timer\n"}
        for name, content in old.items():
            (self.root / name).write_text(content)
            (self.root / name).chmod(0o640)
        self.systemd.states.update({"audit.service": ["inactive", "static"],
                                    "audit.timer": ["active", "enabled-runtime"]})
        return old

    def fail(self, command: list[str]):
        def before(actual, **kwargs):
            if actual == command:
                raise subprocess.CalledProcessError(1, actual)
        self.systemd.before = before

    def test_idle_collection_runs_before_timer_and_unchanged_setup_refreshes(self) -> None:
        self.assertTrue(self.configure())
        commands = self.systemd.commands
        self.assertLess(commands.index(["systemctl", "start", "audit.service"]),
                        commands.index(["systemctl", "restart", "audit.timer"]))
        self.assertFalse(self.configure())
        self.assertEqual(commands.count(["systemctl", "start", "audit.service"]), 2)
        self.assertFalse(self.marker.exists())

    def test_start_failure_restores_full_and_partial_installations(self) -> None:
        for missing in (None, "audit.service", "audit.timer", "both"):
            with self.subTest(missing=missing):
                old = self.existing()
                for name in tuple(old):
                    if missing in {name, "both"}:
                        (self.root / name).unlink()
                        old.pop(name)
                        self.systemd.states.pop(name)
                self.fail(["systemctl", "start", "audit.service"])
                with self.assertRaises(subprocess.CalledProcessError):
                    self.configure()
                for name in ("audit.service", "audit.timer"):
                    path = self.root / name
                    self.assertEqual(path.read_text() if path.exists() else None, old.get(name))
                    if name in old:
                        self.assertEqual(path.stat().st_mode & 0o777, 0o640)
                if "audit.timer" in old:
                    self.assertEqual(self.systemd.states["audit.timer"], ["active", "enabled-runtime"])
                self.assertFalse(self.marker.exists())

    def test_running_oneshot_is_never_started_stopped_or_replayed_on_timer_failure(self) -> None:
        for state in ("active", "activating"):
            for fail in (False, True):
                with self.subTest(state=state, fail=fail):
                    old = self.existing()
                    self.systemd.states["audit.service"][0] = state
                    self.systemd.commands.clear()
                    self.systemd.before = None
                    if fail:
                        count = 0
                        def before(command, **kwargs):
                            nonlocal count
                            if command == ["systemctl", "restart", "audit.timer"]:
                                count += 1
                                if count == 1:
                                    raise RuntimeError("timer activation failed")
                        self.systemd.before = before
                        with self.assertRaisesRegex(RuntimeError, "timer activation failed"):
                            self.configure()
                        self.assertEqual(self.service.read_text(), old["audit.service"])
                    else:
                        self.configure()
                    self.assertEqual(self.systemd.states["audit.service"][0], state)
                    for action in ("start", "stop", "restart", "enable", "disable"):
                        self.assertNotIn(["systemctl", action, "audit.service"], self.systemd.commands)
                    self.assertFalse(self.marker.exists())

    def test_job_starting_while_old_timer_stops_is_preserved(self) -> None:
        self.existing()
        def before(command, **kwargs):
            if command == ["systemctl", "stop", "audit.timer"]:
                self.systemd.states["audit.service"][0] = "activating"
        self.systemd.before = before
        self.configure()
        self.assertEqual(self.systemd.states["audit.service"][0], "activating")
        self.assertNotIn(["systemctl", "start", "audit.service"], self.systemd.commands)

    def test_uncertain_owned_start_and_refused_stop_retain_definitions_and_guard(self) -> None:
        def before(command, **kwargs):
            if command == ["systemctl", "start", "audit.service"]:
                self.systemd.states["audit.service"][0] = "activating"
                raise subprocess.TimeoutExpired(command, 120)
            if command == ["systemctl", "stop", "audit.service"]:
                raise subprocess.CalledProcessError(1, command)
        self.systemd.before = before
        with self.assertRaises(units.UnitRecoveryError) as error:
            self.configure()
        self.assertIsInstance(error.exception.__cause__, subprocess.TimeoutExpired)
        self.assertTrue(self.service.is_file())
        self.assertTrue(self.timer.is_file())
        snapshot = json.loads((Path(error.exception.backup_dir) / "previous.json").read_text())
        self.assertEqual(snapshot["units"], {"audit.service": None, "audit.timer": None})
        self.assertEqual(json.loads(self.marker.read_text())["status"], "recovery_required")
        self.assertEqual(json.loads(self.marker.read_text())["context"]["started_jobs"], ["audit.service"])
        with self.assertRaisesRegex(ValueError, "Unfinished"):
            self.configure()

    def assert_timer_refusal_preserves_files(self, action: str) -> None:
        old = self.existing()
        def before(command, **kwargs):
            if command[:2] == ["systemctl", action] and command[-1] == "audit.timer":
                raise subprocess.CalledProcessError(1, command)
        self.systemd.before = before
        with self.assertRaises(units.UnitRecoveryError):
            self.configure()
        self.assertEqual(self.service.read_text(), old["audit.service"])
        self.assertEqual(self.timer.read_text(), old["audit.timer"])
        self.assertNotIn(["systemctl", "start", "audit.service"], self.systemd.commands)

    def test_refused_timer_stop_preserves_previous_files(self) -> None:
        self.assert_timer_refusal_preserves_files("stop")

    def test_refused_timer_disable_preserves_previous_files(self) -> None:
        self.assert_timer_refusal_preserves_files("disable")

    def test_unreadable_unit_and_wrong_service_type_fail_before_mutation(self) -> None:
        with patch("builtins.open", side_effect=PermissionError("unreadable")):
            with self.assertRaises(PermissionError):
                self.configure()
        self.assertEqual(self.systemd.commands, [])
        self.service.write_text(panel._SERVICE_MARKER + "\n[Service]\nType=simple\n")
        with self.assertRaisesRegex(RuntimeError, "Expected a oneshot"):
            self.configure()
        self.assertNotIn(["systemctl", "daemon-reload"], self.systemd.commands)
        self.assertFalse(self.marker.exists())

    def test_failed_restore_keeps_evidence_and_does_not_rearm_timer(self) -> None:
        old = self.existing()
        self.fail(["systemctl", "start", "audit.service"])
        write = units.write_text_atomic
        def restore(path, content, **kwargs):
            if path == str(self.service) and content == old["audit.service"]:
                raise OSError("restore refused")
            return write(path, content, **kwargs)
        with patch.object(units, "write_text_atomic", side_effect=restore), self.assertRaises(units.UnitRecoveryError):
            self.configure()
        self.assertNotIn(["systemctl", "restart", "audit.timer"], self.systemd.commands)
        self.assertTrue(self.marker.exists())

    def test_failed_rollback_reload_keeps_evidence_and_does_not_rearm_timer(self) -> None:
        self.existing()
        count = 0
        def before(command, **kwargs):
            nonlocal count
            if command == ["systemctl", "start", "audit.service"]:
                raise RuntimeError("collection failed")
            if command == ["systemctl", "daemon-reload"]:
                count += 1
                if count == 2:
                    raise OSError("reload failed")
        self.systemd.before = before
        with self.assertRaises(units.UnitRecoveryError) as error:
            self.configure()
        self.assertEqual(str(error.exception.__cause__), "collection failed")
        self.assertNotIn(["systemctl", "restart", "audit.timer"], self.systemd.commands)
        self.assertTrue(self.marker.exists())


if __name__ == "__main__":
    unittest.main()
