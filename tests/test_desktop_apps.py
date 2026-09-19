"""Tests for desktop application setup helpers."""

from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from lib.config import SetupConfig


class TestDesktopApps(unittest.TestCase):
    def test_desktop_steps_exports_librewolf_browser_config(self):
        """desktop.steps should expose browser configuration with LibreWolf support."""
        from desktop import steps
        from desktop.browser_steps import configure_default_browser

        self.assertIs(steps.configure_default_browser, configure_default_browser)

    @patch("desktop.apps_steps.install_package", return_value=True)
    def test_geany_editor_uses_debian_package(self, mock_install_package):
        from desktop.apps_steps import install_editor

        config = SetupConfig(
            host="test.example.com",
            username="testuser",
            system_type="workstation_dev",
            include_desktop=True,
            editor="geany",
        )

        install_editor(config)

        mock_install_package.assert_called_once_with(
            "Geany",
            "geany",
            "apt-get install -y -qq geany",
        )

    @patch("desktop.apps_steps.install_package", return_value=False)
    def test_explicit_geany_failure_stops_the_step(self, _mock_install_package):
        from desktop.apps_steps import install_editor

        config = SetupConfig(
            host="test.example.com",
            username="testuser",
            system_type="workstation_dev",
            include_desktop=True,
            editor="geany",
        )

        with self.assertRaisesRegex(RuntimeError, "Geany installation failed"):
            install_editor(config)

    @patch("desktop.apps_steps.write_text_atomic")
    @patch("desktop.apps_steps.os.path.exists", return_value=False)
    @patch("desktop.apps_steps.is_package_installed", side_effect=[False, True])
    @patch("desktop.apps_steps.run")
    def test_vscode_uses_scoped_microsoft_repository(
        self,
        mock_run,
        _mock_package,
        _mock_exists,
        mock_write,
    ):
        from desktop.apps_steps import (
            MICROSOFT_KEY_FINGERPRINT,
            VSCODE_SOURCES,
            VSCODE_SOURCE_CONTENT,
            install_editor,
        )

        success = Mock(returncode=0, stdout="", stderr="")
        fingerprint = Mock(
            returncode=0,
            stdout=f"fpr:::::::::{MICROSOFT_KEY_FINGERPRINT}:\n",
            stderr="",
        )
        mock_run.side_effect = [
            success,
            success,
            fingerprint,
            success,
            success,
            success,
            success,
        ]
        config = SetupConfig(
            host="test.example.com",
            username="testuser",
            system_type="workstation_dev",
            include_desktop=True,
            editor="vscode",
        )

        install_editor(config)

        commands = [call.args[0] for call in mock_run.call_args_list]
        self.assertTrue(
            any(
                "packages.microsoft.com/keys/microsoft.asc" in command
                for command in commands
            )
        )
        self.assertFalse(any("extrepo" in command for command in commands))
        self.assertIn("apt-get update -qq", commands)
        self.assertIn("apt-get install -y -qq code", commands)
        mock_write.assert_called_once_with(
            VSCODE_SOURCES,
            VSCODE_SOURCE_CONTENT,
            mode=0o644,
        )

    @patch("desktop.apps_steps.write_text_atomic")
    @patch("desktop.apps_steps.os.path.exists", return_value=False)
    @patch("desktop.apps_steps.is_package_installed", return_value=False)
    @patch("desktop.apps_steps.run")
    def test_explicit_vscode_repository_failure_stops_the_step(
        self,
        mock_run,
        _mock_package,
        _mock_exists,
        _mock_write,
    ):
        from desktop.apps_steps import MICROSOFT_KEY_FINGERPRINT, install_editor

        success = Mock(returncode=0, stdout="", stderr="")
        fingerprint = Mock(
            returncode=0,
            stdout=f"fpr:::::::::{MICROSOFT_KEY_FINGERPRINT}:\n",
            stderr="",
        )
        failed = Mock(returncode=1, stdout="", stderr="repository unavailable")
        mock_run.side_effect = [
            success,
            success,
            fingerprint,
            success,
            success,
            failed,
        ]
        config = SetupConfig(
            host="test.example.com",
            username="testuser",
            system_type="workstation_dev",
            include_desktop=True,
            editor="vscode",
        )

        with self.assertRaisesRegex(RuntimeError, "could not refresh"):
            install_editor(config)


class TestBrowserSteps(unittest.TestCase):
    def setUp(self):
        home_patcher = patch(
            "desktop.browser_steps.get_user_home",
            return_value="/home/testuser",
        )
        self.addCleanup(home_patcher.stop)
        home_patcher.start()

    @patch("desktop.browser_steps.run")
    @patch("desktop.browser_steps.os.makedirs")
    @patch("builtins.open", new_callable=unittest.mock.mock_open)
    def test_configures_librewolf_as_default_browser(
        self,
        mock_open,
        mock_makedirs,
        mock_run,
    ):
        """LibreWolf remains available as an explicitly selected browser."""
        from desktop.browser_steps import configure_default_browser

        mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
        config = SetupConfig(
            host="test.example.com",
            username="testuser",
            system_type="workstation_desktop",
            browser="librewolf",
        )

        configure_default_browser(config)

        mock_open.assert_any_call(
            "/home/testuser/.config/mimeapps.list", "w", encoding="utf-8"
        )
        mock_open.assert_any_call(
            "/home/testuser/.config/xfce4/helpers.rc", "w", encoding="utf-8"
        )
        write_calls = [call.args[0] for call in mock_open().write.call_args_list]
        self.assertIn("librewolf.desktop", "".join(write_calls))
        run_commands = [call.args[0] for call in mock_run.call_args_list]
        self.assertTrue(
            any(
                "xdg-mime default librewolf.desktop x-scheme-handler/http" in command
                for command in run_commands
            )
        )
        self.assertTrue(
            any(
                "xdg-mime default librewolf.desktop x-scheme-handler/https" in command
                for command in run_commands
            )
        )
        self.assertTrue(
            any(
                "xdg-settings set default-web-browser librewolf.desktop" in command
                for command in run_commands
            )
        )
        self.assertIn("WebBrowser=librewolf", "".join(write_calls))
        helper_content = "".join(write_calls)
        self.assertIn("Type=X-XFCE-Helper", helper_content)
        self.assertIn("X-XFCE-Binaries=librewolf;", helper_content)
        self.assertIn("X-XFCE-Category=WebBrowser", helper_content)

    @patch("desktop.browser_steps._install_via_extrepo")
    @patch("desktop.browser_steps.is_package_installed")
    def test_librewolf_install_does_not_remove_legacy_repo_files(self, mock_package, mock_extrepo):
        """Legacy LibreWolf repo cleanup has been removed from the install path."""
        from desktop.browser_steps import install_single_browser

        mock_package.return_value = False
        mock_extrepo.return_value = True

        with patch(
            "desktop.browser_steps._configure_librewolf_apparmor_profile"
        ) as mock_profile:
            install_single_browser("librewolf", use_flatpak=False)

        mock_extrepo.assert_called_once_with("LibreWolf", "librewolf", "librewolf")
        mock_profile.assert_called_once_with()

    @patch("desktop.browser_steps._install_helium_browser")
    def test_installs_helium_browser(self, mock_install_helium):
        """Helium should be accepted by the browser installer."""
        from desktop.browser_steps import install_single_browser

        install_single_browser("helium", use_flatpak=False)

        mock_install_helium.assert_called_once_with()

    @patch("desktop.browser_steps.run")
    @patch("desktop.browser_steps.os.makedirs")
    @patch("builtins.open", new_callable=unittest.mock.mock_open)
    def test_configures_helium_as_default_browser(
        self,
        mock_open,
        mock_makedirs,
        mock_run,
    ):
        """Helium should be configurable as the default browser."""
        from desktop.browser_steps import configure_default_browser

        mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
        config = SetupConfig(
            host="test.example.com",
            username="testuser",
            system_type="workstation_desktop",
            browser="helium",
        )

        configure_default_browser(config)

        write_calls = [call.args[0] for call in mock_open().write.call_args_list]
        self.assertIn("helium.desktop", "".join(write_calls))
        run_commands = [call.args[0] for call in mock_run.call_args_list]
        self.assertTrue(
            any(
                "xdg-mime default helium.desktop x-scheme-handler/http" in command
                for command in run_commands
            )
        )
        self.assertTrue(
            any(
                "xdg-mime default helium.desktop x-scheme-handler/https" in command
                for command in run_commands
            )
        )

    @patch("desktop.browser_steps.os.path.exists")
    @patch("desktop.browser_steps.is_package_installed")
    @patch("desktop.browser_steps.run")
    def test_helium_installer_uses_latest_release_deb(self, mock_run, mock_package, mock_exists):
        """Helium installer should resolve and install the latest Debian package."""
        from desktop.browser_steps import _install_helium_browser

        mock_package.side_effect = [False, True]
        mock_exists.return_value = False
        mock_run.side_effect = [
            Mock(returncode=0, stdout="https://example.test/helium-bin_1.0-1_amd64.deb\n", stderr=""),
            Mock(returncode=0, stdout="", stderr=""),
            Mock(returncode=0, stdout="", stderr=""),
            Mock(returncode=0, stdout="", stderr=""),
        ]

        _install_helium_browser()

        commands = [call.args[0] for call in mock_run.call_args_list]
        download_command = next(
            command for command in commands if command.startswith("wget --https-only -qO ")
        )
        self.assertIn("/basaltwater-helium-", download_command)
        self.assertNotIn("-qO /tmp/helium.deb", download_command)
        self.assertTrue(
            any(
                command.startswith("apt-get install -y -qq ")
                and "/basaltwater-helium-" in command
                for command in commands
            )
        )


if __name__ == "__main__":
    unittest.main()
