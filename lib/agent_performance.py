"""Optional, bounded performance observations around a project recipe."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import math
import os
from pathlib import Path
import re
import shutil
import sys

# The wrapper is launched by absolute path with isolated Python, including when
# installed in a wheel. Import only this installation's libraries, not cwd.
if __name__ == "__main__" and not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.agent_probe import run_probe
from lib.atomic_io import write_json_atomic
from lib.streamed_process import caller_process_group, run_streamed
from lib.validation import validate_filesystem_path, validate_no_control_characters


SYSTEM_PATH = "/usr/local/bin:/usr/bin:/bin"
PROFILES = ("performance", "power-saver")


def _text(path: Path) -> str | None:
    try:
        with path.open(encoding="utf-8") as stream:
            value = stream.read(513).strip()
        if len(value) > 512:
            return None
        return value
    except (OSError, UnicodeError):
        return None


def _number(value: str | None) -> float | None:
    try:
        number = float(value) if value is not None else float("nan")
        return number if math.isfinite(number) else None
    except ValueError:
        return None


def _entries(path: Path, pattern: str, limit: int = 128) -> tuple[list[Path], bool]:
    """Bound enumeration and distinguish absent/unreadable from an empty list."""
    try:
        selected = []
        for index, entry in enumerate(path.iterdir()):
            if index >= 4096:
                return sorted(selected), False
            if re.fullmatch(pattern, entry.name):
                if len(selected) == limit:
                    return sorted(selected), False
                selected.append(entry)
        return sorted(selected), True
    except OSError:
        return [], False


def performance_snapshot(*, sys_root: Path = Path("/sys")) -> dict:
    """Read snapshots, not peak temperatures or an assertion of exclusive load."""
    policies, cpu_complete = _entries(sys_root / "devices/system/cpu/cpufreq", r"policy[0-9]+")
    cpu = [{"policy": policy.name, **{name: _text(policy / name) for name in (
        "scaling_driver", "scaling_governor", "energy_performance_preference",
        "scaling_min_freq", "scaling_max_freq", "scaling_cur_freq",
    )}} for policy in policies]
    monitors, thermal_complete = _entries(sys_root / "class/hwmon", r"hwmon[0-9]+", 32)
    temperatures = []
    for monitor in monitors:
        sensors, complete = _entries(monitor, r"temp[0-9]+_input", 32)
        thermal_complete &= complete
        for sensor in sensors:
            value = _number(_text(sensor))
            temperatures.append({"monitor": monitor.name, "name": _text(monitor / "name"),
                                 "sensor": sensor.name, "label": _text(monitor / sensor.name.replace("_input", "_label")),
                                 "celsius": value / 1000 if value is not None else None})
    cards, gpu_complete = _entries(sys_root / "class/drm", r"card[0-9]+", 32)
    gpu_sysfs = [{"card": card.name, **{name: _text(card / "device" / name) for name in (
        "gpu_busy_percent", "pp_dpm_sclk", "pp_dpm_mclk",
    )}} for card in cards]
    result = {"schema_version": 1, "recorded_at": datetime.now(timezone.utc).isoformat(),
              "cpu_policies": cpu, "cpu_inventory_complete": cpu_complete,
              "temperatures": temperatures, "thermal_inventory_complete": thermal_complete,
              "gpu_sysfs": gpu_sysfs, "gpu_inventory_complete": gpu_complete,
              "power_profile": None, "nvidia_gpus": None, "nvidia_compute_processes": None,
              "processes": None, "process_cpu_scope": "ps-lifetime-average-percent", "issues": []}

    def observe(tool: str, arguments: list[str], name: str) -> str | None:
        executable = shutil.which(tool, path=SYSTEM_PATH)
        if not executable:
            result["issues"].append(name + "_missing")
            return None
        probe = run_probe([executable, *arguments], timeout=3, max_bytes=16384, env={**os.environ, "LC_ALL": "C"})
        if probe.issue:
            result["issues"].append(name + "_" + probe.issue)
            return None
        return probe.output

    profile = observe("powerprofilesctl", ["get"], "power_profile")
    result["power_profile"] = profile.strip() if profile and profile.strip() in (*PROFILES, "balanced") else None
    gpu_fields = ("index", "clocks.current.graphics", "clocks.current.memory", "temperature.gpu", "utilization.gpu", "power.draw")
    gpu = observe("nvidia-smi", ["--query-gpu=" + ",".join(gpu_fields), "--format=csv,noheader,nounits"], "nvidia_gpu")
    if gpu is not None:
        result["nvidia_gpus"] = [dict(zip(gpu_fields, (_number(value.strip()) for value in row)))
                                 for row in list(csv.reader(gpu.splitlines()))[:32] if len(row) == len(gpu_fields)]
        processes = observe("nvidia-smi", ["--query-compute-apps=pid,used_gpu_memory", "--format=csv,noheader,nounits"], "nvidia_compute")
        if processes is not None:
            result["nvidia_compute_processes"] = [{"pid": _number(row[0]), "memory_mib": _number(row[1])}
                                                  for row in list(csv.reader(processes.splitlines()))[:128] if len(row) == 2]
    processes = observe("ps", ["-eo", "pid=,comm=,pcpu=,pmem=", "--sort=-pcpu"], "processes")
    if processes is not None:
        result["processes"] = []
        for line in processes.splitlines()[:20]:
            fields = line.split()
            if len(fields) >= 4:
                result["processes"].append({"pid": _number(fields[0]), "name": " ".join(fields[1:-2])[:128],
                                            "cpu_percent": _number(fields[-2]), "memory_percent": _number(fields[-1])})
    return result


def performance_argv(command: list[str], directory: str, timeout: int, profile: str | None) -> list[str]:
    """Place the active observation inside the optional daemon-owned hold."""
    validate_filesystem_path(directory, must_exist=True)
    if profile is not None and profile not in PROFILES:
        raise ValueError("Power profile must be performance or power-saver")
    argv = [sys.executable, "-I", os.path.abspath(__file__), "--observation",
            os.path.join(directory, "performance-active.json"), "--timeout", str(timeout)]
    if profile:
        argv.extend(("--expect-profile", profile))
    argv.extend(("--", *command))
    if profile:
        executable = shutil.which("powerprofilesctl", path=SYSTEM_PATH)
        if not executable:
            raise ValueError("Requested power-profile hold requires powerprofilesctl")
        argv = [executable, "launch", "--profile", profile, "--reason", "Basaltwater project recipe",
                "--appid", "org.basaltwater.Agent", *argv]
    return argv


def execute_observed(command: list[str], observation: str, timeout: int, profile: str | None) -> int:
    """Run inside the caller's supervised group so cancellation reaches the recipe."""
    validate_filesystem_path(observation)
    if not command or not command[0] or not 1 <= timeout <= 3600:
        raise ValueError("Supply a foreground recipe and timeout from 1 through 3600 seconds")
    if profile is not None and profile not in PROFILES:
        raise ValueError("Power profile must be performance or power-saver")
    for argument in command:
        validate_no_control_characters(argument, "Recipe argument")
    with caller_process_group():
        snapshot = performance_snapshot()
        write_json_atomic(observation, snapshot)
        if profile is not None and snapshot["power_profile"] != profile:
            raise RuntimeError("Requested power profile was not observed inside the hold; recipe was not started")
        return run_streamed(command, timeout=timeout, on_output=lambda chunk: print(chunk, end="", flush=True))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observation", required=True)
    parser.add_argument("--timeout", required=True, type=int)
    parser.add_argument("--expect-profile", choices=PROFILES)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    try:
        code = execute_observed(command, args.observation, args.timeout, args.expect_profile)
        return code if code >= 0 else 128 - code
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Performance session failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
