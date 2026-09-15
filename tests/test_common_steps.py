"""Tests for common setup steps."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from common.common_steps import (
    AV_TOOL_PACKAGES,
    CLI_TOOL_PACKAGES,
    CONTROL_PLANE_PACKAGES,
    DATA_ANALYSIS_PACKAGES,
    GL_TOOL_PACKAGES,
    _ensure_vm_setup_user_sudoers,
    _run_as_login_user,
    check_debian_package_sources,
    configure_time_sync,
    ensure_python_alias,
    install_av_tools,
    install_cli_tools,
    install_data_analysis_tools,
    install_gl_tools,
    update_and_upgrade_packages,
)
from lib.config import SetupConfig


class TestUpdateAndUpgradePackages(unittest.TestCase):
    @patch("common.common_steps.check_debian_package_sources")
    @patch("common.common_steps.run")
    def test_refresh_failure_does_not_upgrade_or_record_completion(self, run, _sources):
        run.return_value = MagicMock(returncode=100, stderr="mirror unavailable")
        with tempfile.TemporaryDirectory() as directory:
            marker = os.path.join(directory, "complete")
            with patch("common.common_steps.PACKAGE_UPDATE_MARKER", marker):
                with self.assertRaisesRegex(RuntimeError, "mirror unavailable"):
                    update_and_upgrade_packages(SetupConfig(host="host", username="root", system_type="server_lite"))
            self.assertFalse(os.path.exists(marker))
        run.assert_called_once()

    @patch("common.common_steps.check_debian_package_sources")
    @patch("common.common_steps.run")
    def test_updates_and_upgrades_packages(self, mock_run, mock_check_sources):
        order = []

        def run_side_effect(command, *_args, **_kwargs):
            order.append(command)
            return MagicMock(returncode=0)

        mock_run.side_effect = run_side_effect

        with tempfile.TemporaryDirectory() as temporary:
            with patch(
                "common.common_steps.PACKAGE_UPDATE_MARKER",
                os.path.join(temporary, "state", "package-update-complete"),
            ):
                update_and_upgrade_packages(
                    SetupConfig(
                        host="testhost",
                        username="testuser",
                        system_type="server_lite",
                    )
                )

        mock_check_sources.assert_called_once()
        self.assertEqual(
            order[0],
            "apt-get -o DPkg::Lock::Timeout=300 -o APT::Update::Error-Mode=any "
            "-o Dpkg::Use-Pty=0 update -q",
        )
        expected_dpkg_options = (
            "-o Dpkg::Options::=--force-confdef "
            "-o Dpkg::Options::=--force-confold"
        )
        self.assertEqual(
            order[1],
            "apt-get -o DPkg::Lock::Timeout=300 "
            f"upgrade -y -qq {expected_dpkg_options}",
        )
        self.assertEqual(
            order[2],
            "apt-get -o DPkg::Lock::Timeout=300 "
            f"autoremove -y -qq {expected_dpkg_options}",
        )

    @patch("common.common_steps.check_debian_package_sources")
    @patch("common.common_steps.run")
    def test_skips_completed_package_reconciliation(self, mock_run, mock_check_sources):
        with tempfile.TemporaryDirectory() as temporary:
            marker = os.path.join(temporary, "package-update-complete")
            open(marker, "w", encoding="utf-8").close()
            with patch("common.common_steps.PACKAGE_UPDATE_MARKER", marker):
                update_and_upgrade_packages(
                    SetupConfig(
                        host="testhost",
                        username="testuser",
                        system_type="server_lite",
                    )
                )

        mock_run.assert_not_called()
        mock_check_sources.assert_called_once()


class TestTimeSynchronization(unittest.TestCase):
    @patch("common.common_steps.install_package")
    @patch(
        "common.common_steps.is_package_installed",
        side_effect=lambda package: package == "chrony",
    )
    @patch("common.common_steps.run")
    @patch("common.common_steps.can_manage_time_sync", return_value=False)
    def test_unprivileged_container_disables_guest_time_daemons(
        self,
        mock_can_manage,
        mock_run,
        _is_installed,
        mock_install,
    ):
        configure_time_sync(
            SetupConfig(
                host="testhost",
                username="testuser",
                system_type="server_lite",
                machine_type="unprivileged",
                timezone="America/Chicago",
            )
        )

        mock_can_manage.assert_called_once_with("unprivileged")
        mock_run.assert_any_call("systemctl disable --now chrony", check=False)
        mock_run.assert_any_call(
            "timedatectl set-timezone America/Chicago",
            check=False,
        )
        self.assertFalse(
            any(
                "systemd-timesyncd" in call.args[0]
                for call in mock_run.call_args_list
            )
        )
        mock_install.assert_not_called()


class TestControlPlanePackages(unittest.TestCase):
    def test_uses_debian_trixie_dns_package_name(self):
        self.assertIn("bind9-dnsutils", CONTROL_PLANE_PACKAGES)
        self.assertNotIn("dnsutils", CONTROL_PLANE_PACKAGES)


class TestDevelopmentToolPackages(unittest.TestCase):
    @patch("common.common_steps.os.path.isfile", return_value=True)
    @patch("common.common_steps.os.path.lexists", return_value=False)
    @patch("common.common_steps.shutil.which", side_effect=["/usr/bin/python3", None])
    @patch("common.common_steps.run")
    def test_python_alias_is_added_when_missing(
        self, mock_run, _which, _lexists, _isfile
    ):
        ensure_python_alias(
            SetupConfig(host="testhost", username="agent", system_type="agent_vm")
        )

        mock_run.assert_called_once_with(
            "ln -s /usr/bin/python3 /usr/local/bin/python"
        )

    @patch("common.common_steps.shutil.which", side_effect=["/usr/bin/python3", "/usr/bin/python"])
    @patch("common.common_steps.run")
    def test_python_alias_keeps_existing_command(self, mock_run, _which):
        ensure_python_alias(
            SetupConfig(host="testhost", username="agent", system_type="agent_vm")
        )
        mock_run.assert_not_called()

    def test_cli_baseline_contains_small_agent_tools(self):
        self.assertTrue(
            {
                "ripgrep",
                "jq",
                "sqlite3",
                "file",
                "tree",
                "make",
                "patch",
            }.issubset(
                CLI_TOOL_PACKAGES
            )
        )

    def test_large_analysis_packages_are_not_in_cli_baseline(self):
        self.assertTrue(
            {
                "jupyterlab",
                "python3-matplotlib",
                "python3-numpy",
                "python3-pandas",
                "python3-scipy",
            }.issubset(DATA_ANALYSIS_PACKAGES)
        )
        self.assertTrue(set(CLI_TOOL_PACKAGES).isdisjoint(DATA_ANALYSIS_PACKAGES))

    def test_av_packages_are_not_in_cli_baseline(self):
        self.assertEqual(
            set(AV_TOOL_PACKAGES),
            {"ffmpeg", "imagemagick", "libimage-exiftool-perl"},
        )
        self.assertTrue(set(CLI_TOOL_PACKAGES).isdisjoint(AV_TOOL_PACKAGES))

    def test_gl_packages_are_not_in_cli_baseline(self):
        self.assertEqual(set(GL_TOOL_PACKAGES), {"apitrace", "mesa-utils"})
        self.assertTrue(set(CLI_TOOL_PACKAGES).isdisjoint(GL_TOOL_PACKAGES))

    @patch("common.common_steps.run")
    @patch("common.common_steps.is_package_installed")
    def test_cli_installer_requests_every_missing_baseline_package(
        self, mock_is_installed, mock_run
    ):
        mock_is_installed.side_effect = (
            [False] * len(CLI_TOOL_PACKAGES) + [True] * len(CLI_TOOL_PACKAGES)
        )
        mock_run.return_value = MagicMock(returncode=0)

        install_cli_tools(
            SetupConfig(host="testhost", username="agent", system_type="agent_vm")
        )

        command = mock_run.call_args.args[0]
        self.assertIn("-o DPkg::Lock::Timeout=300", command)
        for package in CLI_TOOL_PACKAGES:
            with self.subTest(package=package):
                self.assertIn(f" {package}", command)

    @patch("common.common_steps.run")
    @patch("common.common_steps.is_package_installed")
    def test_data_analysis_installer_requests_only_opt_in_bundle(
        self, mock_is_installed, mock_run
    ):
        mock_is_installed.side_effect = (
            [False] * len(DATA_ANALYSIS_PACKAGES)
            + [True] * len(DATA_ANALYSIS_PACKAGES)
        )
        mock_run.return_value = MagicMock(returncode=0)

        install_data_analysis_tools(
            SetupConfig(
                host="testhost",
                username="agent",
                system_type="agent_vm",
                install_data_analysis_tools=True,
            )
        )

        command = mock_run.call_args.args[0]
        for package in DATA_ANALYSIS_PACKAGES:
            with self.subTest(package=package):
                self.assertIn(f" {package}", command)
        for package in CLI_TOOL_PACKAGES:
            with self.subTest(default_package=package):
                self.assertNotIn(f" {package}", command)

    @patch("common.common_steps.run")
    @patch("common.common_steps.is_package_installed")
    def test_av_installer_requests_only_opt_in_bundle(
        self, mock_is_installed, mock_run
    ):
        mock_is_installed.side_effect = (
            [False] * len(AV_TOOL_PACKAGES) + [True] * len(AV_TOOL_PACKAGES)
        )
        mock_run.return_value = MagicMock(returncode=0)

        install_av_tools(
            SetupConfig(
                host="testhost",
                username="agent",
                system_type="agent_vm",
                install_av_tools=True,
            )
        )

        command = mock_run.call_args.args[0]
        for package in AV_TOOL_PACKAGES:
            with self.subTest(package=package):
                self.assertIn(f" {package}", command)
        for package in CLI_TOOL_PACKAGES:
            with self.subTest(default_package=package):
                self.assertNotIn(f" {package}", command)

    @patch("common.common_steps.run")
    @patch("common.common_steps.is_package_installed")
    def test_gl_installer_requests_only_opt_in_bundle(
        self, mock_is_installed, mock_run
    ):
        mock_is_installed.side_effect = (
            [False] * len(GL_TOOL_PACKAGES) + [True] * len(GL_TOOL_PACKAGES)
        )
        mock_run.return_value = MagicMock(returncode=0)

        install_gl_tools(
            SetupConfig(
                host="testhost",
                username="agent",
                system_type="agent_vm",
                install_gl_tools=True,
            )
        )

        command = mock_run.call_args.args[0]
        for package in GL_TOOL_PACKAGES:
            with self.subTest(package=package):
                self.assertIn(f" {package}", command)
        for package in CLI_TOOL_PACKAGES:
            with self.subTest(default_package=package):
                self.assertNotIn(f" {package}", command)

    @patch("common.common_steps.run")
    @patch("common.common_steps.is_package_installed")
    def test_cli_installer_fails_when_a_package_remains_missing(
        self, mock_is_installed, mock_run
    ):
        mock_is_installed.side_effect = (
            [False] * len(CLI_TOOL_PACKAGES) + [False] * len(CLI_TOOL_PACKAGES)
        )
        mock_run.return_value = MagicMock(returncode=100)

        with self.assertRaisesRegex(RuntimeError, "CLI tool installation failed"):
            install_cli_tools(
                SetupConfig(
                    host="testhost",
                    username="agent",
                    system_type="agent_vm",
                )
            )


class TestUserCommandEnvironment(unittest.TestCase):
    @patch("common.common_steps.pwd.getpwnam")
    @patch("common.common_steps.run")
    def test_login_user_commands_use_target_home_and_system_path(
        self, mock_run, mock_getpwnam
    ):
        mock_getpwnam.return_value = MagicMock(pw_uid=1000)
        _run_as_login_user("agent", "/home/agent", "command -v codex")

        command = mock_run.call_args.args[0]
        self.assertIn("HOME=/home/agent", command)
        self.assertIn("XDG_RUNTIME_DIR=/run/user/1000", command)
        self.assertIn(
            "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus", command
        )
        self.assertIn("PATH=/home/agent/.local/bin:/home/agent/.opencode/bin", command)
        self.assertIn("/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", command)
        self.assertNotIn("/home/loren", command)


class TestVMSudoers(unittest.TestCase):
    @patch("common.common_steps.os.chown")
    @patch("common.common_steps.run")
    def test_installs_validated_sudoers_drop_in_with_mode_0440(
        self, mock_run, mock_chown
    ):
        mock_run.return_value = MagicMock(returncode=0)
        config = SetupConfig(
            host="testhost",
            username="agent",
            system_type="workstation_dev",
            machine_type="vm",
            nopasswd=True,
        )

        with tempfile.TemporaryDirectory() as temporary:
            with patch("common.common_steps.VM_SETUP_SUDOERS_DIR", temporary):
                _ensure_vm_setup_user_sudoers(config)

            sudoers_path = os.path.join(temporary, "infra-tools-agent")
            self.assertEqual(
                os.stat(sudoers_path).st_mode & 0o777,
                0o440,
            )
            with open(sudoers_path, encoding="utf-8") as file_obj:
                self.assertEqual(
                    file_obj.read(), "agent ALL=(ALL) NOPASSWD:ALL\n"
                )

        mock_run.assert_called_once()
        self.assertIn("visudo -cf", mock_run.call_args.args[0])
        self.assertTrue(mock_chown.called)

    @patch("common.common_steps.run")
    def test_repairs_existing_wrong_mode(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        config = SetupConfig(
            host="testhost",
            username="agent",
            system_type="workstation_dev",
            machine_type="vm",
            nopasswd=True,
        )

        with tempfile.TemporaryDirectory() as temporary:
            sudoers_path = os.path.join(temporary, "infra-tools-agent")
            with open(sudoers_path, "w", encoding="utf-8") as file_obj:
                file_obj.write("agent ALL=(ALL) NOPASSWD:ALL\n")
            os.chmod(sudoers_path, 0o644)
            with patch("common.common_steps.VM_SETUP_SUDOERS_DIR", temporary), \
                patch("common.common_steps.os.chown"):
                _ensure_vm_setup_user_sudoers(config)
            self.assertEqual(os.stat(sudoers_path).st_mode & 0o777, 0o440)

    @patch("common.common_steps.run")
    def test_ignores_non_vm_setup(self, mock_run):
        with tempfile.TemporaryDirectory() as temporary, patch(
            "common.common_steps.VM_SETUP_SUDOERS_DIR", temporary
        ):
            _ensure_vm_setup_user_sudoers(
                SetupConfig(
                    host="testhost",
                    username="agent",
                    system_type="server_lite",
                    machine_type="hardware",
                )
            )
        mock_run.assert_not_called()

    def test_default_removes_managed_passwordless_rule(self):
        config = SetupConfig(
            host="testhost",
            username="agent",
            system_type="agent_vm",
            machine_type="vm",
        )

        with tempfile.TemporaryDirectory() as temporary:
            sudoers_path = os.path.join(temporary, "infra-tools-agent")
            with open(sudoers_path, "w", encoding="utf-8") as file_obj:
                file_obj.write("agent ALL=(ALL) NOPASSWD:ALL\n")
            with patch("common.common_steps.VM_SETUP_SUDOERS_DIR", temporary):
                _ensure_vm_setup_user_sudoers(config)
            self.assertFalse(os.path.exists(sudoers_path))

    def test_default_refuses_modified_sudoers_rule(self):
        config = SetupConfig(
            host="testhost",
            username="agent",
            system_type="agent_vm",
            machine_type="vm",
        )

        with tempfile.TemporaryDirectory() as temporary:
            sudoers_path = os.path.join(temporary, "infra-tools-agent")
            with open(sudoers_path, "w", encoding="utf-8") as file_obj:
                file_obj.write("agent ALL=(ALL) NOPASSWD:/usr/bin/apt\n")
            with patch("common.common_steps.VM_SETUP_SUDOERS_DIR", temporary):
                with self.assertRaisesRegex(
                    RuntimeError, "Refusing to remove modified sudoers policy"
                ):
                    _ensure_vm_setup_user_sudoers(config)


class TestDebianPackageSources(unittest.TestCase):
    @patch("common.common_steps.ensure_debian_package_sources")
    def test_checks_sources_before_package_update(self, mock_ensure):
        check_debian_package_sources(
            SetupConfig(host="testhost", username="testuser", system_type="server_lite")
        )
        mock_ensure.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
