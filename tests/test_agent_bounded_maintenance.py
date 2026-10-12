"""Maintenance captures bound memory, pipes, process groups and interruption."""

from __future__ import annotations

import os
import signal
import subprocess
import unittest
from unittest.mock import MagicMock, patch

from common.service_tools import cleanup_maintenance, user_cache_maintenance
from lib import maintenance_agent, streamed_process
from lib.agent_probe import ProbeResult


class BoundedMaintenanceTests(unittest.TestCase):
    def pipe(self, content=None):
        reader, writer = os.pipe()
        output, held = os.fdopen(reader, "rb", buffering=0), os.fdopen(writer, "wb", buffering=0)
        self.addCleanup(output.close)
        self.addCleanup(held.close)
        if content is not None:
            held.write(content)
            held.close()
        return output, held

    def test_separate_stdout_stderr_share_a_byte_budget_and_preserve_exit_status(self):
        for maximum, exceeded in ((5, False), (4, True)):
            with self.subTest(maximum=maximum):
                output, _out = self.pipe(b"ok")
                errors, _err = self.pipe(b"bad")
                process = MagicMock(pid=123, stdin=None, stdout=output, stderr=errors)
                process.wait.return_value = 7
                with patch.object(streamed_process.subprocess, "Popen", return_value=process), \
                        patch.object(streamed_process, "_terminate_timed_out_process") as stop:
                    if exceeded:
                        with self.assertRaises(streamed_process.CommandOutputLimitError):
                            streamed_process.run_captured(["fixture"], timeout=1, max_bytes=maximum)
                        stop.assert_called_once_with(process, isolated=True)
                    else:
                        result = streamed_process.run_captured(["fixture"], timeout=1, max_bytes=maximum)
                        self.assertEqual((result.returncode, result.stdout, result.stderr), (7, "ok", "bad"))
                        stop.assert_not_called()
                self.assertTrue(output.closed)
                self.assertTrue(errors.closed)

    def test_exited_leader_does_not_prevent_term_then_kill_of_pipe_holding_descendants(self):
        output, _held = self.pipe()
        process = MagicMock(pid=123, stdin=None, stdout=output, stderr=None)
        process.poll.return_value = 0
        process.wait.return_value = 0
        with patch.object(streamed_process.subprocess, "Popen", return_value=process) as start, \
                patch("lib.remote_utils.os.killpg") as kill:
            with self.assertRaises(subprocess.TimeoutExpired):
                streamed_process.run_captured(["fixture"], timeout=0.02, capture_stderr=False)
        self.assertEqual(kill.call_args_list[0].args, (123, signal.SIGTERM))
        self.assertEqual(kill.call_args_list[1].args, (123, signal.SIGKILL))
        self.assertTrue(start.call_args.kwargs["start_new_session"])
        self.assertTrue(output.closed)

    def test_interruption_terminates_the_group_even_after_the_leader_exits(self):
        output, _held = self.pipe()
        process = MagicMock(pid=123, stdin=None, stdout=output, stderr=None)
        process.poll.return_value = process.wait.return_value = 0
        with patch.object(streamed_process.subprocess, "Popen", return_value=process), \
                patch.object(streamed_process.selectors, "DefaultSelector", side_effect=KeyboardInterrupt), \
                patch("lib.remote_utils.os.killpg") as kill:
            with self.assertRaises(KeyboardInterrupt):
                streamed_process.run_captured(["fixture"], timeout=1, capture_stderr=False)
        self.assertEqual([call.args[1] for call in kill.call_args_list], [signal.SIGTERM, signal.SIGKILL])
        self.assertTrue(output.closed)

    def test_cleanup_output_overflow_is_reported_without_retry_or_partial_output(self):
        error = streamed_process.CommandOutputLimitError("Command output exceeded 262144 bytes")
        with patch.object(cleanup_maintenance, "run_captured", side_effect=error) as root, \
                patch.object(user_cache_maintenance, "run_captured", side_effect=error) as user:
            result = cleanup_maintenance.run_cleanup_command(["fixture"], "Fixture cleanup")
            context = user_cache_maintenance.UserContext("fixture", "/fixture", 1000)
            other = user_cache_maintenance.run_cleanup_command(context, ["fixture"], "Fixture cleanup", dry_run=False)
        self.assertIn("output exceeded", result)
        self.assertIn("output exceeded", other)
        root.assert_called_once()
        user.assert_called_once()

    def test_review_failure_remains_a_fixed_failure_with_a_bounded_protocol(self):
        from types import SimpleNamespace
        from lib.maintenance_agent_config import DEFAULTS
        with patch.object(maintenance_agent, "run_probe", return_value=ProbeResult(issue="probe_output_limit")) as probe:
            result = maintenance_agent._review_as_user(SimpleNamespace(pw_name="fixture", pw_dir="/fixture"),
                                                       DEFAULTS, {"units": []}, [], {"model": "fixture", "effort": "low"})
        self.assertEqual(result["status"], "failed")
        self.assertEqual(probe.call_args.kwargs["max_bytes"], 16384)
        self.assertEqual(probe.call_args.kwargs["timeout"], DEFAULTS["timeout"] * 60 + 30)
        probe.assert_called_once()
