"""Storage review evidence stays bounded, metadata-only and explicitly incomplete."""

from __future__ import annotations

import argparse
from contextlib import nullcontext, redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from lib import agent_storage_inventory as inventory
from lib.agent_cli import add_agent_subparser, run_agent_command


class StorageInventoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.enterContext(patch.object(inventory.pwd, "getpwuid", return_value=SimpleNamespace(pw_dir=str(self.home))))
        self.mounts = self.enterContext(patch.object(inventory, "_mounts", return_value={"/": "ext4"}))
        self.enterContext(patch.object(inventory, "_ROOTS", tuple(row for row in inventory._ROOTS if not row[0].startswith("/"))))
        self.enterContext(patch.object(inventory.shutil, "disk_usage", return_value=SimpleNamespace(total=1000000, used=900000, free=100000)))
        self.enterContext(patch.object(inventory.os, "statvfs", return_value=SimpleNamespace(f_favail=100, f_files=1000)))
        self.context = {"ownership_scope": "observer_user_namespace", "effective_uid": os.geteuid()}
        self.enterContext(patch.object(inventory, "_inspection_context", return_value=self.context))

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
        self.assertEqual(report["inspection_context"], self.context)
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

    def test_session_state_entry_failures_continue_with_ordered_metadata_only_listing(self) -> None:
        directory = self.home / ".t3/userdata"
        directory.mkdir(parents=True)
        database = directory / "state.sqlite"
        database.write_text("private fixture payload")
        entries = [
            SimpleNamespace(name="state.sqlite-wal", stat=Mock(side_effect=FileNotFoundError)),
            SimpleNamespace(name="state.sqlite-shm", stat=Mock(side_effect=PermissionError)),
            SimpleNamespace(name=database.name, stat=Mock(return_value=database.stat())),
        ]
        descriptor = inventory._open_directory(str(directory))
        with patch.object(inventory, "_open_directory", side_effect=[FileNotFoundError(), descriptor]), \
                patch.object(inventory.os, "scandir", return_value=nullcontext(iter(entries))), \
                patch("builtins.open", side_effect=AssertionError("No database content reads")):
            result = inventory._session_state_files(str(self.home), {"/": "ext4"})
        self.assertEqual([row["path"] for row in result["files"]], [str(database)])
        self.assertEqual(result["issues"], ["session_state_metadata_unavailable"])
        self.assertNotIn("private fixture payload", json.dumps(result))
        for entry in entries:
            entry.stat.assert_called_once_with(follow_symlinks=False)
        with self.assertRaises(OSError):
            os.fstat(descriptor)

    def test_session_state_only_missing_root_open_is_silent(self) -> None:
        for failure, expected in ((FileNotFoundError, []),
                                  (PermissionError, ["session_state_metadata_unavailable"])):
            with self.subTest(failure=failure), patch.object(
                inventory, "_open_directory", side_effect=failure,
            ), patch.object(inventory.os, "scandir") as listing:
                result = inventory._session_state_files(str(self.home), {"/": "ext4"})
            self.assertEqual(result, {"files": [], "issues": expected})
            listing.assert_not_called()

    def test_session_state_listing_failure_is_reported_even_when_not_found(self) -> None:
        for failure in (FileNotFoundError, PermissionError):
            descriptor = inventory._open_directory(str(self.home))
            with self.subTest(failure=failure), patch.object(
                inventory, "_open_directory", side_effect=[FileNotFoundError(), descriptor],
            ), patch.object(inventory.os, "scandir", side_effect=failure):
                result = inventory._session_state_files(str(self.home), {"/": "ext4"})
            self.assertEqual(result, {"files": [], "issues": ["session_state_metadata_unavailable"]})
            with self.assertRaises(OSError):
                os.fstat(descriptor)

    def test_cli_unavailable_selection_fails_without_changing_other_scan_statuses(self) -> None:
        cache = self.home / ".cache"
        cache.mkdir()
        for name in ("first", "second"):
            (cache / name).write_bytes(b"x" * 4096)
        missing = self.home / "missing"
        measured = self.home / "measured"
        measured.mkdir()
        parser = argparse.ArgumentParser()
        add_agent_subparser(parser.add_subparsers())
        real_open = inventory._open_directory
        cases = (
            ([], True, cache, "unavailable", 0),
            (["--path", str(cache)], True, cache, "unavailable", 1),
            (["--path", str(measured), "--path", str(cache)], True, cache, "unavailable", 1),
            (["--path", str(cache), "--max-entries", "1"], False, cache, "partial", 0),
            (["--path", str(missing)], False, missing, "absent", 0),
        )
        for summary in (False, True):
            for options, denied, target, status, exit_code in cases:
                def open_directory(path):
                    if denied and path == str(cache):
                        raise PermissionError("fixture directory is unreadable")
                    return real_open(path)

                args = parser.parse_args(["agent", "storage", "inspect", "--json", *options,
                                          *(["--summary"] if summary else [])])
                with self.subTest(summary=summary, options=options), patch.object(
                    inventory, "_open_directory", side_effect=open_directory,
                ), redirect_stdout(StringIO()) as output:
                    self.assertEqual(run_agent_command(args), exit_code)
                report = json.loads(output.getvalue())
                row = next(row for row in report["candidates"] if row["path"] == str(target))
                self.assertEqual(row["status"], status)

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
        self.assertEqual(summary["inspection_context"], report["inspection_context"])
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

    def test_npm_guidance_covers_content_cache_without_classifying_npx_as_disposable(self) -> None:
        cache = self.home / ".npm"
        content, npx = cache / "_cacache", cache / "_npx"
        content.mkdir(parents=True)
        npx.mkdir()
        for path in (cache, content):
            report = self.inspect(path)
            self.assertEqual(report["candidates"][0]["cleanup_convention"], "npm-content")
            self.assertIn("npm-content", report["cleanup_conventions"])
        self.assertNotIn("cleanup_convention", self.inspect(npx)["candidates"][0])

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
        with patch.object(inventory, "inspect_storage", return_value={"read_only": True, "issues": [], "candidates": []}) as scan, redirect_stdout(StringIO()) as output:
            self.assertEqual(run_agent_command(args), 0)
        scan.assert_called_once_with(paths=[str(self.home)], max_entries=12, timeout=3)
        self.assertTrue(json.loads(output.getvalue())["read_only"])
        for options in ({"max_entries": 0}, {"max_entries": 2000001}, {"timeout": 0}, {"timeout": 61}, {"paths": ["relative"]}, {"paths": [str(self.home)] * 9}):
            with self.subTest(options=options), patch.object(inventory, "_scan") as scan, self.assertRaises(ValueError):
                inventory.inspect_storage(home=str(self.home), **options)
            scan.assert_not_called()


class InspectionContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.proc = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.process = self.proc / "42"
        self.process.mkdir()
        (self.proc / "self").symlink_to("42")
        self.enterContext(patch.object(inventory.os, "getpid", return_value=42))
        for name in ("getuid", "geteuid", "getgid", "getegid"):
            self.enterContext(patch.object(inventory.os, name, return_value=1000))

    def test_nested_mapping_is_reported_to_parent_without_claiming_host_ownership(self) -> None:
        (self.process / "uid_map").write_text("1000 0 1\n65534 1000 1\n")
        (self.process / "gid_map").write_text("1000 0 1\n")
        result = inventory._inspection_context(str(self.proc))
        self.assertEqual(result["uid_map_to_parent"], [[1000, 0, 1], [65534, 1000, 1]])
        self.assertEqual(result["gid_map_to_parent"], [[1000, 0, 1]])
        self.assertEqual(result["ownership_scope"], "observer_user_namespace")
        self.assertEqual([result[key] for key in ("uid", "effective_uid", "gid", "effective_gid")], [1000] * 4)
        self.assertTrue(result["proc_self_pid_matches"])
        self.assertEqual(result["issues"], [])
        with patch.object(inventory.os, "getpid", return_value=2):
            self.assertFalse(inventory._inspection_context(str(self.proc))["proc_self_pid_matches"])

    def test_missing_malformed_and_oversized_maps_stay_unknown(self) -> None:
        for value in (None, "", "0 0\n", "0 0 0\n", "-1 0 1\n", "invalid", "\N{SNOWMAN}", "0 0 1\n" * 1000):
            with self.subTest(value=value):
                if value is not None:
                    (self.process / "uid_map").write_text(value)
                result = inventory._inspection_context(str(self.proc))
                self.assertIsNone(result["uid_map_to_parent"])
                self.assertIsNone(result["gid_map_to_parent"])
                self.assertEqual(result["issues"], ["uid_map_unavailable", "gid_map_unavailable"])
        (self.proc / "self").unlink()
        result = inventory._inspection_context(str(self.proc))
        self.assertIsNone(result["proc_self_pid_matches"])
        self.assertIn("proc_self_pid_unavailable", result["issues"])


if __name__ == "__main__":
    unittest.main()
