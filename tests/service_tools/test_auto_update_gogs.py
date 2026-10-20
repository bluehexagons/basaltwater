"""Tests for common.service_tools.auto_update_gogs."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))

from common.service_tools import auto_update_gogs


class TestAutoUpdateGogs(unittest.TestCase):
    def setUp(self):
        self.record_update_function = auto_update_gogs._record_update_result
        self.record_update_patcher = patch(
            "common.service_tools.auto_update_gogs._record_update_result"
        )
        self.record_update = self.record_update_patcher.start()
        self.addCleanup(self.record_update_patcher.stop)
        self.port_function = auto_update_gogs._read_gogs_http_port
        port_patcher = patch.object(auto_update_gogs, "_read_gogs_http_port", return_value=13000)
        self.port = port_patcher.start()
        self.addCleanup(port_patcher.stop)
        ready_patcher = patch.object(auto_update_gogs, "_wait_for_gogs_ready")
        self.ready = ready_patcher.start()
        self.addCleanup(ready_patcher.stop)
        home_patcher = patch("web.gogs_steps._get_git_home", return_value="/home/git")
        home_patcher.start()
        self.addCleanup(home_patcher.stop)

    @patch("common.service_tools.auto_update_gogs.send_notification_safe")
    @patch("common.service_tools.auto_update_gogs._run_command")
    @patch("common.service_tools.auto_update_gogs._run_shell_command")
    @patch("common.service_tools.auto_update_gogs.install_or_update_gogs_release", return_value=("v1.2.3", False, "a" * 64))
    @patch("common.service_tools.auto_update_gogs.read_gogs_state", return_value={"tag_name": "v1.2.3", "config_path": "/srv/gogs/custom/conf/app.ini"})
    @patch("common.service_tools.auto_update_gogs.os.path.exists", return_value=True)
    @patch("common.service_tools.auto_update_gogs.load_notification_configs_from_state", return_value=[])
    def test_skips_when_gogs_is_current(
        self,
        _configs,
        _exists,
        _state,
        _install,
        mock_shell,
        mock_command,
        mock_notify,
    ):
        with self.assertLogs(auto_update_gogs.logger, level="INFO") as logs:
            result = auto_update_gogs.main()
        self.assertEqual(result, 0)
        self.record_update.assert_called_once_with(0)
        mock_shell.assert_not_called()
        mock_command.assert_not_called()
        mock_notify.assert_not_called()
        self.assertIn("Gogs already up to date", "\n".join(logs.output))

    @patch("common.service_tools.auto_update_gogs.send_notification_safe")
    @patch("common.service_tools.auto_update_gogs._run_command")
    @patch("common.service_tools.auto_update_gogs._run_shell_command")
    @patch("common.service_tools.auto_update_gogs.write_gogs_state")
    @patch("common.service_tools.auto_update_gogs.install_or_update_gogs_release", return_value=("v1.2.4", True, "a" * 64))
    @patch("common.service_tools.auto_update_gogs.read_gogs_state", return_value={"tag_name": "v1.2.3", "config_path": "/srv/gogs/custom/conf/app.ini"})
    @patch("common.service_tools.auto_update_gogs.os.path.exists", return_value=True)
    @patch("common.service_tools.auto_update_gogs.load_notification_configs_from_state", return_value=[])
    def test_restarts_service_after_update(
        self,
        _configs,
        _exists,
        _state,
        _install,
        mock_write_state,
        mock_shell,
        mock_command,
        mock_notify,
    ):
        mock_shell.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        mock_command.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        self.ready.side_effect = lambda _port: mock_command.assert_called_once_with(["systemctl", "restart", "gogs"])
        mock_write_state.side_effect = lambda *_args: self.ready.assert_called_once_with(13000)

        result = auto_update_gogs.main()

        self.assertEqual(result, 0)
        self.record_update.assert_called_once_with(0)
        self.assertEqual(mock_shell.call_count, 2)
        self.ready.assert_called_once_with(13000)
        mock_write_state.assert_called_once_with(
            "v1.2.4",
            "/srv/gogs",
            "/srv/gogs/custom/conf/app.ini",
            "a" * 64,
        )
        mock_command.assert_called_once_with(["systemctl", "restart", "gogs"])
        mock_notify.assert_called_once()
        self.assertIn("Success: Gogs updated", mock_notify.call_args.kwargs["subject"])
        self.assertEqual(mock_notify.call_args.kwargs["status"], "good")

    @patch("common.service_tools.auto_update_gogs.send_notification_safe")
    @patch("common.service_tools.auto_update_gogs.install_or_update_gogs_release", side_effect=RuntimeError("boom"))
    @patch("common.service_tools.auto_update_gogs.read_gogs_state", return_value={"tag_name": "v1.2.3", "config_path": "/srv/gogs/custom/conf/app.ini"})
    @patch("common.service_tools.auto_update_gogs.os.path.exists", return_value=True)
    @patch("common.service_tools.auto_update_gogs.load_notification_configs_from_state", return_value=["cfg"])
    def test_notifies_when_release_install_fails(
        self,
        _configs,
        _exists,
        _state,
        _install,
        mock_notify,
    ):
        result = auto_update_gogs.main()
        self.assertEqual(result, 1)
        self.record_update.assert_called_once_with(1)
        mock_notify.assert_called_once()
        self.assertIn("Gogs update failed", mock_notify.call_args.kwargs["subject"])

    @patch("common.service_tools.auto_update_gogs.send_notification_safe")
    @patch("common.service_tools.auto_update_gogs._rollback_gogs_release", return_value=True)
    @patch("common.service_tools.auto_update_gogs._current_release_path", return_value="/opt/gogs/releases/v1.2.3")
    @patch("common.service_tools.auto_update_gogs._run_command")
    @patch("common.service_tools.auto_update_gogs._run_shell_command")
    @patch("common.service_tools.auto_update_gogs.install_or_update_gogs_release", return_value=("v1.2.4", True, "a" * 64))
    @patch(
        "common.service_tools.auto_update_gogs.read_gogs_state",
        return_value={"tag_name": "v1.2.3", "config_path": "/srv/gogs/custom/conf/app.ini"},
    )
    @patch("common.service_tools.auto_update_gogs.os.path.exists", return_value=True)
    @patch("common.service_tools.auto_update_gogs.load_notification_configs_from_state", return_value=["cfg"])
    def test_rolls_back_when_post_update_command_fails(
        self,
        _configs,
        _exists,
        _state,
        _install,
        mock_shell,
        mock_command,
        _previous_release,
        mock_rollback,
        mock_notify,
    ):
        mock_shell.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="incompatible command"
        )

        result = auto_update_gogs.main()

        self.assertEqual(result, 1)
        self.record_update.assert_called_once_with(1)
        mock_command.assert_not_called()
        mock_rollback.assert_called_once_with("/opt/gogs/releases/v1.2.3", 13000)
        self.assertIn("Previous release restored", mock_notify.call_args.kwargs["details"])

    def test_http_failure_rolls_back_without_persisting_or_announcing_success(self):
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        for rollback_ready in (True, False):
            with self.subTest(rollback_ready=rollback_ready):
                self.ready.reset_mock()
                self.ready.side_effect = [
                    RuntimeError("Gogs did not become HTTP-ready after service startup"),
                    None if rollback_ready else RuntimeError("rollback HTTP unavailable"),
                ]
                with (
                    patch.object(auto_update_gogs, "load_notification_configs_from_state", return_value=[]),
                    patch.object(auto_update_gogs.os.path, "exists", return_value=True),
                    patch.object(auto_update_gogs, "read_gogs_state", return_value={
                        "tag_name": "v1.2.3", "config_path": "/srv/gogs/custom/conf/app.ini",
                    }),
                    patch.object(auto_update_gogs, "_current_release_path", return_value="/opt/gogs/releases/v1.2.3"),
                    patch.object(auto_update_gogs, "install_or_update_gogs_release", return_value=("v1.2.4", True, "a" * 64)),
                    patch.object(auto_update_gogs, "_run_shell_command", return_value=completed),
                    patch.object(auto_update_gogs, "_run_command", return_value=completed) as command,
                    patch.object(auto_update_gogs, "write_gogs_state") as write_state,
                    patch.object(auto_update_gogs, "send_notification_safe") as notify,
                ):
                    self.assertEqual(auto_update_gogs.main(), 1)
                write_state.assert_not_called()
                self.record_update.assert_called_with(1)
                self.assertEqual(self.ready.call_count, 2)
                self.assertEqual(command.call_args_list[1].args[0], [
                    "ln", "-sfn", "/opt/gogs/releases/v1.2.3", "/opt/gogs/current",
                ])
                notify.assert_called_once()
                self.assertEqual(notify.call_args.kwargs["status"], "error")
                expected = "Previous release restored." if rollback_ready else "Previous release could not be restored."
                self.assertIn(expected, notify.call_args.kwargs["details"])

    @patch("common.service_tools.auto_update_gogs._run_command")
    @patch("common.service_tools.auto_update_gogs.os.path.exists", return_value=True)
    def test_rollback_relinks_and_restarts_previous_release(self, _exists, mock_command):
        mock_command.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        rolled_back = auto_update_gogs._rollback_gogs_release("/opt/gogs/releases/v1.2.3", 13000)

        self.assertTrue(rolled_back)
        self.ready.assert_called_once_with(13000)
        self.assertEqual(
            [call.args[0] for call in mock_command.call_args_list],
            [
                ["ln", "-sfn", "/opt/gogs/releases/v1.2.3", "/opt/gogs/current"],
                ["systemctl", "restart", "gogs"],
            ],
        )

    def test_reads_actual_backend_port_without_interpolating_gogs_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = os.path.join(directory, "app.ini")
            for value in ("13001", "0", "65536", "invalid", "3000; touch /tmp/oops"):
                with self.subTest(port=value):
                    with open(config_path, "w", encoding="utf-8") as source:
                        source.write(f"RUN_USER = git\n[server]\nHTTP_PORT = {value}\n[security]\nSECRET_KEY = %secret\n")
                    if value == "13001":
                        self.assertEqual(self.port_function(config_path), 13001)
                    else:
                        with self.assertRaises(ValueError):
                            self.port_function(config_path)

    def test_unreadable_backend_port_stops_before_release_install(self):
        self.port.side_effect = ValueError("Cannot read Gogs HTTP port")
        with (
            patch.object(auto_update_gogs, "load_notification_configs_from_state", return_value=[]),
            patch.object(auto_update_gogs.os.path, "exists", return_value=True),
            patch.object(auto_update_gogs, "read_gogs_state", return_value={"config_path": "/srv/gogs/app.ini"}),
            patch.object(auto_update_gogs, "install_or_update_gogs_release") as install,
            patch.object(auto_update_gogs, "send_notification_safe") as notify,
        ):
            self.assertEqual(auto_update_gogs.main(), 1)
        install.assert_not_called()
        self.assertEqual(notify.call_args.kwargs["status"], "error")

    def test_records_root_owned_update_health_state(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = os.path.join(directory, "gogs_update.json")
            with patch.object(
                auto_update_gogs,
                "GOGS_UPDATE_STATE_FILE",
                state_path,
            ):
                self.record_update_function(1)

            with open(state_path, encoding="utf-8") as source:
                state = json.load(source)

        self.assertEqual(state["schema_version"], 1)
        self.assertEqual(state["exit_code"], 1)
        self.assertFalse(state["successful"])
        self.assertTrue(state["checked_at"].endswith("+00:00"))


if __name__ == "__main__":
    unittest.main()
