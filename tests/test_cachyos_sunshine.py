"""Sunshine startup and encoder defaults without live service or GPU changes."""

from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import basaltwater
from common import cachyos_sunshine as sunshine
from lib.cachyos import cachyos_config_from_args
from lib.system_types import get_steps_for_system_type


default_intel_encoder = sunshine._default_intel_encoder


class SunshineSetupTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.home = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        parser, _, _ = basaltwater.create_basaltwater_parser()
        self.config = cachyos_config_from_args(parser.parse_args([
            "setup", "agent_cachyos", "localhost", "human", "--sunshine",
        ]))
        self.path = self.home / ".config/sunshine/sunshine.conf"
        self.state = "inactive"
        stack.enter_context(patch.object(sunshine, "_home", return_value=self.home))
        stack.enter_context(patch.object(sunshine, "can_manage_system_services", return_value=True))
        stack.enter_context(patch.object(sunshine, "is_dry_run", return_value=False))
        stack.enter_context(patch.object(sunshine.time, "sleep"))
        self.encoder = stack.enter_context(patch.object(sunshine, "_default_intel_encoder"))
        self.command = stack.enter_context(patch.object(sunshine, "run", side_effect=self.system_command))

    def system_command(self, command, **kwargs):
        text = ""
        if "show" in command:
            text = (f"LoadState=loaded\nFragmentPath={sunshine.UNIT}\nDropInPaths=\n"
                    f"Restart=on-failure\nActiveState={self.state}\n")
        elif command[0] == "pacman":
            text = "sunshine\n"
        elif "is-active" in command:
            text = "active\n"
        elif "is-enabled" in command:
            text = "enabled\n"
        return subprocess.CompletedProcess(command, 0, text, "")

    def test_selection_orders_firewall_before_startup_and_startup_before_readiness(self):
        from common.cachyos_firewall import configure_firewall
        from common.cachyos_steps import report_cachyos_readiness

        self.config.access_sources = ["192.168.1.0/24"]
        functions = [function for _, function in get_steps_for_system_type(self.config)]
        self.assertLess(functions.index(configure_firewall), functions.index(sunshine.configure))
        self.assertLess(functions.index(sunshine.configure), functions.index(report_cachyos_readiness))
        self.config.install_sunshine = False
        self.assertNotIn(sunshine.configure, [function for _, function in get_steps_for_system_type(self.config)])

    def test_setup_enables_package_service_without_root_or_restarting_active_service(self):
        self.state = "active"
        sunshine.configure(self.config)
        calls = [call.args[0] for call in self.command.call_args_list]
        self.assertIn(["systemctl", "--user", "enable", "--now", sunshine.SERVICE], calls)
        self.assertEqual(sum(call[-1] == sunshine.SERVICE and "is-active" in call for call in calls), 3)
        self.assertFalse(any("sudo" in call or "restart" in call or "stop" in call for call in calls))
        self.encoder.assert_not_called()

    def test_crash_after_activation_fails_setup_instead_of_claiming_readiness(self):
        count = 0

        def command(argv, **kwargs):
            nonlocal count
            if "is-active" in argv and argv[-1] == sunshine.SERVICE:
                count += 1
                return subprocess.CompletedProcess(argv, 0 if count == 1 else 3,
                                                   "active\n" if count == 1 else "failed\n", "")
            return self.system_command(argv, **kwargs)

        self.command.side_effect = command
        with self.assertRaisesRegex(RuntimeError, "did not start"):
            sunshine.configure(self.config)

    def test_custom_units_and_missing_graphical_session_stop_before_enable(self):
        def command(argv, **kwargs):
            result = self.system_command(argv, **kwargs)
            if "show" in argv:
                result.stdout = result.stdout.replace(sunshine.UNIT, "/home/human/custom.service")
            return result

        self.command.side_effect = command
        with self.assertRaisesRegex(RuntimeError, "custom Sunshine units"):
            sunshine.configure(self.config)
        self.assertFalse(any("enable" in call.args[0] for call in self.command.call_args_list))
        self.command.return_value = subprocess.CompletedProcess([], 3, "inactive\n", "")
        self.command.side_effect = None
        with self.assertRaisesRegex(ValueError, "graphical session"):
            sunshine.preflight(self.config)

    def test_dry_run_does_not_create_config_or_touch_services(self):
        self.config.dry_run = True
        sunshine.configure(self.config)
        self.command.assert_not_called()
        self.encoder.assert_not_called()
        self.assertFalse(self.path.parent.exists())

    def test_verified_intel_defaults_to_vaapi_and_preserves_other_settings(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("# personal comment\nfps = 60\n")
        vendor = self.home / "vendor"
        vendor.write_text("0x8086\n")
        with patch.object(Path, "glob", return_value=[vendor]), \
                patch("lib.cachyos_health.collect_sunshine_health", return_value=[
                    ("graphics.sunshine-vaapi", "available", "Verified fixture.")]), \
                patch("lib.cachyos_doctor._probe") as probe:
            default_intel_encoder(self.path)
            probe.assert_not_called()
        self.assertIn("fps = 60\n", self.path.read_text())
        self.assertIn("encoder = vaapi\n", self.path.read_text())

    def test_explicit_encoder_or_unverified_hardware_is_preserved(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("encoder = vulkan\n")
        with patch.object(Path, "glob") as hardware:
            default_intel_encoder(self.path)
            hardware.assert_not_called()
        self.assertEqual(self.path.read_text(), "encoder = vulkan\n")
        self.path.unlink()
        vendor = self.home / "vendor"
        vendor.write_text("0x8086\n")
        with patch.object(Path, "glob", return_value=[vendor]), \
                patch("lib.cachyos_health.collect_sunshine_health", return_value=[
                    ("graphics.sunshine-vaapi", "deferred", "Unknown fixture.")]):
            default_intel_encoder(self.path)
        self.assertFalse(self.path.exists())

    def test_symlinked_config_is_preserved_and_refused(self):
        self.path.parent.mkdir(parents=True)
        outside = self.home / "personal.conf"
        outside.write_text("keep\n")
        self.path.symlink_to(outside)
        with self.assertRaises(ValueError):
            sunshine.configure(self.config)
        self.assertEqual(outside.read_text(), "keep\n")
        self.command.assert_not_called()


if __name__ == "__main__":
    unittest.main()
