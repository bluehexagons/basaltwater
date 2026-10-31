"""Optional panel components borrow the outer unit owner during teardown."""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from common import proxmox_web_panel_steps as proxmox
from desktop import guacamole_steps as guacamole
from lib import unit_transaction as units
from lib.operation_state import OperationStateStore
from tests.unit_fakes import FakeSystemd
from web import github_runner_registration_steps as registration


class PanelComponentOwnershipTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.marker = self.root / ".basaltwater-unit-operation.json"
        self.systemd = FakeSystemd(self.root)
        self.enterContext(patch.object(units, "run", side_effect=self.systemd))
        self.enterContext(patch.object(units, "is_dry_run", return_value=False))
        for module in (proxmox, guacamole, registration):
            self.enterContext(patch.object(module, "snapshot_unit_file", side_effect=lambda path:
                                          units.snapshot_unit_file(str(self.root / Path(path).name))))
        self.inventory = self.root / "inventory"
        self.inventory.mkdir()
        self.snapshot = self.inventory / "inventory.json"
        self.snapshot.write_text("retained snapshot")
        self.enterContext(patch.multiple(proxmox, SNAPSHOT_DIR=str(self.inventory), SNAPSHOT=str(self.snapshot)))
        self.config = self.root / "guacamole.json"
        self.ssh = self.root / "guacamole-sshd.conf"
        self.config.write_text("retained config")
        self.ssh.write_text("retained ssh config")
        self.enterContext(patch.multiple(guacamole, CONFIG=str(self.config), SSH_CONFIG=str(self.ssh)))
        self.enterContext(patch.object(guacamole, "preflight_units"))
        self.enterContext(patch.object(guacamole, "preflight_configuration"))
        self.enterContext(patch.object(guacamole, "Path", side_effect=lambda path:
                                      self.root / Path(path).name if str(path).startswith("/etc/systemd/system/") else Path(path)))
        self.enterContext(patch.object(registration, "runner_lock", side_effect=nullcontext))
        definitions = {
            proxmox.SNAPSHOT_SERVICE: proxmox._MARKER,
            proxmox.SNAPSHOT_TIMER: proxmox._MARKER,
            **dict.fromkeys(guacamole.managed_units(), guacamole.UNIT_MARKER),
            registration.SOCKET_UNIT: registration._MARKER,
            registration.SERVICE_UNIT: registration._MARKER,
        }
        for name, marker in definitions.items():
            (self.root / name).write_text(marker + "\n[Service]\nType=oneshot\n")

    def assert_outer_owned(self) -> None:
        with self.assertRaisesRegex(ValueError, "another process"):
            with OperationStateStore(str(self.marker)).ownership():
                self.fail("Nested removal released outer ownership")

    def test_all_actual_component_removals_share_owner_and_release_only_after_scope_exit(self) -> None:
        with units.unit_ownership(str(self.root)) as owner:
            for remove in (proxmox.remove_proxmox_snapshot, guacamole.remove_guacamole,
                           registration.remove_registration_service):
                with self.subTest(remove=remove.__name__):
                    remove(unit_owner=owner)
                    self.assert_outer_owned()
                    self.assertFalse(self.marker.exists())
            self.assertFalse(self.snapshot.exists())
            self.assertFalse(self.config.exists())
            self.assertFalse(self.ssh.exists())
            self.assertEqual(list(self.root.glob("*.service")), [])
            self.assertEqual(list(self.root.glob("*.timer")), [])
            self.assertEqual(list(self.root.glob("*.socket")), [])
            # Repeated component removals do not need a second ownership lock.
            for remove in (proxmox.remove_proxmox_snapshot, guacamole.remove_guacamole,
                           registration.remove_registration_service):
                remove(unit_owner=owner)
                self.assert_outer_owned()
        with OperationStateStore(str(self.marker)).ownership():
            pass

    def test_nested_failure_preserves_inventory_and_recovery_while_outer_owner_remains(self) -> None:
        def before(command, **kwargs):
            if command == ["systemctl", "stop", proxmox.SNAPSHOT_TIMER]:
                raise subprocess.CalledProcessError(1, command)
        self.systemd.before = before
        with units.unit_ownership(str(self.root)) as owner:
            with self.assertRaises(units.UnitRecoveryError):
                proxmox.remove_proxmox_snapshot(unit_owner=owner)
            self.assert_outer_owned()
            self.assertEqual(self.snapshot.read_text(), "retained snapshot")
            self.assertTrue((self.root / proxmox.SNAPSHOT_TIMER).is_file())
            self.assertTrue(self.marker.is_file())
            with self.assertRaisesRegex(ValueError, "Unfinished"):
                registration.remove_registration_service(unit_owner=owner)
            self.assert_outer_owned()
        with self.assertRaisesRegex(ValueError, "Unfinished"):
            with units.unit_ownership(str(self.root)):
                self.fail("Nested recovery evidence was bypassed")

    def test_busy_runner_registration_aborts_without_releasing_outer_ownership(self) -> None:
        with units.unit_ownership(str(self.root)) as owner:
            with patch.object(registration, "runner_lock", side_effect=RuntimeError("registration is running")):
                with self.assertRaisesRegex(RuntimeError, "registration is running"):
                    registration.remove_registration_service(unit_owner=owner)
            self.assert_outer_owned()
            self.assertTrue((self.root / registration.SOCKET_UNIT).is_file())
            self.assertTrue((self.root / registration.SERVICE_UNIT).is_file())
            self.assertFalse(self.marker.exists())
            self.assertEqual(self.systemd.commands, [])


if __name__ == "__main__":
    unittest.main()
