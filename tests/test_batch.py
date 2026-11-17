"""Behavioral coverage for persistent command batches."""

from __future__ import annotations

from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

import basaltwater
from lib import batch


class BatchTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {"BASALTWATER_WORKSPACE": str(self.root / "workspace"),
                                "BASALTWATER_BATCH": "test"}).start()
        patch("lib.concurrency._lock_root", return_value=str(self.root)).start()
        self.output = StringIO()
        self.enterContext(redirect_stdout(self.output))
        self.errors = StringIO()
        self.enterContext(redirect_stderr(self.errors))

    def cli(self, *argv: str) -> int:
        with patch.object(basaltwater.sys, "argv", ["basaltw", *argv]):
            return basaltwater.main()

    def state(self) -> dict:
        return json.loads(batch._path().read_text())

    def test_commands_across_invocations_summary_history_and_privacy(self) -> None:
        self.assertEqual(self.cli("batch", "start", "Systems"), 0)
        with patch.object(basaltwater, "run_setup_command", return_value=0):
            self.assertEqual(self.cli("setup", "server_web", "web.example.com",
                                      "--password", "private-value", "--dry-run"), 0)
        with patch.object(basaltwater, "run_patch_command", return_value=7):
            self.assertEqual(self.cli("patch", "worker.example.com"), 7)
        self.assertEqual(self.cli("batch", "note", "Check DNS"), 0)
        self.assertEqual(self.cli("batch", "end"), 1)
        self.assertIn("1 succeeded, 1 failed, 0 unfinished", self.output.getvalue())
        self.assertIn("web.example.com [dry run]", self.output.getvalue())
        self.assertIn("Check DNS", self.output.getvalue())
        self.assertNotIn("private-value", batch._path().read_text())
        self.assertEqual(batch._path().stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.cli("batch", "status"), 1)
        self.assertEqual(self.cli("batch", "start"), 0)
        self.assertEqual(len(list((batch._path().parent / "history").glob("*.json"))), 1)
        self.assertEqual(self.cli("batch", "end"), 0)

    def test_external_argv_and_failure_are_preserved_without_capturing_output(self) -> None:
        self.cli("batch", "start")
        with patch("lib.remote_utils.run", return_value=Mock(returncode=23)) as run:
            self.assertEqual(self.cli("batch", "run", "--label", "External check",
                                      "--", "tool", "a b", "--token=secret"), 23)
        run.assert_called_once_with(["tool", "a b", "--token=secret"],
                                    interactive=True, check=False, timeout=None)
        self.assertEqual(self.state()["entries"][0]["code"], 23)
        self.assertNotIn("secret", batch._path().read_text())

    def test_refusals_and_closed_batch_do_not_execute_external_commands(self) -> None:
        with patch("lib.remote_utils.run") as run:
            self.assertEqual(self.cli("batch", "run", "--", "tool"), 1)
            run.assert_not_called()
        self.assertEqual(self.cli("batch", "start"), 0)
        self.assertEqual(self.cli("batch", "start"), 1)
        self.assertEqual(self.cli("batch", "run"), 1)
        self.assertEqual(self.cli("batch", "end"), 0)
        self.assertEqual(self.cli("batch", "note", "late note"), 1)

    def test_interruption_and_pending_recovery(self) -> None:
        self.cli("batch", "start")
        with patch.object(basaltwater, "run_patch_command", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.cli("patch", "web.example.com")
        self.assertEqual(self.state()["entries"][0]["code"], 130)
        data = self.state()
        batch._append(data, "Killed command", "command")
        batch.write_json_atomic(str(batch._path()), data)
        self.assertEqual(self.cli("batch", "end"), 1)
        self.assertIsNone(self.state()["ended"])
        self.assertEqual(self.cli("batch", "end", "--force"), 1)
        self.assertIsNotNone(self.state()["ended"])

    def test_isolation_and_global_workspace(self) -> None:
        self.cli("batch", "start", "First")
        with patch.dict(os.environ, {"BASALTWATER_BATCH": "second"}):
            self.assertEqual(self.cli("batch", "start", "Second"), 0)
            self.assertEqual(self.state()["title"], "Second")
        self.assertEqual(self.state()["title"], "First")
        other = str(self.root / "other")
        self.assertEqual(self.cli("--workspace", other, "batch", "start", "Other"), 0)
        self.assertEqual(self.state()["title"], "Other")

    def test_corrupt_state_is_preserved_and_prevents_mutation(self) -> None:
        self.cli("batch", "start")
        batch._path().write_text('{"schema_version": 99}')
        with patch.object(basaltwater, "run_patch_command") as action:
            self.assertEqual(self.cli("patch", "web.example.com"), 1)
            action.assert_not_called()
        self.assertEqual(batch._path().read_text(), '{"schema_version": 99}')

    def test_unbatched_json_output_is_unchanged_and_creates_no_state(self) -> None:
        self.assertEqual(self.cli("list", "--json"), 0)
        self.assertEqual(self.output.getvalue(), "[]\n")
        self.assertFalse(batch._path().exists())

    def test_overlapping_commands_keep_both_records_and_block_early_end(self) -> None:
        self.cli("batch", "start")

        def outer() -> int:
            self.assertEqual(batch._tracked(batch._path(), "Inner", lambda: 9, required=True), 9)
            self.assertEqual(self.cli("batch", "end"), 1)
            self.assertIsNone(self.state()["ended"])
            return 0

        self.assertEqual(batch._tracked(batch._path(), "Outer", outer, required=True), 0)
        self.assertEqual([e["code"] for e in self.state()["entries"]], [0, 9])
        self.assertEqual(self.cli("batch", "end"), 1)

    def test_tracking_write_failure_prevents_action_and_completion_failure_keeps_status(self) -> None:
        self.cli("batch", "start")
        action = Mock(return_value=0)
        with patch.object(batch, "write_json_atomic", side_effect=OSError("disk full")):
            self.assertEqual(batch._tracked(batch._path(), "Check", action, required=True), 1)
        action.assert_not_called()

        original_write = batch.write_json_atomic

        def fail_completion(path: str, data: dict) -> None:
            if data["entries"][-1]["code"] is not None:
                raise OSError("disk full")
            original_write(path, data)

        with patch.object(batch, "write_json_atomic", side_effect=fail_completion):
            self.assertEqual(batch._tracked(batch._path(), "Check", lambda: 4, required=True), 4)
        self.assertIsNone(self.state()["entries"][-1]["code"])
        self.assertIn("unable to finish batch record", self.errors.getvalue())


if __name__ == "__main__":
    unittest.main()
