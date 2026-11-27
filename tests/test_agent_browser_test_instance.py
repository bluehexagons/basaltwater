from __future__ import annotations

import argparse
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from lib import browser_test_instance as instance


class BrowserTestInstanceTest(unittest.TestCase):
    def test_private_sqlite_copy_can_be_modified_independently(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory, "offline source.sqlite3")
            target = Path(directory, "fixture.sqlite3")
            with closing(sqlite3.connect(source)) as db, db:
                db.execute("create table fixture (value text)")
                db.execute("insert into fixture values ('original')")
            instance._copy_snapshot(source, target)
            with closing(sqlite3.connect(target)) as db, db:
                db.execute("update fixture set value = 'changed'")
            with closing(sqlite3.connect(source)) as db:
                self.assertEqual(db.execute("select value from fixture").fetchone(), ("original",))
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            Path(str(source) + "-wal").touch()
            with self.assertRaisesRegex(ValueError, "checkpointed"):
                instance._copy_snapshot(source, Path(directory, "second.sqlite3"))

    def test_uncertain_container_start_cleans_up_before_removing_network(self) -> None:
        args = argparse.Namespace(port=8080, sqlite_snapshot="fixture.sqlite3", database_env="APP_DB",
                                  seconds=15, image="localhost/fixture:test", argv=[])
        with tempfile.TemporaryDirectory(prefix="bw-test-") as directory:
            data = Path(directory)
            with patch.object(instance, "_podman", side_effect=[
                "", RuntimeError("Container startup failed (probe_timeout)"), "", "",
            ]) as podman, self.assertRaisesRegex(RuntimeError, "probe_timeout"):
                instance._run_instance(args, ["podman", "--remote=false"], data)
            commands = [call.args[1] for call in podman.call_args_list]
        self.assertEqual(commands[0], ["network", "create", "--driver", "bridge", "--internal", data.name])
        self.assertIn("127.0.0.1::8080/tcp", commands[1])
        self.assertIn(f"{directory}:/basaltwater-test:rw,Z", commands[1])
        self.assertIn("APP_DB=/basaltwater-test/fixture.sqlite3", commands[1])
        self.assertEqual(commands[2], ["rm", "--force", "--ignore", data.name])
        self.assertEqual(commands[3], ["network", "rm", data.name])
