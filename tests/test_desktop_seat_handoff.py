"""Local-player transfer and interrupted-run recovery."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from desktop import run_checkpoint


class SeatHandoffTest(unittest.TestCase):
    def test_recovery_keeps_original_clocks_and_durable_result(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for delivery in ("acknowledged", "unknown"):
                started = run_checkpoint.start("run", "match", "play", output=str(root / delivery))
                ledger = started["artifact"]
                receipt = Path(ledger).with_name("action.json")
                receipt.write_text('{"delivery":"' + delivery + '"}')
                run_checkpoint.update(ledger, "context", context="seat-one", expect_events=1)
                with self.assertRaisesRegex(ValueError, "changed before handoff"):
                    run_checkpoint.update(ledger, "context", context="seat-two", expect_events=1)
                recovery = run_checkpoint.update(ledger, "context", context="recovery", expect_events=2,
                    note=f"worker stopped; opaque failure; delivery {delivery}", artifacts=[str(receipt)])
                run_checkpoint.update(ledger, "checkpoint", note="Coordinator inspected evidence")
                shown = run_checkpoint.update(ledger)
                self.assertEqual(shown["started"], started["started"])
                self.assertEqual((shown["run_id"], shown["match_id"]), ("run", "match"))
                self.assertEqual(shown["last_context"], recovery["latest"])
                self.assertEqual(shown["last_context"]["artifacts"], [str(receipt)])
                self.assertIn(delivery, receipt.read_text())
