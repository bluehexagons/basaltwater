from __future__ import annotations

import argparse
from contextlib import closing, redirect_stdout
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from lib import browser_test_instance as instance
from lib.agent_probe import ProbeResult


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

    def test_uncertain_network_creation_removes_only_the_created_network(self) -> None:
        for created in (False, True):
            with self.subTest(created=created), tempfile.TemporaryDirectory(prefix="bw-test-") as directory:
                data = Path(directory)
                failure = RuntimeError("Network creation failed (probe_timeout)")
                with (
                    patch.object(instance, "_podman", side_effect=[failure, data.name if created else "", ""]) as podman,
                    self.assertRaisesRegex(RuntimeError, "probe_timeout"),
                ):
                    instance._run_instance(argparse.Namespace(), ["podman"], data)
                cleanup = [["network", "ls", "--filter", f"name=^{data.name}$", "--format", "{{.Name}}"]]
                if created:
                    cleanup.append(["network", "rm", data.name])
                self.assertEqual([call.args[1] for call in podman.call_args_list[1:]], cleanup)

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

    def test_ready_instance_cleans_up_at_deadline_and_reports_an_early_crash(self) -> None:
        args = argparse.Namespace(port=8080, sqlite_snapshot=None, seconds=5,
                                  image="localhost/fixture:test", argv=[], health_path="/health", json=True)
        for result, expected_error in (
            (ProbeResult(issue="probe_timeout"), None),
            (ProbeResult(output="17\n"), "exited with status 17"),
        ):
            with self.subTest(result=result), tempfile.TemporaryDirectory(prefix="bw-test-") as directory:
                data = Path(directory)
                output = io.StringIO()
                with (
                    patch.object(instance, "_podman", side_effect=["", "id", "127.0.0.1:4173", "", ""]) as podman,
                    patch.object(instance, "_wait_for_http") as ready,
                    patch.object(instance, "run_probe", return_value=result) as wait,
                    patch.object(instance.time, "monotonic", side_effect=[100, 101, 102]),
                    redirect_stdout(output),
                ):
                    if expected_error:
                        with self.assertRaisesRegex(RuntimeError, expected_error):
                            instance._run_instance(args, ["podman"], data)
                    else:
                        self.assertEqual(instance._run_instance(args, ["podman"], data), 0)
                ready.assert_called_once()
                self.assertEqual(json.loads(output.getvalue())["url"], "http://127.0.0.1:4173")
                self.assertEqual(wait.call_args.args[0], ["podman", "wait", data.name])
                self.assertEqual(wait.call_args.kwargs["timeout"], 3)
                start = podman.call_args_list[1].args[1]
                self.assertGreater(int(start[start.index("--timeout") + 1]), args.seconds)
                self.assertEqual(podman.call_args_list[-2].args[1], ["rm", "--force", "--ignore", data.name])
                self.assertEqual(podman.call_args_list[-1].args[1], ["network", "rm", data.name])
