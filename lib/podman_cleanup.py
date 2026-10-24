"""Bounded rootless Podman image cleanup using native reference-aware pruning."""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import stat
import subprocess
import time

from lib.maintenance_defaults import (
    PODMAN_IMAGE_PRESSURE_RETENTION_DAYS,
    PODMAN_IMAGE_RETENTION_DAYS,
    PODMAN_KEEP_LABEL,
    USER_CACHE_FREE_MAX_BYTES,
    USER_CACHE_FREE_MIN_BYTES,
)
from lib.streamed_process import run_captured
from lib.validation import validate_filesystem_path


_COMMAND_TIMEOUT = 30
_PRUNE_TIMEOUT = 300
_OUTPUT_LIMIT = 256 * 1024
_PROCESS_LIMIT = 16384
_PROCESS_SECONDS = 3
_PREVIEW_LIMIT = 12
_ID = re.compile(r"(?:sha256:)?[0-9a-f]{12,64}\Z")
_PROCESS_NAMES = frozenset((
    "podman", "buildah", "buildkitd", "buildctl", "conmon", "crun", "runc",
    "containerd", "containers-storage",
))
_SIZE = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*(B|kB|KB|MB|GB|TB|KiB|MiB|GiB|TiB)\Z")
_NOTES = [
    "Podman image prune protects tagged and referenced images at prune time.",
    "Image size totals are estimates; shared layers can make them overstate reclaimed space.",
    "Native prune may also remove newly dangling parents; candidate IDs are preflight evidence, not an exact deletion set.",
    "A fresh process snapshot cannot exclude a build starting immediately afterwards.",
]


class _Unknown(Exception):
    """An observation was incomplete or did not match the expected protocol."""


def _integer(value: object) -> int | None:
    if type(value) is int and 0 <= value <= 2**63 - 1:
        return value
    if isinstance(value, str) and value.isdecimal() and len(value) < 20:
        parsed = int(value)
        return parsed if parsed <= 2**63 - 1 else None
    return None


def _image_size(value: object) -> int | None:
    if (parsed := _integer(value)) is not None:
        return parsed
    if not isinstance(value, str):
        return None
    match = _SIZE.fullmatch(value.strip())
    if not match:
        return None
    unit = match[2]
    power = 0 if unit == "B" else "KMGT".index(unit[0].upper()) + 1
    result = float(match[1]) * (1024 if "iB" in unit else 1000) ** power
    return int(result) if result <= 2**63 - 1 else None


def _base_result(apply: bool) -> dict:
    return {
        "status": "failed", "issue": None, "issues": [], "apply": apply,
        "pressure": None, "retention_days": None, "cutoff_epoch": None,
        "pressure_threshold_bytes": None,
        "candidate_count": 0, "candidate_ids": [], "candidate_preview_truncated": False,
        "image_estimate_bytes": None, "free_bytes_before": None, "free_bytes_after": None,
        "free_bytes_change": None, "cleanup_executed": False, "cleanup_attempted": False,
        "notes": list(_NOTES),
    }


def _issue(result: dict, status: str, code: str) -> dict:
    result["status"], result["issue"] = status, code
    result["issues"].append(code)
    return result


def _home_and_identity() -> tuple[str, int]:
    uid, euid = os.getuid(), os.geteuid()
    if uid != euid or euid == 0:
        raise _Unknown("unsafe_account")
    home = pwd.getpwuid(euid).pw_dir
    validate_filesystem_path(home, must_exist=True)
    if not os.path.isabs(home) or os.path.normpath(home) != home or os.path.realpath(home) != home:
        raise _Unknown("unsafe_home")
    info = os.stat(home, follow_symlinks=False)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != euid or info.st_mode & 0o022:
        raise _Unknown("unsafe_home")
    return home, euid


def _lock_file(home: str, uid: int) -> int:
    """Open a private per-user lock without following or repairing unsafe paths."""
    directory = Path(home) / ".local" / "state" / "basaltwater" / "locks"
    current = Path(home)
    for component in (".local", "state", "basaltwater", "locks"):
        current = current / component
        try:
            current.mkdir(mode=0o700)
        except FileExistsError:
            pass
        info = os.stat(current, follow_symlinks=False)
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or info.st_mode & 0o022
                or os.path.realpath(current) != str(current)):
            raise _Unknown("unsafe_lock_path")
        if component in ("basaltwater", "locks") and info.st_mode & 0o077:
            raise _Unknown("unsafe_lock_path")
    lock_path = directory / "podman-image-cleanup.lock"
    validate_filesystem_path(str(lock_path))
    descriptor = os.open(
        lock_path, os.O_CREAT | os.O_RDWR | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600,
    )
    info = os.fstat(descriptor)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_nlink != 1
            or info.st_mode & 0o077):
        os.close(descriptor)
        raise _Unknown("unsafe_lock_file")
    return descriptor


def _graph_root(value: object, home: str, uid: int) -> str:
    if not isinstance(value, str):
        raise _Unknown("invalid_graph_root")
    validate_filesystem_path(value, must_exist=True)
    if (not os.path.isabs(value) or os.path.normpath(value) != value
            or value == home or os.path.commonpath((home, value)) != home
            or os.path.realpath(value) != value):
        raise _Unknown("unsafe_graph_root")
    info = os.stat(value, follow_symlinks=False)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid:
        raise _Unknown("unsafe_graph_root")
    return value


def _command(executable: str, home: str, args: list[str], *,
             timeout: int = _COMMAND_TIMEOUT) -> subprocess.CompletedProcess[str]:
    # Do not inherit Podman-specific environment overrides. Remote selections
    # are detected before reaching this helper and cause a safe refusal.
    environment = {"HOME": home, "PATH": "/usr/local/bin:/usr/bin:/bin", "LC_ALL": "C"}
    return run_captured([executable, "--remote=false", *args], cwd=home, env=environment,
                        timeout=timeout, max_bytes=_OUTPUT_LIMIT)


def _process_activity(uid: int) -> tuple[str, str | None]:
    deadline = time.monotonic() + _PROCESS_SECONDS
    try:
        with os.scandir("/proc") as processes:
            for index, entry in enumerate(processes):
                if index >= _PROCESS_LIMIT or time.monotonic() >= deadline:
                    raise _Unknown("process_inventory_limit")
                if not entry.name.isdecimal():
                    continue
                try:
                    info = entry.stat(follow_symlinks=False)
                    if info.st_uid != uid:
                        continue
                    with open(os.path.join(entry.path, "comm"), "rb") as source:
                        raw = source.read(65)
                    if len(raw) > 64 or not raw.endswith(b"\n"):
                        raise _Unknown("process_metadata_limit")
                    name = raw[:-1].decode("ascii", errors="strict")
                    if name in _PROCESS_NAMES:
                        return "deferred_active", "active_container_workload"
                except FileNotFoundError as exc:
                    raise _Unknown("process_inventory_changed") from exc
        if time.monotonic() >= deadline:
            raise _Unknown("process_inventory_limit")
    except _Unknown:
        raise
    except (OSError, UnicodeError) as exc:
        raise _Unknown("process_inventory_incomplete") from exc
    return "eligible", None


def _inventory(output: str, *, keep_label: str, cutoff: int) -> tuple[list[dict], int | None]:
    try:
        rows = json.loads(output)
    except (ValueError, RecursionError) as exc:
        raise _Unknown("invalid_image_inventory") from exc
    if not isinstance(rows, list) or len(rows) > 100000:
        raise _Unknown("invalid_image_inventory")
    candidates: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            raise _Unknown("invalid_image_inventory")
        created, containers = _integer(row.get("Created")), _integer(row.get("Containers"))
        tags, labels = row.get("RepoTags"), row.get("Labels")
        identifier = row.get("Id", row.get("ID"))
        if (created is None or containers is None or not (tags is None or isinstance(tags, list))
                or not (labels is None or isinstance(labels, dict))
                or not isinstance(identifier, str) or not _ID.fullmatch(identifier)):
            raise _Unknown("invalid_image_inventory")
        # The native selector already restricts the list to dangling images;
        # apply the remaining eligibility rules before reporting estimates.
        if created > cutoff or tags not in (None, []) or containers != 0:
            continue
        if labels is not None and keep_label in labels:
            continue
        candidates.append({"id": identifier, "size": _image_size(row.get("Size"))})
    estimate = sum(row["size"] for row in candidates if row["size"] is not None)
    return candidates, estimate if all(row["size"] is not None for row in candidates) else None


def clean_podman_images(*, apply: bool = False) -> dict:
    """Preview or prune old, unreferenced rootless Podman images safely.

    Native Podman image prune is the only mutation. It rechecks image
    references atomically and never removes containers, volumes, build cache,
    tagged images, or images newer than the selected cutoff.
    """
    if type(apply) is not bool:
        raise TypeError("apply must be a boolean")
    result = _base_result(apply)
    lock_descriptor = None
    locked = False
    try:
        if any(os.environ.get(key) for key in ("CONTAINER_HOST", "CONTAINER_CONNECTION")):
            return _issue(result, "deferred_unknown", "remote_selection_unsupported")
        home, uid = _home_and_identity()
        executable = shutil.which("podman", path="/usr/local/bin:/usr/bin:/bin")
        if executable is None:
            result["status"] = "absent"
            return result
        lock_descriptor = _lock_file(home, uid)
        try:
            fcntl.flock(lock_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except BlockingIOError:
            return _issue(result, "deferred_active", "cleanup_already_running")

        info_result = _command(executable, home, ["info", "--format", "json"])
        if info_result.returncode != 0:
            return _issue(result, "deferred_unknown", "podman_info_failed")
        try:
            info = json.loads(info_result.stdout)
        except (ValueError, RecursionError) as exc:
            raise _Unknown("invalid_podman_info") from exc
        if not isinstance(info, dict):
            raise _Unknown("invalid_podman_info")
        host, store = info.get("host"), info.get("store")
        if not isinstance(host, dict) or not isinstance(store, dict):
            raise _Unknown("invalid_podman_info")
        if host.get("serviceIsRemote") is True:
            return _issue(result, "deferred_unknown", "remote_selection_unsupported")
        if host.get("security", {}).get("rootless") is not True:
            return _issue(result, "deferred_unknown", "rootful_engine_unsupported")
        root = _graph_root(store.get("graphRoot"), home, uid)
        container_store = store.get("containerStore")
        if not isinstance(container_store, dict):
            raise _Unknown("invalid_podman_info")
        running = _integer(container_store.get("running"))
        if running is None:
            raise _Unknown("invalid_podman_info")
        activity, reason = _process_activity(uid)
        if activity != "eligible":
            return _issue(result, "deferred_active", reason or "active_container_workload")
        if running:
            return _issue(result, "deferred_active", "running_containers")

        disk = shutil.disk_usage(root)
        total, free = _integer(disk.total), _integer(disk.free)
        if total is None or total <= 0 or free is None:
            raise _Unknown("filesystem_measurement_unavailable")
        result["free_bytes_before"] = free
        threshold = min(USER_CACHE_FREE_MAX_BYTES, max(USER_CACHE_FREE_MIN_BYTES, total // 5))
        pressure = free < threshold
        retention = PODMAN_IMAGE_PRESSURE_RETENTION_DAYS if pressure else PODMAN_IMAGE_RETENTION_DAYS
        cutoff = max(0, int(time.time()) - retention * 86400)
        result.update(pressure=pressure, retention_days=retention, cutoff_epoch=cutoff,
                      pressure_threshold_bytes=threshold)

        images = _command(executable, home, ["image", "ls", "--filter", "dangling=true", "--format", "json"])
        if images.returncode != 0:
            return _issue(result, "deferred_unknown", "image_inventory_failed")
        candidates, estimate = _inventory(images.stdout, keep_label=PODMAN_KEEP_LABEL, cutoff=cutoff)
        result["candidate_count"] = len(candidates)
        result["candidate_ids"] = [row["id"] for row in candidates[:_PREVIEW_LIMIT]]
        result["candidate_preview_truncated"] = len(candidates) > _PREVIEW_LIMIT
        result["image_estimate_bytes"] = estimate
        if not apply or not candidates:
            result["status"] = "preview"
            return result

        # Refresh activity and the critical identity immediately before the
        # native engine mutation. Podman itself performs atomic ref checks.
        activity, reason = _process_activity(uid)
        if activity != "eligible":
            return _issue(result, "deferred_active", reason or "active_container_workload")
        if _graph_root(store.get("graphRoot"), home, uid) != root:
            raise _Unknown("graph_root_changed")
        result["cleanup_attempted"] = True
        prune = _command(executable, home, [
            "image", "prune", "--force", "--filter", f"until={cutoff}",
            "--filter", f"label!={PODMAN_KEEP_LABEL}",
        ], timeout=_PRUNE_TIMEOUT)
        if prune.returncode != 0:
            return _issue(result, "uncertain", "prune_result_uncertain")
        result["cleanup_executed"] = True
        result["status"] = "cleaned"
        after = _integer(shutil.disk_usage(root).free)
        if after is None:
            return _issue(result, "uncertain", "post_cleanup_measurement_unavailable")
        result["free_bytes_after"] = after
        result["free_bytes_change"] = after - free
        return result
    except subprocess.TimeoutExpired:
        if result["cleanup_attempted"]:
            return _issue(result, "uncertain", "prune_result_uncertain")
        return _issue(result, "deferred_unknown", "podman_probe_timeout")
    except subprocess.SubprocessError:
        if result["cleanup_attempted"]:
            return _issue(result, "uncertain", "prune_result_uncertain")
        return _issue(result, "deferred_unknown", "podman_probe_failed")
    except _Unknown as exc:
        if result["cleanup_attempted"]:
            return _issue(result, "uncertain", "prune_result_uncertain")
        return _issue(result, "deferred_unknown", str(exc))
    except (OSError, ValueError, TypeError, KeyError, IndexError, OverflowError):
        if result["cleanup_attempted"]:
            return _issue(result, "uncertain", "prune_result_uncertain")
        return _issue(result, "deferred_unknown", "cleanup_observation_failed")
    finally:
        if lock_descriptor is not None:
            if locked:
                try:
                    fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
                except OSError:
                    pass
            try:
                os.close(lock_descriptor)
            except OSError:
                pass
