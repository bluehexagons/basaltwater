"""Diagnostic subprocesses stop on deadlines and output limits without raw errors."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from lib import agent_probe


class ProbeTests(unittest.TestCase):
    def run_case(self, chunks, *, max_bytes=4, timeout=False, exit_code=0):
        process = MagicMock(pid=123)
        process.wait.return_value = exit_code
        process.poll.return_value = exit_code
        selector = MagicMock()
        active = {"pipe": True}
        selector.__enter__.return_value = selector
        selector.get_map.side_effect = lambda: active
        selector.unregister.side_effect = lambda _: active.clear()
        selector.select.return_value = [(SimpleNamespace(fd=7, fileobj=process.stdout), 1)]
        with (
            patch.object(agent_probe.subprocess, "Popen", return_value=process) as start,
            patch.object(agent_probe.selectors, "DefaultSelector", return_value=selector),
            patch.object(agent_probe.os, "read", side_effect=chunks),
            patch.object(agent_probe.os, "killpg") as kill,
            patch.object(agent_probe.time, "monotonic", side_effect=[0, 11] if timeout else lambda: 0),
        ):
            result = agent_probe.run_probe(["probe"], max_bytes=max_bytes)
        self.assertEqual(start.call_args.kwargs["cwd"], "/")
        self.assertTrue(start.call_args.kwargs["start_new_session"])
        process.stdout.close.assert_called_once()
        return result, kill

    def test_normal_output_and_nonzero_exit(self):
        result, kill = self.run_case([b"ok", b""])
        self.assertEqual(result.output, "ok")
        self.assertIsNone(result.issue)
        kill.assert_not_called()
        result, kill = self.run_case([b"raw", b""], exit_code=1)
        self.assertEqual(result.issue, "probe_failed")
        self.assertEqual(result.output, "")
        kill.assert_called_once()

    def test_timeout_and_excess_output_terminate_group(self):
        result, kill = self.run_case([], timeout=True)
        self.assertEqual(result.issue, "probe_timeout")
        kill.assert_called_once()
        result, kill = self.run_case([b"12345"])
        self.assertEqual(result.issue, "probe_output_limit")
        self.assertEqual(result.output, "")
        kill.assert_called_once()

    def test_absent_executable_and_wait_deadline(self):
        with patch.object(agent_probe.subprocess, "Popen", side_effect=FileNotFoundError("private path")):
            self.assertEqual(agent_probe.run_probe(["missing"]).issue, "probe_unavailable")
        process = MagicMock(pid=123)
        process.wait.side_effect = subprocess.TimeoutExpired("probe", 1)
        process.poll.return_value = None
        selector = MagicMock()
        selector.__enter__.return_value = selector
        selector.get_map.return_value = {}
        with patch.object(agent_probe.subprocess, "Popen", return_value=process), \
                patch.object(agent_probe.selectors, "DefaultSelector", return_value=selector), \
                patch.object(agent_probe.os, "killpg", side_effect=ProcessLookupError):
            self.assertEqual(agent_probe.run_probe(["probe"]).issue, "probe_timeout")
