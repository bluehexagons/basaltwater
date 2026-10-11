"""Read-only affinity, visible inherited cgroup limits, and pressure snapshots."""

from __future__ import annotations

from datetime import datetime, timezone
import math
import os
from pathlib import Path
import re
import stat

from lib.validation import validate_filesystem_path


def _read(path: Path) -> str | None:
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            return None
        with os.fdopen(descriptor, encoding="ascii") as stream:
            descriptor = -1
            value = stream.read(65537)
        return value if len(value) <= 65536 else None
    except (OSError, UnicodeError):
        return None
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _unescape(value: str) -> str:
    return re.sub(r"\\(040|011|012|134)", lambda match: chr(int(match[1], 8)), value)


def _hierarchies(proc: Path) -> dict[str, tuple[Path, Path, bool]]:
    memberships = {}
    for line in (_read(proc / "self/cgroup") or "").splitlines():
        fields = line.split(":", 2)
        if len(fields) == 3:
            for controller in fields[1].split(",") if fields[1] else ["v2"]:
                memberships[controller] = fields[2]
    hierarchies = {}
    for line in (_read(proc / "self/mountinfo") or "").splitlines():
        fields = line.split()
        try:
            separator = fields.index("-")
            filesystem = fields[separator + 1]
            if separator < 6 or filesystem not in ("cgroup", "cgroup2"):
                continue
            mount_root, mount = _unescape(fields[3]), Path(_unescape(fields[4]))
            controllers = ["v2"] if filesystem == "cgroup2" else fields[separator + 3].split(",")
            for controller in controllers:
                member = memberships.get(controller)
                if member is None or not member.startswith("/") or ".." in Path(member).parts:
                    continue
                if member == mount_root or member == "/":
                    relative = ""
                elif mount_root == "/" or member.startswith(mount_root.rstrip("/") + "/"):
                    relative = member[len(mount_root):].lstrip("/")
                else:
                    continue
                leaf = mount / relative
                validate_filesystem_path(str(leaf), must_exist=True)
                if not leaf.is_dir() or not leaf.resolve().is_relative_to(mount.resolve()):
                    continue
                hierarchies[controller] = (leaf, mount, mount_root == "/")
        except (IndexError, OSError, ValueError):
            continue
    return hierarchies


def _ancestors(leaf: Path, mount: Path) -> list[Path]:
    paths = []
    current = leaf
    for _ in range(64):
        paths.append(current)
        if current == mount:
            return paths
        current = current.parent
    return []


def _limit(hierarchy: tuple[Path, Path, bool] | None, filename: str, *, cpu: bool = False,
           v1: bool = False) -> dict:
    result = {"available": False, "value": None, "unlimited": None, "ancestry_complete": False}
    if hierarchy is None:
        return result
    leaf, mount, complete = hierarchy
    paths = _ancestors(leaf, mount)
    values = []
    seen = False
    for directory in paths:
        path = directory / filename
        text = _read(path)
        if text is None:
            # Controller files legitimately do not exist in an unconstrained
            # hierarchy root or where a controller is not enabled. An existing
            # unreadable interface, however, makes the inherited result partial.
            try:
                path.stat()
            except FileNotFoundError:
                pass
            except OSError:
                complete = False
            else:
                complete = False
            continue
        try:
            if cpu:
                if v1:
                    quota = text.strip()
                    period = (_read(directory / "cpu.cfs_period_us") or "").strip()
                else:
                    quota, period = text.split()
                period_number = int(period)
                if period_number <= 0 or period_number > 2**63 - 1:
                    raise ValueError("Invalid CPU period")
                if quota not in (("-1",) if v1 else ("max",)):
                    quota_number = int(quota)
                    if not 0 < quota_number <= 2**63 - 1:
                        raise ValueError("Invalid CPU quota")
                    values.append(quota_number / period_number)
            else:
                token = text.strip()
                if token != "max":
                    number = int(token)
                    if not 0 <= number <= 2**63 - 1:
                        raise ValueError("Invalid memory limit")
                    # v1's page-rounded LONG_MAX represents unlimited, not RAM.
                    if not (v1 and number >= 2**60):
                        values.append(number)
            seen = True
        except ValueError:
            complete = False
    result.update(available=seen, value=min(values) if values else None,
                  unlimited=not values if seen and complete else None,
                  ancestry_complete=complete and bool(paths))
    return result


def _pressure(path: Path) -> dict:
    text = _read(path)
    records = {}
    for line in (text or "").splitlines():
        fields = line.split()
        if not fields or fields[0] not in ("some", "full"):
            continue
        try:
            raw = dict(field.split("=", 1) for field in fields[1:])
            values = {name: float(raw[name]) for name in ("avg10", "avg60", "avg300")}
            if not all(math.isfinite(value) and 0 <= value <= 100 for value in values.values()):
                continue
            total = int(raw["total"])
            if not 0 <= total <= 2**63 - 1:
                continue
            records[fields[0]] = {**values, "total_microseconds": total}
        except (ValueError, KeyError):
            continue
    return {"available": bool(records), **records}


def inspect_workload_limits(*, proc_root: str = "/proc") -> dict:
    """Observe this command's workload, not guest capacity or another service."""
    validate_filesystem_path(proc_root)
    proc = Path(proc_root)
    try:
        affinity = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        affinity = None
    hierarchies = _hierarchies(proc)
    v2 = hierarchies.get("v2")
    # Controllers bound to v1 cannot also belong to v2. A hybrid host may
    # expose a unified mount while its CPU or memory controller remains in v1.
    legacy_cpu = hierarchies.get("cpu")
    legacy_memory = hierarchies.get("memory")
    cpu = _limit(legacy_cpu or v2, "cpu.cfs_quota_us" if legacy_cpu else "cpu.max",
                 cpu=True, v1=legacy_cpu is not None)
    memory = _limit(legacy_memory or v2, "memory.limit_in_bytes" if legacy_memory else "memory.max",
                    v1=legacy_memory is not None)
    high = _limit(None if legacy_memory else v2, "memory.high")  # v1 soft_limit is not v2 memory.high.
    bounds = [value for value in (affinity, cpu["value"]) if value is not None]
    budget = min(bounds) if bounds else None
    version = "hybrid" if v2 and len(hierarchies) > 1 else "v2" if v2 else "v1" if hierarchies else "unavailable"
    return {"sampled_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "scope": "current-process", "ancestry_scope": "visible-cgroup-mount",
            "affinity_available": affinity is not None,
            "affinity_cpu_count": affinity, "cgroup_version": version,
            "cpu_quota": cpu, "memory_max": memory, "memory_high": high,
            "cpu_budget_upper_bound": budget, "cpu_budget_is_concurrency_recommendation": False,
            "pressure": {"guest": {name: _pressure(proc / "pressure" / name) for name in ("cpu", "memory", "io")},
                         "workload": {name: _pressure(v2[0] / (name + ".pressure")) if v2 else {"available": False}
                                      for name in ("cpu", "memory", "io")}}}


def sanitize_workload_limits(value: object) -> dict:
    """Keep numeric measurements, fixed scopes, and availability flags only."""
    record = value if isinstance(value, dict) else {}
    def number(value: object) -> int | float | None:
        return value if type(value) in (int, float) and 0 <= value <= 2**63 - 1 and math.isfinite(value) else None
    limits = {}
    for name in ("cpu_quota", "memory_max", "memory_high"):
        item = record.get(name) if isinstance(record.get(name), dict) else {}
        limits[name] = {"available": item.get("available") is True, "value": number(item.get("value")),
                        "unlimited": item.get("unlimited") if type(item.get("unlimited")) is bool else None,
                        "ancestry_complete": item.get("ancestry_complete") is True}
    pressure = {}
    raw_pressure = record.get("pressure") if isinstance(record.get("pressure"), dict) else {}
    for scope in ("guest", "workload"):
        raw_scope = raw_pressure.get(scope) if isinstance(raw_pressure.get(scope), dict) else {}
        pressure[scope] = {}
        for resource in ("cpu", "memory", "io"):
            item = raw_scope.get(resource) if isinstance(raw_scope.get(resource), dict) else {}
            groups = {}
            for name in ("some", "full"):
                group = item.get(name)
                if isinstance(group, dict):
                    groups[name] = {key: number(group.get(key)) for key in ("avg10", "avg60", "avg300", "total_microseconds")}
            pressure[scope][resource] = {"available": item.get("available") is True, **groups}
    timestamp = record.get("sampled_at")
    return {"sampled_at": timestamp if isinstance(timestamp, str) and re.fullmatch(r"[0-9T:.Z+\-]{20,40}", timestamp) else None,
            "scope": "current-process", "ancestry_scope": "visible-cgroup-mount",
            "affinity_available": record.get("affinity_available") is True,
            "affinity_cpu_count": number(record.get("affinity_cpu_count")),
            "cgroup_version": record.get("cgroup_version") if record.get("cgroup_version") in ("v1", "v2", "hybrid") else "unavailable",
            **limits, "cpu_budget_upper_bound": number(record.get("cpu_budget_upper_bound")),
            "cpu_budget_is_concurrency_recommendation": False, "pressure": pressure}
