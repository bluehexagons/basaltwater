"""Session-scoped task discovery and evidence collection in private fixtures."""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from desktop import development, evidence
from lib.atomic_io import write_json_atomic
from lib import desktop_cli


class DevelopmentInventoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.project = self.home / "project"
        self.project.mkdir()
        self.root = self.home / "tasks"
        self.root.mkdir(mode=0o700)
        self.enterContext(patch.object(development, "_guard", return_value=os.getuid()))
        self.enterContext(patch.object(development, "_root", return_value=self.root))
        self.enterContext(patch.object(Path, "home", return_value=self.home))

    def task(self, number: int, created: str, *, project: Path | None = None) -> str:
        task = f"{number:032x}"
        directory = self.root / task
        directory.mkdir(mode=0o700)
        write_json_atomic(str(directory / "task.json"), {
            "schema_version": 1, "task": task, "unit": f"{development.UNIT_PREFIX}{task}.service",
            "kind": "exec", "owner_uid": os.getuid(), "created_at": created,
            "project": str(project or self.project), "argv": ["/usr/bin/true"]}, mode=0o600)
        return task

    def test_session_filters_precede_limit_and_include_bounds(self) -> None:
        for number in range(60):
            self.task(number, "2026-01-14T10:00:00Z")
        expected = [self.task(61, "2026-01-15T10:00:00Z"), self.task(62, "2026-01-15T11:00:00Z")]
        self.task(63, "2026-01-15T12:00:00Z")
        self.task(64, "2026-01-15T10:30:00Z", project=self.home)
        result = development.list_tasks(str(self.project), since="2026-01-15T10:00:00Z", until="2026-01-15T11:00:00Z")
        self.assertEqual([row["task"] for row in result["tasks"]], list(reversed(expected)))
        self.assertFalse(result["truncated"])
        self.assertFalse(result["scan_truncated"])
        self.assertFalse(result["matches_truncated"])
        self.assertTrue(development.list_tasks(str(self.project))["matches_truncated"])

    def test_timezone_offsets_are_compared_and_sorted_as_instants(self) -> None:
        early = self.task(1, "2026-01-15T01:00:00+02:00")
        late = self.task(2, "2026-01-15T00:00:00Z")
        result = development.list_tasks(since="2026-01-14T18:00:00-05:00", until="2026-01-15T00:00:00+00:00")
        self.assertEqual([row["task"] for row in result["tasks"]], [late, early])
        result = development.list_tasks(since="2026-01-14T23:30:00Z")
        self.assertEqual([row["task"] for row in result["tasks"]], [late])

    def test_invalid_intervals_fail_before_reading_storage(self) -> None:
        with patch.object(development, "_root") as root:
            for bounds in ({"since": ""}, {"since": True}, {"since": "2026-01-15"},
                           {"until": "2026-01-15T10:00:00"}, {"until": "bad"},
                           {"since": "2026-01-16T00:00:00Z", "until": "2026-01-15T00:00:00Z"}):
                with self.subTest(bounds=bounds), self.assertRaises(ValueError):
                    development.list_tasks(**bounds)
        root.assert_not_called()

    def test_scan_bound_remains_visible_even_when_no_session_matches(self) -> None:
        root = Mock()
        root.iterdir.return_value = [Path(f"/{number:032x}") for number in range(501)]
        with patch.object(development, "_root", return_value=root), \
                patch.object(development, "load_task", return_value={"created_at": "2026-01-14T10:00:00Z"}) as load:
            result = development.list_tasks(since="2026-01-15T00:00:00Z")
        self.assertEqual(load.call_count, 500)
        self.assertEqual(result["tasks"], [])
        self.assertTrue(result["scan_truncated"])
        self.assertTrue(result["truncated"])
        self.assertFalse(result["matches_truncated"])

    def test_empty_storage_retains_validated_filter_metadata(self) -> None:
        with patch.object(development, "_root", side_effect=FileNotFoundError):
            result = development.list_tasks(since="2026-01-15T12:00:00+02:00")
        self.assertEqual(result["tasks"], [])
        self.assertEqual(result["filters"]["since"], "2026-01-15T10:00:00+00:00")
        self.assertFalse(result["truncated"])

    def test_evidence_attaches_session_tasks_with_old_history_and_explicit_earlier_task(self) -> None:
        for number in range(60):
            self.task(number, "2026-01-14T10:00:00Z")
        task = self.task(61, "2026-01-15T05:30:00-05:00")
        earlier = self.task(62, "2026-01-15T09:00:00Z")
        with patch.object(evidence, "revision", return_value={"commit": None, "dirty": None}), \
                patch.object(evidence, "_now", return_value="2026-01-15T10:00:00Z"):
            record = evidence.Recorder(str(self.project), "g")
        with patch.object(evidence, "_now", return_value="2026-01-15T11:00:00Z"):
            record.finish({})
        with patch.object(evidence, "revision", return_value={"commit": None, "dirty": None}), \
                patch.object(development, "task_status", side_effect=lambda value:
                    {"task": value, "project": str(self.project), "state": "completed", "returncode": 0}):
            result = evidence.finalize(str(record.path), tasks=[earlier], renderer=None)
            self.assertEqual([row["task"] for row in result["tasks"]], [earlier, task])
            self.assertFalse(result["task_inventory_truncated"])
            # A refresh preserves explicit attachments outside the recording interval.
            result = evidence.finalize(str(record.path), tasks=[], renderer=None)
            self.assertEqual([row["task"] for row in result["tasks"]], [earlier, task])
        self.assertFalse(json.loads(record.path.read_text())["task_inventory_scan_truncated"])

    def test_cli_forwards_time_bounds_without_desktop_access(self) -> None:
        parser = argparse.ArgumentParser()
        desktop_cli.add_desktop_subparser(parser.add_subparsers())
        args = parser.parse_args(["desktop", "--native", "develop", "list", "--project", str(self.project),
                                  "--since", "2026-01-15T10:00:00Z", "--until", "2026-01-15T11:00:00Z", "--json"])
        with patch.object(development, "list_tasks", return_value={"ok": True}) as listing, redirect_stdout(StringIO()):
            self.assertEqual(desktop_cli.run_desktop_command(args), 0)
        listing.assert_called_once_with(str(self.project), since="2026-01-15T10:00:00Z", until="2026-01-15T11:00:00Z")


if __name__ == "__main__":
    unittest.main()
