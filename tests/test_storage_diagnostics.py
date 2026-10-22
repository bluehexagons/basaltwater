"""Metadata accounting, traversal boundaries and on-demand worker contracts."""

from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, mock_open, patch

from lib import storage_diagnostics as diagnostics


class StorageCollectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.root = self.temporary / "root"
        self.home = self.root / "home/agent"
        self.home.mkdir(parents=True)
        self.enterContext(patch.object(diagnostics, "_ROOT", str(self.root)))
        self.read_mounts = diagnostics._mounts
        self.mounts = self.enterContext(patch.object(diagnostics, "_mounts", return_value={str(self.root): "ext4"}))
        self.enterContext(patch.object(diagnostics, "_open_root", side_effect=lambda: os.open(self.root, diagnostics._OPEN_DIRECTORY)))
        self.capacity = self.enterContext(patch.object(diagnostics.os, "fstatvfs", return_value=SimpleNamespace(
            f_frsize=1024, f_bsize=4096, f_blocks=1000, f_bfree=400, f_bavail=300)))
        self.clock = self.enterContext(patch.object(diagnostics.time, "monotonic", return_value=100.0))

    def file(self, path: str, size: int = 4096) -> Path:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"x" * size)
        return target

    def collect(self, **options: object) -> dict:
        # Mount observations are mocked; no application file content may be opened.
        with patch("builtins.open", side_effect=AssertionError("File contents must not be read")):
            return diagnostics.collect_storage_usage(str(self.home), **options)

    def row(self, report: dict, facet: str, identifier: str) -> dict:
        return next(row for row in report[facet] if row["id"] == identifier)

    def test_disjoint_areas_overlapping_facets_and_directory_metadata(self) -> None:
        project = self.file("home/agent/repos/project/source", 8192)
        self.file("home/agent/.codex/sessions/session", 4096)
        self.file("var/tmp/job", 1024)
        self.file("var/log/log", 512)
        self.file("root-file", 128)
        report = self.collect()
        self.assertTrue(diagnostics._complete(report))
        for field, total in (("allocated_bytes", "scanned_allocated_bytes"),
                             ("apparent_bytes", "scanned_apparent_bytes"), ("files", "files_scanned")):
            self.assertEqual(sum(row[field] for row in report["areas"]), report[total])
        self.assertEqual(report["files_scanned"], 5)
        self.assertEqual(self.row(report, "areas", "area:var/tmp")["files"], 1)
        self.assertEqual(self.row(report, "areas", "area:var")["files"], 1)
        self.assertEqual(self.row(report, "areas", "area:other")["files"], 1)
        project_row = self.row(report, "projects", "project:project")
        self.assertEqual(project_row["allocated_bytes"],
                         (project.stat().st_blocks + project.parent.stat().st_blocks) * 512)
        codex = self.row(report, "services", "codex")
        self.assertEqual(codex["files"], 1)
        self.assertGreater(codex["allocated_bytes"], 0)
        self.assertEqual(report["root_filesystem"], {
            "total_bytes": 1024000, "used_bytes": 614400,
            "available_bytes": 307200, "reserved_bytes": 102400})
        self.assertTrue(any("need not match df" in issue for issue in report["issues"]))
        self.assertTrue(any("overlap" in issue for issue in report["issues"]))
        self.assertEqual(set(report), {"generated_at", "duration_seconds", "entries_scanned", "files_scanned",
                                      "scanned_allocated_bytes", "scanned_apparent_bytes", "root_filesystem",
                                      "areas", "services", "projects", "issues", "limits"})
        datetime.fromisoformat(report["generated_at"])
        self.assertEqual(set(project_row), {"id", "label", "path", "allocated_bytes", "apparent_bytes",
                                            "files", "complete", "issues"})

    def test_hardlinks_deduplicate_globally_and_sparse_blocks_remain_distinct(self) -> None:
        payload = self.file("home/agent/repos/project/payload", 1)
        alias = self.root / "var/tmp/alias"
        alias.parent.mkdir(parents=True)
        alias.hardlink_to(payload)
        real_stat = os.stat

        def sparse_stat(path: str, **kwargs: object) -> object:
            info = real_stat(path, **kwargs)
            if stat.S_ISREG(info.st_mode):
                return SimpleNamespace(st_mode=info.st_mode, st_dev=info.st_dev, st_ino=info.st_ino,
                                       st_blocks=2, st_size=1000000)
            return info

        directory_blocks = payload.parent.stat().st_blocks * 512
        with patch.object(diagnostics.os, "stat", side_effect=sparse_stat):
            report = self.collect()
        self.assertEqual(report["files_scanned"], 1)
        project = report["projects"][0]
        self.assertEqual(project["allocated_bytes"], directory_blocks + 1024)
        self.assertGreaterEqual(project["apparent_bytes"], 1000000)
        self.assertEqual(self.row(report, "areas", "area:var/tmp")["files"], 0)
        self.assertTrue(any("first observed path" in issue for issue in report["issues"]))

    def test_symlink_escape_special_files_and_known_mounts_are_never_followed(self) -> None:
        self.file("home/agent/repos/local/file")
        outside = self.temporary / "outside"
        outside.mkdir()
        (outside / "private").write_text("private fixture contents")
        (self.home / "repos/escape").symlink_to(outside, target_is_directory=True)
        os.mkfifo(self.home / "repos/pipe")
        mounted = self.file("home/agent/repos/mounted/remote")
        self.mounts.return_value[str(mounted.parent)] = "fuse.sshfs"
        real_stat, real_open = os.stat, os.open

        def guarded_stat(path: str, **kwargs: object) -> object:
            self.assertNotEqual(path, "mounted", "Known mounts must be skipped before stat")
            self.assertFalse(kwargs.get("follow_symlinks", True))
            self.assertIn("dir_fd", kwargs)
            return real_stat(path, **kwargs)

        def guarded_open(path: str, flags: int, **kwargs: object) -> int:
            self.assertTrue(flags & os.O_NOFOLLOW)
            self.assertNotIn(str(path), ("escape", "mounted", "pipe"))
            return real_open(path, flags, **kwargs)

        with patch.object(diagnostics.os, "stat", side_effect=guarded_stat), \
                patch.object(diagnostics.os, "open", side_effect=guarded_open):
            report = self.collect()
        self.assertEqual(report["files_scanned"], 1)
        self.assertNotIn("private fixture contents", json.dumps(report))
        self.assertEqual({row["id"] for row in report["projects"]}, {"project:local", "project:mounted"})
        self.assertFalse(self.row(report, "projects", "project:mounted")["complete"])
        self.assertFalse(diagnostics._complete(report))

    def test_home_on_other_mount_is_omitted_and_not_compared_to_capacity(self) -> None:
        self.file("home/agent/repos/project/file", 8192)
        self.mounts.return_value[str(self.home)] = "ext4"
        report = self.collect()
        self.assertEqual(report["files_scanned"], 0)
        self.assertEqual(report["projects"], [])
        self.assertFalse(self.row(report, "areas", "area:home")["complete"])
        self.assertTrue(any("Home is on" in issue for issue in report["issues"]))
        self.assertEqual(report["root_filesystem"]["total_bytes"], 1024000)

    def test_sandbox_identity_mounts_are_measured_but_nested_remaps_are_not_touched(self) -> None:
        for path in ("usr/visible", "etc/visible", "boot/visible", "usr/remapped/secret"):
            self.file(path)
        content = f"1 0 8:1 / {self.root} rw - ext4 /dev/fixture rw\n"
        for index, path in enumerate(("usr", "etc", "boot"), 2):
            content += f"{index} 1 8:1 /{path} {self.root}/{path} ro - ext4 /dev/fixture ro\n"
        content += f"5 2 8:1 /private {self.root}/usr/remapped ro - ext4 /dev/fixture ro\n"
        with patch("builtins.open", mock_open(read_data=content)):
            self.mounts.return_value = self.read_mounts()
        real_stat, real_open = os.stat, os.open

        def guarded_stat(path: str, **kwargs: object) -> object:
            self.assertNotEqual(path, "remapped")
            return real_stat(path, **kwargs)

        def guarded_open(path: str, flags: int, **kwargs: object) -> int:
            self.assertNotEqual(path, "remapped")
            return real_open(path, flags, **kwargs)

        with patch.object(diagnostics.os, "stat", side_effect=guarded_stat), \
                patch.object(diagnostics.os, "open", side_effect=guarded_open):
            report = self.collect()
        self.assertEqual(report["files_scanned"], 3)
        for area in ("usr", "etc", "boot"):
            self.assertEqual(self.row(report, "areas", "area:" + area)["files"], 1)
        self.assertTrue(self.row(report, "areas", "area:etc")["complete"])
        self.assertFalse(self.row(report, "areas", "area:usr")["complete"])
        self.assertFalse(diagnostics._complete(report))
        self.assertEqual(sum(row["allocated_bytes"] for row in report["areas"]), report["scanned_allocated_bytes"])

    def test_unlisted_device_boundary_is_also_omitted(self) -> None:
        self.file("home/agent/repos/project/file")
        real_stat = os.stat

        def changed_device(path: str, **kwargs: object) -> object:
            info = real_stat(path, **kwargs)
            if path == "agent":
                return SimpleNamespace(st_mode=info.st_mode, st_dev=info.st_dev + 1)
            return info

        with patch.object(diagnostics.os, "stat", side_effect=changed_device):
            report = self.collect()
        self.assertEqual(report["files_scanned"], 0)
        self.assertTrue(any("Home is on" in issue for issue in report["issues"]))

    def test_unknown_remote_fuse_and_pseudo_root_refuse_all_scanning(self) -> None:
        for kind in (None, "nfs", "cifs", "fuse.sshfs", "tmpfs", "proc"):
            with self.subTest(kind=kind):
                self.mounts.return_value = {} if kind is None else {str(self.root): kind}
                with patch.object(diagnostics, "_open_root") as opening:
                    report = self.collect()
                opening.assert_not_called()
                self.assertIsNone(report["root_filesystem"])
                self.assertEqual(report["entries_scanned"], 0)
                self.assertFalse(any(row["complete"] for row in report["areas"]))
                self.assertTrue(any("refused" in issue for issue in report["issues"]))

    def test_entry_budget_preserves_completed_projects_before_os_data(self) -> None:
        self.file("home/agent/repos/project/file")
        for index in range(70):
            self.file(f"usr/data/file-{index}", 1)
        report = self.collect(entry_limit=40)
        self.assertLessEqual(report["entries_scanned"], 40)
        project = self.row(report, "projects", "project:project")
        self.assertTrue(project["complete"])
        self.assertEqual(project["files"], 1)
        self.assertFalse(self.row(report, "areas", "area:usr")["complete"])
        self.assertTrue(any("limit" in issue for issue in report["issues"]))

    def test_service_roots_precede_large_unrelated_ancestor_subtrees(self) -> None:
        self.file("home/agent/.t3/userdata/session")
        self.file("var/log/basaltwater/log")
        for index in range(70):
            self.file(f"home/agent/.local/share/unrelated/file-{index}", 1)
            self.file(f"var/lib/unrelated/file-{index}", 1)
        report = self.collect(entry_limit=65)
        for identifier in ("t3", "basaltwater-logs"):
            row = self.row(report, "services", identifier)
            self.assertTrue(row["complete"])
            self.assertEqual(row["files"], 1)
        self.assertFalse(diagnostics._complete(report))
        self.assertLessEqual(report["entries_scanned"], 65)

    def test_large_locations_share_entry_budget_and_resume_with_spare_budget(self) -> None:
        for index in range(80):
            self.file(f"home/agent/repos/large/file-{index}", 1)
            self.file(f"home/agent/.local/share/basaltwater/large/file-{index}", 1)
        self.file("home/agent/repos/small/source")
        self.file("home/agent/.t3/userdata/session")
        self.file("home/agent/.codex/sessions/session")
        bounded = self.collect(entry_limit=65)
        self.assertEqual(bounded["entries_scanned"], 65)
        for facet, identifier in (("services", "t3"), ("services", "codex"), ("projects", "project:small")):
            with self.subTest(identifier=identifier):
                row = self.row(bounded, facet, identifier)
                self.assertTrue(row["complete"])
                self.assertEqual(row["files"], 1)
        for facet, identifier in (("services", "basaltwater-data"), ("projects", "project:large")):
            row = self.row(bounded, facet, identifier)
            self.assertFalse(row["complete"])
            self.assertLess(row["files"], 80)
        self.assertTrue(any("share reached" in issue for issue in bounded["issues"]))
        resumed = self.collect(entry_limit=300)
        self.assertTrue(diagnostics._complete(resumed))
        self.assertEqual(resumed["files_scanned"], 163)
        self.assertEqual(self.row(resumed, "projects", "project:large")["files"], 80)
        self.assertEqual(self.row(resumed, "services", "basaltwater-data")["files"], 80)
        self.assertFalse(any("share reached" in issue for issue in resumed["issues"]))
        self.assertEqual(sum(row["allocated_bytes"] for row in resumed["areas"]),
                         resumed["scanned_allocated_bytes"])

    def test_large_project_shares_time_budget_with_services_and_other_projects(self) -> None:
        for index in range(12):
            self.file(f"home/agent/repos/large/file-{index}", 1)
        self.file("home/agent/repos/small/source")
        self.file("home/agent/.t3/userdata/session")
        self.file("home/agent/.codex/sessions/session")
        now = 0.0
        self.clock.side_effect = lambda: now
        real_stat = os.stat

        def timed_stat(path: str, **kwargs: object) -> object:
            nonlocal now
            info = real_stat(path, **kwargs)
            if str(path).startswith("file-"):
                now += 0.2
            return info

        with patch.object(diagnostics.os, "stat", side_effect=timed_stat):
            report = self.collect(duration_seconds=1)
        self.assertGreaterEqual(report["duration_seconds"], 1)
        self.assertLessEqual(report["duration_seconds"], 1.2)
        self.assertFalse(self.row(report, "projects", "project:large")["complete"])
        for facet, identifier in (("services", "t3"), ("services", "codex"), ("projects", "project:small")):
            row = self.row(report, facet, identifier)
            self.assertTrue(row["complete"])
            self.assertEqual(row["files"], 1)

    def test_project_discovery_is_bounded_before_services(self) -> None:
        self.file("home/agent/.t3/userdata/session")
        real_scandir = os.scandir

        class LargeDirectory:
            def __enter__(self):
                # Simulate a huge directory using repeated metadata observations.
                # No files, mounts or large fixture payloads are needed.
                return iter(SimpleNamespace(name=f"absent-project-{index}") for index in range(10000))

            def __exit__(self, *args: object) -> None:
                pass

        (self.home / "repos").mkdir()
        repos_inode = (self.home / "repos").stat().st_ino
        calls = 0

        def listing(descriptor: int) -> object:
            nonlocal calls
            if os.fstat(descriptor).st_ino == repos_inode:
                calls += 1
                if calls == 1:
                    return LargeDirectory()
            return real_scandir(descriptor)

        with patch.object(diagnostics.os, "scandir", side_effect=listing):
            report = self.collect(entry_limit=80)
        self.assertLessEqual(report["entries_scanned"], 80)
        self.assertTrue(self.row(report, "services", "t3")["complete"])
        self.assertEqual(self.row(report, "services", "t3")["files"], 1)
        self.assertFalse(any("Project discovery share" in issue for issue in report["issues"]))

    def test_resumed_project_discovery_clears_transient_limit(self) -> None:
        for index in range(120):
            self.file(f"home/agent/repos/plain-file-{index}", 1)
        self.file("home/agent/repos/project/source")
        report = self.collect(entry_limit=260)
        self.assertEqual(report["files_scanned"], 121)
        self.assertTrue(self.row(report, "projects", "project:project")["complete"])
        self.assertTrue(diagnostics._complete(report))
        self.assertFalse(any("discovery share" in issue for issue in report["issues"]))

    def test_nested_service_cannot_consume_parent_location_share(self) -> None:
        self.home = self.root / "var/lib/docker"
        for index in range(80):
            self.file(f"var/lib/docker/.codex/file-{index}", 1)
        self.file("var/lib/docker/own-layer")
        self.file("var/lib/docker/.t3/session")
        self.file("var/lib/docker/repos/project/source")
        bounded = self.collect(entry_limit=100)
        self.assertEqual(self.row(bounded, "services", "docker")["files"], 2)
        self.assertFalse(self.row(bounded, "services", "codex")["complete"])
        resumed = self.collect(entry_limit=200)
        self.assertTrue(diagnostics._complete(resumed))
        self.assertEqual(resumed["files_scanned"], 83)
        self.assertEqual(sum(row["files"] for row in resumed["services"]), 83)

    def test_previously_observed_directory_cannot_become_absent_zero(self) -> None:
        self.file("home/agent/.codex/session")
        real_stat = os.stat
        calls = 0

        def disappear(path: str, **kwargs: object) -> object:
            nonlocal calls
            if path == ".codex":
                calls += 1
                if calls == 2:
                    raise FileNotFoundError("private fixture detail")
            return real_stat(path, **kwargs)

        with patch.object(diagnostics.os, "stat", side_effect=disappear):
            report = self.collect()
        row = self.row(report, "services", "codex")
        self.assertFalse(row["complete"])
        self.assertFalse(any("Not present" in issue for issue in row["issues"]))
        self.assertIn("Previously observed directory disappeared during scan.", row["issues"])
        self.assertNotIn("private fixture detail", json.dumps(report))

    def test_nested_service_prefixes_attribute_bytes_to_one_service(self) -> None:
        self.home = self.root / "var/lib/docker"
        self.file("var/lib/docker/repos/project/source")
        self.file("var/lib/docker/.codex/session")
        report = self.collect()
        self.assertTrue(diagnostics._complete(report))
        self.assertEqual(report["files_scanned"], 2)
        self.assertEqual(sum(row["files"] for row in report["services"]), 2)
        self.assertEqual(self.row(report, "services", "docker")["files"], 1)
        self.assertEqual(self.row(report, "services", "codex")["files"], 1)
        self.assertEqual(report["projects"][0]["files"], 1)

    def test_project_discovery_parent_symlink_is_unavailable(self) -> None:
        (self.home / "repos").symlink_to(self.temporary, target_is_directory=True)
        report = self.collect()
        self.assertEqual(report["projects"], [])
        self.assertFalse(diagnostics._complete(report))
        self.assertTrue(any("symlink" in issue for issue in report["issues"][5:]))

    def test_shared_time_budget_and_depth_limit_produce_lower_bounds(self) -> None:
        self.file("home/agent/repos/project/deeper/file")
        self.clock.side_effect = [0.0, 0.0, 20.0, 20.0]
        timed = self.collect(duration_seconds=1)
        self.assertEqual(timed["files_scanned"], 0)
        self.assertEqual(timed["duration_seconds"], 20.0)
        self.assertTrue(any("limit" in issue for issue in timed["issues"]))
        self.clock.side_effect = None
        with patch.object(diagnostics, "_MAX_DEPTH", 4):
            deep = self.collect()
        self.assertFalse(self.row(deep, "projects", "project:project")["complete"])
        self.assertEqual(deep["files_scanned"], 0)
        self.assertTrue(any("depth" in issue for issue in deep["issues"]))

    def test_permission_failures_are_partial_sanitized_and_not_zero(self) -> None:
        self.file("home/agent/repos/blocked/file")
        real_open = os.open

        def deny(path: str, flags: int, **kwargs: object) -> int:
            if path == "blocked":
                raise PermissionError("private token must never appear")
            return real_open(path, flags, **kwargs)

        with patch.object(diagnostics.os, "open", side_effect=deny):
            report = self.collect()
        blocked = self.row(report, "projects", "project:blocked")
        self.assertFalse(blocked["complete"])
        self.assertEqual(blocked["allocated_bytes"], 0)
        self.assertIn(diagnostics._PERMISSION, blocked["issues"])
        self.assertNotIn("private token", json.dumps(report))

    def test_permission_denied_metadata_is_identified_without_exception_details(self) -> None:
        self.file("home/agent/.codex/session")
        real_stat = os.stat

        def deny(path: str, **kwargs: object) -> object:
            if path == ".codex":
                raise PermissionError("private metadata detail")
            return real_stat(path, **kwargs)

        with patch.object(diagnostics.os, "stat", side_effect=deny):
            report = self.collect()
        codex = self.row(report, "services", "codex")
        self.assertFalse(codex["complete"])
        self.assertIn(diagnostics._PERMISSION, codex["issues"])
        self.assertNotIn("private metadata detail", json.dumps(report))

    def test_disappearing_listed_entry_is_partial(self) -> None:
        self.file("home/agent/repos/project/vanished")
        real_stat = os.stat

        def disappear(path: str, **kwargs: object) -> object:
            if path == "vanished":
                raise FileNotFoundError("private fixture detail")
            return real_stat(path, **kwargs)

        with patch.object(diagnostics.os, "stat", side_effect=disappear):
            report = self.collect()
        self.assertFalse(report["projects"][0]["complete"])
        self.assertTrue(any("disappeared" in issue for issue in report["projects"][0]["issues"]))

    def test_directory_identity_race_and_listing_failures_are_partial(self) -> None:
        directory = self.file("home/agent/repos/racy/file").parent
        real_open, real_fstat = os.open, os.fstat
        replaced = set()

        def opened(path: str, flags: int, **kwargs: object) -> int:
            descriptor = real_open(path, flags, **kwargs)
            if path == directory.name:
                replaced.add(descriptor)
            else:
                replaced.discard(descriptor)
            return descriptor

        def changed(descriptor: int) -> object:
            info = real_fstat(descriptor)
            if descriptor in replaced:
                return SimpleNamespace(st_dev=info.st_dev, st_ino=info.st_ino + 1)
            return info

        with patch.object(diagnostics.os, "open", side_effect=opened), \
                patch.object(diagnostics.os, "fstat", side_effect=changed):
            raced = self.collect()
        row = self.row(raced, "projects", "project:racy")
        self.assertEqual(row["files"], 0)
        self.assertFalse(row["complete"])
        self.assertIn("Directory changed during scan.", row["issues"])
        real_scandir = os.scandir

        def listing(descriptor: int) -> object:
            if isinstance(descriptor, int) and real_fstat(descriptor).st_ino == directory.stat().st_ino:
                raise PermissionError("private listing exception")
            return real_scandir(descriptor)

        with patch.object(diagnostics.os, "scandir", side_effect=listing):
            unreadable = self.collect()
        self.assertEqual(len(unreadable["projects"]), 1)
        self.assertFalse(unreadable["projects"][0]["complete"])
        self.assertIn(diagnostics._PERMISSION, unreadable["projects"][0]["issues"])
        self.assertNotIn("private listing exception", json.dumps(unreadable))

    def test_absent_zero_differs_from_unavailable_and_empty_directory(self) -> None:
        report = self.collect()
        absent = self.row(report, "services", "codex")
        self.assertTrue(absent["complete"])
        self.assertEqual(absent["allocated_bytes"], 0)
        self.assertIn("Not present", absent["issues"][0])
        (self.home / ".codex").mkdir()
        empty = self.row(self.collect(), "services", "codex")
        self.assertTrue(empty["complete"])
        self.assertEqual(empty["files"], 0)
        self.assertEqual(empty["issues"], [])
        self.assertEqual(empty["allocated_bytes"], (self.home / ".codex").stat().st_blocks * 512)
        self.mounts.side_effect = PermissionError("private failure")
        unavailable = self.row(self.collect(), "services", "codex")
        self.assertFalse(unavailable["complete"])
        self.assertEqual(unavailable["allocated_bytes"], 0)
        self.assertIn("Unavailable", unavailable["issues"][0])

    def test_conventional_symlink_is_unavailable_and_prefix_matching_is_strict(self) -> None:
        self.file("home/agent/.codex-other/session")
        self.file("var/lib/docker-other/layer")
        (self.home / ".codex").symlink_to(self.temporary, target_is_directory=True)
        report = self.collect()
        codex = self.row(report, "services", "codex")
        self.assertFalse(codex["complete"])
        self.assertEqual(codex["files"], 0)
        self.assertEqual(self.row(report, "services", "docker")["files"], 0)

    def test_project_rows_bounded_and_only_fixed_directories_discovered(self) -> None:
        for index in range(66):
            self.file(f"home/agent/repos/project-{index}/nested/source", index + 1)
        self.file("home/agent/arbitrary/another-project/source")
        self.file("home/agent/repos/plain-file")
        report = self.collect()
        self.assertEqual(len(report["projects"]), 30)
        self.assertEqual(report["files_scanned"], 68)
        self.assertTrue(all(row["path"].startswith(str(self.home / "repos") + "/")
                            for row in report["projects"]))
        self.assertTrue(all(row["label"].startswith("project-") for row in report["projects"]))
        self.assertTrue(any("discovery limit" in issue for issue in report["issues"]))
        self.assertTrue(any("largest 30" in issue for issue in report["issues"]))
        self.assertEqual([row["allocated_bytes"] for row in report["projects"]],
                         sorted((row["allocated_bytes"] for row in report["projects"]), reverse=True))

    def test_capacity_failure_keeps_observed_bytes_but_marks_partial(self) -> None:
        self.file("home/agent/repos/project/file")
        self.capacity.side_effect = OSError("sensitive host detail")
        report = self.collect()
        self.assertIsNone(report["root_filesystem"])
        self.assertEqual(report["files_scanned"], 1)
        self.assertFalse(diagnostics._complete(report))
        self.assertNotIn("sensitive host detail", json.dumps(report))

    def test_invalid_inputs_fail_before_host_observation(self) -> None:
        cases = ({"home": "relative"}, {"home": str(self.home) + "/.."}, {"home": "/tmp\n"},
                 {"home": 1}, {"duration_seconds": float("nan")}, {"duration_seconds": float("inf")},
                 {"duration_seconds": 0}, {"duration_seconds": 61}, {"duration_seconds": True},
                 {"entry_limit": 0}, {"entry_limit": True}, {"entry_limit": 2000001})
        for options in cases:
            with self.subTest(options=options), self.assertRaises(ValueError):
                diagnostics.collect_storage_usage(**{"home": str(self.home), **options})
        self.mounts.assert_not_called()


class StorageMountTests(unittest.TestCase):
    def mounts(self, content: str) -> dict:
        with patch("builtins.open", mock_open(read_data=content)):
            return diagnostics._mounts()

    def test_identity_projections_include_subvolume_roots_and_decoded_paths(self) -> None:
        for root in ("/", "/@"):
            with self.subTest(root=root):
                content = f"1 0 8:1 {root} / rw - ext4 /dev/fixture rw\n"
                for index, path in enumerate(("usr", "etc", "boot", r"space\040here", r"slash\134040"), 2):
                    prefix = root.rstrip("/")
                    content += f"{index} 1 8:1 {prefix}/{path} /{path} ro - ext4 /dev/fixture ro\n"
                self.assertEqual(self.mounts(content), {"/": "ext4"})

    def test_remaps_aliases_other_devices_types_and_nested_mounts_remain_boundaries(self) -> None:
        content = "1 0 8:1 /@ / rw - ext4 /dev/fixture rw\n2 1 8:1 /@/usr /usr ro - ext4 /dev/fixture ro\n"
        cases = (("/var/tmp", "8:1", "/@/var/tmp/private", "ext4"),
                 ("/alias", "8:1", "/@/home", "ext4"),
                 ("/usr/other", "8:2", "/@/usr/other", "ext4"),
                 ("/usr/fuse", "8:1", "/@/usr/fuse", "fuse.sshfs"),
                 ("/usr/nfs", "0:9", "/", "nfs"),
                 ("/tmp", "0:7", "/", "tmpfs"))
        for index, (path, device, root, kind) in enumerate(cases, 3):
            content += f"{index} 1 {device} {root} {path} rw - {kind} source rw\n"
        self.assertEqual(self.mounts(content), {"/": "ext4", **{case[0]: case[3] for case in cases}})

    def test_duplicate_nested_mounts_are_excluded_in_either_order(self) -> None:
        root = "1 0 8:1 / / rw - ext4 source rw\n"
        identity = "2 1 8:1 /usr /usr ro - ext4 source ro\n"
        fuse = "3 1 0:8 / /usr rw - fuse.sshfs source rw\n"
        for records in ((identity, fuse), (fuse, identity), (identity, identity)):
            self.assertEqual(self.mounts(root + "".join(records)), {"/": "ext4", "/usr": "ambiguous"})
        for records in ((root, root), (root, "4 0 0:9 / / rw - nfs source rw\n")):
            with self.assertRaises(ValueError):
                self.mounts("".join(records))

    def test_malformed_metadata_cannot_become_trusted_by_normalization(self) -> None:
        root = "1 0 8:1 / / rw - ext4 source rw\n"
        for device, mount_root, path in (("invalid", "/usr", "/usr"), ("8:1", "relative", "/usr"),
                                         ("8:1", "/usr/../usr", "/usr"), ("8:1", "/usr", "//usr"),
                                         ("8:1", "/usr", "/usr/"), ("8:1", "/usr", r"/usr\041")):
            with self.subTest(device=device, root=mount_root, path=path), self.assertRaises(ValueError):
                self.mounts(root + f"2 1 {device} {mount_root} {path} ro - ext4 source ro\n")

    def test_mount_table_is_bounded_decoded_and_sanitized(self) -> None:
        content = "1 0 1:1 / / rw - ext4 /dev/private rw\n2 1 1:2 / /space\\040here rw - nfs secret:/remote rw\n"
        stream = Mock()
        stream.__enter__ = Mock(return_value=stream)
        stream.__exit__ = Mock(return_value=False)
        stream.read.return_value = content
        with patch("builtins.open", return_value=stream):
            self.assertEqual(diagnostics._mounts(), {"/": "ext4", "/space here": "nfs"})
            stream.read.assert_called_once_with(1024 * 1024 + 1)
            stream.read.return_value = "x" * (1024 * 1024 + 1)
            with self.assertRaises(ValueError):
                diagnostics._mounts()

    def test_root_open_anchors_each_component_and_closes_on_failure(self) -> None:
        # Entirely mocked descriptors: no observation of host ancestors.
        with patch.object(diagnostics, "_ROOT", "/fixture/root"), \
                patch.object(diagnostics.os, "open", side_effect=[10, 11, PermissionError("private")]) as opening, \
                patch.object(diagnostics.os, "close") as closing:
            with self.assertRaises(PermissionError):
                diagnostics._open_root()
        self.assertEqual([call.args[0] for call in opening.call_args_list], ["/", "fixture", "root"])
        self.assertEqual(opening.call_args_list[1].kwargs, {"dir_fd": 10})
        self.assertEqual(opening.call_args_list[2].kwargs, {"dir_fd": 11})
        self.assertTrue(all(call.args[1] & os.O_NOFOLLOW for call in opening.call_args_list))
        self.assertEqual([call.args[0] for call in closing.call_args_list], [10, 11])


class StorageManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.thread_type = threading.Thread

    def report(self, *, complete: bool = True) -> dict:
        return {"root_filesystem": {"total_bytes": 100}, "areas": [{"complete": complete}],
                "services": [], "projects": [], "issues": [], "files_scanned": 1}

    def finish(self, manager: diagnostics.StorageDiagnostics) -> dict:
        # Capture/join only this manager's daemon, avoiding polling and sleeps.
        manager._test_thread.join(3)
        self.assertFalse(manager._test_thread.is_alive())
        return manager.snapshot()

    def tracked_manager(self, collector: Mock) -> diagnostics.StorageDiagnostics:
        manager = diagnostics.StorageDiagnostics("/home/fixture", collector=collector)

        def thread(**kwargs: object) -> threading.Thread:
            manager._test_thread = self.thread_type(**kwargs)
            return manager._test_thread

        self.enterContext(patch.object(diagnostics.threading, "Thread", side_effect=thread))
        return manager

    def test_idle_and_snapshot_never_collect_and_are_detached(self) -> None:
        collector = Mock()
        manager = diagnostics.StorageDiagnostics("/home/fixture", collector=collector)
        snapshot = manager.snapshot()
        self.assertEqual(snapshot, {"state": "idle", "started_at": None, "finished_at": None,
                                    "message": "Ready. Diagnostics run only when requested.", "report": None, "scan_mode": "quick"})
        snapshot["state"] = "failed"
        self.assertEqual(manager.snapshot()["state"], "idle")
        collector.assert_not_called()

    def test_concurrent_starts_launch_exactly_one_daemon(self) -> None:
        entered, release = threading.Event(), threading.Event()

        def blocked(home: str, **limits: object) -> dict:
            self.assertEqual(home, "/home/fixture")
            self.assertEqual(limits, diagnostics.SCAN_LIMITS["quick"])
            entered.set()
            self.assertTrue(release.wait(3))
            return self.report()

        collector = Mock(side_effect=blocked)
        manager = self.tracked_manager(collector)
        workers = []
        results = []
        try:
            for _ in range(12):
                worker = self.thread_type(target=lambda: results.append(manager.start()))
                workers.append(worker)
                worker.start()
            for worker in workers:
                worker.join(3)
            self.assertTrue(entered.wait(3))
            self.assertEqual(results.count(True), 1)
            self.assertEqual(results.count(False), 11)
            running = manager.snapshot()
            self.assertEqual(running["state"], "running")
            self.assertIsNone(running["finished_at"])
            self.assertTrue(manager._test_thread.daemon)
            datetime.fromisoformat(running["started_at"])
            collector.assert_called_once_with("/home/fixture", **diagnostics.SCAN_LIMITS["quick"])
            self.assertFalse(manager.start(mode="extended"))
            self.assertEqual(manager.snapshot()["scan_mode"], "quick")
        finally:
            release.set()
        complete = self.finish(manager)
        self.assertEqual(complete["state"], "complete")
        datetime.fromisoformat(complete["finished_at"])

    def test_rerun_keeps_report_and_failure_is_sanitized(self) -> None:
        report = self.report()
        collector = Mock(return_value=report)
        manager = self.tracked_manager(collector)
        self.assertTrue(manager.start())
        first = self.finish(manager)
        first["report"]["areas"][0]["complete"] = False
        report["files_scanned"] = 999
        self.assertEqual(manager.snapshot()["report"]["files_scanned"], 1)
        self.assertTrue(manager.snapshot()["report"]["areas"][0]["complete"])
        release, entered = threading.Event(), threading.Event()

        def fail(home: str, **limits: object) -> dict:
            entered.set()
            self.assertTrue(release.wait(3))
            raise RuntimeError("private token, raw exception dump")

        collector.side_effect = fail
        self.assertTrue(manager.start())
        try:
            self.assertTrue(entered.wait(3))
            self.assertEqual(manager.snapshot()["report"]["files_scanned"], 1)
        finally:
            release.set()
        failed = self.finish(manager)
        self.assertEqual(failed["state"], "failed")
        self.assertEqual(failed["report"]["files_scanned"], 1)
        self.assertNotIn("private token", json.dumps(failed))
        self.assertIsNotNone(failed["finished_at"])
        collector.side_effect = None
        collector.return_value = self.report(complete=False)
        self.assertTrue(manager.start())
        self.assertEqual(self.finish(manager)["state"], "partial")

    def test_extended_scan_uses_fixed_limits_and_unknown_modes_do_not_launch(self) -> None:
        collector = Mock(return_value=self.report())
        manager = self.tracked_manager(collector)
        for mode in ("", "deep", "/root", 60, None, []):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                manager.start(mode=mode)
        collector.assert_not_called()
        self.assertEqual(manager.snapshot()["state"], "idle")
        self.assertTrue(manager.start(mode="extended"))
        self.assertEqual(self.finish(manager)["scan_mode"], "extended")
        collector.assert_called_once_with("/home/fixture", **diagnostics.SCAN_LIMITS["extended"])

    def test_global_coverage_issue_and_missing_capacity_are_partial(self) -> None:
        report = self.report()
        for variant in (dict(report, root_filesystem=None),
                        dict(report, issues=[diagnostics._INCOMPLETE + "Project rows omitted."])):
            with self.subTest(report=variant):
                collector = Mock(return_value=variant)
                manager = self.tracked_manager(collector)
                self.assertTrue(manager.start())
                self.assertEqual(self.finish(manager)["state"], "partial")

    def test_initial_failure_and_worker_launch_failure_can_retry(self) -> None:
        manager = self.tracked_manager(Mock(side_effect=OSError("secret exception")))
        self.assertTrue(manager.start())
        failed = self.finish(manager)
        self.assertEqual(failed["state"], "failed")
        self.assertIsNone(failed["report"])
        self.assertNotIn("secret exception", json.dumps(failed))
        with patch.object(diagnostics.threading, "Thread", side_effect=RuntimeError("secret launch")):
            self.assertFalse(manager.start())
        self.assertEqual(manager.snapshot()["state"], "failed")
        self.assertNotIn("secret launch", json.dumps(manager.snapshot()))


if __name__ == "__main__":
    unittest.main()
