"""Pressure cleanup and T3 retention tests with isolated files and processes."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from common.service_tools import user_cache_maintenance as maintenance
from lib.types import BYTES_PER_GB


class TestPressureCleanup(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.context = maintenance.UserContext("agent", str(self.home), os.getuid())
        for name in ("tool_is_active", "process_uses_path"):
            patcher = patch.object(maintenance, name, return_value=False)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_free_space_target_scales_and_is_capped(self):
        for total, free, pressure in (
            (32, 5, True), (32, 7, False), (128, 7, True),
            (128, 9, False), (8, 1, True), (8, 2, False),
        ):
            with self.subTest(total=total, free=free), patch.object(
                maintenance.shutil, "disk_usage",
                return_value=SimpleNamespace(total=total * BYTES_PER_GB, free=free * BYTES_PER_GB),
            ) as disk:
                self.assertEqual(maintenance.storage_pressure(str(self.home)), pressure)
                disk.assert_called_once_with(str(self.home))

    def test_space_is_rechecked_and_tiny_caches_are_retained(self):
        usage = maintenance.CacheUsage(maintenance.USER_CACHE_PRESSURE_MIN_BYTES, time.time())
        with patch.object(maintenance, "storage_pressure", side_effect=[True, False]) as pressure:
            self.assertTrue(maintenance.cache_needs_eviction("/cache", usage, BYTES_PER_GB))
            self.assertFalse(maintenance.cache_needs_eviction("/cache", usage, BYTES_PER_GB))
            self.assertFalse(maintenance.cache_needs_eviction("/cache", maintenance.CacheUsage(10, 0), BYTES_PER_GB))
            self.assertEqual(pressure.call_count, 2)

    def test_unavailable_disk_stats_do_not_force_eviction(self):
        with patch.object(maintenance.shutil, "disk_usage", side_effect=OSError("unavailable")):
            self.assertFalse(maintenance.storage_pressure(str(self.home)))

    def test_pressure_cleans_below_limit_pip_and_go_but_defers_active_tools(self):
        usage = maintenance.CacheUsage(maintenance.USER_CACHE_PRESSURE_MIN_BYTES, time.time())
        for tool in ("pip3", "go"):
            for active in (False, True):
                with self.subTest(tool=tool, active=active), \
                     patch.object(maintenance, "query_cache_path", return_value=(str(self.home / "cache"), tool, None)), \
                     patch.object(maintenance, "inventory_cache", return_value=(usage, None)), \
                     patch.object(maintenance, "storage_pressure", return_value=True), \
                     patch.object(maintenance, "tool_is_active", return_value=active), \
                     patch.object(maintenance, "run_cleanup_command", return_value=None) as clean:
                    if tool == "go":
                        result = maintenance.cleanup_go_cache(
                            self.context, cache_name="Go build", go_env_name="GOCACHE",
                            max_bytes=BYTES_PER_GB, clean_args=["-cache"], dry_run=False,
                        )
                    else:
                        result = maintenance.cleanup_pip_cache(self.context, dry_run=False)
                    self.assertEqual(result, [])
                    self.assertEqual(clean.call_count, 0 if active else 1)

    def test_npm_pressure_is_rechecked_after_verify(self):
        usage = maintenance.CacheUsage(maintenance.USER_CACHE_PRESSURE_MIN_BYTES, time.time())
        for pressure in (False, True):
            with self.subTest(pressure=pressure), \
                 patch.object(maintenance, "query_cache_path", return_value=(str(self.home / "cache"), "npm", None)), \
                 patch.object(maintenance, "inventory_cache", return_value=(usage, None)), \
                 patch.object(maintenance, "cleanup_managed_directory", return_value=[]), \
                 patch.object(maintenance, "storage_pressure", return_value=pressure), \
                 patch.object(maintenance, "run_cleanup_command", return_value=None) as clean:
                self.assertEqual(maintenance.cleanup_npm_cache(self.context, dry_run=False), [])
                self.assertEqual(clean.call_count, 2 if pressure else 1)

    def test_command_reports_measured_reduction_and_dry_run_is_read_only(self):
        cache = self.home / "cache"
        cache.mkdir()
        data = cache / "download"
        data.write_bytes(b"cache")

        def clean(*args, **kwargs):
            data.unlink()
            return subprocess.CompletedProcess([], 0, "", "")

        with patch.object(maintenance, "run_tool_command", side_effect=clean) as command, \
             patch.object(maintenance, "log_event") as log:
            for dry_run in (True, False):
                self.assertIsNone(maintenance.run_cleanup_command(
                    self.context, ["npm", "cache", "clean", "--force"], "test",
                    dry_run=dry_run, cache_path=str(cache),
                ))
                self.assertEqual(data.exists(), dry_run)
            self.assertEqual(command.call_count, 1)
            self.assertEqual(log.call_args.kwargs["retained_mb"], 0)

    def test_electron_cleanup_preserves_fresh_unknown_linked_and_active_files(self):
        root = self.home / ".cache" / "electron" / "hash"
        root.mkdir(parents=True)
        old = root / "electron-v40.0.0-linux-x64.zip"
        fresh = root / "electron-v41.0.0-linux-x64.zip"
        unknown = root / "project.zip"
        for path in (old, fresh, unknown):
            path.write_bytes(b"download")
        old_time = time.time() - 31 * 86400
        os.utime(old, (old_time, old_time))
        os.utime(unknown, (old_time, old_time))
        linked = root / "electron-v39.0.0-linux-x64.zip"
        linked.symlink_to(unknown)
        with patch.object(maintenance, "storage_pressure", return_value=True):
            self.assertEqual(maintenance.cleanup_electron_downloads(self.context, dry_run=True), [])
            self.assertTrue(old.exists())
            with patch.object(maintenance, "tool_is_active", return_value=True):
                self.assertEqual(maintenance.cleanup_electron_downloads(self.context, dry_run=False), [])
                self.assertTrue(old.exists())
            self.assertEqual(maintenance.cleanup_electron_downloads(self.context, dry_run=False), [])
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())
        self.assertTrue(unknown.exists())
        self.assertTrue(linked.is_symlink())


class TestT3RuntimeRetention(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.context = maintenance.UserContext("agent", str(self.home), os.getuid())
        self.runtime = self.home / ".t3" / "runtime"
        self.versions = self.runtime / "versions"
        self.state = self.runtime / "service-state.json"
        for version in range(1, 7):
            root = self.versions / f"0.0.{version}"
            package = root / "node_modules" / "t3"
            (package / "dist").mkdir(parents=True)
            (package / "dist" / "bin.mjs").write_text("// fixture")
            (package / "package.json").write_text(json.dumps({"name": "t3", "version": root.name}))
            (root / ".install-complete").write_text("")
            old = time.time() - 10 * 86400
            for path in [*root.rglob("*"), root]:
                os.utime(path, (old, old))
        self.write_state()
        for name in ("tool_is_active", "process_uses_path"):
            patcher = patch.object(maintenance, name, return_value=False)
            patcher.start()
            self.addCleanup(patcher.stop)

    def write_state(self, **extra):
        self.state.write_text(json.dumps({"protocol": 2, "activeVersion": "0.0.5", **extra}))

    def remaining(self):
        return sorted(path.name for path in self.versions.iterdir())

    def test_retains_active_previous_newer_and_running_versions(self):
        with patch.object(maintenance, "process_uses_path", side_effect=lambda path: path.endswith("0.0.2")):
            self.assertEqual(maintenance.cleanup_t3_runtimes(self.context, dry_run=False), [])
        self.assertEqual(self.remaining(), ["0.0.2", "0.0.4", "0.0.5", "0.0.6"])

    def test_dry_run_preserves_every_version(self):
        self.assertEqual(maintenance.cleanup_t3_runtimes(self.context, dry_run=True), [])
        self.assertEqual(len(self.remaining()), 6)

    def test_current_native_active_runtime_is_validated(self):
        active = self.versions / "0.0.5"
        shutil.rmtree(active / "node_modules")
        binary = active / "t3"
        binary.write_text("#!/bin/sh\n")
        binary.chmod(0o755)
        (active / ".install-complete").write_text("0.0.5")

        self.assertEqual(maintenance.cleanup_t3_runtimes(self.context, dry_run=False), [])
        self.assertEqual(self.remaining(), ["0.0.4", "0.0.5", "0.0.6"])

    def test_pending_unknown_and_changed_state_prevent_removal(self):
        self.write_state(update={"status": "pending", "fromVersion": "0.0.5", "targetVersion": "0.0.6"})
        self.assertEqual(maintenance.cleanup_t3_runtimes(self.context, dry_run=False), [])
        self.assertEqual(len(self.remaining()), 6)
        self.write_state(protocol=3)
        self.assertTrue(maintenance.cleanup_t3_runtimes(self.context, dry_run=False))
        self.assertEqual(len(self.remaining()), 6)
        self.write_state(update={"status": "failed", "fromVersion": "0.0.4", "targetVersion": "0.0.6"})
        self.assertTrue(maintenance.cleanup_t3_runtimes(self.context, dry_run=False))
        self.assertEqual(len(self.remaining()), 6)
        self.write_state()
        states = [{"protocol": 2, "activeVersion": "0.0.5"}, {"protocol": 2, "activeVersion": "0.0.6"}]
        with patch.object(maintenance, "_t3_state", side_effect=states):
            self.assertTrue(maintenance.cleanup_t3_runtimes(self.context, dry_run=False))
        self.assertEqual(len(self.remaining()), 6)

    def test_retains_recovery_reference_recent_and_unrecognized_layouts(self):
        self.write_state(update={"status": "committed", "fromVersion": "0.0.1", "targetVersion": "0.0.5"})
        manifest = self.versions / "0.0.2" / "node_modules" / "t3" / "package.json"
        manifest.write_text('{"name": "unrelated"}')
        (self.versions / "0.0.3" / "recent").write_text("still being installed")
        self.assertEqual(maintenance.cleanup_t3_runtimes(self.context, dry_run=False), [])
        self.assertEqual(len(self.remaining()), 6)

    def test_linked_versions_root_is_not_followed(self):
        real = self.runtime / "real-versions"
        self.versions.rename(real)
        self.versions.symlink_to(real)
        self.assertEqual(maintenance.cleanup_t3_runtimes(self.context, dry_run=False), [])
        self.assertEqual(len(list(real.iterdir())), 6)

    def test_incomplete_and_linked_packages_are_preserved(self):
        (self.versions / "0.0.1" / ".install-complete").unlink()
        manifest = self.versions / "0.0.2" / "node_modules" / "t3" / "package.json"
        outside = self.home / "project.json"
        manifest.rename(outside)
        manifest.symlink_to(outside)
        self.assertEqual(maintenance.cleanup_t3_runtimes(self.context, dry_run=False), [])
        self.assertEqual(self.remaining(), ["0.0.1", "0.0.2", "0.0.4", "0.0.5", "0.0.6"])
        self.assertTrue(outside.exists())


if __name__ == "__main__":
    unittest.main()
