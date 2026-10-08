"""Tests that required setup mutations cannot fail silently."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from lib.config import SetupConfig
from common import common_steps
from smb import samba_steps
from web.cloudflare_steps import _allow_antistatic_direct_access_for_cloudflare
from security.security_steps import configure_firewall
from web.cicd_steps import create_cicd_directories, install_cicd_dependencies


def _config() -> SetupConfig:
    return SetupConfig(host="target", username="admin", system_type="server_lite")


class TestRequiredCommonMutations(unittest.TestCase):
    def test_requested_password_failure_stops_before_privilege_changes(self):
        config = _config()
        config.password = "test-only-password"
        with patch.object(common_steps, "run", return_value=SimpleNamespace(returncode=0)) as command, patch.object(
            common_steps, "set_user_password", return_value=False,
        ):
            with self.assertRaisesRegex(RuntimeError, "password could not be updated"):
                common_steps.setup_user(config)
        command.assert_called_once_with("id admin", check=False)

    def test_hardening_only_tolerates_verified_absent_group_membership(self):
        config = _config()
        config.harden_agent = True
        for output in ("admin", "admin sudo"):
            with self.subTest(groups=output):
                def run(command, **kwargs):
                    return SimpleNamespace(
                        returncode=1 if isinstance(command, str) and command.startswith("gpasswd") else 0,
                        stdout=output,
                    )
                with patch.object(common_steps, "run", side_effect=run), patch.object(
                    common_steps, "_ensure_vm_setup_user_sudoers",
                ):
                    if "sudo" in output:
                        with self.assertRaisesRegex(RuntimeError, "sudo privileges"):
                            common_steps.setup_user(config)
                    else:
                        common_steps.setup_user(config)

    def test_time_sync_start_failure_stops_before_timezone_and_success(self):
        def run(command, **kwargs):
            if command == "systemctl start chrony":
                self.assertTrue(kwargs.get("check", True))
                raise RuntimeError("chrony failed")
            return SimpleNamespace(returncode=0)
        with patch.object(common_steps, "can_manage_time_sync", return_value=True), patch.object(
            common_steps, "is_package_installed", return_value=False,
        ), patch.object(common_steps, "install_package"), patch.object(
            common_steps, "run", side_effect=run,
        ) as command:
            with self.assertRaisesRegex(RuntimeError, "chrony failed"):
                common_steps.configure_time_sync(_config())
        self.assertFalse(any("set-timezone" in call.args[0] for call in command.call_args_list))

    def test_requested_package_failure_stops_later_installations(self):
        config = _config()
        config.apt_packages = ["curl", "git"]
        with patch.object(common_steps, "is_package_installed", return_value=False), patch.object(
            common_steps, "run", side_effect=RuntimeError("package failed"),
        ) as command:
            with self.assertRaisesRegex(RuntimeError, "package failed"):
                common_steps.install_apt_packages(config)
        self.assertEqual(command.call_count, 1)
        self.assertTrue(command.call_args.kwargs.get("check", True))

    def test_requested_package_missing_after_installation_is_fatal(self):
        config = _config()
        config.apt_packages = ["curl"]
        with patch.object(common_steps, "is_package_installed", return_value=False), patch.object(
            common_steps, "run", return_value=SimpleNamespace(returncode=0),
        ):
            with self.assertRaisesRegex(RuntimeError, "was not installed"):
                common_steps.install_apt_packages(config)

    def test_unavailable_flatpak_cannot_satisfy_requested_packages(self):
        config = _config()
        config.flatpak_packages = ["org.example.Application"]
        with (
            patch("lib.machine_state.is_container", return_value=False),
            patch("desktop.apps_steps.install_flatpak_if_needed"),
            patch("desktop.apps_steps.is_flatpak_installed", return_value=False),
        ):
            with self.assertRaisesRegex(RuntimeError, "Flatpak is unavailable"):
                common_steps.install_flatpak_packages(config)

    def test_cloudflare_direct_access_rule_failure_stops_configuration(self):
        config = _config()
        config.antistatic_server = "game.example.com"
        with patch("web.cloudflare_steps.run", side_effect=RuntimeError("rule failed")) as command:
            with self.assertRaisesRegex(RuntimeError, "rule failed"):
                _allow_antistatic_direct_access_for_cloudflare(config)
        self.assertTrue(command.call_args.kwargs.get("check", True))


class TestRequiredSambaMutations(unittest.TestCase):
    def test_invalid_username_is_rejected_before_commands(self):
        with patch.object(samba_steps, "run") as command:
            with self.assertRaisesRegex(ValueError, "Invalid Samba username"):
                samba_steps.create_samba_user("admin\n", "test-only-password")
        command.assert_not_called()

    def test_user_enablement_failure_reaches_setup(self):
        def run(command, **kwargs):
            if command.startswith("smbpasswd -e "):
                self.assertTrue(kwargs.get("check", True))
                raise RuntimeError("account enablement failed")
            return SimpleNamespace(returncode=0)
        with patch.object(samba_steps, "run", side_effect=run):
            with self.assertRaisesRegex(RuntimeError, "account enablement failed"):
                samba_steps.create_samba_user("admin", "test-only-password")


class TestRequiredCICDMutations(unittest.TestCase):
    @patch("web.cicd_steps.run")
    @patch("web.cicd_steps.is_package_installed", return_value=False)
    def test_missing_dependency_after_install_stops_setup(
        self, _installed, mock_run
    ) -> None:
        with self.assertRaisesRegex(RuntimeError, "not present after installation"):
            install_cicd_dependencies(_config())

        mock_run.assert_called_once_with(
            ["apt-get", "install", "-y", "-qq", "git"],
        )

    @patch("web.cicd_steps.os.makedirs")
    @patch("web.cicd_steps.run", return_value=SimpleNamespace(returncode=1))
    def test_missing_service_user_stops_directory_setup(
        self, _run, _makedirs
    ) -> None:
        with self.assertRaisesRegex(RuntimeError, "user 'webhook' does not exist"):
            create_cicd_directories(_config())

    @patch("web.cicd_steps.os.makedirs")
    @patch("web.cicd_steps.run")
    def test_directory_permission_failure_reaches_caller(
        self, mock_run, _makedirs
    ) -> None:
        def run_side_effect(
            command: str | list[str],
            **_kwargs: object,
        ) -> SimpleNamespace:
            if command == ["id", "webhook"]:
                return SimpleNamespace(returncode=0)
            raise RuntimeError("permission mutation failed")

        mock_run.side_effect = run_side_effect

        with self.assertRaisesRegex(RuntimeError, "permission mutation failed"):
            create_cicd_directories(_config())


class TestRequiredFirewallMutations(unittest.TestCase):
    @patch("security.security_steps.is_container", return_value=False)
    @patch("security.security_steps.run")
    def test_default_policy_failure_reaches_caller(
        self, mock_run, _container
    ) -> None:
        def run_side_effect(command: str, **kwargs: object) -> SimpleNamespace:
            if command.startswith("ufw status 2>"):
                return SimpleNamespace(returncode=1, stdout="")
            if command == "ufw default deny incoming":
                self.assertTrue(kwargs["check"])
                raise RuntimeError("default policy failed")
            return SimpleNamespace(returncode=0, stdout="")

        mock_run.side_effect = run_side_effect

        with self.assertRaisesRegex(RuntimeError, "default policy failed"):
            configure_firewall(_config())

    @patch("security.security_steps.is_container", return_value=False)
    @patch("security.security_steps.run")
    def test_enable_failure_stops_non_container_setup(
        self, mock_run, _container
    ) -> None:
        def run_side_effect(command: str, **_kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(
                returncode=(
                    1
                    if command.startswith("ufw status 2>")
                    or command == "ufw --force enable"
                    else 0
                ),
                stdout="",
            )

        mock_run.side_effect = run_side_effect

        with self.assertRaisesRegex(RuntimeError, "Firewall could not be enabled"):
            configure_firewall(_config())

    @patch("security.security_steps.is_container", return_value=True)
    @patch("security.security_steps.run")
    def test_enable_failure_remains_best_effort_in_container(
        self, mock_run, _container
    ) -> None:
        def run_side_effect(command: str, **_kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(
                returncode=(
                    1
                    if command.startswith("ufw status 2>")
                    or command == "ufw --force enable"
                    else 0
                ),
                stdout="",
            )

        mock_run.side_effect = run_side_effect

        with patch("builtins.print") as mock_print:
            configure_firewall(_config())

        mock_print.assert_any_call(
            "  ⚠ Firewall could not be enabled (container may lack capabilities)"
        )


if __name__ == "__main__":
    unittest.main()
