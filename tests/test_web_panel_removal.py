"""Failure boundaries for removing the panel and its audit exporter."""

from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from common import web_panel_steps as panel


class WebPanelRemovalTest(unittest.TestCase):
    def setUp(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        paths = {
            "WEB_PANEL_SERVICE_FILE": root / "panel.service",
            "WEB_PANEL_AUDIT_SERVICE_FILE": root / "audit.service",
            "WEB_PANEL_AUDIT_TIMER_FILE": root / "audit.timer",
            "WEB_PANEL_CONFIG_DIR": root / "config",
            "WEB_PANEL_MANIFEST": root / "config" / "config.json",
            "WEB_PANEL_AUTH_FILE": root / "config" / "htpasswd",
            "WEB_PANEL_INGEST_TOKEN": root / "config" / "ingest.token",
            "WEB_PANEL_DATA_DIR": root / "data",
            "WEB_PANEL_AUDIT_DIR": root / "data" / "audit",
            "WEB_PANEL_NOTIFICATION_DIR": root / "data" / "notifications",
            "WEB_PANEL_AUDIT_SNAPSHOT": root / "data" / "audit" / "events.json",
            "WEB_PANEL_NOTIFICATION_LOG": root / "data" / "notifications" / "events.jsonl",
            "WEB_PANEL_NGINX_SITE": root / "site",
            "WEB_PANEL_NGINX_LINK": root / "link",
        }
        for name, path in paths.items():
            if name.endswith("_DIR"):
                path.mkdir(parents=True, exist_ok=True)
        self.files = {}
        for name, path in paths.items():
            if name.endswith(("_DIR", "_LINK")):
                continue
            payload = "# Managed by basaltwater web panel\n" if name.endswith(("_FILE", "_SITE")) else "retained private data\n"
            path.write_text(payload)
            path.chmod(0o600)
            self.files[path] = (payload, path.stat().st_ino, path.stat().st_mode)
        paths["WEB_PANEL_NGINX_LINK"].symlink_to(paths["WEB_PANEL_NGINX_SITE"])
        self.paths = paths
        self.storage = paths["WEB_PANEL_AUDIT_DIR"] / "storage.json"
        self.storage.write_text("managed storage snapshot\n")
        self.storage.chmod(0o600)
        self.files[self.storage] = (self.storage.read_text(), self.storage.stat().st_ino, self.storage.stat().st_mode)
        self.enterContext(patch.multiple(panel, **{name: str(path) for name, path in paths.items()}))
        for module, names in (
            ("desktop.guacamole_steps", ("preflight_units", "preflight_configuration", "remove_guacamole")),
            ("web.github_runner_registration_steps", ("preflight_registration_units", "remove_registration_service")),
            ("common.proxmox_web_panel_steps", ("preflight_proxmox_snapshot", "remove_proxmox_snapshot")),
        ):
            for name in names:
                mock = self.enterContext(patch(f"{module}.{name}"))
                if name.startswith("remove_"):
                    setattr(self, name, mock)
        self.enterContext(patch("common.proxmox_web_panel_steps.proxmox_snapshot_present", return_value=False))
        self.enterContext(patch.object(panel.shutil, "which", return_value=None))
        self.missing_unit = self.enterContext(patch.object(panel, "_missing_panel_unit_needs_stop", return_value=False))
        self.remove_ban = self.enterContext(patch.object(panel, "remove_nginx_auth_failure_ban"))

    def assert_installation_preserved(self) -> None:
        for path, expected in self.files.items():
            self.assertTrue(path.is_file(), str(path))
            self.assertEqual((path.read_text(), path.stat().st_ino, path.stat().st_mode), expected)
        self.assertTrue(self.paths["WEB_PANEL_NGINX_LINK"].is_symlink())
        for name in ("remove_guacamole", "remove_registration_service", "remove_proxmox_snapshot", "remove_ban"):
            getattr(self, name).assert_not_called()

    def test_refused_stop_preserves_units_configuration_credentials_and_events(self) -> None:
        commands = (
            "systemctl disable --now basaltwater-web-panel-audit.timer",
            "systemctl stop basaltwater-web-panel-audit.service",
            "systemctl disable --now basaltwater-web-panel.service",
        )
        for failed in commands:
            with self.subTest(command=failed):
                def run(command, *, check=True, **kwargs):
                    if command == failed and check:
                        raise subprocess.CalledProcessError(1, command)
                    return subprocess.CompletedProcess(command, 1 if command == failed else 0)

                with patch.object(panel, "run", side_effect=run):
                    with self.assertRaises(subprocess.CalledProcessError):
                        panel.remove_web_panel()
                self.assert_installation_preserved()

    def test_partial_audit_installations_remove_only_the_present_units(self) -> None:
        for missing in ("WEB_PANEL_AUDIT_SERVICE_FILE", "WEB_PANEL_AUDIT_TIMER_FILE"):
            with self.subTest(missing=missing):
                path = self.paths[missing]
                path.unlink()
                with patch.object(panel, "run", return_value=subprocess.CompletedProcess([], 0)) as run:
                    panel.remove_web_panel()
                commands = [call.args[0] for call in run.call_args_list]
                absent_command = (
                    "systemctl stop basaltwater-web-panel-audit.service"
                    if missing == "WEB_PANEL_AUDIT_SERVICE_FILE"
                    else "systemctl disable --now basaltwater-web-panel-audit.timer"
                )
                self.assertNotIn(absent_command, commands)
                for present in self.files:
                    self.assertFalse(present.exists(), str(present))
                # Restore only this temporary fixture for the other variant.
                for present, (payload, _inode, _mode) in self.files.items():
                    present.parent.mkdir(parents=True, exist_ok=True)
                    present.write_text(payload)
                self.paths["WEB_PANEL_NGINX_LINK"].symlink_to(self.paths["WEB_PANEL_NGINX_SITE"])

    def test_removal_deletes_managed_storage_snapshot_but_preserves_unknown_files(self) -> None:
        retained = self.storage.parent / "operator-note"
        retained.write_text("keep this file\n")
        with patch.object(panel, "run", return_value=subprocess.CompletedProcess([], 0)):
            panel.remove_web_panel()
        self.assertFalse(self.storage.exists())
        self.assertEqual(retained.read_text(), "keep this file\n")

    def test_removal_rejects_a_symlinked_storage_snapshot_before_mutation(self) -> None:
        self.storage.unlink()
        target = self.storage.parent / "unowned-data"
        target.write_text("preserved\n")
        self.storage.symlink_to(target)
        with patch.object(panel, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "unsafe.*event file"):
                panel.remove_web_panel()
        run.assert_not_called()
        self.assertEqual(target.read_text(), "preserved\n")
        self.assertTrue(self.paths["WEB_PANEL_SERVICE_FILE"].is_file())

    def test_missing_unit_file_does_not_bypass_a_refused_stop_of_a_live_exporter(self) -> None:
        self.paths["WEB_PANEL_AUDIT_SERVICE_FILE"].unlink()
        del self.files[self.paths["WEB_PANEL_AUDIT_SERVICE_FILE"]]
        self.missing_unit.return_value = True
        failed = "systemctl stop basaltwater-web-panel-audit.service"

        def run(command, *, check=True, **kwargs):
            if command == failed and check:
                raise subprocess.CalledProcessError(1, command)
            return subprocess.CompletedProcess(command, 1 if command == failed else 0)

        with patch.object(panel, "run", side_effect=run):
            with self.assertRaises(subprocess.CalledProcessError):
                panel.remove_web_panel()
        self.missing_unit.assert_called_once_with("basaltwater-web-panel-audit.service")
        self.assert_installation_preserved()


class MissingPanelUnitInspectionTest(unittest.TestCase):
    def test_only_explicitly_absent_inactive_unenabled_unit_can_skip_stop(self) -> None:
        for load, active, enabled, expected in (
            ("not-found", "inactive", "", False),
            ("not-found", "active", "", True),
            ("loaded", "activating", "static", True),
            ("loaded", "inactive", "enabled", True),
        ):
            with self.subTest(load=load, active=active, enabled=enabled), patch.object(
                panel, "run", return_value=subprocess.CompletedProcess([], 0,
                    stdout=f"LoadState={load}\nActiveState={active}\nUnitFileState={enabled}\n"),
            ):
                self.assertEqual(panel._missing_panel_unit_needs_stop("basaltwater-web-panel-audit.service"), expected)

    def test_missing_or_invalid_inspection_never_proves_absence(self) -> None:
        for output in ("", "LoadState=not-found\n", "LoadState=not-found\nActiveState=inactive\n",
                       "LoadState=masked\nActiveState=inactive\nUnitFileState=masked\n"):
            with self.subTest(output=output), patch.object(
                panel, "run", return_value=subprocess.CompletedProcess([], 0, stdout=output),
            ):
                with self.assertRaisesRegex(RuntimeError, "Cannot inspect"):
                    panel._missing_panel_unit_needs_stop("basaltwater-web-panel-audit.service")
        with patch.object(panel, "run", side_effect=subprocess.CalledProcessError(1, "systemctl show")):
            with self.assertRaises(subprocess.CalledProcessError):
                panel._missing_panel_unit_needs_stop("basaltwater-web-panel-audit.service")


if __name__ == "__main__":
    unittest.main()
