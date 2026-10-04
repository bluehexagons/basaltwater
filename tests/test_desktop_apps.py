"""Tests for desktop application setup helpers."""

from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from lib.config import SetupConfig


class TestDesktopApps(unittest.TestCase):
    @patch("lib.remote_utils.subprocess.run")
    @patch("lib.remote_utils.run")
    def test_blender_installs_and_verifies_debian_package(self, mock_run, mock_probe):
        from desktop.apps_steps import install_blender

        mock_probe.side_effect = [
            Mock(returncode=1, stdout="", stderr=""),
            Mock(returncode=0, stdout="install ok installed", stderr=""),
            Mock(returncode=1, stdout="", stderr=""),
            Mock(returncode=0, stdout="install ok installed", stderr=""),
        ]
        mock_run.return_value = Mock(returncode=0)
        config = SetupConfig(
            host="test.example.com", username="testuser", system_type="agent_vm",
            install_blender=True, use_flatpak=True, machine_type="unprivileged",
        )

        install_blender(config)

        self.assertEqual([call.args[0] for call in mock_run.call_args_list], [
            "apt-get install -y -qq blender", "apt-get install -y -qq python3-numpy",
        ])
        self.assertEqual([call.args[0] for call in mock_probe.call_args_list], [
            ["dpkg-query", "-W", "-f=${Status}", "blender"],
            ["dpkg-query", "-W", "-f=${Status}", "blender"],
            ["dpkg-query", "-W", "-f=${Status}", "python3-numpy"],
            ["dpkg-query", "-W", "-f=${Status}", "python3-numpy"],
        ])

    @patch("lib.remote_utils.subprocess.run")
    @patch("lib.remote_utils.run")
    def test_blender_rerun_keeps_installed_debian_package(self, mock_run, mock_probe):
        from desktop.apps_steps import install_blender

        mock_probe.return_value = Mock(returncode=0, stdout="install ok installed")
        config = SetupConfig(
            host="test.example.com", username="testuser", system_type="agent_vm",
            install_blender=True,
        )

        install_blender(config)

        mock_run.assert_not_called()
        self.assertEqual([call.args[0] for call in mock_probe.call_args_list], [
            ["dpkg-query", "-W", "-f=${Status}", "blender"],
            ["dpkg-query", "-W", "-f=${Status}", "python3-numpy"],
        ])

    @patch("lib.remote_utils.subprocess.run")
    @patch("lib.remote_utils.run")
    def test_blender_rerun_repairs_missing_numpy(self, mock_run, mock_probe):
        from desktop.apps_steps import install_blender

        mock_probe.side_effect = [Mock(returncode=0, stdout="install ok installed"),
                                 Mock(returncode=1, stdout=""),
                                 Mock(returncode=0, stdout="install ok installed")]
        mock_run.return_value = Mock(returncode=0)
        install_blender(SetupConfig(host="vm", username="agent", system_type="agent_vm", install_blender=True))
        mock_run.assert_called_once_with("apt-get install -y -qq python3-numpy", check=False)

    @patch("lib.remote_utils.subprocess.run")
    @patch("lib.remote_utils.run")
    def test_blender_numpy_failure_stops_setup(self, mock_run, mock_probe):
        from desktop.apps_steps import install_blender

        for returncode in (0, 1):
            with self.subTest(returncode=returncode):
                mock_probe.side_effect = [Mock(returncode=0, stdout="install ok installed"),
                                         Mock(returncode=1, stdout=""), Mock(returncode=1, stdout="")]
                mock_run.return_value = Mock(returncode=returncode)
                with self.assertRaisesRegex(RuntimeError, "NumPy support installation failed"):
                    install_blender(SetupConfig(host="vm", username="agent", system_type="agent_vm", install_blender=True))

    @patch("lib.remote_utils.subprocess.run")
    @patch("lib.remote_utils.run")
    def test_blender_installation_failure_stops_setup(self, mock_run, mock_probe):
        from desktop.apps_steps import install_blender

        mock_run.return_value = Mock(returncode=1, stdout="", stderr="")
        mock_probe.return_value = Mock(returncode=1, stdout="", stderr="")
        config = SetupConfig(
            host="test.example.com", username="testuser", system_type="agent_vm",
            install_blender=True,
        )

        with self.assertRaisesRegex(RuntimeError, "Blender installation failed"):
            install_blender(config)

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


class TestMediaApps(unittest.TestCase):
    @patch("lib.remote_utils.subprocess.run")
    @patch("lib.remote_utils.run")
    def test_selected_debian_editors_install_and_verify_without_recommends(self, execute, probe):
        from desktop.apps_steps import install_media_apps

        for package in ("inkscape", "gimp", "krita", "audacity", "shotcut"):
            with self.subTest(package=package):
                execute.reset_mock()
                probe.reset_mock()
                execute.return_value = Mock(returncode=0)
                probe.side_effect = [Mock(returncode=1, stdout=""),
                                     Mock(returncode=0, stdout="install ok installed")]
                config = SetupConfig(
                    host="vm", username="agent", system_type="agent_vm",
                    machine_type="unprivileged", use_flatpak=True,
                    **{"install_" + package: True},
                )
                install_media_apps(config)
                execute.assert_called_once_with(
                    ["apt-get", "install", "-y", "-qq", "--no-install-recommends", package],
                    check=False,
                )
                self.assertEqual([call.args[0] for call in probe.call_args_list], [
                    ["dpkg-query", "-W", "-f=${Status}", package],
                    ["dpkg-query", "-W", "-f=${Status}", package],
                ])

    @patch("lib.remote_utils.subprocess.run")
    @patch("lib.remote_utils.run")
    def test_rerun_retains_installed_editors(self, execute, probe):
        from desktop.apps_steps import install_media_apps

        probe.return_value = Mock(returncode=0, stdout="install ok installed")
        install_media_apps(SetupConfig(
            host="vm", username="agent", system_type="agent_vm",
            install_inkscape=True, install_gimp=True,
        ))
        execute.assert_not_called()
        self.assertEqual([call.args[0][-1] for call in probe.call_args_list], ["inkscape", "gimp"])

    @patch("lib.remote_utils.subprocess.run")
    @patch("lib.remote_utils.run")
    def test_unverified_or_failed_installation_stops_before_next_editor(self, execute, probe):
        from desktop.apps_steps import install_media_apps

        for returncode in (0, 1):
            with self.subTest(returncode=returncode):
                execute.reset_mock()
                execute.return_value = Mock(returncode=returncode)
                probe.return_value = Mock(returncode=1, stdout="")
                with self.assertRaisesRegex(RuntimeError, "Inkscape installation failed"):
                    install_media_apps(SetupConfig(
                        host="vm", username="agent", system_type="agent_vm",
                        install_inkscape=True, install_gimp=True,
                    ))
                execute.assert_called_once()
                self.assertIn("inkscape", execute.call_args.args[0])

    def test_plan_is_opt_in_across_standard_debian_profiles(self):
        from desktop.apps_steps import install_media_apps
        from lib.system_types import get_steps_for_system_type

        for profile in (
            "agent_vm", "agent_workstation", "agent_code_vm", "workstation_desktop",
            "workstation_dev", "pc_dev", "control_plane", "server_dev", "server_web", "server_lite",
        ):
            with self.subTest(profile=profile):
                config = SetupConfig(host="vm", username="agent", system_type=profile)
                self.assertNotIn(install_media_apps, [step for _, step in get_steps_for_system_type(config)])
                config.install_inkscape = True
                config.install_gimp = True
                functions = [step for _, step in get_steps_for_system_type(config)]
                self.assertEqual(functions.count(install_media_apps), 1)
                if profile == "agent_vm":
                    self.assertFalse(config.include_desktop)
                    self.assertFalse(config.enable_rdp)
                    self.assertFalse(config.install_python)
                    self.assertFalse(config.install_node)

    def test_flags_round_trip_on_debian_and_patch_can_clear_them(self):
        import basaltwater
        import shlex
        from lib.arg_parser import create_setup_argument_parser

        flags = ["--inkscape", "--gimp", "--krita", "--audacity", "--shotcut"]
        parser, _, _ = basaltwater.create_basaltwater_parser()
        args = parser.parse_args(["setup", "agent_vm", "vm", "agent", "--timezone", "UTC", *flags])
        config = SetupConfig.from_args(args, args.system_type)
        saved = SetupConfig.from_dict(config.host, config.system_type, config.to_dict())
        remote = create_setup_argument_parser("test", for_remote=True)
        parsed_remote = remote.parse_args(shlex.split(" ".join(config.to_remote_args())))
        for flag in flags:
            field = "install_" + flag.removeprefix("--")
            with self.subTest(flag=flag):
                self.assertTrue(getattr(saved, field))
                self.assertTrue(getattr(parsed_remote, field))
                self.assertIn(flag, config.to_setup_command())
                patch_args = parser.parse_args(["patch", "vm", "agent", "--no-" + flag[2:]])
                self.assertIs(getattr(patch_args, field), False)
        omitted_patch = parser.parse_args(["patch", "vm", "agent"])
        self.assertIsNone(omitted_patch.install_inkscape)

    def test_dedicated_profiles_reject_unsupported_media_flags(self):
        for profile in ("server_proxmox", "server_wsl"):
            for package in ("inkscape", "gimp", "krita", "audacity", "shotcut"):
                with self.subTest(profile=profile, package=package):
                    with self.assertRaisesRegex(ValueError, "Debian workstation/server profile"):
                        SetupConfig(host="vm", username="agent", system_type=profile,
                                    **{"install_" + package: True})


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
