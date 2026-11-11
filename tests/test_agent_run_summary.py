"""Offline phase attribution and compact gap reporting for agent-owned run clocks."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from desktop import run_checkpoint


class RunSummaryTests(unittest.TestCase):
    def test_phase_intervals_waits_and_finished_reads_preserve_history(self):
        domain = {"boot_id": "test-boot", "monotonic_scope": "linux-boot"}
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(run_checkpoint, "clock", return_value=domain), \
                patch.object(run_checkpoint, "stamp") as stamp:
            def at(seconds):
                stamp.return_value = {"at": "2026-01-01T00:00:00+00:00", "monotonic_ns": seconds * 10**9}

            at(1)
            path = run_checkpoint.start("run-a", "match-a", "setup", output=str(Path(temporary) / "run"))["artifact"]
            at(11)
            run_checkpoint.update(path, "checkpoint", phase="play")
            at(31)
            run_checkpoint.update(path, "suspend", note="Owner wait")
            at(71)
            run_checkpoint.update(path, "context", context="continuation-a")
            at(91)
            run_checkpoint.update(path, "resume", phase="cleanup")
            at(101)
            finished = run_checkpoint.update(path, "finish")
            self.assertEqual(finished["wall_seconds"], 100)
            self.assertEqual(finished["phase_times"], {
                "setup": {"wall_seconds": 10, "owner_wait_seconds": 0, "declared_active_wall_seconds": 10},
                "play": {"wall_seconds": 80, "owner_wait_seconds": 60, "declared_active_wall_seconds": 20},
                "cleanup": {"wall_seconds": 10, "owner_wait_seconds": 0, "declared_active_wall_seconds": 10},
            })
            self.assertEqual(sum(row["wall_seconds"] for row in finished["phase_times"].values()), 100)
            self.assertEqual(finished["event_gaps"]["count"], 5)
            self.assertEqual(finished["event_gaps"]["median_seconds"], 20)
            self.assertEqual(finished["event_gaps"]["max_seconds"], 40)
            self.assertEqual(finished["event_gaps"]["longest"][0], {
                "from_index": 2, "to_index": 3, "phase": "play", "seconds": 40, "owner_wait": True})
            self.assertEqual(finished["context_changes"], 1)
            self.assertIsNone(finished["model_reasoning_seconds"])
            before = Path(path).read_bytes()
            at(1001)
            shown = run_checkpoint.update(path)
            self.assertEqual(shown["phase_times"], finished["phase_times"])
            self.assertEqual(shown["event_gaps"], finished["event_gaps"])
            self.assertEqual(Path(path).read_bytes(), before)
            with patch.object(run_checkpoint, "clock", return_value={**domain, "boot_id": "other-boot"}):
                unsupported = run_checkpoint.update(path)
                self.assertEqual(unsupported["timing"], "unsupported-clock")
                self.assertNotIn("phase_times", unsupported)

    def test_open_interval_is_attributed_but_not_a_completed_gap(self):
        rows = [{"event": "start", "phase": "play", "monotonic_ns": 10**9}]
        report = run_checkpoint._interval_summary(rows, 11 * 10**9)
        self.assertEqual(report["phase_times"]["play"]["wall_seconds"], 10)
        self.assertEqual(report["event_gaps"], {
            "count": 0, "median_seconds": None, "max_seconds": None, "longest": [],
            "scope": "Completed ledger-event intervals, including recorded owner waits; not action latency or model time."})

    def test_summary_is_bounded_and_includes_zero_length_gaps(self):
        rows = [{"event": "start" if index == 0 else "checkpoint", "phase": "play",
                 "monotonic_ns": (index // 2) * 10**9} for index in range(1000)]
        report = run_checkpoint._interval_summary(rows, rows[-1]["monotonic_ns"])
        self.assertEqual(report["event_gaps"]["count"], 999)
        self.assertEqual(report["event_gaps"]["median_seconds"], 0)
        self.assertEqual(len(report["event_gaps"]["longest"]), 3)


if __name__ == "__main__":
    unittest.main()
