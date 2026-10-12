"""Diagnostic captures use shared supervision and never expose raw errors."""

from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from lib import agent_probe
from lib.streamed_process import CommandOutputLimitError


class ProbeTests(unittest.TestCase):
    def test_normal_output_and_nonzero_exit(self):
        for code, output, issue in ((0, "ok", None), (1, "raw private error", "probe_failed")):
            with self.subTest(code=code), patch.object(agent_probe, "run_captured", return_value=
                    subprocess.CompletedProcess(["probe"], code, output, "")) as run:
                result = agent_probe.run_probe(["probe"])
            self.assertEqual(result.output, output if code == 0 else "")
            self.assertEqual(result.issue, issue)
            self.assertEqual(run.call_args.kwargs["cwd"], "/")
            self.assertFalse(run.call_args.kwargs["capture_stderr"])

    def test_fixed_failure_categories_drop_output_and_exception_text(self):
        for error, issue in ((CommandOutputLimitError("private"), "probe_output_limit"),
                             (subprocess.TimeoutExpired("private", 10), "probe_timeout"),
                             (FileNotFoundError("private path"), "probe_unavailable")):
            with self.subTest(issue=issue), patch.object(agent_probe, "run_captured", side_effect=error):
                result = agent_probe.run_probe(["probe"])
            self.assertEqual(result.issue, issue)
            self.assertEqual(result.output, "")

    def test_bounded_input_and_cwd_are_forwarded_without_a_shell(self):
        with patch.object(agent_probe, "run_captured", return_value=subprocess.CompletedProcess([], 0, "ok", "")) as run:
            agent_probe.run_probe(["worker", "--review"], cwd="/fixture", input_data=b"{}", max_bytes=16384, timeout=30)
        self.assertEqual(run.call_args.args[0], ["worker", "--review"])
        self.assertEqual(run.call_args.kwargs["input_data"], b"{}")
        self.assertEqual(run.call_args.kwargs["cwd"], "/fixture")
        self.assertEqual(run.call_args.kwargs["max_bytes"], 16384)
        self.assertNotIn("shell", run.call_args.kwargs)
