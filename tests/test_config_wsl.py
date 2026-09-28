"""Focused tests for Ubuntu WSL setup boundaries and source preparation."""

from __future__ import annotations

from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import wsl_steps
from windows import wsl_prepare


class WslStepTests(unittest.TestCase):
    def _config(self, **overrides):
        values = dict(system_type="server_wsl", machine_type="wsl", username="agent",
                      host="localhost", web_interfaces=(), t3code_desktop=False,
                      enable_rdp=False, storage_mounts=(), swap_files=(), swap_devices=(),
                      is_app_server=False, enable_ssl=False, enable_cloudflare=False,
                      is_build_server=False, enable_cicd=False)
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_preflight_rejects_nonlocal_and_linux_webhook_configuration(self):
        with patch.object(wsl_steps, "is_dry_run", return_value=True):
            for config in (self._config(host="example.com"),
                           self._config(enable_cicd=True),
                           self._config(machine_type="vm")):
                with self.subTest(config=config), self.assertRaises(ValueError):
                    wsl_steps.preflight_wsl(config)

    def test_preflight_requires_ubuntu_systemd_and_wsl(self):
        config = self._config()
        with patch.object(wsl_steps, "is_dry_run", return_value=False), \
             patch.object(wsl_steps.platform, "freedesktop_os_release", return_value={"ID": "ubuntu"}), \
             patch.object(wsl_steps, "detect_machine_type", return_value="wsl"), \
             patch.object(wsl_steps.os, "geteuid", return_value=0), \
             patch.object(wsl_steps.pwd, "getpwnam"), \
             patch.object(wsl_steps.Path, "read_text", return_value="systemd\n"):
            wsl_steps.preflight_wsl(config)
        with patch.object(wsl_steps, "is_dry_run", return_value=False), \
             patch.object(wsl_steps.platform, "freedesktop_os_release", return_value={"ID": "debian"}):
            with self.assertRaisesRegex(ValueError, "requires Ubuntu"):
                wsl_steps.preflight_wsl(config)

    def test_base_package_actions_are_bounded_to_apt(self):
        with patch.object(wsl_steps, "is_dry_run", return_value=False), \
             patch.object(wsl_steps, "run") as run:
            wsl_steps.install_wsl_base(self._config())
        self.assertEqual(run.call_args_list[0].args[0][:2], ["apt-get", "update"])
        self.assertEqual(run.call_args_list[1].args[0][:2], ["apt-get", "upgrade"])
        self.assertIn("sudo", run.call_args_list[2].args[0])


class WslPrepareTests(unittest.TestCase):
    def test_ini_changes_preserve_unrelated_settings_and_are_idempotent(self):
        lines = ["[interop]\n", "appendWindowsPath=false\n"]
        self.assertTrue(wsl_prepare._set_ini_value(lines, "boot", "systemd", "true"))
        self.assertTrue(wsl_prepare._set_ini_value(lines, "user", "default", "agent"))
        self.assertFalse(wsl_prepare._set_ini_value(lines, "boot", "systemd", "true"))
        self.assertIn("appendWindowsPath=false\n", lines)

    def test_stage_copies_pinned_source_and_reuses_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            (source / "plugins").mkdir(parents=True)
            (source / "plugins/wsl.py").write_text("ready\n", encoding="utf-8")
            revision = "a" * 40
            with patch.object(wsl_prepare, "RELEASES", root / "releases"), \
                 patch.object(wsl_prepare.os, "geteuid", return_value=0):
                wsl_prepare.stage(str(source), revision)
                wsl_prepare.stage(str(source), revision)
            self.assertEqual((root / "releases" / revision / "plugins/wsl.py").read_text(),
                             "ready\n")

    def test_stage_rejects_symlink_and_bad_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            (source / "plugins").mkdir(parents=True)
            (source / "plugins/wsl.py").write_text("ready\n", encoding="utf-8")
            (source / "link").symlink_to("plugins/wsl.py")
            with patch.object(wsl_prepare, "RELEASES", root / "releases"), \
                 patch.object(wsl_prepare.os, "geteuid", return_value=0):
                with self.assertRaisesRegex(ValueError, "commit SHA"):
                    wsl_prepare.stage(str(source), "main")
                with self.assertRaisesRegex(ValueError, "symbolic link"):
                    wsl_prepare.stage(str(source), "b" * 40)


if __name__ == "__main__":
    unittest.main()
