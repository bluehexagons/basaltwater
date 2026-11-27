"""Tests for desktop.browser_steps helper behavior."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from desktop import browser_extensions
from desktop.browser_steps import (
    _configure_librewolf_apparmor_profile,
    _ensure_extrepo_and_update,
    _install_via_extrepo,
    _refresh_existing_extrepo_sources,
    install_single_browser,
    install_browser,
    is_flatpak_app_installed,
)


class TestBrowserSteps(unittest.TestCase):
    def test_requested_browser_failures_stop_setup(self):
        for browser, flatpak in (("chromium", False), ("firefox", False), ("librewolf", False),
                                 ("lynx", False),
                                 ("chromium", True), ("firefox", True), ("librewolf", True)):
            for code in (0, 1):
                with self.subTest(browser=browser, flatpak=flatpak, code=code), \
                        patch("desktop.browser_steps.is_package_installed", return_value=False), \
                        patch("desktop.browser_steps.is_flatpak_app_installed", return_value=False), \
                        patch("desktop.browser_steps.install_flatpak_if_needed", return_value=True), \
                        patch("desktop.browser_steps.shutil.which", return_value=None), \
                        patch("desktop.browser_steps.os.path.exists", return_value=False), \
                        patch("desktop.browser_steps._install_via_extrepo", return_value=False), \
                        patch("desktop.browser_steps.run", return_value=SimpleNamespace(
                            returncode=code, stdout="https://example.test/browser.deb\n", stderr="")):
                    with self.assertRaisesRegex(RuntimeError, "LibreWolf" if browser == "librewolf" else browser.capitalize()):
                        install_single_browser(browser, use_flatpak=flatpak)

    def test_extrepo_install_requires_successful_command_and_present_package(self):
        for code, installed, expected in ((1, True, False), (0, False, False), (0, True, True)):
            with self.subTest(code=code, installed=installed), \
                    patch("desktop.browser_steps._ensure_extrepo_and_update"), \
                    patch("desktop.browser_steps._update_apt_metadata"), \
                    patch("desktop.browser_steps.os.path.exists", return_value=True), \
                    patch("desktop.browser_steps.is_package_installed", return_value=installed), \
                    patch("desktop.browser_steps.run", return_value=SimpleNamespace(returncode=code)):
                self.assertEqual(_install_via_extrepo("LibreWolf", "librewolf", "librewolf"), expected)

    def test_failed_first_browser_stops_before_installing_later_selections(self):
        with patch("desktop.browser_steps.is_package_installed", return_value=False), \
                patch("desktop.browser_steps.run", return_value=SimpleNamespace(returncode=1)) as run:
            with self.assertRaisesRegex(RuntimeError, "Firefox"):
                install_browser(SimpleNamespace(use_flatpak=False, browsers=["firefox", "lynx"], browser="firefox", dry_run=False))
        run.assert_called_once_with("apt-get install -y -qq firefox-esr", check=False)

    def test_chromium_uses_debian_package_and_skips_installed_copy(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(browser_extensions, "_CHROMIUM_ENTRY", Path(temp) / "extension.json") as entry, \
                patch("desktop.browser_steps.is_package_installed", side_effect=[False, True, True]) as installed, \
                patch("desktop.browser_steps.run", return_value=SimpleNamespace(returncode=0)) as run:
            install_single_browser("chromium", use_flatpak=True)
            self.assertEqual(json.loads(entry.read_text()), {"external_update_url": "https://clients2.google.com/service/update2/crx"})
            entry.unlink()
            install_single_browser("chromium", use_flatpak=False)
            self.assertEqual(entry.stat().st_mode & 0o777, 0o644)
        self.assertEqual([call.args for call in installed.call_args_list], [("chromium",)] * 3)
        run.assert_called_once_with("apt-get install -y -qq chromium", check=False)

    def test_native_firefox_repairs_and_verifies_blocker_on_rerun(self):
        with patch("desktop.browser_steps.is_package_installed", return_value=True), \
                patch.object(browser_extensions, "is_package_installed", side_effect=[False, True, True, False, False]), \
                patch.object(browser_extensions, "run") as apt:
            install_single_browser("firefox", False)
            install_single_browser("firefox", False)
            apt.assert_called_once_with(["apt-get", "install", "--no-install-recommends", "-y", "-qq",
                                         "webext-ublock-origin-firefox"])
            with self.assertRaisesRegex(RuntimeError, "missing webext-ublock-origin-firefox"):
                install_single_browser("firefox", False)

    def test_firefox_flatpak_seeds_only_its_own_branch_and_preserves_existing_policy(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(browser_extensions, "_FLATPAK_SYSTEMCONFIG", Path(temp)), \
                patch("desktop.browser_steps.is_flatpak_app_installed", side_effect=[False, True, True, True]), \
                patch("desktop.browser_steps.install_flatpak_if_needed", return_value=True) as prepare, \
                patch("desktop.browser_steps.run", return_value=SimpleNamespace(returncode=0)) as install, \
                patch.object(browser_extensions, "run", return_value=SimpleNamespace(
                    stdout="app/org.mozilla.firefox/aarch64/stable\n")):
            previous_umask = os.umask(0o077)
            try:
                install_single_browser("firefox", True)
            finally:
                os.umask(previous_umask)
            policy = Path(temp, "aarch64/stable/policies/policies.json")
            for directory in (policy.parent, policy.parent.parent, policy.parent.parent.parent):
                self.assertEqual(directory.stat().st_mode & 0o777, 0o755)
            self.assertEqual(Path(temp).stat().st_mode & 0o777, 0o700)
            settings = json.loads(policy.read_text())["policies"]["ExtensionSettings"]["uBlock0@raymondhill.net"]
            self.assertEqual(settings, {
                "installation_mode": "normal_installed",
                "install_url": "https://addons.mozilla.org/firefox/downloads/latest/ublock-origin/latest.xpi",
            })
            previous = policy.stat()
            install_single_browser("firefox", True)
            self.assertEqual(policy.stat().st_mtime_ns, previous.st_mtime_ns)
            custom = '{"policies": {"ExtensionSettings": {"*": {"installation_mode": "blocked"}}}}\n'
            policy.write_text(custom)
            install_single_browser("firefox", True)
            self.assertEqual(policy.read_text(), custom)
            install.assert_called_once_with("flatpak install --system -y flathub org.mozilla.firefox", check=False)
            prepare.assert_called_once_with()

    def test_missing_flatpak_remote_stops_before_install_or_extension_registration(self):
        with patch("desktop.browser_steps.is_flatpak_app_installed", return_value=False), \
                patch("desktop.browser_steps.install_flatpak_if_needed", return_value=False), \
                patch("desktop.browser_steps.run") as install, patch("desktop.browser_steps.install_firefox_blocker") as blocker:
            with self.assertRaisesRegex(RuntimeError, "prepare Flatpak and Flathub"):
                install_single_browser("firefox", True)
            install.assert_not_called()
            blocker.assert_not_called()

    def test_flatpak_ref_must_belong_to_firefox_and_contain_safe_path_components(self):
        for ref in ("app/other/x86_64/stable", "app/org.mozilla.firefox/../stable",
                    "app/org.mozilla.firefox/aarch64/../policy"):
            with self.subTest(ref=ref), patch.object(browser_extensions, "run", return_value=SimpleNamespace(stdout=ref)), \
                    patch.object(browser_extensions, "_seed_extension_file") as write:
                with self.assertRaisesRegex(RuntimeError, "Flatpak architecture and branch"):
                    browser_extensions.install_firefox_blocker(flatpak=True)
                write.assert_not_called()

    def test_chromium_registration_preserves_an_administrator_link(self):
        with tempfile.TemporaryDirectory() as temp:
            admin = Path(temp, "admin.json")
            admin.write_text("{}\n")
            entry = Path(temp, "extension.json")
            entry.symlink_to(admin)
            with patch.object(browser_extensions, "_CHROMIUM_ENTRY", entry):
                browser_extensions.install_chromium_blocker()
            self.assertTrue(entry.is_symlink())
            self.assertEqual(admin.read_text(), "{}\n")

    def test_browser_dry_run_does_not_probe_or_install_packages(self):
        with patch("desktop.browser_steps.run") as run, \
                patch("desktop.browser_steps.is_package_installed") as installed:
            install_browser(SimpleNamespace(use_flatpak=False, browsers=["firefox", "lynx"], browser="firefox", dry_run=True))
        run.assert_not_called()
        installed.assert_not_called()

    def test_failed_package_command_is_fatal_even_when_browser_appears_afterward(self):
        for browser, flatpak in (("firefox", False), ("lynx", False),
                                 ("firefox", True), ("librewolf", True)):
            with self.subTest(browser=browser, flatpak=flatpak), \
                    patch("desktop.browser_steps.is_package_installed", side_effect=[False, False, True]), \
                    patch("desktop.browser_steps.is_flatpak_app_installed", side_effect=[False, True]), \
                    patch("desktop.browser_steps.install_flatpak_if_needed", return_value=True), \
                    patch("desktop.browser_steps.run", return_value=SimpleNamespace(returncode=1)):
                with self.assertRaises(RuntimeError):
                    install_single_browser(browser, use_flatpak=flatpak)

    @patch("desktop.apps_steps.run")
    def test_flatpak_app_detection_uses_argument_list(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=["flatpak"],
            returncode=0,
            stdout="org.mozilla.firefox\n",
            stderr="",
        )
        self.assertTrue(is_flatpak_app_installed("org.mozilla.firefox", system=True))
        mock_run.assert_called_once_with(
            ["flatpak", "list", "--system", "--app", "--columns=application"],
            capture_output=True,
            check=False,
        )

    @patch("desktop.apps_steps.run")
    def test_flatpak_app_detection_handles_missing_app(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=["flatpak"],
            returncode=0,
            stdout="org.mozilla.firefox\n",
            stderr="",
        )
        self.assertFalse(is_flatpak_app_installed("io.gitlab.librewolf-community"))
        mock_run.side_effect = FileNotFoundError
        self.assertFalse(is_flatpak_app_installed("org.mozilla.firefox", system=True))

    @patch("desktop.browser_steps.os.path.isfile", return_value=True)
    @patch("desktop.browser_steps.shutil.which", return_value="/usr/bin/apparmor-tool")
    @patch("desktop.browser_steps.run")
    @patch("builtins.open", new_callable=unittest.mock.mock_open)
    def test_recreates_and_reloads_librewolf_apparmor_profile(
        self, mock_open, mock_run, _which, _isfile
    ):
        mock_run.side_effect = [
            subprocess.CompletedProcess(args=["aa-enabled"], returncode=0),
            subprocess.CompletedProcess(args=["apparmor_parser"], returncode=0),
            subprocess.CompletedProcess(args=["apparmor_parser"], returncode=0),
        ]

        _configure_librewolf_apparmor_profile()

        self.assertEqual(mock_run.call_count, 3)
        mock_run.assert_any_call("aa-enabled -q", check=False)
        mock_run.assert_any_call(
            "apparmor_parser -R /etc/apparmor.d/librewolf", check=False
        )
        mock_run.assert_any_call(
            "apparmor_parser -r -W /etc/apparmor.d/librewolf", check=False
        )
        written = "".join(
            call.args[0] for call in mock_open().write.call_args_list if call.args
        )
        self.assertIn(
            "profile librewolf /usr/share/librewolf/{librewolf,librewolf-bin} "
            "flags=(unconfined) {",
            written,
        )

    @patch("desktop.browser_steps.os.path.isfile", return_value=True)
    @patch("desktop.browser_steps.shutil.which", return_value="/usr/bin/apparmor-tool")
    @patch("desktop.browser_steps.run")
    def test_skips_librewolf_profile_reload_when_apparmor_is_inactive(
        self, mock_run, _which, _isfile
    ):
        mock_run.return_value = subprocess.CompletedProcess(
            args=["aa-enabled"], returncode=1
        )

        _configure_librewolf_apparmor_profile()

        mock_run.assert_called_once_with("aa-enabled -q", check=False)

    @patch("desktop.browser_steps.os.path.isfile", return_value=True)
    @patch("desktop.browser_steps.shutil.which", return_value=None)
    @patch("desktop.browser_steps.run")
    def test_skips_librewolf_profile_reload_when_apparmor_tools_are_missing(
        self, mock_run, _which, _isfile
    ):
        _configure_librewolf_apparmor_profile()

        mock_run.assert_not_called()

    @patch("desktop.browser_steps.os.path.isfile")
    @patch("desktop.browser_steps.run")
    def test_refreshes_existing_extrepo_source_definitions(self, mock_run, mock_isfile):
        mock_isfile.side_effect = lambda path: path.endswith(
            "extrepo_librewolf.sources"
        )
        mock_run.return_value = subprocess.CompletedProcess(
            args=["extrepo"], returncode=0
        )

        _refresh_existing_extrepo_sources()

        mock_run.assert_called_once_with(
            "timeout --kill-after=5s 30s extrepo update librewolf", check=False
        )

    @patch("desktop.browser_steps.os.path.isfile", return_value=True)
    @patch("desktop.browser_steps.run")
    def test_reports_but_keeps_using_source_when_extrepo_refresh_fails(
        self, mock_run, _isfile
    ):
        mock_run.return_value = subprocess.CompletedProcess(
            args=["extrepo"], returncode=1
        )

        _refresh_existing_extrepo_sources()

        self.assertEqual(
            [call.args[0] for call in mock_run.call_args_list],
            [
                "timeout --kill-after=5s 30s extrepo update librewolf",
                "timeout --kill-after=5s 30s extrepo update vscode",
            ],
        )

    @patch("desktop.browser_steps._refresh_existing_extrepo_sources")
    @patch("desktop.browser_steps._update_apt_metadata")
    @patch("desktop.browser_steps.is_package_installed", return_value=True)
    def test_does_not_refresh_extrepo_sources_after_successful_apt_update(
        self, _is_installed, mock_update, mock_refresh
    ):
        mock_update.return_value = subprocess.CompletedProcess(
            args=["apt-get"], returncode=0
        )

        with patch("desktop.browser_steps._apt_update_done", False):
            _ensure_extrepo_and_update()

        mock_update.assert_called_once_with()
        mock_refresh.assert_not_called()

    @patch("desktop.browser_steps._refresh_existing_extrepo_sources", return_value=True)
    @patch("desktop.browser_steps._update_apt_metadata")
    @patch("desktop.browser_steps.is_package_installed", return_value=True)
    def test_retries_apt_after_a_failed_refresh(
        self, _is_installed, mock_update, mock_refresh
    ):
        mock_update.side_effect = [
            subprocess.CompletedProcess(args=["apt-get"], returncode=100),
            subprocess.CompletedProcess(args=["apt-get"], returncode=0),
        ]

        with patch("desktop.browser_steps._apt_update_done", False):
            _ensure_extrepo_and_update()

        self.assertEqual(mock_update.call_count, 2)
        mock_refresh.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
