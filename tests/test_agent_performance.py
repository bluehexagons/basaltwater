"""Performance sessions observe power policy without persistent mutations."""

from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lib import agent_performance as performance
from lib.agent_probe import ProbeResult


class PerformanceTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(performance.os, "sched_getaffinity", return_value={0, 1, 2, 3}))
        self.discover = self.enterContext(patch.object(performance.shutil, "which", side_effect=lambda name, **kwargs: "/usr/bin/" + name))
        self.probe = self.enterContext(patch.object(performance, "run_probe", side_effect=self.observe))

    def write(self, path, content):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    def observe(self, argv, **kwargs):
        self.assertEqual(kwargs["timeout"], 3)
        self.assertEqual(kwargs["max_bytes"], 16384)
        if argv[0].endswith("powerprofilesctl"):
            self.assertEqual(argv[1:], ["get"])
            return ProbeResult("balanced\n")
        if argv[0].endswith("ps"):
            return ProbeResult("123 compiler task 80.2 4.1\n456 compositor 2.0 1.5\n")
        if "--query-compute-apps" in argv[1]:
            return ProbeResult("789, 128\n")
        return ProbeResult("0, 1450, 7000, 65, 20, N/A\n")

    def test_snapshot_observes_cpu_gpu_thermal_and_competing_processes(self):
        self.write("devices/system/cpu/cpufreq/policy0/scaling_governor", "powersave\n")
        self.write("devices/system/cpu/cpufreq/policy0/energy_performance_preference", "balance_power\n")
        self.write("class/hwmon/hwmon0/name", "synthetic_cpu\n")
        self.write("class/hwmon/hwmon0/temp1_input", "62500\n")
        self.write("class/hwmon/hwmon0/temp1_label", "Package\n")
        self.write("class/drm/card0/device/pp_dpm_sclk", "0: 500Mhz\n1: 1500Mhz *\n")
        result = performance.performance_snapshot(sys_root=self.root, proc_root=self.root / "proc")
        self.assertEqual(result["cpu_policies"][0]["energy_performance_preference"], "balance_power")
        self.assertIsNone(result["cpu_policies"][0]["scaling_cur_freq"])
        self.assertEqual(result["temperatures"][0]["celsius"], 62.5)
        self.assertIn("1500Mhz *", result["gpu_sysfs"][0]["pp_dpm_sclk"])
        self.assertEqual(result["nvidia_gpus"][0]["clocks.current.graphics"], 1450)
        self.assertIsNone(result["nvidia_gpus"][0]["power.draw"])
        self.assertEqual(result["nvidia_compute_processes"][0]["pid"], 789)
        self.assertEqual(result["processes"][0]["name"], "compiler task")
        self.assertEqual(result["power_profile"], "balanced")
        self.assertNotIn("cmdline", json.dumps(result))
        json.dumps(result, allow_nan=False)

    def test_unavailable_probes_and_nonfinite_values_stay_unknown(self):
        self.probe.return_value = ProbeResult(issue="probe_timeout")
        self.probe.side_effect = None
        result = performance.performance_snapshot(sys_root=self.root, proc_root=self.root / "proc")
        self.assertFalse(result["cpu_inventory_complete"])
        self.assertIsNone(result["power_profile"])
        self.assertIsNone(result["nvidia_gpus"])
        self.assertIsNone(result["processes"])
        self.assertIn("nvidia_gpu_probe_timeout", result["issues"])
        self.assertEqual(result["workload_limits"]["affinity_cpu_count"], 4)
        self.assertFalse(result["workload_limits"]["cpu_quota"]["available"])
        self.assertIsNone(result["workload_limits"]["cpu_quota"]["unlimited"])
        for text in (None, "N/A", "nan", "inf", "1e999"):
            self.assertIsNone(performance._number(text))
        self.discover.return_value = None
        self.discover.side_effect = None
        self.probe.reset_mock()
        result = performance.performance_snapshot(sys_root=self.root, proc_root=self.root / "proc")
        self.probe.assert_not_called()
        self.assertIn("power_profile_missing", result["issues"])

    def test_vm_limits_pressure_and_memory_evidence_survive_absent_hardware_sensors(self):
        mount = self.root / "private-cgroup"
        leaf = mount / "worker"
        leaf.mkdir(parents=True)
        self.write("proc/self/cgroup", "0::/worker\n")
        self.write("proc/self/mountinfo", f"1 0 0:1 / {mount} rw - cgroup2 cgroup rw\n")
        self.write("private-cgroup/cpu.max", "75000 100000\n")
        self.write("private-cgroup/memory.max", "1073741824\n")
        self.write("private-cgroup/worker/cpu.max", "max 100000\n")
        self.write("private-cgroup/worker/memory.current", "536870912\n")
        self.write("private-cgroup/worker/memory.events", "high 3\noom 0\noom_kill 0\n")
        self.write("proc/pressure/cpu", "some avg10=4.5 avg60=2 avg300=1 total=1234\n")
        self.write("private-cgroup/worker/cpu.pressure", "some avg10=2 avg60=1 avg300=0 total=123\n")
        result = performance.performance_snapshot(sys_root=self.root, proc_root=self.root / "proc")
        limits = result["workload_limits"]
        self.assertFalse(result["cpu_inventory_complete"])
        self.assertEqual(limits["cpu_quota"]["value"], 0.75)
        self.assertEqual(limits["cpu_budget_upper_bound"], 0.75)
        self.assertFalse(limits["cpu_budget_is_concurrency_recommendation"])
        self.assertEqual(limits["memory_max"]["value"], 1073741824)
        self.assertEqual(limits["memory_accounting"]["current_bytes"], 536870912)
        self.assertEqual(limits["memory_accounting"]["events"]["values"]["high"], 3)
        self.assertEqual(limits["pressure"]["guest"]["cpu"]["some"]["total_microseconds"], 1234)
        self.assertEqual(limits["pressure"]["workload"]["cpu"]["some"]["total_microseconds"], 123)
        self.assertNotIn(str(self.root), json.dumps(result))

    def test_hold_wraps_the_observer_and_literal_recipe_without_setting_a_profile(self):
        argv = performance.performance_argv(["./bench", "$(literal)"], str(self.root), 60, "performance")
        self.assertEqual(argv[:4], ["/usr/bin/powerprofilesctl", "launch", "--profile", "performance"])
        self.assertIn("-I", argv)
        self.assertEqual(argv[-3:], ["--", "./bench", "$(literal)"])
        self.assertIn("--expect-profile", argv)
        self.assertNotIn("set", argv)
        ordinary = performance.performance_argv(["./bench"], str(self.root), 60, None)
        self.assertNotIn("launch", ordinary)
        with self.assertRaises(ValueError):
            performance.performance_argv(["./bench"], str(self.root), 60, "balanced")
        self.discover.side_effect = None
        self.discover.return_value = None
        with self.assertRaisesRegex(ValueError, "requires powerprofilesctl"):
            performance.performance_argv(["./bench"], str(self.root), 60, "performance")

    def test_active_snapshot_is_inside_hold_and_child_shares_supervised_group(self):
        snapshot = {"schema_version": 1, "power_profile": "performance"}
        path = self.root / "active.json"
        def observe():
            from lib.streamed_process import _CALLER_OWNS_GROUP
            self.assertTrue(_CALLER_OWNS_GROUP.get())
            return snapshot
        def execute(argv, **kwargs):
            from lib.streamed_process import _CALLER_OWNS_GROUP
            self.assertTrue(_CALLER_OWNS_GROUP.get())
            self.assertEqual(json.loads(path.read_text()), snapshot)
            self.assertEqual(argv, ["./bench", "literal"])
            kwargs["on_output"]("measured\n")
            return 17
        with patch.object(performance, "performance_snapshot", side_effect=observe), \
                patch.object(performance, "run_streamed", side_effect=execute), redirect_stdout(StringIO()):
            self.assertEqual(performance.execute_observed(["./bench", "literal"], str(path), 60, "performance"), 17)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        snapshot["power_profile"] = "balanced"
        with patch.object(performance, "performance_snapshot", return_value=snapshot), \
                patch.object(performance, "run_streamed") as execute, self.assertRaisesRegex(RuntimeError, "not observed"):
            performance.execute_observed(["./bench"], str(path), 60, "performance")
        execute.assert_not_called()

    def test_bounded_sysfs_values_and_incomplete_inventory(self):
        self.write("oversized", "x" * 513)
        self.assertIsNone(performance._text(self.root / "oversized"))
        for name in ("policy1", "policy2", "policy3"):
            (self.root / name).mkdir()
        entries, complete = performance._entries(self.root, "policy[0-9]+", limit=2)
        self.assertEqual(len(entries), 2)
        self.assertFalse(complete)
