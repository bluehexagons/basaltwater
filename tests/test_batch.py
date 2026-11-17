"""Behavioral coverage for persistent command batches."""

from __future__ import annotations

from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
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
                                "BASALTWATER_BATCH": "test",
                                batch._WRAPPED_ENV: "0"}).start()
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
        self.assertEqual(batch._path().parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.cli("batch", "status"), 1)
        self.assertEqual(self.cli("batch", "start"), 0)
        self.assertEqual(len(list((batch._path().parent / "history").glob("*.json"))), 1)
        self.assertEqual((batch._path().parent / "history").stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.cli("batch", "end"), 0)

    def test_external_argv_and_failure_are_preserved_without_capturing_output(self) -> None:
        self.cli("batch", "start")
        with patch("lib.remote_utils.run", return_value=Mock(returncode=23)) as run:
            self.assertEqual(self.cli("batch", "run", "--label", "External check",
                                      "--", "tool", "a b", "--token=secret"), 23)
        run.assert_called_once_with(["tool", "a b", "--token=secret"],
                                    interactive=True, check=False, timeout=None,
                                    env={**os.environ, batch._WRAPPED_ENV: "1"},
                                    display_cmd="batch command")
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

    def test_malformed_states_refuse_status_start_and_command_without_rewriting(self) -> None:
        self.cli("batch", "start")
        data = self.state()
        batch._append(data, "Check", "command")
        cases = []
        for key, value in (("schema_version", True), ("id", "../escape"),
                           ("started", 10**400), ("ended", -1)):
            changed = deepcopy(data)
            changed[key] = value
            cases.append(changed)
        for key, value in (("id", "bad"), ("seconds", -1), ("started", True), ("kind", {})):
            changed = deepcopy(data)
            changed["entries"][0][key] = value
            cases.append(changed)
        duplicate = deepcopy(data)
        duplicate["entries"].append(deepcopy(duplicate["entries"][0]))
        cases.append(duplicate)
        for changed in cases:
            with self.subTest(state=changed):
                batch.write_json_atomic(str(batch._path()), changed)
                before = batch._path().read_bytes()
                self.assertEqual(self.cli("batch", "status"), 1)
                self.assertEqual(self.cli("batch", "start"), 1)
                with patch.object(basaltwater, "run_patch_command") as action:
                    self.assertEqual(self.cli("patch", "web.example.com"), 1)
                    action.assert_not_called()
                self.assertEqual(batch._path().read_bytes(), before)

    def test_workspace_symlink_uses_same_state_and_lock_identity(self) -> None:
        self.cli("batch", "start")
        canonical = batch._path()
        alias = self.root / "alias"
        alias.symlink_to(canonical.parent.parent, target_is_directory=True)
        with patch.dict(os.environ, {"BASALTWATER_WORKSPACE": str(alias)}):
            self.assertEqual(batch._path(), canonical)
            self.assertEqual(self.cli("batch", "start"), 1)

    def test_wrapped_cli_commands_have_one_record_and_keep_failure_status(self) -> None:
        self.cli("batch", "start")

        def child(argv, **kwargs):
            with patch.dict(os.environ, kwargs["env"]):
                self.assertEqual(self.cli("patch", "web.example.com"), 19)
            return Mock(returncode=19)

        with patch("lib.remote_utils.run", side_effect=child), patch.object(
            basaltwater, "run_patch_command", return_value=19,
        ):
            self.assertEqual(self.cli("batch", "run", "--", "basaltw", "patch", "web.example.com"), 19)
        self.assertEqual(len(self.state()["entries"]), 1)
        self.assertEqual(self.state()["entries"][0]["code"], 19)

    def test_refresh_is_supervised_only_when_batched(self) -> None:
        with patch("lib.refresh.run_refresh_command", return_value=0) as refresh, patch(
            "lib.remote_utils.run", return_value=Mock(returncode=17),
        ) as run:
            self.assertEqual(self.cli("refresh", "--dry-run"), 0)
            run.assert_not_called()
            self.cli("batch", "start")
            self.assertEqual(self.cli("refresh", "--dry-run"), 17)
            self.assertEqual(self.state()["entries"][0]["code"], 17)
            self.assertEqual(run.call_args.args[0][-2:], ["refresh", "--dry-run"])
            self.assertEqual(run.call_args.kwargs["env"][batch._WRAPPED_ENV], "1")
            self.assertEqual(self.cli("batch", "end"), 1)
            self.assertEqual(self.cli("refresh", "--dry-run"), 0)
            self.assertEqual(refresh.call_count, 2)
            self.assertEqual(run.call_count, 1)

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
