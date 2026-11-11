"""Reservations coordinate actual temporary locks while commands stay mocked."""

from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import os
from pathlib import Path
import signal
import tempfile
import unittest
from unittest.mock import patch

from lib import agent_reservations as reservations
from lib.agent_cli import add_agent_subparser, run_agent_command


class ReservationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(reservations, "_root", return_value=self.root))
        self.enterContext(patch.object(reservations, "is_dry_run", return_value=False))

    def test_busy_owner_all_or_none_and_reuse_preserve_lock_inode(self):
        with reservations.reserve(["gpu:all"], owner="worker-a", run_id="trial-1"):
            record = reservations.reservation_status(["gpu:all"])[0]
            self.assertTrue(record["active"])
            self.assertEqual(record["last_reservation"]["run_id"], "trial-1")
            with self.assertRaisesRegex(RuntimeError, "worker-a.*trial-1"):
                with reservations.reserve(["build:temporary", "gpu:all"], owner="worker-b", run_id="trial-2"):
                    self.fail("Conflicting command must not run")
            self.assertFalse(reservations.reservation_status(["build:temporary"])[0]["active"])
        lock, _ = reservations._paths(self.root, "gpu:all")
        inode = lock.stat().st_ino
        with reservations.reserve(["gpu:all"], owner="worker-b", run_id="trial-2"):
            self.assertEqual(lock.stat().st_ino, inode)
        self.assertFalse(reservations.reservation_status(["gpu:all"])[0]["active"])

    def test_inherited_copy_keeps_reservation_after_supervisor_closes(self):
        with reservations.reserve(["gpu:all"], owner="worker", run_id="trial") as (_, descriptors):
            child_copy = os.dup(descriptors[0])
        try:
            self.assertTrue(reservations.reservation_status(["gpu:all"])[0]["active"])
        finally:
            os.close(child_copy)
        self.assertFalse(reservations.reservation_status(["gpu:all"])[0]["active"])

    def test_release_on_exception_and_failed_metadata_write(self):
        for error in (RuntimeError("child failure"), KeyboardInterrupt(), TimeoutError()):
            with self.subTest(error=type(error)), self.assertRaises(type(error)):
                with reservations.reserve(["gpu:all"], owner="worker", run_id="trial"):
                    raise error
            self.assertFalse(reservations.reservation_status(["gpu:all"])[0]["active"])
        with patch.object(reservations, "write_json_atomic", side_effect=OSError("full")), self.assertRaises(OSError):
            with reservations.reserve(["gpu:all"], owner="worker", run_id="trial"):
                self.fail()
        self.assertFalse(reservations.reservation_status(["gpu:all"])[0]["active"])

    def test_canonical_output_alias_and_invalid_selectors(self):
        (self.root / "actual").mkdir()
        (self.root / "alias").symlink_to(self.root / "actual", target_is_directory=True)
        keys = reservations.resource_keys(["gpu:all", "gpu:all"], [str(self.root / "actual"), str(self.root / "alias")])
        self.assertEqual(len(keys), 2)
        for resources, outputs in (([], []), (["bad\nname"], []), (["build:fake"], []), (["x"] * 33, []), ([], ["bad\npath"])):
            with self.subTest(resources=resources), self.assertRaises(ValueError):
                reservations.resource_keys(resources, outputs)

    def test_unsafe_lock_and_corrupt_record_are_preserved(self):
        lock, metadata = reservations._paths(self.root, "gpu:all")
        lock.symlink_to(self.root / "other")
        with self.assertRaises(OSError):
            reservations.reservation_status(["gpu:all"])
        lock.unlink()
        for payload in ("{invalid", "null", '{"version":1}', '{"version":true,"owner":"a","run_id":"b"}'):
            metadata.write_text(payload)
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                with reservations.reserve(["gpu:all"], owner="worker", run_id="trial"):
                    self.fail()
            self.assertEqual(metadata.read_text(), payload)

    def test_cli_passes_descriptors_literal_argv_and_exit_status(self):
        parser = argparse.ArgumentParser()
        add_agent_subparser(parser.add_subparsers(dest="command"))
        args = parser.parse_args(["agent", "reservations", "run", "--resource", "gpu:all", "--owner", "worker",
                                  "--run-id", "trial", "--timeout", "30", "--", "project-bench", "$(literal)"])
        def execute(argv, **kwargs):
            self.assertEqual(argv, ["project-bench", "$(literal)"])
            self.assertTrue(kwargs["pass_fds"])
            self.assertEqual(kwargs["timeout"], 30)
            self.assertTrue(reservations.reservation_status(["gpu:all"])[0]["active"])
            return 17
        with patch.object(reservations, "run_streamed", side_effect=execute), redirect_stderr(StringIO()):
            self.assertEqual(run_agent_command(args), 17)
        self.assertFalse(reservations.reservation_status(["gpu:all"])[0]["active"])
        args = parser.parse_args(["agent", "reservations", "status", "--resource", "gpu:all", "--json"])
        with redirect_stdout(output := StringIO()):
            self.assertEqual(run_agent_command(args), 0)
        self.assertIn('"active": false', output.getvalue())

    def test_sigterm_uses_cancellation_and_restores_previous_handler(self):
        previous = signal.getsignal(signal.SIGTERM)
        with self.assertRaises(KeyboardInterrupt):
            with reservations.termination_cleanup():
                signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        self.assertIs(signal.getsignal(signal.SIGTERM), previous)
