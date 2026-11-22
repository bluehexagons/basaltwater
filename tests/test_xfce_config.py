"""Regression coverage for preserving XFCE preferences during setup."""

from __future__ import annotations

import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from desktop.xfce_config import (
    LEGACY_DISPLAYS, LEGACY_PM_STUB, clean_legacy_xfwm,
    remove_legacy_override, remove_legacy_pm_stub, update_channel,
)
from desktop import home_setup


class XfceConfigTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "settings.xml"

    def test_merge_preserves_custom_preferences_and_is_repeatable(self):
        self.path.write_text('''<channel name="power" version="1.0">
          <property name="manager" type="empty">
            <property name="dpms" type="bool" value="true"/>
            <property name="custom" type="string" value="keep"/>
          </property></channel>''')
        desired = '''<channel name="power"><property name="manager" type="empty">
          <property name="dpms" type="bool" value="false"/></property></channel>'''
        update_channel(str(self.path), desired)
        first = self.path.read_text()
        update_channel(str(self.path), desired)
        self.assertEqual(first, self.path.read_text())
        root = ET.fromstring(first)
        self.assertEqual(root.find(".//property[@name='custom']").get("value"), "keep")
        self.assertEqual(root.find(".//property[@name='dpms']").get("value"), "false")

    def test_invalid_xml_is_not_overwritten(self):
        self.path.write_text("broken<xml")
        with self.assertRaises(ET.ParseError):
            update_channel(str(self.path), '<channel name="power"/>')
        self.assertEqual(self.path.read_text(), "broken<xml")

    def test_symlink_is_not_followed(self):
        target = self.path.with_name("target")
        target.write_text('<channel name="power"/>')
        self.path.symlink_to(target)
        with self.assertRaises(ValueError):
            update_channel(str(self.path), '<channel name="power"/>')
        self.assertEqual(target.read_text(), '<channel name="power"/>')

    def test_cleanup_preserves_real_theme_and_unrecognized_settings(self):
        self.path.write_text('''<channel name="xfwm4"><property name="general" type="empty">
          <property name="theme" type="string" value="Custom"/>
          <property name="Xfwm" type="empty">
            <property name="Xinerama" type="bool" value="false"/>
            <property name="theme" type="string" value="Default-xhdpi"/>
            <property name="custom" type="string" value="keep"/>
          </property></property></channel>''')
        clean_legacy_xfwm(str(self.path))
        root = ET.parse(self.path).getroot()
        self.assertEqual(root.find("property/property[@name='theme']").get("value"), "Custom")
        self.assertIsNone(root.find(".//property[@name='Xinerama']"))
        self.assertEqual(root.find(".//property[@name='custom']").get("value"), "keep")
        self.assertNotIn("Default-xhdpi", self.path.read_text())

    def test_stub_migration_only_removes_known_content(self):
        for content in ("#!/bin/sh\nexit 0\n", LEGACY_PM_STUB + "# custom\n"):
            self.path.write_text(content)
            remove_legacy_pm_stub(str(self.path))
            self.assertEqual(self.path.read_text(), content)
        self.path.write_text(LEGACY_PM_STUB)
        remove_legacy_pm_stub(str(self.path))
        self.assertFalse(self.path.exists())
        remove_legacy_pm_stub(str(self.path))

    def test_display_migration_preserves_custom_profile(self):
        custom = LEGACY_DISPLAYS.replace("1920x1080", "1280x720")
        self.path.write_text(custom)
        remove_legacy_override(str(self.path), LEGACY_DISPLAYS)
        self.assertEqual(self.path.read_text(), custom)
        self.path.write_text(LEGACY_DISPLAYS)
        remove_legacy_override(str(self.path), LEGACY_DISPLAYS)
        self.assertFalse(self.path.exists())

    def test_setup_rerun_preserves_user_settings(self):
        from desktop.desktop_environment_steps import configure_dark_theme, configure_xfce_for_rdp
        from lib.config import SetupConfig

        home = self.path.parent
        config_dir = home / ".config/xfce4/xfconf/xfce-perchannel-xml"
        config_dir.mkdir(parents=True)
        appearance = config_dir / "xsettings.xml"
        appearance.write_text('''<channel name="xsettings"><property name="Gtk" type="empty">
          <property name="FontName" type="string" value="Custom 12"/></property></channel>''')
        displays = config_dir / "displays.xml"
        displays.write_text('<channel name="displays"><property name="custom"/></channel>')
        config = SetupConfig(host="vm", username="agent", system_type="workstation_dev", desktop="xfce", dark_theme=True)
        with (patch("desktop.desktop_environment_steps.apply_home_settings",
                    side_effect=lambda username, edits: home_setup._apply(home, edits)),
              patch("desktop.desktop_environment_steps.glob.glob", return_value=[]),
              patch("desktop.desktop_environment_steps.remove_legacy_pm_stub")):
            for _ in range(2):
                configure_xfce_for_rdp(config)
                configure_dark_theme(config)
        self.assertIn('value="Custom 12"', appearance.read_text())
        self.assertIn('value="Adwaita-dark"', appearance.read_text())
        self.assertEqual(displays.read_text(), '<channel name="displays"><property name="custom"/></channel>')
        self.assertFalse((config_dir / "xfwm4.xml").exists())

    def test_setup_invokes_helper_as_desktop_user(self):
        edits = [("write", ".config/mimeapps.list", "browser settings")]
        with (patch.object(home_setup.pwd, "getpwnam", return_value=SimpleNamespace(pw_uid=1000)),
              patch.object(home_setup, "run") as run):
            home_setup.apply_home_settings("alice", edits)
        command = run.call_args.args[0]
        self.assertEqual(command[:8], ["/usr/sbin/runuser", "-u", "alice", "--",
                                       "/usr/bin/python3", "-I", "-B", "-c"])
        self.assertEqual(command[-1], "alice")
        self.assertEqual(json.loads(run.call_args.kwargs["input_data"]), [list(edits[0])])

    def test_home_worker_requires_selected_uid_and_preserves_other_files(self):
        home = self.path.parent
        edits = [("write", ".config/mimeapps.list", "browser settings")]
        account = SimpleNamespace(pw_uid=1000, pw_dir=str(home))
        with (patch.object(home_setup.pwd, "getpwnam", return_value=account),
              patch.object(home_setup.sys, "argv", ["-c", "source", "alice"]),
              patch.object(home_setup.os, "umask"),
              patch.object(home_setup.os, "getuid", return_value=1000),
              patch.object(home_setup.os, "geteuid", return_value=0) as euid):
            with self.assertRaisesRegex(ValueError, "selected non-root account"):
                home_setup.main()
            settings = home / ".config/mimeapps.list"
            self.assertFalse(settings.exists())
            euid.return_value = 1000
            with patch.object(home_setup.sys, "stdin", io.StringIO(json.dumps(edits))):
                home_setup.main()
            self.assertEqual(settings.read_text(), "browser settings")
            unrelated = settings.with_name("other")
            unrelated.write_text("keep")
            before = unrelated.stat()
            with patch.object(home_setup.sys, "stdin", io.StringIO(json.dumps(edits))):
                home_setup.main()
            self.assertEqual(unrelated.read_text(), "keep")
            self.assertEqual(unrelated.stat().st_uid, before.st_uid)
            self.assertEqual(unrelated.stat().st_ino, before.st_ino)

    def test_user_home_edits_preserve_linked_targets(self):
        home = self.path.parent / "home"
        home.mkdir()
        outside = self.path.parent / "separate"
        outside.mkdir()
        linked = outside / "mimeapps.list"
        linked.write_text("keep")
        config = home / ".config"
        config.symlink_to(outside, target_is_directory=True)
        edits = [("write", ".config/mimeapps.list", "replace")]
        with self.assertRaisesRegex(ValueError, "symlink"):
            home_setup._apply(home, edits)
        config.unlink()
        config.mkdir()
        (config / "mimeapps.list").symlink_to(linked)
        with self.assertRaisesRegex(ValueError, "linked"):
            home_setup._apply(home, edits)
        self.assertEqual(linked.read_text(), "keep")
        (config / "mimeapps.list").unlink()
        (config / "mimeapps.list").hardlink_to(linked)
        home_setup._apply(home, edits)
        self.assertEqual((config / "mimeapps.list").read_text(), "replace")
        self.assertEqual(linked.read_text(), "keep")
