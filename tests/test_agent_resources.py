"""Workload limits follow visible ancestors and never expose cgroup identities."""

from __future__ import annotations

import json
import argparse
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lib import agent_resources as resources
from lib.agent_readiness import build_agent_readiness_record
from lib.agent_cli import add_agent_subparser, run_agent_command


class ResourceTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.proc = self.root / "proc"
        (self.proc / "self").mkdir(parents=True)
        self.mount = self.root / "private-cgroup-name"
        self.leaf = self.mount / "parent/worker"
        self.leaf.mkdir(parents=True)
        self.enterContext(patch.object(resources.os, "sched_getaffinity", return_value={0, 1, 2, 3, 4, 5}))

    def hierarchy(self, version="v2", mount_root="/"):
        member = "/parent/worker" if mount_root == "/" else mount_root + "/parent/worker"
        (self.proc / "self/cgroup").write_text("0::" + member if version == "v2" else "2:cpu,memory:" + member)
        filesystem = "cgroup2" if version == "v2" else "cgroup"
        (self.proc / "self/mountinfo").write_text(f"1 0 0:1 {mount_root} {self.mount} rw - {filesystem} cgroup rw,cpu,memory\n")

    def write(self, directory, **files):
        for filename, value in files.items():
            (directory / filename).write_text(value)

    def inspect(self):
        return resources.inspect_workload_limits(proc_root=str(self.proc))

    def test_v2_tighter_parent_cpu_and_memory_limits(self):
        self.hierarchy()
        self.write(self.leaf, **{"cpu.max": "400000 100000", "memory.max": "4000", "memory.high": "max"})
        self.write(self.leaf.parent, **{"cpu.max": "150000 100000", "memory.max": "3000", "memory.high": "2000"})
        self.write(self.mount, **{"cpu.max": "max 100000", "memory.max": "max", "memory.high": "max"})
        result = self.inspect()
        self.assertEqual(result["affinity_cpu_count"], 6)
        self.assertEqual(result["cpu_quota"]["value"], 1.5)
        self.assertEqual(result["cpu_budget_upper_bound"], 1.5)
        self.assertTrue(result["cpu_quota"]["ancestry_complete"])
        self.assertEqual(result["memory_max"]["value"], 3000)
        self.assertEqual(result["memory_high"]["value"], 2000)
        self.assertNotIn("private-cgroup-name", json.dumps(result))

    def test_v1_limits_and_unlimited_sentinel_do_not_invent_memory_high(self):
        self.hierarchy("v1")
        self.write(self.leaf, **{"cpu.cfs_quota_us": "-1", "cpu.cfs_period_us": "100000",
                                "memory.limit_in_bytes": str(2**63 - 4096), "memory.soft_limit_in_bytes": "100"})
        self.write(self.leaf.parent, **{"cpu.cfs_quota_us": "50000", "cpu.cfs_period_us": "100000", "memory.limit_in_bytes": "2048"})
        result = self.inspect()
        self.assertEqual(result["cgroup_version"], "v1")
        self.assertEqual(result["cpu_quota"]["value"], 0.5)
        self.assertEqual(result["memory_max"]["value"], 2048)
        self.assertFalse(result["memory_high"]["available"])

    def test_hybrid_hosts_select_cpu_and_memory_controllers_independently(self):
        for controllers in ("cpu", "memory", "cpu,memory"):
            with self.subTest(controllers=controllers):
                self.hierarchy()
                self.write(self.leaf, **{"cpu.max": "200000 100000", "memory.max": "4096", "memory.high": "3072"})
                for filename in ("cpu.max", "memory.max", "memory.high"):
                    if filename.split(".", 1)[0] in controllers.split(","):
                        (self.leaf / filename).unlink()
                legacy = self.root / ("legacy-" + controllers)
                worker = legacy / "parent/worker"
                worker.mkdir(parents=True)
                self.write(worker, **{"cpu.cfs_quota_us": "100000", "cpu.cfs_period_us": "100000",
                                      "memory.limit_in_bytes": "2048", "memory.soft_limit_in_bytes": "100"})
                self.write(worker.parent, **{"cpu.cfs_quota_us": "50000", "cpu.cfs_period_us": "100000",
                                             "memory.limit_in_bytes": "1024"})
                with (self.proc / "self/cgroup").open("a") as stream:
                    stream.write(f"\n2:{controllers}:/parent/worker\n")
                with (self.proc / "self/mountinfo").open("a") as stream:
                    stream.write(f"2 0 0:2 / {legacy} rw - cgroup cgroup rw,{controllers}\n")
                result = self.inspect()
                self.assertEqual(result["cgroup_version"], "hybrid")
                self.assertEqual(result["cpu_quota"]["value"], 0.5 if "cpu" in controllers else 2)
                self.assertEqual(result["cpu_budget_upper_bound"], 0.5 if "cpu" in controllers else 2)
                self.assertEqual(result["memory_max"]["value"], 1024 if "memory" in controllers else 4096)
                self.assertEqual(result["memory_high"]["available"], "memory" not in controllers)
                sanitized = resources.sanitize_workload_limits(result)
                record = build_agent_readiness_record([], [{"capability": "host", "healthy": True,
                                                           "workload_limits": result}], trigger="manual")
                self.assertEqual(sanitized["cgroup_version"], "hybrid")
                self.assertEqual(sanitized["cpu_quota"]["value"], result["cpu_quota"]["value"])
                self.assertIn('"cgroup_version": "hybrid"', json.dumps(record))
                self.assertNotIn(str(self.root), json.dumps(record))

    def test_known_cpu_quota_remains_an_upper_bound_when_affinity_is_unavailable(self):
        self.hierarchy()
        self.write(self.leaf, **{"cpu.max": "75000 100000"})
        with patch.object(resources.os, "sched_getaffinity", side_effect=OSError):
            result = self.inspect()
            self.assertFalse(result["affinity_available"])
            self.assertIsNone(result["affinity_cpu_count"])
            self.assertEqual(result["cpu_budget_upper_bound"], 0.75)
            self.assertFalse(result["cpu_budget_is_concurrency_recommendation"])
            self.write(self.leaf, **{"cpu.max": "max 100000"})
            self.assertIsNone(self.inspect()["cpu_budget_upper_bound"])

    def test_missing_malformed_and_hidden_ancestry_are_unknown(self):
        result = self.inspect()
        self.assertEqual(result["cgroup_version"], "unavailable")
        self.assertFalse(result["cpu_quota"]["available"])
        self.assertIsNone(result["cpu_quota"]["unlimited"])
        self.hierarchy(mount_root="/hidden-parent")
        self.write(self.leaf, **{"cpu.max": "max 100000", "memory.max": "broken", "memory.high": "-1"})
        result = self.inspect()
        self.assertFalse(result["cpu_quota"]["ancestry_complete"])
        self.assertIsNone(result["cpu_quota"]["unlimited"])
        self.assertFalse(result["memory_max"]["available"])
        self.assertFalse(result["memory_high"]["available"])
        self.write(self.leaf, **{"cpu.max": "max 0"})
        self.assertFalse(self.inspect()["cpu_quota"]["available"])
        with patch.object(resources.os, "sched_getaffinity", side_effect=OSError):
            self.assertFalse(self.inspect()["affinity_available"])

    def test_pressure_has_timestamp_scope_and_rejects_bad_counters(self):
        self.hierarchy()
        (self.proc / "pressure").mkdir()
        (self.proc / "pressure/cpu").write_text("some avg10=1.50 avg60=0.25 avg300=0.10 total=1234\n")
        self.write(self.leaf, **{"memory.pressure": "full avg10=nan avg60=0 avg300=0 total=1\n",
                                "io.pressure": "some avg10=0 avg60=0 avg300=0 total=-1\n"})
        result = self.inspect()
        self.assertTrue(result["sampled_at"].endswith("Z"))
        self.assertEqual(result["pressure"]["guest"]["cpu"]["some"]["total_microseconds"], 1234)
        self.assertFalse(result["pressure"]["workload"]["memory"]["available"])
        self.assertFalse(result["pressure"]["workload"]["io"]["available"])

    def test_privacy_filtering_includes_only_numeric_known_fields(self):
        result = self.inspect()
        result.update(cgroup_path="/private/group", sampled_at="private timestamp")
        result["cpu_quota"].update(value="private limit", path="private")
        result["pressure"]["guest"]["cpu"] = {"available": True, "path": "private", "some": {"avg10": "private", "raw": "private"}}
        output = resources.sanitize_workload_limits(result)
        record = build_agent_readiness_record([], [{"capability": "host", "healthy": True, "workload_limits": result}], trigger="manual")
        self.assertNotIn("private", json.dumps(output))
        self.assertNotIn("private", json.dumps(record))
        self.assertIsNone(output["cpu_quota"]["value"])

    def test_invalid_and_escaping_mounts_do_not_read_outside_hierarchy(self):
        self.hierarchy()
        (self.proc / "self/cgroup").write_text("0::/../../outside")
        self.assertEqual(self.inspect()["cgroup_version"], "unavailable")
        (self.proc / "self/mountinfo").write_text("malformed mount\n")
        self.assertEqual(self.inspect()["cgroup_version"], "unavailable")

    def test_standalone_resource_command_does_not_require_vm_maintenance(self):
        parser = argparse.ArgumentParser()
        add_agent_subparser(parser.add_subparsers(dest="command"))
        args = parser.parse_args(["agent", "resources", "--json"])
        with patch("lib.agent_cli.inspect_workload_limits", return_value=self.inspect()), \
                patch("lib.agent_cli._maintenance_status", side_effect=AssertionError("VM-only check")), \
                redirect_stdout(output := StringIO()):
            self.assertEqual(run_agent_command(args), 0)
        self.assertEqual(json.loads(output.getvalue())["affinity_cpu_count"], 6)

    def test_unreadable_parent_limit_does_not_claim_complete_ancestry(self):
        self.hierarchy()
        self.write(self.leaf, **{"memory.max": "4096"})
        self.write(self.leaf.parent, **{"memory.max": "2048"})
        original_read = resources._read
        with patch.object(resources, "_read", side_effect=lambda path: None if path == self.leaf.parent / "memory.max" else original_read(path)):
            result = self.inspect()
        self.assertEqual(result["memory_max"]["value"], 4096)
        self.assertFalse(result["memory_max"]["ancestry_complete"])
