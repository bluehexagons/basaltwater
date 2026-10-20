"""Bounded read-only storage evidence for cleanup planning, never deletion."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import stat
import time

from lib.validation import validate_filesystem_path


_LOCAL_TYPES = frozenset(("ext2", "ext3", "ext4", "xfs", "btrfs", "f2fs", "zfs", "tmpfs", "overlay"))
_OPEN_DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_ROOTS = (
    (".cache", "tool-caches", "Rebuildable caches; inspect the largest child and check active tools before choosing a native cleanup command."),
    (".npm", "npm-cache", "Downloaded packages, npx installs and logs; cleaning costs future downloads. Preserve npm configuration."),
    (".local/share/pnpm/store", "pnpm-store", "Shared package store; use the installed package manager and account for active installs and offline dependencies."),
    (".local/share/Trash/files", "trash", "User-discarded files may still be valuable; review retention before permanently deleting selected old items."),
    (".codex/packages/standalone/releases", "codex-releases", "Preserve the active release and rollback; use managed retention, never delete the whole Codex directory."),
    (".codex/cache", "codex-cache", "Protected agent directory: use managed maintenance; ordinary task write grants cannot include it."),
    (".codex/tmp", "codex-temporary", "Protected agent directory: stale entries need managed maintenance and active-process checks."),
    (".codex/sessions", "codex-sessions", "Preserve resumable sessions. Review app-owned retention and selected handoffs; never bulk-delete transcripts based on age."),
    (".codex/archived_sessions", "archived-sessions", "Archived does not mean disposable; verify supported app removal and resume dependencies before proposing reclamation."),
    (".t3/runtime/versions", "t3-runtimes", "Preserve the active runtime, rollback and all process references; use managed retention."),
    (".t3/userdata/logs", "t3-logs", "Prefer old numbered rotations; preserve current logs, sessions, attachments and application state."),
    (".t3/userdata/attachments", "session-attachments", "Preserve attachments referenced by sessions and visual development history; use app-managed retention."),
    (".t3/scratch", "t3-scratch", "Check task ownership and running work before selecting abandoned scratch data."),
    ("repos", "project-checkouts", "Preserve source and Git data. Drill into the largest projects to investigate node_modules, .venv, .next, target or build output; verify generated and unused before proposing cleanup."),
    (".t3/worktrees", "task-worktrees", "Preserve unfinished work and branches. Use managed workspace ownership and integration checks; never delete the worktree directory wholesale."),
    ("/tmp", "temporary", "Temporary names and old timestamps do not prove abandonment; inspect owner and live references."),
    ("/var/tmp", "temporary", "Persistent temporary data can belong to running jobs; prefer existing retention policies."),
    ("/var/cache/apt/archives", "apt-downloads", "Downloaded package archives only; package cleanup requires separate administrator authorization."),
    ("/var/log/journal", "journal", "Evidence retention requires an operator decision; do not truncate active logs."),
)


def _mounts() -> dict[str, str]:
    """Read only mount paths/types; never publish mount sources or options."""
    with open("/proc/self/mountinfo", encoding="utf-8") as stream:
        data = stream.read(1024 * 1024 + 1)
    if len(data) > 1024 * 1024:
        raise ValueError("Mount inventory exceeds limit")
    mounts = {}
    for line in data.splitlines():
        fields = line.split()
        separator = fields.index("-")
        path = re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), fields[4])
        mounts[path] = fields[separator + 1]
    return mounts


def _local(path: str, mounts: dict[str, str]) -> bool:
    ancestors = [mount for mount in mounts if path == mount or path.startswith(mount.rstrip("/") + "/")]
    return bool(ancestors) and mounts[max(ancestors, key=len)] in _LOCAL_TYPES


def _open_directory(path: str) -> int:
    """Walk every component with no-follow descriptors, including ancestors."""
    descriptor = os.open("/", _OPEN_DIRECTORY)
    try:
        for part in Path(path).parts[1:]:
            child = os.open(part, _OPEN_DIRECTORY, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


class _LimitReached(Exception):
    pass


def _session_state_files(home: str, mounts: dict[str, str]) -> dict:
    """Stat a bounded directory listing; never open session databases or WALs."""
    result = {"files": [], "issues": []}
    for relative in (".codex", ".t3/userdata"):
        directory = os.path.join(home, relative)
        if not _local(directory, mounts):
            result["issues"].append("session_state_filesystem_unavailable")
            continue
        descriptor = -1
        try:
            descriptor = _open_directory(directory)
            with os.scandir(descriptor) as entries:
                for index, entry in enumerate(entries):
                    if index >= 1024:
                        result["issues"].append("session_state_listing_limit")
                        break
                    if not entry.name.endswith((".sqlite", ".sqlite-wal", ".sqlite-shm")):
                        continue
                    info = entry.stat(follow_symlinks=False)
                    if stat.S_ISREG(info.st_mode):
                        result["files"].append({"path": os.path.join(directory, entry.name),
                            "allocated_bytes": info.st_blocks * 512, "apparent_bytes": info.st_size,
                            "owner_uid": info.st_uid, "mtime": info.st_mtime,
                            "retention": "Protected app state; age and size do not authorize deletion, SQL edits, truncation or vacuum."})
        except FileNotFoundError:
            pass
        except OSError:
            result["issues"].append("session_state_metadata_unavailable")
        finally:
            if descriptor >= 0:
                os.close(descriptor)
    result["files"].sort(key=lambda row: (-row["allocated_bytes"], row["path"]))
    return result


def _scan(path: str, mounts: dict[str, str], budget: dict, *, entry_limit: int, now: float) -> dict:
    result = {"path": path, "status": "unavailable", "allocated_bytes": 0, "apparent_bytes": 0,
              "files": 0, "older_than_30_days_bytes": 0, "newest_file_mtime": None,
              "owner_uid": None, "other_owner_entries": 0, "hardlinked_files": 0,
              "is_mountpoint": path in mounts,
              "issues": [], "largest_children": []}
    if not _local(path, mounts):
        result["issues"] = ["nonlocal_or_unknown_filesystem"]
        return result
    seen = set()
    children: dict[str, int] = {}
    visited = 0

    def issue(code: str) -> None:
        if code not in result["issues"]:
            result["issues"].append(code)

    def walk(descriptor: int, current: str, group: str, depth: int) -> None:
        nonlocal visited
        with os.scandir(descriptor) as entries:
            for entry in entries:
                if budget["entries"] <= 0 or visited >= entry_limit or time.monotonic() >= budget["deadline"]:
                    issue("scan_limit_reached")
                    raise _LimitReached
                budget["entries"] -= 1
                visited += 1
                target = os.path.join(current, entry.name)
                try:
                    info = entry.stat(follow_symlinks=False)
                    if stat.S_ISLNK(info.st_mode):
                        issue("symlinks_skipped")
                        continue
                    if target in mounts or info.st_dev != device:
                        issue("nested_mounts_skipped")
                        continue
                    if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                        issue("special_files_skipped")
                        continue
                    key = (info.st_dev, info.st_ino)
                    if key in seen:
                        continue
                    seen.add(key)
                    bucket = group or (target if stat.S_ISDIR(info.st_mode) else path)
                    allocated = info.st_blocks * 512
                    result["allocated_bytes"] += allocated
                    result["apparent_bytes"] += info.st_size
                    children[bucket] = children.get(bucket, 0) + allocated
                    if info.st_uid != result["owner_uid"]:
                        result["other_owner_entries"] += 1
                    if stat.S_ISREG(info.st_mode):
                        result["files"] += 1
                        result["hardlinked_files"] += int(info.st_nlink > 1)
                        result["newest_file_mtime"] = max(result["newest_file_mtime"] or 0, info.st_mtime)
                        if info.st_mtime < now - 30 * 86400:
                            result["older_than_30_days_bytes"] += allocated
                    elif depth >= 24:
                        issue("depth_limit_reached")
                    else:
                        child = os.open(entry.name, _OPEN_DIRECTORY, dir_fd=descriptor)
                        try:
                            opened = os.fstat(child)
                            if (opened.st_dev, opened.st_ino) != key:
                                issue("changed_during_scan")
                            else:
                                walk(child, target, bucket, depth + 1)
                        finally:
                            os.close(child)
                except OSError:
                    issue("unreadable_or_changed_entries")

    try:
        descriptor = _open_directory(path)
    except FileNotFoundError:
        result["status"] = "absent"
        return result
    except OSError:
        result["issues"] = ["unreadable_or_symlink_path"]
        return result
    try:
        info = os.fstat(descriptor)
        device = info.st_dev
        result["owner_uid"] = info.st_uid
        try:
            walk(descriptor, path, "", 0)
        except _LimitReached:
            pass
        except OSError:
            issue("unreadable_or_changed_entries")
        incomplete = {"scan_limit_reached", "depth_limit_reached", "unreadable_or_changed_entries", "changed_during_scan"}
        result["status"] = "partial" if incomplete.intersection(result["issues"]) else "measured"
        result["largest_children"] = [{"path": key, "allocated_bytes": size}
            for key, size in sorted(children.items(), key=lambda item: (-item[1], item[0]))[:8]]
        return result
    finally:
        os.close(descriptor)


def inspect_storage(*, home: str | None = None, paths: list[str] | None = None,
                    max_entries: int = 250000, timeout: int = 20) -> dict:
    """Measure fixed local candidates with a shared entry/time budget; never sum overlapping roots."""
    if type(max_entries) is not int or not 1 <= max_entries <= 2000000 or type(timeout) is not int or not 1 <= timeout <= 60:
        raise ValueError("Use 1-2000000 entries and a 1-60 second timeout")
    home = os.path.abspath(home or pwd.getpwuid(os.geteuid()).pw_dir)
    validate_filesystem_path(home, must_exist=True)
    if paths is not None and (not paths or len(paths) > 8):
        raise ValueError("Select one to eight directories")
    if paths:
        for path in paths:
            validate_filesystem_path(path)
            if not os.path.isabs(path) or os.path.normpath(path) != path:
                raise ValueError("Storage paths must be absolute and normalized")
    result = {"read_only": True, "candidates": [], "filesystems": [], "issues": [],
              "limits": {"max_entries": max_entries, "timeout_seconds": timeout},
              "notes": ["Allocated bytes are observed usage, not guaranteed reclaimable space.",
                        "Partial sizes are lower bounds; missing evidence is not zero usage.",
                        "Child sizes are included in their parent; hardlinks, shared/reflink extents and overlapping requested paths must not be summed as savings.",
                        "Old timestamps do not prove disuse. Recheck ownership, active work and retention before any cleanup."]}
    try:
        mounts = _mounts()
    except (OSError, ValueError, IndexError):
        result["issues"].append("mount_inventory_unavailable")
        return result
    budget = {"entries": max_entries, "deadline": time.monotonic() + timeout}
    if not paths:
        result["session_state"] = _session_state_files(home, mounts)
    targets = [(path, "selected", "Operator-selected path; inspect retention and active users before cleanup.") for path in dict.fromkeys(paths)] if paths else [
        (path if path.startswith("/") else os.path.join(home, path), category, caution) for path, category, caution in _ROOTS]
    for path in dict.fromkeys(("/", home)):
        if _local(path, mounts):
            try:
                usage, inodes = shutil.disk_usage(path), os.statvfs(path)
                result["filesystems"].append({"path": path, "total_bytes": usage.total, "used_bytes": usage.used,
                    "free_bytes": usage.free, "free_inodes": inodes.f_favail, "total_inodes": inodes.f_files})
            except OSError:
                result["issues"].append("filesystem_capacity_unavailable")
    now = time.time()
    for path, category, caution in targets:
        record = _scan(path, mounts, budget, entry_limit=max_entries if paths else min(max_entries, 50000), now=now)
        record.update(category=category, retention=caution)
        result["candidates"].append(record)
    result["candidates"].sort(key=lambda row: (-row["allocated_bytes"], row["path"]))
    result["limits"]["entries_examined"] = max_entries - budget["entries"]
    return result


def add_storage_parser(commands: argparse._SubParsersAction) -> None:
    parser = commands.add_parser("storage", help="Read-only local cleanup candidates and capacity")
    actions = parser.add_subparsers(dest="agent_storage_command", required=True)
    inspect = actions.add_parser("inspect", help="Measure caches, temporary data and project artifacts without deleting")
    inspect.add_argument("--path", action="append", help="Inspect one exact absolute directory instead of default candidates; repeat up to eight times")
    inspect.add_argument("--max-entries", type=int, default=250000)
    inspect.add_argument("--timeout", type=int, default=20, help="Shared scan budget in seconds (1-60)")
    inspect.add_argument("--json", action="store_true")
    project = actions.add_parser("project", help="Inspect project cleanup rules and artifacts without running scripts")
    project.add_argument("repository", metavar="ABSOLUTE_PROJECT")
    project.add_argument("--policy", default=".basaltwater/cleanup.json", help="Literal project-relative JSON policy path; existing project conventions take precedence")
    project.add_argument("--max-entries", type=int, default=50000)
    project.add_argument("--timeout", type=int, default=10)
    project.add_argument("--json", action="store_true")


def run_storage_command(args: argparse.Namespace) -> int:
    try:
        if args.agent_storage_command == "project":
            from lib.agent_project_cleanup import inspect_project_cleanup

            result = inspect_project_cleanup(args.repository, policy_path=args.policy,
                                             max_entries=args.max_entries, timeout=args.timeout)
            print(json.dumps(result, indent=2))
            return 1 if result["evidence"] and result["evidence"]["issues"] else 0
        result = inspect_storage(paths=args.path, max_entries=args.max_entries, timeout=args.timeout)
        print(json.dumps(result, indent=2))
        return 1 if result["issues"] else 0
    except (OSError, ValueError) as exc:
        print(json.dumps({"read_only": True, "error": str(exc)}))
        return 1
