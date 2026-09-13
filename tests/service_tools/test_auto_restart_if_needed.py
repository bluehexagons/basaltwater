"""Tests for common.service_tools.auto_restart_if_needed."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))

from common.service_tools import auto_restart_if_needed


class TestAutoRestartIfNeeded(unittest.TestCase):
    def setUp(self):
        config = patch.object(auto_restart_if_needed, "load_setup_config", return_value={})
        config.start()
        self.addCleanup(config.stop)

    @patch("common.service_tools.auto_restart_if_needed.perform_restart")
    @patch("common.service_tools.auto_restart_if_needed.record_deferral")
    @patch("common.service_tools.auto_restart_if_needed.newer_installed_kernel", return_value="6.12.44+deb13-amd64")
    @patch("common.service_tools.auto_restart_if_needed.check_restart_required", return_value=False)
    @patch("common.service_tools.auto_restart_if_needed.load_notification_configs_from_state", return_value=["cfg"])
    def test_markerless_kernel_warns_without_scheduling_restart(self, _load, _marker, _kernel, defer, restart):
        self.assertEqual(auto_restart_if_needed.main(), 0)
        self.assertTrue(defer.call_args.kwargs["advisory"])
        self.assertIn("6.12.44+deb13-amd64", defer.call_args.args[0])
        restart.assert_not_called()

    def test_reminder_uses_calendar_day_despite_timer_jitter(self):
        sent = time.mktime((2026, 9, 10, 2, 6, 44, 0, 0, -1))
        next_run = time.mktime((2026, 9, 11, 2, 1, 44, 0, 0, -1))
        self.assertTrue(auto_restart_if_needed.should_notify({"last_notified": sent}, next_run))
        self.assertFalse(auto_restart_if_needed.should_notify({"last_notified": sent}, sent + 60))

    @patch("common.service_tools.auto_restart_if_needed.save_restart_state")
    @patch("common.service_tools.auto_restart_if_needed.load_restart_state", return_value={})
    @patch("common.service_tools.auto_restart_if_needed.send_notification_safe", return_value=False)
    def test_failed_delivery_does_not_start_cooldown(self, send, _load, save):
        auto_restart_if_needed.record_deferral("disabled", ["cfg"])
        self.assertNotIn("last_notified", save.call_args.args[0])
        self.assertIn("first_required", save.call_args.args[0])
        send.return_value = True
        auto_restart_if_needed.record_deferral("disabled", ["cfg"])
        self.assertIn("last_notified", save.call_args.args[0])

    @patch("common.service_tools.auto_restart_if_needed.save_restart_state")
    @patch("common.service_tools.auto_restart_if_needed.load_restart_state", return_value={"first_required": 1})
    @patch("common.service_tools.auto_restart_if_needed.send_notification_safe")
    def test_advisory_does_not_start_force_deadline_or_record_empty_delivery(self, send, _load, save):
        auto_restart_if_needed.record_deferral("review kernel", [], advisory=True)
        self.assertNotIn("first_required", save.call_args.args[0])
        self.assertNotIn("last_notified", save.call_args.args[0])
        send.assert_not_called()

    @patch("common.service_tools.auto_restart_if_needed.clear_restart_state")
    @patch("common.service_tools.auto_restart_if_needed.newer_installed_kernel")
    @patch("common.service_tools.auto_restart_if_needed.check_restart_required", return_value=False)
    @patch("common.service_tools.auto_restart_if_needed.load_notification_configs_from_state", return_value=[])
    def test_probe_failure_is_not_an_all_clear_and_success_clears_state(self, _load, _marker, probe, clear):
        probe.side_effect = subprocess.TimeoutExpired("dpkg-query", 30)
        self.assertEqual(auto_restart_if_needed.main(), 1)
        clear.assert_not_called()
        probe.side_effect = None
        probe.return_value = None
        self.assertEqual(auto_restart_if_needed.main(), 0)
        clear.assert_called_once()

    @patch("common.service_tools.auto_restart_if_needed.save_restart_state")
    @patch("common.service_tools.auto_restart_if_needed.load_restart_state", return_value={})
    @patch("common.service_tools.auto_restart_if_needed.send_notification_safe")
    def test_filtered_targets_do_not_start_delivery_cooldown(self, send, _load, save):
        configs = [SimpleNamespace(level="off"), SimpleNamespace(level="error")]
        auto_restart_if_needed.record_deferral("disabled", configs)
        send.assert_not_called()
        self.assertNotIn("last_notified", save.call_args.args[0])

    def test_detects_agent_build_multiplexer_and_managed_worktree_categories(self):
        with (
            tempfile.TemporaryDirectory() as home,
            tempfile.TemporaryDirectory() as proc_root,
        ):
            managed = os.path.join(
                home,
                ".local",
                "share",
                "infra_tools",
                "worktrees",
                "project",
            )
            os.makedirs(managed)
            processes = (
                ("101", "codex\n", home),
                ("102", "cargo\n", home),
                ("103", "tmux: server\n", home),
                ("104", "node\n", managed),
            )
            for pid, command, working_directory in processes:
                process = os.path.join(proc_root, pid)
                os.mkdir(process)
                with open(
                    os.path.join(process, "comm"),
                    "w",
                    encoding="utf-8",
                ) as file_obj:
                    file_obj.write(command)
                os.symlink(working_directory, os.path.join(process, "cwd"))

            account = SimpleNamespace(pw_uid=os.getuid(), pw_dir=home)
            with (
                patch(
                    "common.service_tools.auto_restart_if_needed._configured_agent_account",
                    return_value=account,
                ),
                patch(
                    "common.service_tools.auto_restart_if_needed.inspect_agent_maintenance",
                    return_value={"status": "inactive"},
                ),
            ):
                categories = auto_restart_if_needed.get_active_agent_workloads(
                    proc_root=proc_root
                )

        self.assertEqual(
            categories,
            [
                "build or Git process",
                "coding agent process",
                "managed agent worktree process",
                "terminal multiplexer process",
            ],
        )

    def test_active_maintenance_hold_is_a_restart_blocker(self):
        account = SimpleNamespace(pw_uid=os.getuid(), pw_dir="/home/agent")
        with (
            tempfile.TemporaryDirectory() as proc_root,
            patch(
                "common.service_tools.auto_restart_if_needed._configured_agent_account",
                return_value=account,
            ),
            patch(
                "common.service_tools.auto_restart_if_needed.inspect_agent_maintenance",
                return_value={"status": "active"},
            ),
        ):
            self.assertEqual(
                auto_restart_if_needed.get_active_agent_workloads(
                    proc_root=proc_root
                ),
                ["agent maintenance hold"],
            )

    @patch("common.service_tools.auto_restart_if_needed.perform_restart")
    @patch("common.service_tools.auto_restart_if_needed.record_deferral")
    @patch(
        "common.service_tools.auto_restart_if_needed.get_active_agent_workloads",
        return_value=["coding agent process"],
    )
    @patch("common.service_tools.auto_restart_if_needed.get_active_sessions", return_value=[])
    @patch("common.service_tools.auto_restart_if_needed.get_uptime_seconds", return_value=3600)
    @patch("common.service_tools.auto_restart_if_needed.can_restart_system", return_value=True)
    @patch("common.service_tools.auto_restart_if_needed.force_deadline_reached", return_value=False)
    @patch("common.service_tools.auto_restart_if_needed.load_restart_policy", return_value={"auto_restart": True, "force_days": 7, "grace": 5})
    @patch("common.service_tools.auto_restart_if_needed.check_restart_required", return_value=True)
    @patch("common.service_tools.auto_restart_if_needed.load_notification_configs_from_state", return_value=["cfg"])
    def test_defers_for_agent_workloads(
        self,
        _load,
        _check,
        _policy,
        _deadline,
        _can_restart,
        _uptime,
        _sessions,
        _workloads,
        mock_defer,
        mock_restart,
    ):
        self.assertEqual(auto_restart_if_needed.main(), 0)
        mock_defer.assert_called_once_with(
            "active agent workloads detected",
            ["cfg"],
            "coding agent process",
        )
        mock_restart.assert_not_called()

    @patch("common.service_tools.auto_restart_if_needed.perform_restart")
    @patch("common.service_tools.auto_restart_if_needed.record_deferral")
    @patch("common.service_tools.auto_restart_if_needed.get_uptime_seconds", return_value=None)
    @patch("common.service_tools.auto_restart_if_needed.can_restart_system", return_value=True)
    @patch("common.service_tools.auto_restart_if_needed.load_restart_policy", return_value={"auto_restart": True, "force_days": 7, "grace": 5})
    @patch("common.service_tools.auto_restart_if_needed.check_restart_required", return_value=True)
    @patch("common.service_tools.auto_restart_if_needed.load_notification_configs_from_state", return_value=["cfg"])
    def test_defers_when_uptime_cannot_be_read(
        self, _load, _check, _policy, _can_restart, _uptime, mock_defer, mock_restart
    ):
        self.assertEqual(auto_restart_if_needed.main(), 0)
        mock_defer.assert_called_once_with("system uptime could not be determined", ["cfg"])
        mock_restart.assert_not_called()

    @patch("common.service_tools.auto_restart_if_needed.perform_restart")
    @patch("common.service_tools.auto_restart_if_needed.record_deferral")
    @patch("common.service_tools.auto_restart_if_needed.get_active_sessions", return_value=None)
    @patch("common.service_tools.auto_restart_if_needed.get_uptime_seconds", return_value=3600)
    @patch("common.service_tools.auto_restart_if_needed.can_restart_system", return_value=True)
    @patch("common.service_tools.auto_restart_if_needed.load_restart_policy", return_value={"auto_restart": True, "force_days": 7, "grace": 5})
    @patch("common.service_tools.auto_restart_if_needed.check_restart_required", return_value=True)
    @patch("common.service_tools.auto_restart_if_needed.load_notification_configs_from_state", return_value=["cfg"])
    def test_defers_when_sessions_cannot_be_queried(
        self, _load, _check, _policy, _can_restart, _uptime, _sessions, mock_defer, mock_restart
    ):
        self.assertEqual(auto_restart_if_needed.main(), 0)
        mock_defer.assert_called_once_with("active sessions could not be determined", ["cfg"])
        mock_restart.assert_not_called()

    @patch("common.service_tools.auto_restart_if_needed.save_restart_state")
    @patch("common.service_tools.auto_restart_if_needed.load_restart_state", return_value={})
    @patch("common.service_tools.auto_restart_if_needed.time.time", return_value=100000)
    @patch("common.service_tools.auto_restart_if_needed.send_notification_safe")
    @patch("common.service_tools.auto_restart_if_needed.load_notification_configs_from_state", return_value=["cfg"])
    @patch("common.service_tools.auto_restart_if_needed.get_active_sessions", return_value=["1 user seat0 tty2"])
    @patch("common.service_tools.auto_restart_if_needed.get_uptime_seconds", return_value=3600)
    @patch("common.service_tools.auto_restart_if_needed.can_restart_system", return_value=True)
    @patch("common.service_tools.auto_restart_if_needed.load_restart_policy", return_value={"auto_restart": True, "force_days": 7, "grace": 5})
    @patch("common.service_tools.auto_restart_if_needed.check_restart_required", return_value=True)
    def test_defers_when_sessions_active(
        self, _check, _policy, _can_restart, _uptime, _sessions, _load, mock_notify, _time, _state, mock_save
    ):
        result = auto_restart_if_needed.main()
        self.assertEqual(result, 0)
        mock_notify.assert_called_once()
        self.assertIn("deferred", mock_notify.call_args.kwargs["subject"])
        mock_save.assert_called_once()

    @patch("common.service_tools.auto_restart_if_needed.perform_restart", return_value=0)
    @patch("common.service_tools.auto_restart_if_needed.get_active_sessions", return_value=[])
    @patch("common.service_tools.auto_restart_if_needed.get_uptime_seconds", return_value=3600)
    @patch("common.service_tools.auto_restart_if_needed.can_restart_system", return_value=True)
    @patch("common.service_tools.auto_restart_if_needed.load_restart_policy", return_value={"auto_restart": True, "force_days": 7, "grace": 5})
    @patch("common.service_tools.auto_restart_if_needed.check_restart_required", return_value=True)
    @patch("common.service_tools.auto_restart_if_needed.load_notification_configs_from_state", return_value=["cfg"])
    def test_auto_restart_path(self, _load, _check, _policy, _can_restart, _uptime, _sessions, mock_restart):
        result = auto_restart_if_needed.main()
        self.assertEqual(result, 0)
        mock_restart.assert_called_once_with(["cfg"], 5, forced=False)

    @patch("common.service_tools.auto_restart_if_needed.send_notification_safe")
    @patch("common.service_tools.auto_restart_if_needed.shutil.which", return_value="/sbin/shutdown")
    @patch("common.service_tools.auto_restart_if_needed.subprocess.run", side_effect=subprocess.CalledProcessError(1, "shutdown"))
    def test_perform_restart_failure_notifies(self, _run, _which, mock_notify):
        with self.assertLogs(auto_restart_if_needed.logger, level="ERROR") as logs:
            result = auto_restart_if_needed.perform_restart(["cfg"], 5)
        self.assertEqual(result, 1)
        self.assertEqual(mock_notify.call_count, 2)
        self.assertIn("automatic restart failed", mock_notify.call_args.kwargs["subject"])
        self.assertIn("Failed to initiate restart | error=", "\n".join(logs.output))

    @patch("common.service_tools.auto_restart_if_needed.perform_restart", return_value=0)
    @patch("common.service_tools.auto_restart_if_needed.load_restart_state", return_value={"first_required": 0})
    @patch("common.service_tools.auto_restart_if_needed.time.time", return_value=8 * 24 * 60 * 60)
    @patch("common.service_tools.auto_restart_if_needed.get_active_sessions", return_value=["1 user seat0 tty2"])
    @patch("common.service_tools.auto_restart_if_needed.get_uptime_seconds", return_value=3600)
    @patch("common.service_tools.auto_restart_if_needed.can_restart_system", return_value=True)
    @patch("common.service_tools.auto_restart_if_needed.load_restart_policy", return_value={"auto_restart": False, "force_days": 7, "grace": 5})
    @patch("common.service_tools.auto_restart_if_needed.check_restart_required", return_value=True)
    @patch("common.service_tools.auto_restart_if_needed.load_notification_configs_from_state", return_value=["cfg"])
    def test_force_deadline_restarts_even_when_disabled(
        self, _load, _check, _policy, _can_restart, _uptime, _sessions, _time, _state, mock_restart
    ):
        result = auto_restart_if_needed.main()
        self.assertEqual(result, 0)
        mock_restart.assert_called_once_with(["cfg"], 5, forced=True)

    @patch("common.service_tools.auto_restart_if_needed.save_restart_state")
    @patch("common.service_tools.auto_restart_if_needed.load_restart_state", return_value={})
    @patch("common.service_tools.auto_restart_if_needed.time.time", return_value=100000)
    @patch("common.service_tools.auto_restart_if_needed.send_notification_safe")
    @patch("common.service_tools.auto_restart_if_needed.load_notification_configs_from_state", return_value=["cfg"])
    @patch("common.service_tools.auto_restart_if_needed.get_uptime_seconds", return_value=60)
    @patch("common.service_tools.auto_restart_if_needed.can_restart_system", return_value=True)
    @patch("common.service_tools.auto_restart_if_needed.load_restart_policy", return_value={"auto_restart": True, "force_days": 7, "grace": 5})
    @patch("common.service_tools.auto_restart_if_needed.check_restart_required", return_value=True)
    def test_defers_during_minimum_uptime(
        self, _check, _policy, _can_restart, _uptime, _load, mock_notify, _time, _state, mock_save
    ):
        result = auto_restart_if_needed.main()
        self.assertEqual(result, 0)
        mock_notify.assert_called_once()
        mock_save.assert_called_once()


class TestRestartPolicy(unittest.TestCase):
    @patch("common.service_tools.auto_restart_if_needed.load_setup_config", return_value={"auto_restart": False, "username": "u", "system_type": "server_lite"})
    def test_reads_configured_auto_restart(self, _load):
        policy = auto_restart_if_needed.load_restart_policy()
        self.assertFalse(policy["auto_restart"])

    @patch("common.service_tools.auto_restart_if_needed.load_setup_config", return_value={"no_restart": True, "username": "u", "system_type": "server_lite"})
    def test_maps_legacy_no_restart(self, _load):
        policy = auto_restart_if_needed.load_restart_policy()
        self.assertFalse(policy["auto_restart"])

    @patch("common.service_tools.auto_restart_if_needed.load_setup_config", return_value={"username": "u", "system_type": "server_proxmox"})
    def test_uses_proxmox_defaults(self, _load):
        policy = auto_restart_if_needed.load_restart_policy()
        self.assertFalse(policy["auto_restart"])
        self.assertEqual(policy["force_days"], 0)

    @patch(
        "common.service_tools.auto_restart_if_needed.load_setup_config",
        return_value={
            "username": "u",
            "system_type": "server_lite",
            "auto_restart_force_days": "invalid",
            "auto_restart_grace": -2,
        },
    )
    def test_invalid_persisted_numbers_fall_back_safely(self, _load):
        policy = auto_restart_if_needed.load_restart_policy()
        self.assertEqual(policy["force_days"], 7)
        self.assertEqual(policy["grace"], 0)

    @patch("common.service_tools.auto_restart_if_needed.load_restart_state", return_value={"first_required": "invalid"})
    @patch("common.service_tools.auto_restart_if_needed.time.time", return_value=1000)
    def test_invalid_restart_timestamp_does_not_force(self, _time, _state):
        self.assertFalse(auto_restart_if_needed.force_deadline_reached({"force_days": 7}))


if __name__ == "__main__":
    unittest.main()
