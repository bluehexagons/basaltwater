"""Tests for desktop.browser_steps helper behavior."""

from __future__ import annotations

import os
import sys
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from desktop.browser_steps import (
    _browsh_architecture,
    _browsh_asset_matches,
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
        for browser, flatpak in (("brave", False), ("firefox", False), ("librewolf", False),
                                 ("helium", False), ("browsh", False), ("lynx", False),
                                 ("brave", True), ("firefox", True), ("librewolf", True)):
            for code in (0, 1):
                with self.subTest(browser=browser, flatpak=flatpak, code=code), \
                        patch("desktop.browser_steps.is_package_installed", return_value=False), \
                        patch("desktop.browser_steps.is_flatpak_app_installed", return_value=False), \
                        patch("desktop.browser_steps.shutil.which", return_value=None), \
                        patch("desktop.browser_steps.os.path.exists", return_value=False), \
                        patch("desktop.browser_steps._install_via_extrepo", return_value=False), \
                        patch("desktop.browser_steps._resolve_browsh_deb", return_value=("v1.8.2", "https://example.test/browsh.deb")), \
                        patch("desktop.browser_steps.run", return_value=SimpleNamespace(
                            returncode=code, stdout="https://example.test/browser.deb\n", stderr="")):
                    with self.assertRaisesRegex(RuntimeError, "LibreWolf" if browser == "librewolf" else browser.capitalize()):
                        install_single_browser(browser, use_flatpak=flatpak)

    def test_browsh_requires_firefox_before_resolving_or_downloading_a_release(self):
        with patch("desktop.browser_steps.is_package_installed", return_value=False), \
                patch("desktop.browser_steps.shutil.which", return_value=None), \
                patch("desktop.browser_steps.run", return_value=SimpleNamespace(returncode=0)), \
                patch("desktop.browser_steps._resolve_browsh_deb") as release:
            with self.assertRaisesRegex(RuntimeError, "Browsh.*Firefox"):
                install_single_browser("browsh", use_flatpak=False)
        release.assert_not_called()

    def test_existing_browsh_repairs_missing_firefox_before_returning(self):
        installed = {"browsh"}

        def install(command, **kwargs):
            self.assertEqual(command, "apt-get install -y -qq firefox-esr")
            installed.add("firefox-esr")
            return SimpleNamespace(returncode=0)

        with patch("desktop.browser_steps.is_package_installed", side_effect=lambda name: name in installed), \
                patch("desktop.browser_steps.run", side_effect=install) as run, \
                patch("desktop.browser_steps._resolve_browsh_deb") as release:
            install_single_browser("browsh", use_flatpak=False)
        run.assert_called_once()
        release.assert_not_called()

    def test_extrepo_install_requires_successful_command_and_present_package(self):
        for code, installed, expected in ((1, True, False), (0, False, False), (0, True, True)):
            with self.subTest(code=code, installed=installed), \
                    patch("desktop.browser_steps._ensure_extrepo_and_update"), \
                    patch("desktop.browser_steps._update_apt_metadata"), \
                    patch("desktop.browser_steps.os.path.exists", return_value=True), \
                    patch("desktop.browser_steps.is_package_installed", return_value=installed), \
                    patch("desktop.browser_steps.run", return_value=SimpleNamespace(returncode=code)):
                self.assertEqual(_install_via_extrepo("Brave", "brave", "brave-browser"), expected)

    def test_failed_first_browser_stops_before_installing_later_selections(self):
        with patch("desktop.browser_steps.is_package_installed", return_value=False), \
                patch("desktop.browser_steps.run", return_value=SimpleNamespace(returncode=1)) as run:
            with self.assertRaisesRegex(RuntimeError, "Firefox"):
                install_browser(SimpleNamespace(use_flatpak=False, browsers=["firefox", "lynx"], browser="firefox", dry_run=False))
        run.assert_called_once_with("apt-get install -y -qq firefox-esr", check=False)

    def test_brave_flatpak_failure_can_fall_back_to_verified_apt(self):
        with patch("desktop.browser_steps.is_flatpak_app_installed", return_value=False), \
                patch("desktop.browser_steps.is_package_installed", return_value=False), \
                patch("desktop.browser_steps.run", return_value=SimpleNamespace(returncode=1)), \
                patch("desktop.browser_steps._install_via_extrepo", return_value=True) as apt:
            install_single_browser("brave", use_flatpak=True)
        apt.assert_called_once_with("Brave", "brave", "brave-browser")

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
                    patch("desktop.browser_steps.run", return_value=SimpleNamespace(returncode=1)):
                with self.assertRaises(RuntimeError):
                    install_single_browser(browser, use_flatpak=flatpak)

    def test_browsh_resolution_download_and_missing_package_are_fatal(self):
        for failure in ("resolution", "download", "package"):
            with self.subTest(failure=failure), \
                    patch("desktop.browser_steps.is_package_installed", side_effect=lambda name: name == "firefox-esr"), \
                    patch("desktop.browser_steps.shutil.which", return_value=None), \
                    patch("desktop.browser_steps._resolve_browsh_deb", return_value=("v1.8.2", "https://example.test/browsh.deb"),
                          side_effect=RuntimeError("no asset") if failure == "resolution" else None), \
                    patch("desktop.browser_steps.run", return_value=SimpleNamespace(returncode=1 if failure == "download" else 0)):
                with self.assertRaisesRegex(RuntimeError, "Browsh installation failed"):
                    install_single_browser("browsh", use_flatpak=False)

    @patch("desktop.browser_steps.run")
    def test_browsh_architecture_maps_debian_armhf(self, mock_run):
        mock_run.return_value = SimpleNamespace(
            returncode=0, stdout="armhf\n", stderr=""
        )

        self.assertEqual(_browsh_architecture(), "armv7")

    def test_browsh_asset_match_uses_release_tag_and_architecture(self):
        self.assertTrue(
            _browsh_asset_matches(
                "v1.8.2", "browsh_1.8.2_linux_arm64.deb", "arm64"
            )
        )
        self.assertFalse(
            _browsh_asset_matches(
                "v1.8.3", "browsh_1.8.2_linux_arm64.deb", "arm64"
            )
        )

    @patch("desktop.browser_steps.shutil.which", return_value="")
    @patch("desktop.browser_steps.is_package_installed", return_value=False)
    @patch(
        "desktop.browser_steps._resolve_browsh_deb",
        return_value=("v1.8.2", "https://example.test/browsh_1.8.2_linux_amd64.deb"),
    )
    @patch("desktop.browser_steps.run")
    def test_browsh_resolves_current_architectural_release(
        self, mock_run, _resolve, _installed, _which
    ):
        installed = set()
        _installed.side_effect = lambda package: package in installed

        def install(command, **kwargs):
            if command == "apt-get install -y -qq firefox-esr":
                installed.add("firefox-esr")
            elif command.startswith("apt-get install -y -qq "):
                installed.add("browsh")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        mock_run.side_effect = install

        install_single_browser("browsh", use_flatpak=False)

        commands = [call.args[0] for call in mock_run.call_args_list]
        self.assertIn(
            "https://example.test/browsh_1.8.2_linux_amd64.deb",
            commands[1],
        )
        self.assertNotIn("v1.8.0", " ".join(commands))
        self.assertIn("/basaltwater-browsh-", commands[1])
        self.assertNotIn("-qO /tmp/browsh.deb", commands[1])
        self.assertIn("--https-only", commands[1])
        self.assertTrue(
            any(
                command.startswith("apt-get install -y -qq ")
                and "/basaltwater-browsh-" in command
                for command in commands
            )
        )

    @patch("desktop.browser_steps.subprocess.run")
    def test_flatpak_app_detection_uses_argument_list(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=["flatpak"],
            returncode=0,
            stdout="org.mozilla.firefox\n",
            stderr="",
        )
        self.assertTrue(is_flatpak_app_installed("org.mozilla.firefox"))
        mock_run.assert_called_once_with(
            ["flatpak", "list", "--app", "--columns=application"],
            capture_output=True,
            text=True,
        )

    @patch("desktop.browser_steps.subprocess.run")
    def test_flatpak_app_detection_handles_missing_app(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=["flatpak"],
            returncode=0,
            stdout="org.mozilla.firefox\n",
            stderr="",
        )
        self.assertFalse(is_flatpak_app_installed("com.brave.Browser"))

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

        self.assertEqual(mock_run.call_count, 3)
        self.assertEqual(
            [call.args[0] for call in mock_run.call_args_list],
            [
                "timeout --kill-after=5s 30s extrepo update brave",
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
