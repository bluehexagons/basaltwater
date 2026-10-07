"""Tests for common.service_tools.auto_update_apt."""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))

from common.service_tools import auto_update_apt
from lib.proxmox_preflight import check_proxmox_upgrade_candidate


def setUpModule() -> None:
    for target, kwargs in (
        ("maintenance_lock", {"side_effect": lambda: nullcontext(True)}),
        ("is_proxmox_host", {"return_value": False}),
    ):
        mocker = patch(f"common.service_tools.auto_update_apt.{target}", **kwargs)
        mocker.start()
        unittest.addModuleCleanup(mocker.stop)


class TestAutoUpdateApt(unittest.TestCase):
    @patch("common.service_tools.auto_update_apt.ensure_debian_package_sources")
    @patch("common.service_tools.auto_update_apt.subprocess.run")
    @patch("common.service_tools.auto_update_apt.send_notification_safe")
    @patch("common.service_tools.auto_update_apt.load_notification_configs_from_state", return_value=["cfg"])
    def test_partial_refresh_or_launch_failure_notifies_without_upgrading(self, _configs, notify, run, _sources):
        for failure in (
            subprocess.CompletedProcess([], 100, stdout="", stderr="index unavailable"),
            FileNotFoundError("apt-get unavailable"),
        ):
            with self.subTest(failure=failure):
                run.reset_mock()
                notify.reset_mock()
                run.side_effect = [failure]
                self.assertEqual(auto_update_apt.main(), 1)
                run.assert_called_once()
                self.assertIn("APT::Update::Error-Mode=any", run.call_args.args[0])
                notify.assert_called_once()

    @patch("common.service_tools.auto_update_apt.upgrade_packages", return_value=(True, ""))
    @patch("common.service_tools.auto_update_apt.update_package_lists", return_value=True)
    @patch("common.service_tools.auto_update_apt.load_notification_configs_from_state", return_value=[])
    def test_successful_update(self, _configs, _update, _upgrade):
        with self.assertLogs(auto_update_apt.logger, level="INFO") as logs:
            result = auto_update_apt.main()
        self.assertEqual(result, 0)
        _update.assert_called_once()
        _upgrade.assert_called_once()
        joined = "\n".join(logs.output)
        self.assertIn("Starting APT package update", joined)
        self.assertIn("APT package update completed successfully", joined)

    @patch("common.service_tools.auto_update_apt.send_notification_safe")
    @patch("common.service_tools.auto_update_apt.update_package_lists", return_value=False)
    @patch("common.service_tools.auto_update_apt.load_notification_configs_from_state", return_value=["cfg"])
    def test_update_failure_notifies(self, _configs, _update, mock_notify):
        result = auto_update_apt.main()
        self.assertEqual(result, 1)
        mock_notify.assert_called_once()
        self.assertIn("APT update failed", mock_notify.call_args.kwargs["subject"])

    @patch("common.service_tools.auto_update_apt.send_notification_safe")
    @patch("common.service_tools.auto_update_apt.upgrade_packages", return_value=(False, "dependency error"))
    @patch("common.service_tools.auto_update_apt.update_package_lists", return_value=True)
    @patch("common.service_tools.auto_update_apt.load_notification_configs_from_state", return_value=["cfg"])
    def test_upgrade_failure_notifies(self, _configs, _update, _upgrade, mock_notify):
        result = auto_update_apt.main()
        self.assertEqual(result, 1)
        mock_notify.assert_called_once()
        self.assertIn("APT upgrade failed", mock_notify.call_args.kwargs["subject"])
        self.assertEqual("dependency error", mock_notify.call_args.kwargs["details"])

    @patch("common.service_tools.auto_update_apt.ensure_debian_package_sources")
    @patch("common.service_tools.auto_update_apt.run_apt_command")
    def test_update_package_lists_logs_structured_error(self, mock_run, _mock_sources):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="mirror offline"
        )

        with self.assertLogs(auto_update_apt.logger, level="ERROR") as logs:
            ok = auto_update_apt.update_package_lists()

        self.assertFalse(ok)
        self.assertIn("apt-get update failed | stderr='mirror offline'", "\n".join(logs.output))

    @patch(
        "common.service_tools.auto_update_apt.ensure_debian_package_sources",
        side_effect=RuntimeError("stale Debian source"),
    )
    @patch("common.service_tools.auto_update_apt.run_apt_command")
    def test_source_preflight_failure_skips_apt_update(self, mock_run, _mock_sources):
        with self.assertLogs(auto_update_apt.logger, level="ERROR") as logs:
            ok = auto_update_apt.update_package_lists()

        self.assertFalse(ok)
        mock_run.assert_not_called()
        self.assertIn("Debian APT source preflight failed", "\n".join(logs.output))


class TestProxmoxUpdateGates(unittest.TestCase):
    def setUp(self) -> None:
        self.enterContext(patch.object(auto_update_apt, "is_proxmox_host", return_value=True))
        self.enterContext(patch.object(auto_update_apt, "load_notification_configs_from_state", return_value=["cfg"]))
        self.notify = self.enterContext(patch.object(auto_update_apt, "send_notification_safe"))
        self.installation = self.enterContext(patch.object(auto_update_apt, "check_proxmox_installation"))
        self.health = self.enterContext(patch.object(auto_update_apt, "check_proxmox_update_safety"))
        self.candidate = self.enterContext(patch.object(auto_update_apt, "check_proxmox_upgrade_candidate"))
        self.refresh = self.enterContext(patch.object(auto_update_apt, "update_package_lists", return_value=True))
        self.upgrade = self.enterContext(patch.object(auto_update_apt, "upgrade_packages", return_value=(True, "")))

    def test_health_rechecked_after_refresh_and_after_upgrade(self) -> None:
        events = []
        self.health.side_effect = lambda **_: events.append("health") or SimpleNamespace(warnings=[])
        self.refresh.side_effect = lambda **_: events.append("refresh") or True
        self.upgrade.side_effect = lambda: (events.append("upgrade") or True, "")
        self.assertEqual(auto_update_apt.main(), 0)
        self.assertEqual(events, ["health", "refresh", "health", "upgrade", "health"])
        self.refresh.assert_called_once_with(repair_sources=False)
        self.assertEqual(self.candidate.call_count, 2)
        self.candidate.assert_called_with(require_current=True)
        self.assertEqual(self.installation.call_count, 2)
        self.assertTrue(all(call.kwargs == {"allow_inactive_storage": True} for call in self.health.call_args_list))

    def test_busy_setup_defers_all_package_commands(self) -> None:
        with patch.object(auto_update_apt, "maintenance_lock", side_effect=lambda: nullcontext(False)):
            self.assertEqual(auto_update_apt.main(), 0)
        self.installation.assert_not_called()
        self.refresh.assert_not_called()
        self.upgrade.assert_not_called()

    def test_point_release_candidate_allows_scheduled_upgrade(self) -> None:
        self.candidate.side_effect = check_proxmox_upgrade_candidate
        self.health.return_value = SimpleNamespace(warnings=[])
        with patch("lib.proxmox_preflight.run", return_value=subprocess.CompletedProcess(
            [], 0, "pve-manager:\n  Installed: 9.3.1\n  Candidate: 9.3.1\n", "",
        )):
            self.assertEqual(auto_update_apt.main(), 0)
        self.refresh.assert_called_once_with(repair_sources=False)
        self.upgrade.assert_called_once()
        self.notify.assert_not_called()

    def test_major_release_candidate_stops_scheduled_upgrade(self) -> None:
        self.candidate.side_effect = check_proxmox_upgrade_candidate
        self.health.return_value = SimpleNamespace(warnings=[])
        with patch("lib.proxmox_preflight.run", return_value=subprocess.CompletedProcess(
            [], 0, "pve-manager:\n  Candidate: 10.0.1\n", "",
        )):
            self.assertEqual(auto_update_apt.main(), 1)
        self.refresh.assert_called_once_with(repair_sources=False)
        self.upgrade.assert_not_called()
        self.assertIn("candidate is not supported", self.notify.call_args.kwargs["message"])

    def test_stale_manager_after_apt_success_is_not_reported_as_success(self) -> None:
        self.candidate.side_effect = check_proxmox_upgrade_candidate
        self.health.return_value = SimpleNamespace(warnings=[])
        with patch("lib.proxmox_preflight.run", return_value=subprocess.CompletedProcess(
            [], 0, "pve-manager:\n  Installed: 9.2.1\n  Candidate: 9.3.1\n", "",
        )):
            self.assertEqual(auto_update_apt.main(), 1)
        self.upgrade.assert_called_once()
        self.assertIn("did not reach its APT candidate", self.notify.call_args.kwargs["message"])

    def test_new_backup_after_refresh_prevents_upgrade(self) -> None:
        self.health.side_effect = [SimpleNamespace(warnings=[]), RuntimeError("1 active Proxmox task(s)")]
        self.assertEqual(auto_update_apt.main(), 1)
        self.upgrade.assert_not_called()
        self.assertIn("active Proxmox task", self.notify.call_args.kwargs["message"])

    def test_unsupported_repository_prevents_refresh(self) -> None:
        self.installation.side_effect = RuntimeError("Unsupported Proxmox repository")
        self.assertEqual(auto_update_apt.main(), 1)
        self.refresh.assert_not_called()
        self.upgrade.assert_not_called()

    def test_post_upgrade_failure_is_not_reported_as_success(self) -> None:
        self.health.side_effect = [SimpleNamespace(warnings=[]), None, RuntimeError("Core service pveproxy is failed")]
        self.assertEqual(auto_update_apt.main(), 1)
        self.upgrade.assert_called_once()
        self.assertIn("pveproxy", self.notify.call_args.kwargs["message"])


class TestRunAptCommand(unittest.TestCase):
    @patch("common.service_tools.auto_update_apt.subprocess.run")
    def test_sets_noninteractive_frontend(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        auto_update_apt.run_apt_command(["update", "-qq"])
        _, kwargs = mock_run.call_args
        self.assertEqual(kwargs["env"]["DEBIAN_FRONTEND"], "noninteractive")

    @patch("common.service_tools.auto_update_apt.subprocess.run")
    def test_passes_arguments_to_apt_get(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        auto_update_apt.run_apt_command(["dist-upgrade", "-y"])
        args, _ = mock_run.call_args
        self.assertEqual(args[0], ["apt-get", "dist-upgrade", "-y"])


class TestUpgradePackages(unittest.TestCase):
    @patch("common.service_tools.auto_update_apt.run_apt_command")
    def test_uses_dpkg_options(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        auto_update_apt.upgrade_packages()
        args = mock_run.call_args[0][0]
        self.assertIn("-o", args)
        self.assertIn("Dpkg::Options::=--force-confdef", args)
        self.assertIn("Dpkg::Options::=--force-confold", args)

    @patch("common.service_tools.auto_update_apt.run_apt_command")
    def test_upgrade_refuses_package_removals(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        auto_update_apt.upgrade_packages()
        args = mock_run.call_args[0][0]
        self.assertIn("--no-remove", args)

    @patch("common.service_tools.auto_update_apt.run_apt_command")
    def test_upgrade_uses_lock_timeout(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        auto_update_apt.upgrade_packages()
        args = mock_run.call_args[0][0]
        self.assertIn("DPkg::Lock::Timeout=300", args)

    @patch("common.service_tools.auto_update_apt.ensure_debian_package_sources")
    @patch("common.service_tools.auto_update_apt.run_apt_command")
    def test_update_uses_lock_timeout(self, mock_run, _mock_sources):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        auto_update_apt.update_package_lists()
        args = mock_run.call_args[0][0]
        self.assertIn("DPkg::Lock::Timeout=300", args)

if __name__ == "__main__":
    unittest.main()
