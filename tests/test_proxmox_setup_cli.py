"""Usable, collision-safe CLI installation on the hypervisor profile."""

from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from common import proxmox_steps
from lib.config import SetupConfig
from plugins.proxmox import build_server_proxmox_steps


class TestProxmoxManagementCommand(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.source = self.root / "installed source" / "basaltwater.py"
        self.source.parent.mkdir()
        self.source.write_text("# fixture entrypoint\n", encoding="utf-8")
        self.bin = self.root / "bin"
        self.enterContext(patch.object(proxmox_steps, "_PROXMOX_CLI_SCRIPT", str(self.source)))
        self.enterContext(patch.object(proxmox_steps, "SYSTEM_LAUNCHER_DIR", str(self.bin)))
        self.dry_run = self.enterContext(patch.object(proxmox_steps, "is_dry_run", return_value=False))
        self.command = self.enterContext(patch.object(proxmox_steps, "run"))
        self.config = SetupConfig(host="pve.example", username="root", system_type="server_proxmox")

    def test_installs_quoted_executable_launcher_and_verifies_cli_on_rerun(self):
        for _ in range(2):
            proxmox_steps.install_proxmox_cli(self.config)
            launcher = self.bin / "basaltw"
            self.assertIn(f"exec python3 '{self.source}' \"$@\"", launcher.read_text())
            self.assertEqual(launcher.stat().st_mode & 0o777, 0o755)
        self.assertEqual(self.command.call_count, 2)
        self.command.assert_called_with([str(launcher), "--help"], capture_output=True, timeout=30)

    def test_unmanaged_command_collision_is_preserved_before_verification(self):
        self.bin.mkdir()
        launcher = self.bin / "basaltw"
        launcher.write_text("#!/bin/sh\n# operator owned\nexit 0\n")
        with self.assertRaisesRegex(ValueError, "Refusing to replace"):
            proxmox_steps.install_proxmox_cli(self.config)
        self.assertIn("operator owned", launcher.read_text())
        self.command.assert_not_called()

    def test_cli_verification_failure_stops_setup(self):
        self.command.side_effect = subprocess.CalledProcessError(1, ["basaltw", "--help"])
        with self.assertRaises(subprocess.CalledProcessError):
            proxmox_steps.install_proxmox_cli(self.config)

    def test_dry_run_does_not_write_launcher_or_run_cli(self):
        self.dry_run.return_value = True
        proxmox_steps.install_proxmox_cli(self.config)
        self.assertFalse(self.bin.exists())
        self.command.assert_not_called()

    def test_default_profile_installs_cli_before_optional_panel(self):
        self.config.web_panel_port = 9443
        functions = [function for _, function in build_server_proxmox_steps(self.config)]
        from common.web_panel_steps import configure_web_panel

        self.assertLess(functions.index(proxmox_steps.upgrade_proxmox_packages),
                        functions.index(proxmox_steps.install_proxmox_cli))
        self.assertLess(functions.index(proxmox_steps.install_proxmox_cli),
                        functions.index(configure_web_panel))
