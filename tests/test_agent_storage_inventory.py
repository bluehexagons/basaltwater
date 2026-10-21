"""Storage review evidence stays bounded, metadata-only and explicitly incomplete."""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from lib import agent_storage_inventory as inventory
from lib.agent_cli import add_agent_subparser, run_agent_command


class StorageInventoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.mounts = self.enterContext(patch.object(inventory, "_mounts", return_value={"/": "ext4"}))
        self.enterContext(patch.object(inventory, "_ROOTS", tuple(row for row in inventory._ROOTS if not row[0].startswith("/"))))
        self.enterContext(patch.object(inventory.shutil, "disk_usage", return_value=SimpleNamespace(total=1000000, used=900000, free=100000)))
        self.enterContext(patch.object(inventory.os, "statvfs", return_value=SimpleNamespace(f_favail=100, f_files=1000)))

    def inspect(self, *paths: Path, **options: int) -> dict:
        return inventory.inspect_storage(home=str(self.home), paths=list(map(str, paths)) if paths else None, **options)

    def test_metadata_measures_allocated_age_and_hardlinks_without_reading_contents(self) -> None:
        cache = self.home / ".cache" / "test-tool"
        cache.mkdir(parents=True)
        old = cache / "old-download"
        old.write_bytes(b"x" * 65536)
        timestamp = time.time() - 45 * 86400
        os.utime(old, (timestamp, timestamp))
        (cache / "same-data").hardlink_to(old)
        (cache / "private-link").symlink_to(self.home / "credentials.json")
        (self.home / "credentials.json").write_text('secret content')
        os.mkfifo(cache / "live-pipe")
        # Only metadata syscalls should run; Python file reads would fail here.
        with patch("builtins.open", side_effect=AssertionError("Must not read file contents")):
            row = self.inspect(cache)["candidates"][0]
        allocated = old.stat().st_blocks * 512
        self.assertEqual((row["files"], row["hardlinked_files"]), (1, 1))
        self.assertEqual(row["allocated_bytes"], allocated)
        self.assertEqual(row["older_than_30_days_bytes"], allocated)
        self.assertEqual(row["newest_file_mtime"], timestamp)
        self.assertEqual(set(row["issues"]), {"symlinks_skipped", "special_files_skipped"})
        self.assertNotIn("secret content", json.dumps(row))
        self.assertTrue((cache / "same-data").exists())

    def test_default_inventory_ranks_cache_children_and_preserves_source_categories(self) -> None:
        for name, count in (("uv", 24), ("pip", 8)):
            path = self.home / ".cache" / name
            path.mkdir(parents=True)
            (path / "download").write_bytes(b"x" * count * 4096)
        report = self.inspect()
        row = next(row for row in report["candidates"] if row["category"] == "tool-caches")
        self.assertEqual(Path(row["largest_children"][0]["path"]).name, "uv")
        self.assertGreater(row["largest_children"][0]["allocated_bytes"], row["largest_children"][1]["allocated_bytes"])
        self.assertEqual(row["status"], "measured")
        self.assertTrue(report["read_only"])
        self.assertTrue(any(row["category"] == "project-checkouts" for row in report["candidates"]))
        self.assertTrue(any("not guaranteed reclaimable" in note for note in report["notes"]))

    def test_session_state_is_metadata_only_and_skips_links_and_pipes(self) -> None:
        for directory in (self.home / ".codex", self.home / ".t3/userdata"):
            directory.mkdir(parents=True)
            (directory / "state.sqlite").write_text("private database payload")
            (directory / "state.sqlite-wal").write_text("private transaction payload")
            (directory / "link.sqlite").symlink_to(directory / "state.sqlite")
            os.mkfifo(directory / "pipe.sqlite")
        with patch("builtins.open", side_effect=AssertionError("Must not read database contents")):
            result = self.inspect()
        files = result["session_state"]["files"]
        self.assertEqual(len(files), 4)
        self.assertEqual(result["session_state"]["issues"], [])
        self.assertTrue(all(row["apparent_bytes"] > 0 and row["owner_uid"] == os.getuid() for row in files))
        self.assertNotIn("private database payload", json.dumps(result))
        self.assertEqual({row["category"] for row in result["candidates"]} &
                         {"codex-sessions", "archived-sessions", "session-attachments"},
                         {"codex-sessions", "archived-sessions", "session-attachments"})

    def test_default_inventory_measures_managed_workspaces_and_shared_tool_data(self) -> None:
        roots = {
            ".local/share/basaltwater/worktrees": "managed-worktrees",
            ".local/state/basaltwater/agent-backups": "agent-backups",
            ".nvm/versions/node": "node-runtimes",
            ".nvm/.cache": "node-downloads",
            "go/pkg/mod": "go-modules",
            ".local/share/containers/storage": "container-storage",
            ".local/share/godot/export_templates": "godot-templates",
            ".local/share/basaltwater/t3code": "t3-launcher",
            ".local/share/basaltwater/evidence": "development-evidence",
            ".local/state/basaltwater/playwright-mcp": "browser-evidence",
        }
        for relative in roots:
            directory = self.home / relative
            directory.mkdir(parents=True)
            (directory / "payload").write_bytes(b"private payload" * 512)
        with patch("builtins.open", side_effect=AssertionError("Metadata only")):
            report = self.inspect(max_entries=64)
        rows = {row["category"]: row for row in report["candidates"]}
        for relative, category in roots.items():
            row = rows[category]
            self.assertEqual(row["path"], str(self.home / relative))
            self.assertEqual((row["status"], row["files"]), ("measured", 1))
            self.assertGreater(row["allocated_bytes"], 0)
            self.assertTrue(row["retention"])
        self.assertLessEqual(report["limits"]["entries_examined"], 64)
        self.assertNotIn("private payload", json.dumps(report))

    def test_summary_keeps_failures_partial_coverage_and_state_without_mutating_inventory(self) -> None:
        cache = self.home / ".cache"
        cache.mkdir()
        for index in range(9):
            child = cache / str(index)
            child.mkdir()
            (child / "payload").write_bytes(b"x" * 4096)
        state = self.home / ".t3/userdata"
        state.mkdir(parents=True)
        (state / "state.sqlite").write_bytes(b"x" * 4096)
        report = self.inspect()
        row = next(row for row in report["candidates"] if row["category"] == "tool-caches")
        row["status"] = "partial"
        row["issues"] = ["scan_limit_reached"]
        unavailable = next(row for row in report["candidates"] if row["category"] == "node-runtimes")
        unavailable.update(status="unavailable", issues=["unreadable_or_symlink_path"])
        before = json.dumps(report)
        summary = inventory._summarize_inventory(report)
        self.assertEqual(json.dumps(report), before)
        self.assertEqual(summary["absent_candidates_omitted"], sum(row["status"] == "absent" for row in report["candidates"]))
        selected = next(row for row in summary["candidates"] if row["category"] == "tool-caches")
        self.assertEqual((selected["status"], selected["issues"]), ("partial", ["scan_limit_reached"]))
        self.assertEqual(selected["retention"], row["retention"])
        self.assertEqual((len(selected["largest_children"]), selected["ranked_children_omitted"]), (3, 5))
        self.assertIn(unavailable["issues"], [row["issues"] for row in summary["candidates"]])
        self.assertEqual(summary["limits"], report["limits"])
        self.assertEqual(summary["session_state"]["issues"], report["session_state"]["issues"])
        self.assertEqual(summary["session_state"]["files"][0]["allocated_bytes"], report["session_state"]["files"][0]["allocated_bytes"])
        self.assertEqual(summary["session_state"]["retention"], [report["session_state"]["files"][0]["retention"]])

    def test_cli_summary_keeps_collection_failure_exit_status(self) -> None:
        parser = argparse.ArgumentParser()
        add_agent_subparser(parser.add_subparsers())
        args = parser.parse_args(["agent", "storage", "inspect", "--summary", "--json"])
        self.mounts.side_effect = PermissionError
        with redirect_stdout(StringIO()) as output:
            self.assertEqual(run_agent_command(args), 1)
        result = json.loads(output.getvalue())
        self.assertEqual(result["view"], "summary")
        self.assertEqual(result["issues"], ["mount_inventory_unavailable"])
        self.assertEqual(result["candidates"], [])

    def test_summary_preserves_explicitly_selected_missing_paths(self) -> None:
        missing = self.home / "missing"
        result = inventory._summarize_inventory(self.inspect(missing))
        self.assertEqual(result["absent_candidates_omitted"], 0)
        self.assertEqual(result["candidates"][0]["path"], str(missing))
        self.assertEqual(result["candidates"][0]["status"], "absent")

    def test_cache_conventions_cover_default_children_and_exact_selected_roots_without_reads(self) -> None:
        expected = {"go-build": "go-build", "ms-playwright": "playwright-browsers",
                    "libretabs-toolchain": "project-toolchain", "other-release-toolchain": "project-toolchain"}
        for name in (*expected, "unrecognized"):
            root = self.home / ".cache" / name
            root.mkdir(parents=True)
            (root / "payload").write_bytes(b"x" * 4096)
        with patch("builtins.open", side_effect=AssertionError("Guidance must not read contents")):
            report = self.inspect()
        cache = next(row for row in report["candidates"] if row["category"] == "tool-caches")
        self.assertEqual({Path(row["path"]).name: row["cleanup_convention"]
                          for row in cache["largest_children"] if "cleanup_convention" in row}, expected)
        self.assertEqual(set(report["cleanup_conventions"]), set(expected.values()))
        for name, convention in expected.items():
            report = self.inspect(self.home / ".cache" / name)
            self.assertEqual(report["candidates"][0]["cleanup_convention"], convention)
            self.assertEqual(set(report["cleanup_conventions"]), {convention})
        self.assertEqual(self.inspect(self.home / ".cache" / "unrecognized")["cleanup_conventions"], {})

    def test_cache_conventions_do_not_label_missing_linked_or_unrelated_paths(self) -> None:
        missing = self.home / ".cache" / "go-build"
        linked = self.home / ".cache" / "ms-playwright"
        unrelated = self.home / "project" / ".cache" / "go-build"
        unrelated.mkdir(parents=True)
        linked.parent.mkdir()
        linked.symlink_to(unrelated, target_is_directory=True)
        for path in (missing, linked, unrelated):
            report = self.inspect(path)
            self.assertNotIn("cleanup_convention", report["candidates"][0])
            self.assertEqual(report["cleanup_conventions"], {})

    def test_cache_convention_keeps_partial_measurement_and_shared_scan_limits(self) -> None:
        cache = self.home / ".cache" / "ms-playwright"
        cache.mkdir(parents=True)
        for name in ("first", "second"):
            (cache / name).write_bytes(b"x" * 4096)
        report = self.inspect(cache, max_entries=1)
        row = report["candidates"][0]
        self.assertEqual(row["status"], "partial")
        self.assertEqual(row["cleanup_convention"], "playwright-browsers")
        self.assertIn("scan_limit_reached", row["issues"])
        self.assertEqual(report["limits"]["entries_examined"], 1)

    def test_entry_and_time_limits_report_partial_evidence(self) -> None:
        for index in range(4):
            (self.home / str(index)).write_bytes(b"x" * 4096)
        for options in ({"max_entries": 1}, {"timeout": 1}):
            with self.subTest(options=options):
                clock = patch.object(inventory.time, "monotonic", side_effect=[0, 2, 2]) if "timeout" in options else patch.object(inventory.time, "monotonic", return_value=0)
                with clock:
                    report = self.inspect(self.home, **options)
                row = report["candidates"][0]
                self.assertEqual(row["status"], "partial")
                self.assertIn("scan_limit_reached", row["issues"])
                self.assertLess(row["files"], 4)
                self.assertLessEqual(report["limits"]["entries_examined"], 1 if "max_entries" in options else 250000)

    def test_nested_mounts_remote_targets_and_symlink_ancestors_are_not_traversed(self) -> None:
        nested = self.home / "mounted"
        nested.mkdir()
        (nested / "private").write_bytes(b"x" * 4096)
        self.mounts.return_value = {"/": "ext4", str(nested): "ext4"}  # Bind mount, same st_dev.
        row = self.inspect(self.home)["candidates"][0]
        self.assertEqual(row["files"], 0)
        self.assertIn("nested_mounts_skipped", row["issues"])
        self.assertTrue(self.inspect(nested)["candidates"][0]["is_mountpoint"])
        self.mounts.return_value[str(nested)] = "nfs4"
        with patch.object(inventory, "_open_directory", side_effect=AssertionError("Do not touch remote paths")):
            row = self.inspect(nested)["candidates"][0]
        self.assertEqual(row["status"], "unavailable")
        link = self.home / "link"
        link.symlink_to(nested, target_is_directory=True)
        row = self.inspect(link)["candidates"][0]
        self.assertIn("unreadable_or_symlink_path", row["issues"])

    def test_missing_mount_metadata_and_permission_denials_are_not_healthy_empty_scans(self) -> None:
        self.mounts.side_effect = PermissionError
        with patch.object(inventory, "_scan") as scan:
            report = self.inspect()
        scan.assert_not_called()
        self.assertEqual(report["issues"], ["mount_inventory_unavailable"])
        self.mounts.side_effect = None
        with patch.object(inventory, "_open_directory", side_effect=PermissionError):
            row = self.inspect(self.home)["candidates"][0]
        self.assertEqual(row["status"], "unavailable")
        self.assertIn("unreadable_or_symlink_path", row["issues"])

    def test_cli_dispatch_and_invalid_bounds_do_not_invoke_scans(self) -> None:
        parser = argparse.ArgumentParser()
        add_agent_subparser(parser.add_subparsers())
        args = parser.parse_args(["agent", "storage", "inspect", "--path", str(self.home), "--max-entries", "12", "--timeout", "3", "--json"])
        with patch.object(inventory, "inspect_storage", return_value={"read_only": True, "issues": []}) as scan, redirect_stdout(StringIO()) as output:
            self.assertEqual(run_agent_command(args), 0)
        scan.assert_called_once_with(paths=[str(self.home)], max_entries=12, timeout=3)
        self.assertTrue(json.loads(output.getvalue())["read_only"])
        for options in ({"max_entries": 0}, {"max_entries": 2000001}, {"timeout": 0}, {"timeout": 61}, {"paths": ["relative"]}, {"paths": [str(self.home)] * 9}):
            with self.subTest(options=options), patch.object(inventory, "_scan") as scan, self.assertRaises(ValueError):
                inventory.inspect_storage(home=str(self.home), **options)
            scan.assert_not_called()


if __name__ == "__main__":
    unittest.main()
