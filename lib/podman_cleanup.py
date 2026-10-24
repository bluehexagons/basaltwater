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
from collections import deque

from lib.agent_storage_inventory import _local, _mounts
from lib.podman_builders import BuilderObservationError, builder_is_removable, plan_builders
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
_CLEANUP_SECONDS = 300
_OUTPUT_LIMIT = 256 * 1024
_SELF_STATUS_LIMIT = 16 * 1024
_PROCESS_LIMIT = 16384
_PROCESS_SECONDS = 3
_PREVIEW_LIMIT = 12
_ID = re.compile(r"(?:sha256:)?[0-9a-f]{64}\Z")
_PROCESS_NAMES = frozenset((
    "podman", "buildah", "buildkitd", "buildctl", "conmon", "crun", "runc",
    "containerd", "containers-storage",
))
_SIZE = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*(B|kB|KB|MB|GB|TB|KiB|MiB|GiB|TiB)\Z")
_NOTES = [
    "Unused testing images are disposable, including tagged images; non-forced native removal protects container references.",
    "Retention uses image/container creation time, not last use or pull time; old shared images can be recreated.",
    "Only old, unmounted, unpinned Buildah working containers are eligible; ordinary containers and volumes are retained.",
    "Image size totals are estimates; shared layers can make them overstate reclaimed space.",
    "Removal uses exact image IDs with parent pruning disabled; retained parents and persistent cache mounts are preserved.",
    "Removing abandoned builders can make additional old images eligible after preview.",
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
        "age_basis": "created", "retained_counts": {},
        "builder_candidate_count": 0, "builder_candidate_ids": [],
        "builder_preview_truncated": False, "builders_removed": 0, "builder_retained_counts": {},
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
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or info.st_mode & 0o022
            or not _local(value, _mounts())):
        raise _Unknown("unsafe_graph_root")
    return value


def _command(executable: str, home: str, args: list[str], *,
             timeout: int = _COMMAND_TIMEOUT, podman: bool = True) -> subprocess.CompletedProcess[str]:
    # Preserve native XDG/storage configuration so every probe and prune uses
    # the account's selected store. Force local mode and validate its graph root.
    environment = dict(os.environ, HOME=home, PATH="/usr/local/bin:/usr/bin:/bin", LC_ALL="C")
    prefix = [executable, "--remote=false"] if podman else [executable]
    return run_captured([*prefix, *args], cwd=home, env=environment,
                        timeout=timeout, max_bytes=_OUTPUT_LIMIT)


def _namespace_helper_restricted(stderr: str) -> bool:
    """Recognize a map-helper denial only with current kernel restrictions."""
    if not isinstance(stderr, str) or len(stderr) > _OUTPUT_LIMIT:
        return False
    denied = any(
        re.search(rf'(?:^|: ){helper}: write to {mapping} failed: Operation not permitted$',
                  stderr, re.MULTILINE)
        and re.search(rf'^Error: cannot set up namespace using "(?:/[A-Za-z0-9_.-]+)*/{helper}": exit status ?1$',
                      stderr, re.MULTILINE)
        for helper, mapping in (("newuidmap", "uid_map"), ("newgidmap", "gid_map"))
    )
    if not denied:
        return False
    try:
        with open("/proc/self/status", "rb") as source:
            raw = source.read(_SELF_STATUS_LIMIT + 1)
    except OSError:
        return False
    if len(raw) > _SELF_STATUS_LIMIT or not raw.endswith(b"\n"):
        return False
    # Require exactly one well-formed instance of both kernel fields. Unknown
    # or incomplete observations retain the ordinary failed-probe contract.
    nnp = [line for line in raw.splitlines() if line.startswith(b"NoNewPrivs:")]
    bounding = [line for line in raw.splitlines() if line.startswith(b"CapBnd:")]
    return (len(nnp) == len(bounding) == 1
            and re.fullmatch(rb"NoNewPrivs:[ \t]+1", nnp[0]) is not None
            and re.fullmatch(rb"CapBnd:[ \t]+0{16}", bounding[0]) is not None)


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
                    name = raw[:-1].decode("utf-8", errors="replace")
                    if name in _PROCESS_NAMES:
                        return "deferred_active", "active_container_workload"
                except FileNotFoundError:
                    # An exited process cannot still be using the store.
                    continue
        if time.monotonic() >= deadline:
            raise _Unknown("process_inventory_limit")
    except _Unknown:
        raise
    except (OSError, UnicodeError) as exc:
        raise _Unknown("process_inventory_incomplete") from exc
    return "eligible", None


def _inventory(output: str, *, keep_label: str, cutoff: int) -> tuple[list[dict], int | None, dict]:
    try:
        rows = json.loads(output)
    except (ValueError, RecursionError) as exc:
        raise _Unknown("invalid_image_inventory") from exc
    if not isinstance(rows, list) or len(rows) > 100000:
        raise _Unknown("invalid_image_inventory")
    candidates: list[dict] = []
    retained = {"recent": 0, "container_referenced": 0, "keep_label": 0, "dependent_image": 0}
    seen: dict[str, tuple] = {}
    records: dict[str, dict] = {}
    parents: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise _Unknown("invalid_image_inventory")
        created, containers = _integer(row.get("Created")), _integer(row.get("Containers"))
        tags, labels = row.get("Names", row.get("RepoTags")), row.get("Labels")
        identifier = row.get("Id", row.get("ID"))
        if (created is None or containers is None or not (tags is None or isinstance(tags, list))
                or not (labels is None or isinstance(labels, dict))
                or not isinstance(identifier, str) or not _ID.fullmatch(identifier)):
            raise _Unknown("invalid_image_inventory")
        identifier = identifier.removeprefix("sha256:")
        parent = row.get("ParentId", "") or ""
        if not isinstance(parent, str) or (parent and not _ID.fullmatch(parent)):
            raise _Unknown("invalid_image_inventory")
        parent = parent.removeprefix("sha256:")
        # Native image ls can repeat a multi-tag image. Count each image once,
        # but do not hide contradictory reference/retention metadata.
        observation = (created, containers, labels, row.get("Size"), parent)
        if identifier in seen:
            if seen[identifier] != observation:
                raise _Unknown("inconsistent_image_inventory")
            continue
        seen[identifier] = observation
        parents[identifier] = parent
        if labels is not None and keep_label in labels:
            retained["keep_label"] += 1
            continue
        if containers != 0:
            retained["container_referenced"] += 1
            continue
        if created >= cutoff:
            retained["recent"] += 1
            continue
        records[identifier] = {"id": identifier, "size": _image_size(row.get("Size")),
                               "untag": len(tags or []) > 1}
    # Remove children before parents, and keep every ancestor of an image
    # that must stay. Do not ask the engine to recursively prune parents:
    # that bypasses their independent age and keep-label eligibility.
    children = dict.fromkeys(parents, 0)
    for parent in parents.values():
        if parent in children:
            children[parent] += 1
    leaves = deque(identifier for identifier, count in children.items() if not count)
    visited = 0
    while leaves:
        identifier = leaves.popleft()
        visited += 1
        parent = parents[identifier]
        if identifier in records:
            candidates.append(records[identifier])
        elif parent in records:
            del records[parent]
            retained["dependent_image"] += 1
        if parent in children:
            children[parent] -= 1
            if children[parent] == 0:
                leaves.append(parent)
    if visited != len(parents):
        raise _Unknown("invalid_image_ancestry")
    estimate = sum(row["size"] for row in candidates if row["size"] is not None)
    return candidates, estimate if all(row["size"] is not None for row in candidates) else None, retained


def clean_podman_images(*, apply: bool = False) -> dict:
    """Preview or prune old, unreferenced rootless Podman images safely.

    Remove abandoned Buildah working containers by exact ID, then native
    Podman removal rechecks image references atomically. Ordinary containers,
    volumes, persistent build cache, pinned and recent images are retained.
    """
    if type(apply) is not bool:
        raise TypeError("apply must be a boolean")
    result = _base_result(apply)
    lock_descriptor = None
    locked = False
    deadline = time.monotonic() + _CLEANUP_SECONDS
    try:
        if any(os.environ.get(key) for key in ("CONTAINER_HOST", "CONTAINER_CONNECTION")):
            return _issue(result, "deferred_unknown", "remote_selection_unsupported")
        home, uid = _home_and_identity()
        executable = shutil.which("podman", path="/usr/local/bin:/usr/bin:/bin")
        if executable is None:
            result["status"] = "absent"
            return result
        def run(args: list[str], *, timeout: int = _COMMAND_TIMEOUT,
                engine: str = executable, podman: bool = True) -> subprocess.CompletedProcess[str]:
            remaining = int(deadline - time.monotonic())
            if remaining <= 0:
                raise subprocess.TimeoutExpired(engine, _CLEANUP_SECONDS)
            return _command(engine, home, args, timeout=min(timeout, remaining), podman=podman)

        buildah = shutil.which("buildah", path="/usr/local/bin:/usr/bin:/bin")
        def run_buildah(args: list[str]) -> subprocess.CompletedProcess[str]:
            if buildah is None:
                raise _Unknown("builder_tool_unavailable")
            return run(args, engine=buildah, podman=False)
        if apply:
            lock_descriptor = _lock_file(home, uid)
            try:
                fcntl.flock(lock_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
            except BlockingIOError:
                return _issue(result, "deferred_active", "cleanup_already_running")

        info_result = run(["info", "--format", "json"])
        if info_result.returncode != 0:
            if _namespace_helper_restricted(info_result.stderr):
                return _issue(result, "deferred_restricted", "namespace_helper_restricted")
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
        security = host.get("security")
        if not isinstance(security, dict):
            raise _Unknown("invalid_podman_info")
        if security.get("rootless") is not True:
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

        builders = plan_builders(run, run_buildah if buildah else None, store, cutoff)
        builder_ids = builders["candidate_ids"]
        result.update(builder_candidate_count=len(builder_ids),
                      builder_candidate_ids=builder_ids[:_PREVIEW_LIMIT],
                      builder_preview_truncated=len(builder_ids) > _PREVIEW_LIMIT,
                      builder_retained_counts=builders["retained_counts"])

        def inventory_images() -> list[dict]:
            images = run(["image", "ls", "--all", "--format", "json"])
            if images.returncode != 0:
                raise _Unknown("image_inventory_failed")
            candidates, estimate, retained = _inventory(images.stdout, keep_label=PODMAN_KEEP_LABEL, cutoff=cutoff)
            result.update(candidate_count=len(candidates),
                          candidate_ids=[row["id"] for row in candidates[:_PREVIEW_LIMIT]],
                          candidate_preview_truncated=len(candidates) > _PREVIEW_LIMIT,
                          image_estimate_bytes=estimate, retained_counts=retained)
            return candidates

        candidates = inventory_images()
        if not apply or not (candidates or builder_ids):
            result["status"] = "preview"
            return result

        # Refresh activity and the critical identity immediately before the
        # native engine mutation. Podman itself performs atomic ref checks.
        activity, reason = _process_activity(uid)
        if activity != "eligible":
            return _issue(result, "deferred_active", reason or "active_container_workload")
        if _graph_root(store.get("graphRoot"), home, uid) != root:
            raise _Unknown("graph_root_changed")
        for identifier in builder_ids:
            # Inspect again and refresh activity for every exact builder ID.
            # A changed/mounted/pinned builder is left for a later run.
            if not builder_is_removable(run_buildah, identifier):
                continue
            activity, reason = _process_activity(uid)
            if activity != "eligible":
                return _issue(result, "uncertain" if result["cleanup_attempted"] else "deferred_active",
                              reason or "active_container_workload")
            result["cleanup_attempted"] = True
            removed = run_buildah(["rm", identifier])
            if removed.returncode != 0:
                return _issue(result, "uncertain", "builder_removal_uncertain")
            result["builders_removed"] += 1
            result["cleanup_executed"] = True
        if result["builders_removed"]:
            candidates = inventory_images()
        if candidates:
            activity, reason = _process_activity(uid)
            if activity != "eligible":
                return _issue(result, "uncertain" if result["cleanup_attempted"] else "deferred_active",
                              reason or "active_container_workload")
            # ID removal refuses multiple names without --force. Untag only
            # that exact eligible image first; never force-delete containers.
            for candidate in candidates:
                if candidate["untag"]:
                    result["cleanup_attempted"] = True
                    untagged = run(["untag", candidate["id"]])
                    if untagged.returncode != 0:
                        return _issue(result, "uncertain", "image_untag_uncertain")
                    result["cleanup_executed"] = True
            result["cleanup_attempted"] = True
            removed = run(["image", "rm", "--no-prune", "--ignore",
                           *[row["id"] for row in candidates]], timeout=_PRUNE_TIMEOUT)
            if removed.returncode != 0:
                return _issue(result, "uncertain", "prune_result_uncertain")
            result["cleanup_executed"] = True
        if not result["cleanup_executed"]:
            result["status"] = "preview"
            return result
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
    except (_Unknown, BuilderObservationError) as exc:
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
