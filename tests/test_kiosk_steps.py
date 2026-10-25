"""Managed kiosk setup contracts with temporary files and mocked system calls."""

from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import shlex
import subprocess
import tempfile
import tomllib
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from desktop import kiosk_steps as steps
from lib.config import SetupConfig


def config(**options):
    values = dict(host="pve.example", username="root", system_type="server_proxmox", machine_type="hardware")
    return SetupConfig(**(values | options))


class KioskRenderingTests(unittest.TestCase):
    def test_homepage_prefers_override_then_panel_then_native_ui(self):
        for options, expected in (
            ({}, "https://pve.example:8006/"),
            ({"web_panel_port": 9443, "enable_ssl": True}, "https://pve.example:9443/"),
            ({"web_panel_port": 80}, "http://pve.example/"),
            ({"web_panel_port": 9443, "disable_web_panel": True}, "https://pve.example:8006/"),
            ({"kiosk_url": "http://localhost/custom#guest"}, "http://localhost/custom#guest"),
        ):
            with self.subTest(options=options):
                self.assertEqual(steps.kiosk_url(config(kiosk_mode="web", **options)), expected)
        ipv6 = config(kiosk_mode="web")
        ipv6.host = "2001:db8::1"
        self.assertEqual(steps.kiosk_url(ipv6), "https://[2001:db8::1]:8006/")

    def test_rendered_units_and_toml_use_immutable_data_and_safe_commands(self):
        with tempfile.TemporaryDirectory(prefix="kiosk '$% ") as directory:
            source = Path(directory)
            (source / "desktop").mkdir()
            (source / "desktop/kiosk_runtime.py").touch()
            url = "https://pve.example/a'b?x=$(id)&y=`id`#v1"
            files, unit = steps.render_configuration(url, source)
            session = next(path for path in files if path.suffix == ".json")
            greetd = tomllib.loads(next(value for path, value in files.items() if path.suffix == ".toml"))
            self.assertEqual(json.loads(files[session])["url"], url)
            argv = shlex.split(greetd["initial_session"]["command"])
            self.assertEqual(argv[-2:], ["--config", str(session)])
            self.assertIn(str(source / "desktop/kiosk_runtime.py"), argv)
            self.assertNotIn(url, unit)
            self.assertNotIn(url, greetd["initial_session"]["command"])
            self.assertEqual(greetd["initial_session"]["user"], steps.USER)
            self.assertFalse(greetd["general"]["source_profile"])
            self.assertEqual(greetd["terminal"]["vt"], 7)
            self.assertIn('RuntimeDirectory=basaltwater-kiosk', unit)
            self.assertIn("Restart=always\n", unit)
            self.assertIn("StartLimitBurst=3\n", unit)
            self.assertIn("TimeoutStopSec=60\n", unit)
            self.assertIn("ExecStartPre=/usr/bin/python3 -I ", unit)
            self.assertIn("StartLimitIntervalSec=600\n", unit)
            self.assertIn("cleanup\n", unit)
            self.assertIn("WantedBy=multi-user.target", unit)
            self.assertIn("After=network.target pveproxy.service nginx.service basaltwater-web-panel.service\n", unit)
            self.assertNotIn("Alias=display-manager.service", unit)
            self.assertIn("$$%%", unit)
            self.assertEqual(steps.render_configuration(url, source), (files, unit))
            changed, _ = steps.render_configuration("https://pve.example/other", source)
            self.assertTrue(set(files).isdisjoint(changed))
            destinations = [{"name": "VM", "url": "https://vm.example/"}]
            changed, changed_unit = steps.render_configuration(url, source, destinations=destinations)
            self.assertTrue(set(files).isdisjoint(changed))
            self.assertNotEqual(unit, changed_unit)
            self.assertEqual(next(json.loads(content)["destinations"] for path, content in changed.items()
                                  if path.suffix == ".json"), destinations)
            self.assertEqual(steps.render_configuration(url, source, destinations=[]), (files, unit))

    def test_plugin_opt_in_preflights_before_profile_mutations(self):
        from plugins.proxmox import build_server_proxmox_steps

        for mode in (None, "web", "cli", "off"):
            with self.subTest(mode=mode):
                functions = [function for _, function in build_server_proxmox_steps(config(kiosk_mode=mode))]
                if mode is None:
                    self.assertNotIn(steps.configure_kiosk, functions)
                else:
                    self.assertIs(functions[0], steps.preflight_kiosk)
                    self.assertIs(functions[-1], steps.configure_kiosk)


class KioskLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for name, suffix in (("UNIT_DIR", "systemd"), ("CONFIG_DIR", "config")):
            path = self.root / suffix
            path.mkdir()
            self.enterContext(patch.object(steps, name, path))
        self.enterContext(patch.object(steps, "PRESET", self.root / "preset"))
        self.enterContext(patch.object(steps, "preflight_kiosk"))
        self.packages = self.enterContext(patch.object(steps, "_install_packages"))
        self.account = self.enterContext(patch.object(steps, "_install_account"))
        self.cleanup = self.enterContext(patch.object(steps, "cleanup_sessions"))
        self.enterContext(patch.object(steps, "_root_directory"))
        self.enterContext(patch.object(steps, "is_dry_run", return_value=False))
        self.enterContext(redirect_stdout(io.StringIO()))
        self.state = {"LoadState": "not-found", "ActiveState": "inactive", "UnitFileState": ""}
        self.enterContext(patch.object(steps, "inspect_unit_state", side_effect=lambda _: dict(self.state)))
        snapshot = steps.snapshot_unit_file

        def root_snapshot(path):
            value = snapshot(path)
            if value is not None:
                value["uid"] = 0
            return value

        self.enterContext(patch.object(steps, "snapshot_unit_file", side_effect=root_snapshot))
        self.replace = self.enterContext(patch.object(steps, "replace_units", side_effect=self.publish))
        self.remove = self.enterContext(patch.object(steps, "remove_units"))

    def publish(self, units, *, activate, deactivate, unit_dir, prepare=None):
        if prepare:
            prepare()
        for name, content in units.items():
            (Path(unit_dir) / name).write_text(content)
            (Path(unit_dir) / name).chmod(0o644)
        self.state.update(LoadState="loaded", ActiveState="active" if activate else "inactive",
                          UnitFileState="enabled" if activate else "disabled")

    def test_web_installs_and_starts_then_unchanged_rerun_preserves_session(self):
        settings = config(kiosk_mode="web")
        steps.configure_kiosk(settings)
        self.assertEqual(self.replace.call_args.kwargs["activate"], (steps.SERVICE,))
        self.assertEqual(len(list(steps.CONFIG_DIR.iterdir())), 2)
        self.replace.reset_mock()
        steps.configure_kiosk(settings)
        self.replace.assert_not_called()

    def test_cli_installs_but_never_activates_browser_and_stops_previous_web_mode(self):
        settings = config(kiosk_mode="cli")
        steps.configure_kiosk(settings)
        self.assertEqual(self.replace.call_args.kwargs["activate"], ())
        self.assertEqual(self.replace.call_args.kwargs["deactivate"], ())
        self.replace.reset_mock()
        steps.configure_kiosk(settings)
        self.replace.assert_not_called()
        self.state.update(ActiveState="active", UnitFileState="enabled")
        steps.configure_kiosk(settings)
        self.assertEqual(self.replace.call_args.kwargs["deactivate"], (steps.SERVICE,))

    def test_changed_url_restarts_web_and_retains_prior_generation_for_rollback(self):
        steps.configure_kiosk(config(kiosk_mode="web"))
        old = set(steps.CONFIG_DIR.iterdir())
        previous = (steps.UNIT_DIR / steps.SERVICE).read_text()
        self.replace.side_effect = RuntimeError("activation failed; unit transaction restored prior unit")
        with self.assertRaisesRegex(RuntimeError, "activation failed"):
            steps.configure_kiosk(config(kiosk_mode="web", kiosk_url="https://pve.example/new"))
        self.assertEqual((steps.UNIT_DIR / steps.SERVICE).read_text(), previous)
        self.assertTrue(all(path.exists() for path in old))
        self.assertEqual(len(list(steps.CONFIG_DIR.iterdir())), 4)

    def test_destinations_restart_web_once_and_clear_returns_to_direct_homepage(self):
        steps.configure_kiosk(config(kiosk_mode="web"))
        old_unit = (steps.UNIT_DIR / steps.SERVICE).read_text()
        settings = config(kiosk_mode="web", kiosk_destinations=[{"name": "VM", "url": "https://vm.example/"}])
        self.replace.reset_mock()
        steps.configure_kiosk(settings)
        self.assertEqual(self.replace.call_count, 1)
        self.assertNotEqual((steps.UNIT_DIR / steps.SERVICE).read_text(), old_unit)
        self.replace.reset_mock()
        steps.configure_kiosk(settings)
        self.replace.assert_not_called()
        steps.configure_kiosk(config(kiosk_mode="web", kiosk_destinations=[]))
        self.assertEqual(self.replace.call_count, 1)
        self.assertEqual((steps.UNIT_DIR / steps.SERVICE).read_text(), old_unit)

    def test_disable_removes_only_owned_configuration_and_retains_home(self):
        steps.configure_kiosk(config(kiosk_mode="cli"))
        files = list(steps.CONFIG_DIR.iterdir())
        steps.PRESET.write_text(steps.MARKER + "disable greetd.service\n")
        steps.PRESET.chmod(0o644)
        with patch.object(steps, "_configuration_files", return_value=files):
            steps.configure_kiosk(config(kiosk_mode="off"))
        self.remove.assert_called_once_with((steps.SERVICE,), unit_dir=str(steps.UNIT_DIR), prepare=self.cleanup)
        self.assertFalse(steps.PRESET.exists())
        self.assertFalse(list(steps.CONFIG_DIR.iterdir()))
        self.assertEqual(self.packages.call_count, 1)
        self.assertEqual(self.account.call_count, 1)

    def test_removal_failure_preserves_files_and_preset(self):
        steps.configure_kiosk(config(kiosk_mode="cli"))
        files = list(steps.CONFIG_DIR.iterdir())
        steps.PRESET.write_text(steps.MARKER + "disable greetd.service\n")
        steps.PRESET.chmod(0o644)
        self.remove.side_effect = RuntimeError("stop failed")
        with self.assertRaisesRegex(RuntimeError, "stop failed"):
            steps.configure_kiosk(config(kiosk_mode="off"))
        self.assertTrue(steps.PRESET.exists())
        self.assertTrue(all(path.exists() for path in files))

    def test_successful_stop_with_failed_teardown_cannot_complete_cli_or_removal(self):
        steps.configure_kiosk(config(kiosk_mode="web"))
        files = list(steps.CONFIG_DIR.iterdir())
        steps.PRESET.write_text(steps.MARKER + "disable greetd.service\n")
        steps.PRESET.chmod(0o644)
        self.cleanup.side_effect = RuntimeError("PAM scopes are still stopping")
        self.remove.side_effect = lambda _names, *, unit_dir, prepare: prepare()
        for mode in ("cli", "off"):
            self.state.update(ActiveState="failed", UnitFileState="disabled")
            with self.subTest(mode=mode), self.assertRaisesRegex(RuntimeError, "still stopping"):
                steps.configure_kiosk(config(kiosk_mode=mode))
            self.assertTrue(steps.PRESET.exists())
            self.assertTrue(all(path.exists() for path in files))

    def test_unchanged_cli_checks_for_orphaned_pam_sessions(self):
        settings = config(kiosk_mode="cli")
        steps.configure_kiosk(settings)
        self.replace.reset_mock()
        self.cleanup.side_effect = RuntimeError("orphaned browser")
        with self.assertRaisesRegex(RuntimeError, "orphaned browser"):
            steps.configure_kiosk(settings)
        self.replace.assert_not_called()

    def test_missing_unit_does_not_bypass_teardown_before_config_removal(self):
        steps.configure_kiosk(config(kiosk_mode="cli"))
        (steps.UNIT_DIR / steps.SERVICE).unlink()
        self.state.update(LoadState="not-found")
        files = list(steps.CONFIG_DIR.iterdir())
        self.cleanup.side_effect = RuntimeError("orphaned browser")
        with patch.object(steps, "_account", return_value=Mock()), \
             self.assertRaisesRegex(RuntimeError, "orphaned browser"):
            steps.configure_kiosk(config(kiosk_mode="off"))
        self.remove.assert_not_called()
        self.assertTrue(all(path.exists() for path in files))

    def test_service_without_managed_file_must_be_unloaded_inactive_and_disabled(self):
        steps.configure_kiosk(config(kiosk_mode="web"))
        (steps.UNIT_DIR / steps.SERVICE).unlink()
        files = list(steps.CONFIG_DIR.iterdir())
        self.cleanup.reset_mock()
        self.packages.reset_mock()
        for load, active, enabled in (("loaded", "active", "enabled"),
                                      ("not-found", "active", ""),
                                      ("not-found", "activating", ""),
                                      ("not-found", "inactive", "enabled"),
                                      ("not-found", "inactive", "enabled-runtime")):
            self.state.update(LoadState=load, ActiveState=active, UnitFileState=enabled)
            for mode in ("web", "cli", "off"):
                with self.subTest(state=self.state, mode=mode), self.assertRaisesRegex(
                        RuntimeError, "without its managed file"):
                    steps.configure_kiosk(config(kiosk_mode=mode))
                self.assertTrue(all(path.exists() for path in files))
        self.cleanup.assert_not_called()
        self.packages.assert_not_called()
        self.remove.assert_not_called()

    def test_unmanaged_unit_is_refused_before_package_or_account_changes(self):
        (steps.UNIT_DIR / steps.SERVICE).write_text("[Service]\nExecStart=/custom\n")
        with self.assertRaisesRegex(RuntimeError, "Unmanaged"):
            steps.configure_kiosk(config(kiosk_mode="web"))
        self.packages.assert_not_called()
        self.account.assert_not_called()
        self.replace.assert_not_called()

    def test_dry_run_has_no_package_account_or_unit_mutations(self):
        with patch.object(steps, "is_dry_run", return_value=True):
            steps.configure_kiosk(config(kiosk_mode="web"))
        for mutation in (self.packages, self.account, self.replace, self.remove):
            mutation.assert_not_called()
        self.assertFalse(list(steps.CONFIG_DIR.iterdir()))


class KioskPreflightTests(unittest.TestCase):
    def seat(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for name in ("CONFIG_DIR", "UNIT_DIR", "HOME", "DISPLAY_MANAGER", "VENDOR_MASK", "PRESET"):
            self.enterContext(patch.object(steps, name, root / name))
        self.enterContext(patch.object(steps, "check_directory_path"))
        self.enterContext(patch.object(steps, "directory_fd"))
        self.enterContext(patch.object(steps.os, "fstat", return_value=SimpleNamespace(st_uid=0, st_mode=0o40755)))
        self.enterContext(patch.object(steps, "_owned"))
        self.enterContext(patch.object(steps, "_configuration_files", return_value=[]))
        self.enterContext(patch.object(steps, "_account", return_value=None))
        self.enterContext(patch.object(steps, "_vendor_state"))
        self.enterContext(patch.object(steps, "is_dry_run", return_value=False))
        self.enterContext(patch.object(steps, "read_os_release", return_value={"ID": "debian", "VERSION_ID": "13"}))
        self.enterContext(patch.object(steps, "VT", Mock(exists=Mock(return_value=True))))
        drm = self.enterContext(patch.object(steps, "DRM_DIR", Mock()))
        drm.glob.return_value = [root / "card0"]
        self.enterContext(patch.object(steps, "INPUT_DIR", Mock(glob=Mock(return_value=[root / "event0"]))))
        run = self.enterContext(patch.object(steps, "run", return_value=Mock(stdout="")))
        return drm, run

    def test_missing_graphics_rejects_before_installation(self):
        drm, _ = self.seat()
        drm.glob.return_value = []
        with patch.object(steps, "_install_packages") as install, self.assertRaisesRegex(RuntimeError, "DRM"):
            steps.configure_kiosk(config(kiosk_mode="web"))
        install.assert_not_called()

    def test_container_cannot_claim_a_seat(self):
        self.seat()
        with self.assertRaisesRegex(ValueError, "not a container"):
            steps.preflight_kiosk(config(kiosk_mode="web", machine_type="privileged"))

    def test_existing_display_manager_and_other_graphical_session_are_preserved(self):
        _, run = self.seat()
        steps.DISPLAY_MANAGER.write_text("custom")
        with self.assertRaisesRegex(RuntimeError, "display manager"):
            steps.preflight_kiosk(config(kiosk_mode="web"))
        steps.DISPLAY_MANAGER.unlink()
        run.side_effect = [Mock(stdout="3 1000 agent seat0 tty2\n"),
                           Mock(stdout="Name=agent\nTTY=tty2\nType=wayland\n")]
        with self.assertRaisesRegex(RuntimeError, "Log out"):
            steps.preflight_kiosk(config(kiosk_mode="web"))

    def test_off_does_not_require_a_working_graphical_seat(self):
        drm, run = self.seat()
        drm.glob.return_value = []
        steps.preflight_kiosk(config(kiosk_mode="off"))
        run.assert_not_called()

    def test_no_feature_and_dry_run_do_not_probe_local_seat(self):
        with patch.object(steps, "run") as run, patch.object(steps, "is_dry_run", return_value=True), \
             redirect_stdout(io.StringIO()):
            steps.preflight_kiosk(config())
            steps.preflight_kiosk(config(kiosk_mode="web", machine_type="hardware"))
        run.assert_not_called()

    def test_unsupported_service_capability_fails_without_system_calls(self):
        with patch.object(steps, "run") as run, patch.object(steps, "can_manage_system_services", return_value=False), \
             self.assertRaisesRegex(ValueError, "systemd"):
            steps.preflight_kiosk(config(kiosk_mode="cli"))
        run.assert_not_called()

    def test_account_refuses_root_other_home_unlocked_password_and_extra_groups(self):
        account = SimpleNamespace(pw_uid=12345, pw_gid=12345, pw_gecos=steps.COMMENT,
                                  pw_dir=str(steps.HOME), pw_shell="/usr/sbin/nologin")
        with patch.object(steps.pwd, "getpwnam", return_value=account), \
             patch.object(steps.grp, "getgrgid", return_value=SimpleNamespace(gr_name=steps.USER)), \
             patch.object(steps, "run") as run:
            run.side_effect = [Mock(stdout="12345\n"), Mock(stdout=f"{steps.USER} L 2026-10-24 0 99999 7 -1\n")]
            self.assertIs(steps._account(), account)
            run.side_effect = [Mock(stdout="12345 27\n")]
            with self.assertRaisesRegex(RuntimeError, "primary group"):
                steps._account()
            run.side_effect = [Mock(stdout="12345\n"), Mock(stdout=f"{steps.USER} P 2026-10-24\n")]
            with self.assertRaisesRegex(RuntimeError, "locked password"):
                steps._account()
            account.pw_uid = 0
            with self.assertRaisesRegex(RuntimeError, "migration"):
                steps._account()

    def test_vendor_service_must_be_inactive_and_disabled(self):
        for output in ("LoadState=loaded\nActiveState=active\nUnitFileState=disabled\n",
                       "LoadState=loaded\nActiveState=inactive\nUnitFileState=enabled\n", ""):
            with self.subTest(output=output), patch.object(steps, "run", return_value=Mock(stdout=output)), \
                 self.assertRaisesRegex(RuntimeError, "existing greetd"):
                steps._vendor_state()

    def test_apt_failure_unmasks_its_temporary_guard_and_does_not_hide_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            preset = Path(directory) / "kiosk.preset"
            with patch.object(steps, "PRESET", preset), \
                 patch.object(steps, "VENDOR_MASK", Path(directory) / "mask"), \
                 patch.object(steps, "_root_directory"), \
                 patch.object(steps, "run", side_effect=[Mock(), subprocess.CalledProcessError(1, "apt-get"), Mock()]) as run:
                with self.assertRaises(subprocess.CalledProcessError):
                    steps._install_packages()
            self.assertEqual(run.call_args.args[0], ["systemctl", "unmask", "--runtime", "greetd.service"])
            self.assertEqual(preset.read_text(), steps.MARKER + "disable greetd.service\n")

    def test_package_success_retains_an_existing_administrator_mask(self):
        with tempfile.TemporaryDirectory() as directory:
            preset = Path(directory) / "kiosk.preset"
            mask = Path(directory) / "greetd.service"
            mask.symlink_to("/dev/null")
            with patch.object(steps, "PRESET", preset), patch.object(steps, "VENDOR_MASK", mask), \
                 patch.object(steps, "_root_directory"), patch.object(steps, "_vendor_state") as verify, \
                 patch.object(steps.os, "access", return_value=True), patch.object(steps, "run") as run:
                steps._install_packages()
            self.assertEqual(run.call_count, 1)
            self.assertIn("--no-remove", run.call_args.args[0])
            self.assertIn("firefox-esr", run.call_args.args[0])
            verify.assert_called_once()
            self.assertTrue(mask.is_symlink())
            self.assertEqual(mask.readlink(), Path("/dev/null"))

    def test_root_directory_rejects_symlink_without_touching_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "target"
            target.mkdir(mode=0o750)
            target.chmod(0o750)
            link = Path(directory) / "link"
            link.symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symlink"):
                steps._root_directory(link)
            self.assertEqual(target.stat().st_mode & 0o777, 0o750)


if __name__ == "__main__":
    unittest.main()
