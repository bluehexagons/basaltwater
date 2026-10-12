"""Memory samples retain service scope, cumulative OOM evidence and privacy."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from lib import agent_resources as resources
from lib.agent_cli import inspect_host_readiness
from lib.agent_readiness import build_agent_readiness_record
from lib.agent_support import build_agent_support_bundle
from lib.types import BYTES_PER_GB


class MemoryAccountingTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.proc = self.root / "proc"
        (self.proc / "self").mkdir(parents=True)
        self.mount = self.root / "private-cgroup-mount"
        self.service = self.mount / "private-service"
        self.worker = self.mount / "worker"
        self.service.mkdir(parents=True)
        self.worker.mkdir()
        (self.proc / "self/cgroup").write_text("0::/worker\n")
        self.mountinfo = self.proc / "self/mountinfo"
        self.mountinfo.write_text(f"1 0 0:1 / {self.mount} rw - cgroup2 cgroup rw\n")

    def files(self, directory=None, **values):
        for name, value in values.items():
            ((directory or self.service) / name).write_text(value)

    def inspect(self):
        return resources.inspect_service_memory("/private-service", proc_root=str(self.proc))

    def test_service_sample_is_distinct_from_invoking_process_and_inherits_limits(self):
        self.files(**{"memory.current": "900", "memory.stat": "anon 100\nfile 800\n",
                      "memory.swap.current": "0", "memory.events": "oom 2\noom_kill 1\nhigh 4\n",
                      "memory.max": "4000", "memory.high": "3000",
                      "memory.pressure": "some avg10=0 avg60=0 avg300=0 total=4\n"})
        self.files(self.mount, **{"memory.max": "2000", "memory.high": "1500"})
        self.files(self.worker, **{"memory.current": "10"})
        result = self.inspect()
        self.assertEqual((result["current_bytes"], result["anonymous_bytes"], result["file_bytes"]), (900, 100, 800))
        self.assertEqual(result["events"]["values"]["oom_kill"], 1)
        self.assertEqual(result["events"]["scope"], "hierarchical")
        self.assertTrue(result["events"]["cumulative"])
        self.assertEqual(result["memory_max"]["value"], 2000)
        self.assertEqual(result["memory_high"]["value"], 1500)
        self.assertEqual(result["pressure"]["some"]["avg10"], 0)
        self.assertTrue(result["sampled_at"].endswith("Z"))
        self.assertNotIn("private", json.dumps(result))
        with patch.object(resources.os, "sched_getaffinity", return_value={0}):
            workload = resources.inspect_workload_limits(proc_root=str(self.proc))
        self.assertEqual(workload["memory_accounting"]["current_bytes"], 10)
        self.assertNotEqual(workload["memory_accounting"]["counter_epoch"], result["counter_epoch"])

    def test_localevents_and_legacy_counters_have_honest_scopes(self):
        self.mountinfo.write_text(f"1 0 0:1 / {self.mount} rw - cgroup2 cgroup rw,memory_localevents\n")
        self.files(**{"memory.events": "oom_kill 0\n"})
        self.assertEqual(self.inspect()["events"]["scope"], "local")
        self.mountinfo.write_text(f"1 0 0:1 / {self.mount} rw - cgroup cgroup rw,memory\n")
        self.files(**{"memory.usage_in_bytes": "500", "memory.stat": "total_rss 200\ntotal_cache 300\n",
                      "memory.oom_control": "under_oom 0\noom_kill 1\n"})
        result = self.inspect()
        self.assertEqual(result["cgroup_version"], "v1")
        self.assertEqual((result["anonymous_bytes"], result["file_bytes"]), (200, 300))
        self.assertEqual(result["events"]["values"]["oom_kill"], 1)
        self.assertEqual(result["events"]["scope"], "local")
        self.assertIsNone(result["events"]["values"]["oom"])
        self.assertIsNone(result["swap_bytes"])
        self.files(**{"memory.stat": "rss 200\ncache 300\n"})
        self.assertIsNone(self.inspect()["anonymous_bytes"])

    def test_hybrid_service_reads_the_legacy_memory_controller(self):
        legacy = self.root / "legacy-memory"
        leaf = legacy / "private-service"
        leaf.mkdir(parents=True)
        with self.mountinfo.open("a") as stream:
            stream.write(f"2 0 0:2 / {legacy} rw - cgroup cgroup rw,memory\n")
        self.files(**{"memory.current": "9999"})
        self.files(leaf, **{"memory.usage_in_bytes": "500", "memory.stat": "total_rss 200\ntotal_cache 300\n"})
        self.assertEqual(self.inspect()["current_bytes"], 500)

    def test_missing_malformed_and_replaced_cgroups_remain_unknown(self):
        for group in (None, "/", "relative", "/../escape", "/bad\nname", "/absent"):
            with self.subTest(group=group):
                result = resources.inspect_service_memory(group, proc_root=str(self.proc))
                self.assertFalse(result["available"])
                self.assertIsNone(result["events"]["values"]["oom_kill"])
        self.files(**{"memory.stat": "anon -1\nfile 5\nfile 6\n", "memory.events": "oom_kill invalid\n",
                      "memory.current": str(2**64 - 1)})
        self.assertFalse(self.inspect()["available"])
        read = resources._read
        for name in ("memory.max", "memory.events"):
            self.files(**{"memory.current": "500", "memory.max": "4000", "memory.events": "oom_kill 1\n"})
            def replace(path):
                value = read(path)
                if path == self.service / name:
                    self.service.rename(self.mount / ("retained-old-service-" + name))
                    self.service.mkdir()
                return value
            with self.subTest(interface=name), patch.object(resources, "_read", side_effect=replace):
                result = self.inspect()
            self.assertFalse(result["available"])
            self.assertIsNone(result["counter_epoch"])
            self.assertFalse(result["memory_max"]["available"])

    def test_privacy_filters_service_and_readiness_fields_without_losing_oom_evidence(self):
        raw = {"memory_current_bytes": 7, "memory_peak_bytes": 10, "invocation_id": "a" * 32,
               "path": "private", "environment": "private", "memory_accounting": {
                   "sampled_at": "private", "counter_epoch": "private", "anonymous_bytes": 3,
                   "file_bytes": "private", "events": {"scope": "hierarchical", "values": {"oom_kill": 1, "private": "private"}}}}
        sanitized = resources.sanitize_t3_resources(raw)
        self.assertEqual(sanitized["memory_accounting"]["events"]["values"]["oom_kill"], 1)
        self.assertIsNone(sanitized["peak_sampled_at"])
        self.assertIsNone(sanitized["memory_capacity_at_peak_bytes"])
        self.assertNotIn("private", json.dumps(sanitized))
        host = {"capability": "host", "healthy": True, "status": "ok", "memory": {}, "disk": {},
                "agent_storage": {"size_bytes": {}, "codex_release_count": 0}, "t3_service": raw,
                "maintenance": {}, "maintenance_hold": {}, "reboot_pending": False, "warnings": [], "errors": []}
        record = build_agent_readiness_record([], [host], trigger="manual", boot_id_path=str(self.root / "missing-boot"))
        self.assertNotIn("private", json.dumps(record))
        browser = {"installed": False, "registrations": {}, "configured": False, "smoke_test": None, "healthy": None}
        with patch("lib.agent_support.inspect_installation_provenance", return_value={}), \
                patch("lib.agent_cli.inspect_agent_tools", return_value=[]), \
                patch("lib.agent_cli.inspect_host_readiness", return_value=host), \
                patch("lib.agent_cli.inspect_browser_automation", return_value=browser), \
                patch("lib.agent_cli.inspect_development_readiness", return_value={}), \
                patch("lib.agent_cli.inspect_t3code", return_value={"healthy": True, "checks": {}, "version": None}):
            bundle = build_agent_support_bundle(str(self.root))
        self.assertEqual(bundle["host"]["t3_service"]["memory_accounting"]["events"]["values"]["oom_kill"], 1)
        self.assertNotIn("private", json.dumps(bundle))

    def test_ballooned_memory_does_not_compare_historical_peak_with_current_ram(self):
        for peak, warning in ((3 * BYTES_PER_GB, False), (5 * BYTES_PER_GB, True)):
            with self.subTest(peak=peak), \
                    patch("lib.agent_cli._read_meminfo", return_value={"MemTotal": 2 * BYTES_PER_GB, "MemAvailable": BYTES_PER_GB}), \
                    patch("lib.agent_cli._read_memory_capacity_bytes", return_value=4 * BYTES_PER_GB), \
                    patch("lib.agent_cli._agent_storage_inventory", return_value={"paths": {}, "size_bytes": {}, "codex_release_count": 0}), \
                    patch("lib.agent_cli._systemd_properties", return_value={"MemoryCurrent": str(BYTES_PER_GB), "MemoryPeak": str(peak), "NRestarts": "1", "InvocationID": "a" * 32}), \
                    patch("lib.agent_cli.inspect_service_memory", return_value=self.inspect()), \
                    patch("lib.agent_cli.inspect_workload_limits", return_value={}), \
                    patch("lib.agent_cli._maintenance_status", return_value={"units": {}, "warnings": [], "errors": []}), \
                    patch("lib.agent_cli.inspect_agent_maintenance", return_value={"status": "inactive"}), \
                    patch("lib.agent_cli.shutil.disk_usage", return_value=SimpleNamespace(total=64 * BYTES_PER_GB, used=16 * BYTES_PER_GB, free=48 * BYTES_PER_GB)), \
                    patch("lib.agent_cli.os.path.exists", return_value=False):
                result = inspect_host_readiness(str(self.root))
            self.assertTrue(result["memory"]["ballooned"])
            self.assertTrue(result["healthy"])
            self.assertEqual(bool(result["warnings"]), warning)
            self.assertEqual(result["t3_service"]["restart_count"], 1)
            self.assertIsNone(result["t3_service"]["memory_capacity_at_peak_bytes"])
            if warning:
                self.assertIn("peak-time capacity is unknown", result["warnings"][0])
