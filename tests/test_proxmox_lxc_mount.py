"""LXC mount compatibility writes remain scoped, deferred, and idempotent."""

from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from common import lxc_steps
from lib.config import SetupConfig
from plugins.common import get_common_steps


class TestLXCMountCompatibility(unittest.TestCase):
    def setUp(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.units = root / "systemd"
        self.marker = root / "reboot-required"
        self.enterContext(patch.object(lxc_steps, "SYSTEMD_OVERRIDE_DIRECTORY", self.units))
        self.enterContext(patch.object(lxc_steps, "REBOOT_MARKER", self.marker))
        self.capability = self.enterContext(patch.object(lxc_steps, "can_manage_system_services", return_value=True))
        self.container = self.enterContext(patch.object(lxc_steps, "is_unprivileged", return_value=True))
        self.dry_run = self.enterContext(patch.object(lxc_steps, "is_dry_run", return_value=False))
        self.release = self.enterContext(patch.object(lxc_steps, "read_os_release", return_value={"ID": "debian", "VERSION_ID": "13"}))
        self.run = self.enterContext(patch.object(lxc_steps, "run", side_effect=self.command))
        self.missing: set[str] = set()
        self.virtualization = "lxc"
        self.config = SetupConfig(host="guest", username="agent", system_type="server_lite", machine_type="unprivileged")

    def command(self, args, **kwargs):
        if args[0] == "systemd-detect-virt":
            output = self.virtualization
        elif args[1] == "show":
            output = "not-found" if args[2] in self.missing else "loaded"
        else:
            self.assertEqual(args, ["systemctl", "daemon-reload"])
            self.assertTrue(self.marker.exists())
            output = ""
        return subprocess.CompletedProcess(args, 0, output + "\n", "")

    def test_writes_only_three_units_and_defers_activation_until_reboot(self) -> None:
        lxc_steps.configure_lxc_mount_compatibility(self.config)
        paths = sorted(self.units.glob("*.mount.d/*.conf"))
        self.assertEqual({path.parent.name for path in paths}, {
            "tmp.mount.d", "run-lock.mount.d", "dev-mqueue.mount.d",
        })
        for path in paths:
            self.assertEqual(path.stat().st_mode & 0o777, 0o644)
            self.assertIn("[Mount]\nEnvironment=LIBMOUNT_FORCE_MOUNT2=always\n", path.read_text())
        self.run.assert_any_call(["systemctl", "daemon-reload"], timeout=30)
        self.run.reset_mock()
        self.marker.unlink()
        lxc_steps.configure_lxc_mount_compatibility(self.config)
        self.assertFalse(self.marker.exists())
        self.assertFalse(any(call.args[0][1] == "daemon-reload" for call in self.run.call_args_list))

    def test_missing_unit_does_not_gain_a_drop_in(self) -> None:
        self.missing = {"tmp.mount"}
        lxc_steps.configure_lxc_mount_compatibility(self.config)
        self.assertFalse((self.units / "tmp.mount.d").exists())
        self.assertEqual(len(list(self.units.glob("*.mount.d/*.conf"))), 2)

    def test_no_writes_outside_qualified_guests_or_in_dry_run(self) -> None:
        variants = (
            ("capability", False), ("container", False), ("dry_run", True),
            ("release", {"ID": "debian", "VERSION_ID": "12"}),
        )
        for name, value in variants:
            with self.subTest(name=name):
                mock = getattr(self, name)
                original = mock.return_value
                mock.return_value = value
                lxc_steps.configure_lxc_mount_compatibility(self.config)
                mock.return_value = original
                self.assertFalse(self.units.exists())
                self.assertFalse(self.marker.exists())
        self.virtualization = "docker"
        lxc_steps.configure_lxc_mount_compatibility(self.config)
        self.assertFalse(self.units.exists())

    def test_common_plan_includes_compatibility_after_package_upgrade(self) -> None:
        steps = get_common_steps(self.config)
        self.assertEqual(steps[1][1], lxc_steps.configure_lxc_mount_compatibility)


if __name__ == "__main__":
    unittest.main()
