"""T3 login-startup configuration in temporary homes, without launching apps."""

from __future__ import annotations

from contextlib import ExitStack
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import cachyos_t3_desktop as desktop


class DesktopAutostartTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.home = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        stack.enter_context(patch.dict(os.environ, {"XDG_CONFIG_HOME": ""}))

    def test_entry_uses_native_executable_and_kde_login_only(self):
        path = desktop.autostart_path(self.home)
        self.assertFalse(path.parent.exists())
        desktop.enable_autostart(self.home)
        self.assertEqual(path.read_text(), desktop.AUTOSTART_ENTRY)
        self.assertIn("Exec=/usr/bin/t3code\n", path.read_text())
        self.assertIn("TryExec=/usr/bin/t3code\n", path.read_text())
        self.assertIn("OnlyShowIn=KDE;\n", path.read_text())
        self.assertEqual(desktop.collect_autostart_health(self.home)[0], "available")
        desktop.disable_autostart(self.home)
        self.assertEqual(desktop.collect_autostart_health(self.home)[0], "failed")

    def test_custom_xdg_config_home_is_honored_and_relative_values_ignored(self):
        configured = self.home / "custom config"
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(configured)}):
            desktop.enable_autostart(self.home)
            path = desktop.autostart_path(self.home)
            self.assertEqual(path.parent, configured / "autostart")
            desktop.disable_autostart(self.home)
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": "relative"}):
            self.assertEqual(desktop.autostart_path(self.home).parent, self.home / ".config/autostart")

    def test_custom_files_and_symlinks_are_preserved(self):
        path = desktop.autostart_path(self.home)
        path.parent.mkdir(parents=True)
        for kind in ("file", "symlink", "directory"):
            with self.subTest(kind=kind):
                if kind == "file":
                    path.write_text("personal fixture")
                elif kind == "symlink":
                    path.symlink_to(self.home / "missing")
                else:
                    path.mkdir()
                for operation in (desktop.enable_autostart, desktop.disable_autostart):
                    with self.assertRaises(ValueError):
                        operation(self.home)
                self.assertEqual(desktop.collect_autostart_health(self.home)[0], "failed")
                if kind == "directory":
                    path.rmdir()
                else:
                    path.unlink()

    def test_symlinked_config_directory_is_not_traversed(self):
        outside = self.home / "personal"
        outside.mkdir()
        (self.home / ".config").symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "autostart directory"):
            desktop.enable_autostart(self.home)
        self.assertEqual(list(outside.iterdir()), [])

    def test_disabled_entry_is_advisory_and_setup_reenables_it(self):
        desktop.enable_autostart(self.home)
        path = desktop.autostart_path(self.home)
        path.write_text(desktop.AUTOSTART_ENTRY + "Hidden=true\n")
        self.assertEqual(desktop.collect_autostart_health(self.home)[0], "deferred")
        desktop.enable_autostart(self.home)
        self.assertEqual(desktop.collect_autostart_health(self.home)[0], "available")

    def test_changed_or_unsafe_entries_do_not_qualify_startup(self):
        desktop.enable_autostart(self.home)
        path = desktop.autostart_path(self.home)
        path.write_text(desktop.AUTOSTART_ENTRY.replace("Exec=/usr/bin/t3code", "Exec=personal-secret"))
        state, reason = desktop.collect_autostart_health(self.home)
        self.assertEqual(state, "failed")
        self.assertNotIn("personal-secret", reason)
        path.chmod(0o666)
        with self.assertRaises(ValueError):
            desktop.disable_autostart(self.home)
        path.chmod(0o644)
        with patch.object(desktop.os, "getuid", return_value=os.getuid() + 1):
            self.assertEqual(desktop.collect_autostart_health(self.home)[0], "failed")
        path.write_bytes(b"x" * 16385)
        self.assertEqual(desktop.collect_autostart_health(self.home)[0], "failed")


if __name__ == "__main__":
    unittest.main()
