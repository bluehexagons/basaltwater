"""Panel service setup through the durable unit recovery boundary."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import web_panel_steps as panel
from lib.config import SetupConfig
from lib.unit_transaction import UnitRecoveryError
from tests.unit_fakes import FakeSystemd


class PanelServiceRecoveryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.name = panel.WEB_PANEL_SERVICE_NAME + ".service"
        self.path = self.root / self.name
        self.previous = panel._SERVICE_MARKER + "\n# previous definition\n"
        self.path.write_text(self.previous)
        self.path.chmod(0o640)
        self.systemd = FakeSystemd(self.root)
        self.systemd.states[self.name] = ["inactive", "disabled"]
        self.enterContext(patch("lib.unit_transaction.run", side_effect=self.systemd))
        self.enterContext(patch("lib.unit_transaction.is_dry_run", return_value=False))
        self.enterContext(patch.object(panel, "WEB_PANEL_SERVICE_FILE", str(self.path)))
        self.enterContext(patch.object(panel.pwd, "getpwnam", return_value=SimpleNamespace(pw_uid=1001, pw_gid=1001)))
        self.readiness = self.enterContext(patch.object(panel, "_verify_service_socket"))
        self.config = SetupConfig(host="fixture", username="agent", system_type="server_lite")

    def configure(self) -> bool:
        return panel._configure_service(self.config, "/home/agent")

    def test_readiness_failure_restores_existing_definition_and_inactive_state(self) -> None:
        self.readiness.side_effect = RuntimeError("socket unavailable")
        with self.assertRaisesRegex(RuntimeError, "socket unavailable"):
            self.configure()
        self.assertEqual(self.path.read_text(), self.previous)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o640)
        self.assertEqual(self.systemd.states[self.name], ["inactive", "disabled"])

    def test_refused_rollback_stop_preserves_new_unit_and_recovery_evidence(self) -> None:
        self.path.unlink()
        self.systemd.states.clear()
        self.readiness.side_effect = RuntimeError("socket unavailable")

        def before(command, **kwargs):
            if command[:2] == ["systemctl", "stop"]:
                raise subprocess.CalledProcessError(1, command)

        self.systemd.before = before
        with self.assertRaises(UnitRecoveryError) as error:
            self.configure()
        self.assertTrue(self.path.is_file())
        self.assertIn("ExecStart=", self.path.read_text())
        self.assertEqual(str(error.exception.__cause__), "socket unavailable")
        previous = json.loads((Path(error.exception.backup_dir) / "previous.json").read_text())
        self.assertIsNone(previous["units"][self.name])
        with self.assertRaisesRegex(ValueError, "Unfinished"):
            self.configure()

    def test_unchanged_definition_still_restarts_and_rolls_back_failed_readiness(self) -> None:
        self.assertTrue(self.configure())
        current = self.path.read_text()
        self.systemd.states[self.name] = ["inactive", "disabled"]
        self.readiness.side_effect = RuntimeError("changed manifest rejected")
        with self.assertRaisesRegex(RuntimeError, "changed manifest rejected"):
            self.configure()
        self.assertEqual(self.path.read_text(), current)
        self.assertEqual(self.systemd.states[self.name], ["inactive", "disabled"])

    def test_unit_read_errors_are_not_treated_as_absence(self) -> None:
        with patch("builtins.open", side_effect=PermissionError("unit unreadable")):
            with self.assertRaisesRegex(PermissionError, "unit unreadable"):
                self.configure()
        self.assertEqual(self.path.read_text(), self.previous)
        self.assertEqual(self.systemd.commands, [])


if __name__ == "__main__":
    unittest.main()
