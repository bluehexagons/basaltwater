"""Idle-only oneshot starts inside timer replacement and recovery."""

from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from lib import unit_transaction as units
from tests.unit_fakes import FakeSystemd


class UnitOneshotTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.service = "job.service"
        self.timer = "job.timer"
        self.desired = {self.service: "[Service]\nType=oneshot\nExecStart=/usr/bin/true\n",
                        self.timer: "[Timer]\nOnBootSec=2min\n"}
        self.systemd = FakeSystemd(self.root)
        self.enterContext(patch.object(units, "run", side_effect=self.systemd))
        self.enterContext(patch.object(units, "is_dry_run", return_value=False))

    def replace(self, **settings) -> None:
        options = dict(activate=(self.timer,), deactivate=(self.timer,), start_once=(self.service,))
        options.update(settings)
        units.replace_units(self.desired, unit_dir=str(self.root), **options)

    def test_invalid_oneshot_groups_fail_before_system_commands(self) -> None:
        for options in ({"deactivate": ()}, {"activate": ()}, {"activate": (self.service,)},
                        {"start_once": (self.timer,)}, {"start_once": ("missing.service",)},
                        {"start_once": (self.service, self.service)}, {"preserve_unchanged": True}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.replace(**options)
        self.assertEqual(self.systemd.commands, [])

    def test_dry_run_does_not_inspect_start_or_stop_jobs(self) -> None:
        with patch.object(units, "is_dry_run", return_value=True):
            self.replace()
        self.assertEqual(self.systemd.commands, [])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_completed_preexisting_job_is_not_replayed_when_timer_activation_fails(self) -> None:
        for name, content in self.desired.items():
            (self.root / name).write_text(content)
        self.systemd.states.update({self.service: ["activating", "static"], self.timer: ["active", "enabled"]})
        count = 0
        def before(command, **kwargs):
            nonlocal count
            if command == ["systemctl", "stop", self.timer]:
                self.systemd.states[self.service][0] = "inactive"
            if command == ["systemctl", "restart", self.timer]:
                count += 1
                if count == 1:
                    raise RuntimeError("timer failed")
        self.systemd.before = before
        with self.assertRaisesRegex(RuntimeError, "timer failed"):
            self.replace()
        for action in ("start", "stop", "restart"):
            self.assertNotIn(["systemctl", action, self.service], self.systemd.commands)
        self.assertFalse((self.root / ".basaltwater-unit-operation.json").exists())

    def test_owned_job_that_stays_running_after_success_is_recovery_required_on_refused_stop(self) -> None:
        def before(command, **kwargs):
            if command == ["systemctl", "stop", self.service]:
                raise subprocess.CalledProcessError(1, command)
        self.systemd.before = before
        # A checked command can return before an unexpected transition ends.
        original = self.systemd
        def run(command, **kwargs):
            result = original(command, **kwargs)
            if command == ["systemctl", "start", self.service]:
                self.systemd.states[self.service][0] = "activating"
            return result
        with patch.object(units, "run", side_effect=run), self.assertRaises(units.UnitRecoveryError) as error:
            self.replace()
        self.assertIn("Oneshot start failed verification", str(error.exception.__cause__))
        self.assertTrue((self.root / self.service).exists())
        self.assertNotIn(["systemctl", "restart", self.timer], self.systemd.commands)

    def test_later_readiness_failure_quiesces_owned_job_before_restoring_files(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "not ready"):
            self.replace(verify=Mock(side_effect=RuntimeError("not ready")))
        self.assertIn(["systemctl", "stop", self.service], self.systemd.commands)
        self.assertFalse((self.root / self.service).exists())
        self.assertFalse((self.root / self.timer).exists())


if __name__ == "__main__":
    unittest.main()
