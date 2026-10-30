"""Bounded benchmark evidence and failure propagation without running workloads."""

from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts import benchmark_development as benchmark


class DevelopmentBenchmarkTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.output = Path(temporary.name) / "evidence.json"
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.enterContext(patch.object(benchmark, "_revision", return_value={"commit": "a" * 40, "dirty": False}))

    def test_success_records_private_evidence_and_explicit_metric_scope(self) -> None:
        sample = {"status": "completed", "return_code": 0, "elapsed_seconds": 0.1,
                  "largest_child_peak_rss_bytes": 1024}
        with patch.object(benchmark, "run_captured", return_value=subprocess.CompletedProcess([], 0, json.dumps(sample), "")) as run:
            result = benchmark.main(["--output", str(self.output), "--workload", "version", "--samples", "2"])
        report = json.loads(self.output.read_text())
        self.assertEqual(result, 0)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(report["status"], "completed")
        self.assertIn("not aggregate", report["metric_scope"]["rss"])
        self.assertEqual(report["workloads"]["version"]["summary"]["samples"], 2)
        self.assertIsNone(report["workloads"]["version"]["summary"]["p95_seconds"])
        self.assertEqual(os.stat(self.output).st_mode & 0o777, 0o600)

    def test_workload_failure_is_retained_and_stops_further_work(self) -> None:
        sample = {"status": "completed", "return_code": 1, "elapsed_seconds": 0.1,
                  "output_tail": "expected failure"}
        with patch.object(benchmark, "run_captured", return_value=subprocess.CompletedProcess([], 0, json.dumps(sample), "")) as run:
            result = benchmark.main(["--output", str(self.output), "--workload", "smoke", "--workload", "atomic"])
        report = json.loads(self.output.read_text())
        self.assertEqual(result, 1)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(report["status"], "failed")
        self.assertNotIn("atomic", report["workloads"])
        self.assertIn("expected failure", report["workloads"]["smoke"]["trials"][0]["output_tail"])

    def test_selected_checkout_is_recorded_and_passed_to_the_worker(self) -> None:
        sample = {"status": "completed", "return_code": 0, "elapsed_seconds": 0.1}
        with patch.object(benchmark, "run_captured", return_value=subprocess.CompletedProcess([], 0, json.dumps(sample), "")) as run:
            result = benchmark.main(["--output", str(self.output), "--workload", "version", "--samples", "1",
                                     "--checkout", str(self.output.parent)])
        self.assertEqual(result, 0)
        report = json.loads(self.output.read_text())
        self.assertEqual(report["checkout"], str(self.output.parent))
        command = run.call_args.args[0]
        self.assertEqual(command[command.index("--checkout") + 1], str(self.output.parent))

    def test_budget_exhaustion_does_not_launch_a_trial_or_claim_success(self) -> None:
        with patch.object(benchmark, "run_captured") as run:
            result = benchmark.main(["--output", str(self.output), "--total-seconds", "1"])
        run.assert_not_called()
        self.assertEqual(result, 1)
        self.assertEqual(json.loads(self.output.read_text())["status"], "budget_exhausted")

    def test_existing_evidence_is_never_overwritten(self) -> None:
        self.output.write_text("retained evidence")
        with patch.object(benchmark, "run_captured") as run, self.assertRaises(FileExistsError):
            benchmark.main(["--output", str(self.output)])
        run.assert_not_called()
        self.assertEqual(self.output.read_text(), "retained evidence")

    def test_timeout_records_unavailable_result_and_keeps_observations(self) -> None:
        with (
            patch.object(benchmark, "inspect_workload_limits", return_value={"scope": "current-process"}),
            patch.object(benchmark.os, "getloadavg", return_value=(0, 0, 0)),
            patch.object(benchmark, "run_captured", side_effect=subprocess.TimeoutExpired([], 1)),
            patch("resource.getrusage", return_value=SimpleNamespace(ru_utime=0.1, ru_stime=0.02, ru_maxrss=123)),
        ):
            result = benchmark._measure("smoke", None, 1)
        self.assertEqual(result["status"], "timeout")
        self.assertIsNone(result["return_code"])
        self.assertEqual(result["observations_before"]["scope"], "current-process")


if __name__ == "__main__":
    unittest.main()
