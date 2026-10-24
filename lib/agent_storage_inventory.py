"""Bounded storage inventories and explicit guarded cleanup commands."""

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
    (".local/share/basaltwater/worktrees", "managed-worktrees", "Basaltwater-owned worktrees: inspect workspace status and preview workspace remove; preserve dirty/unmerged/unowned work and ignored evidence. T3 owns its separate worktrees."),
    (".local/state/basaltwater/agent-backups", "agent-backups", "Recovery snapshots: use managed agent maintenance and preserve the selected rollback and running executables; age alone does not establish redundancy."),
    (".nvm/versions/node", "node-runtimes", "Check active processes, nvm aliases, project .nvmrc/.node-version/package engines and per-version global packages before approved nvm uninstall of an exact unused version."),
    (".nvm/.cache", "node-downloads", "Downloaded Node archives, separate from installed versions; native nvm cache clear costs future downloads and must not race an installation."),
    ("go/pkg/mod", "go-modules", "Module downloads can include active Go toolchains. Check go env GOMODCACHE/GOTOOLCHAIN and project/offline dependencies; build-cache cleanup does not authorize go clean -modcache."),
    (".local/share/containers/storage", "container-storage", "Preview agent storage clean-podman for old unused testing images (including tagged images) and abandoned Buildah builders. Reuse compatible images; preserve active work, ordinary containers and volumes; never remove overlay storage directly."),
    (".local/share/godot/export_templates", "godot-templates", "Preserve versions required by project toolchain locks and exports; these are installed templates, not redundant download archives."),
    (".local/share/basaltwater/t3code", "t3-launcher", "Managed T3 launcher dependencies; preserve active process/service references and use managed updates rather than deleting node_modules."),
    (".local/share/basaltwater/evidence", "development-evidence", "Review and curate project evidence with provenance before selecting removals; preserve milestone captures and verification records."),
    (".local/state/basaltwater/playwright-mcp", "browser-evidence", "Browser captures can document project history; inspect retention and active browser output paths before curating exact old evidence."),
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

_CACHE_REVIEWS = {
    "go-build": {
        "check": "Preview basaltw agent storage clean-go-build --json; within approved cleanup scope add --apply. It checks builds and distinguishes verified waiting go run wrappers.",
        "preserve": "Keep module downloads, source and running programs; do not add -modcache.",
        "cost": "Later builds recompile.",
    },
    "playwright-browsers": {
        "check": "Match .links package registrations and their browsers.json revisions to installed payloads and active browser/MCP references before owner-native unused-version cleanup.",
        "preserve": "Keep the latest testing browsers AND older revisions required by installed projects or managed tooling; revision age alone is insufficient.",
        "cost": "Removed browser payloads require downloads; different Playwright packages can require different revisions.",
    },
    "project-toolchain": {
        "check": "The directory name suggests a toolchain cache. Read its project's installer and version/checksum lock; distinguish downloaded archives, installed executables/templates and retained evidence.",
        "preserve": "Keep required installed tools and templates. Before removing duplicate archives, verify checksums, active use, a retained copy and the installer's restore behavior.",
        "cost": "Installers may download missing archives again even when extracted tools remain; equal sizes do not prove duplicate content.",
    },
}


def _cache_convention(path: str, home: str) -> str | None:
    """Recognize conventional cache roots, not deletion eligibility or versions."""
    target = Path(path)
    if target.parent != Path(home) / ".cache":
        return None
    if target.name == "go-build":
        return "go-build"
    if target.name == "ms-playwright":
        return "playwright-browsers"
    if target.name.endswith("-toolchain"):
        return "project-toolchain"
    return None


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

    def issue(code: str) -> None:
        if code not in result["issues"]:
            result["issues"].append(code)

    for relative in (".codex", ".t3/userdata"):
        directory = os.path.join(home, relative)
        if not _local(directory, mounts):
            issue("session_state_filesystem_unavailable")
            continue
        try:
            descriptor = _open_directory(directory)
        except FileNotFoundError:
            continue
        except OSError:
            issue("session_state_metadata_unavailable")
            continue
        try:
            with os.scandir(descriptor) as entries:
                for index, entry in enumerate(entries):
                    if index >= 1024:
                        issue("session_state_listing_limit")
                        break
                    if not entry.name.endswith((".sqlite", ".sqlite-wal", ".sqlite-shm")):
                        continue
                    try:
                        info = entry.stat(follow_symlinks=False)
                    except OSError:
                        issue("session_state_metadata_unavailable")
                        continue
                    if stat.S_ISREG(info.st_mode):
                        result["files"].append({"path": os.path.join(directory, entry.name),
                            "allocated_bytes": info.st_blocks * 512, "apparent_bytes": info.st_size,
                            "owner_uid": info.st_uid, "mtime": info.st_mtime,
                            "retention": "Protected app state; age and size do not authorize deletion, SQL edits, truncation or vacuum."})
        except OSError:
            issue("session_state_metadata_unavailable")
        finally:
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
    result = {"read_only": True, "candidates": [], "filesystems": [], "issues": [], "cleanup_conventions": {},
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
        if record["status"] in ("measured", "partial"):
            for item in (record, *record["largest_children"]):
                convention = _cache_convention(item["path"], home)
                if convention:
                    item["cleanup_convention"] = convention
                    result["cleanup_conventions"][convention] = _CACHE_REVIEWS[convention].copy()
        result["candidates"].append(record)
    result["candidates"].sort(key=lambda row: (-row["allocated_bytes"], row["path"]))
    result["limits"]["entries_examined"] = max_entries - budget["entries"]
    return result


def _summarize_inventory(result: dict) -> dict:
    """Reduce presentation only, retaining incomplete coverage and retention guidance."""
    summary = {**result, "view": "summary", "candidates": []}
    summary["absent_candidates_omitted"] = sum(row["status"] == "absent" and row["category"] != "selected" for row in result["candidates"])
    fields = ("path", "category", "status", "allocated_bytes", "issues", "retention", "cleanup_convention")
    for row in result["candidates"]:
        if row["status"] == "absent" and row["category"] != "selected":
            continue
        item = {key: row[key] for key in fields if key in row}
        children = row["largest_children"]
        item["largest_children"] = children[:3]
        item["ranked_children_omitted"] = max(0, len(children) - 3)
        summary["candidates"].append(item)
    if "session_state" in result:
        state = result["session_state"]
        summary["session_state"] = {**state,
            "files": [{key: value for key, value in row.items() if key != "retention"} for row in state["files"]],
            "retention": sorted({row["retention"] for row in state["files"]}),
        }
    return summary


def add_storage_parser(commands: argparse._SubParsersAction) -> None:
    parser = commands.add_parser("storage", help="Inspect storage and preview or apply bounded cache/generated-output cleanup")
    actions = parser.add_subparsers(dest="agent_storage_command", required=True)
    inspect = actions.add_parser("inspect", help="Measure caches, temporary data and project artifacts without deleting")
    inspect.add_argument("--path", action="append", help="Inspect one exact absolute directory instead of default candidates; repeat up to eight times")
    inspect.add_argument("--max-entries", type=int, default=250000)
    inspect.add_argument("--timeout", type=int, default=20, help="Shared scan budget in seconds (1-60)")
    inspect.add_argument("--summary", action="store_true", help="Omit absent default roots and detailed file metrics; retain selected paths, issues, retention and three ranked children per root")
    inspect.add_argument("--json", action="store_true")
    project = actions.add_parser("project", help="Inspect project cleanup rules and artifacts without running scripts")
    project.add_argument("repository", metavar="ABSOLUTE_PROJECT")
    project.add_argument("--policy", default=".basaltwater/cleanup.json", help="Literal project-relative JSON policy path; existing project conventions take precedence")
    project.add_argument("--max-entries", type=int, default=50000)
    project.add_argument("--timeout", type=int, default=10)
    project.add_argument("--json", action="store_true")
    clean = actions.add_parser("clean-generated", help="Preview one declared generated-output rule; delete only with --apply")
    clean.add_argument("repository", metavar="ABSOLUTE_PROJECT")
    clean.add_argument("--rule", required=True, help="Exact generated rule name in the reviewed project policy")
    clean.add_argument("--policy", default=".basaltwater/cleanup.json", help="Literal project-relative policy path")
    mode = clean.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Preview without deleting (default)")
    mode.add_argument("--apply", action="store_true", help="Remove the rule's generated directories after preflight")
    go = actions.add_parser("clean-go-build", help="Preview native Go build-cache cleanup with active-build checks")
    go.add_argument("--apply", action="store_true", help="Execute go clean -cache within authorized cleanup scope")
    go.add_argument("--json", action="store_true")
    podman = actions.add_parser("clean-podman", help="Preview age-limited unused test images and abandoned Buildah builders")
    mode = podman.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Preview without deleting (default)")
    mode.add_argument("--apply", action="store_true", help="Remove eligible abandoned builders and prune unused images, including tagged images")
    podman.add_argument("--json", action="store_true")


def run_storage_command(args: argparse.Namespace) -> int:
    try:
        if args.agent_storage_command == "clean-podman":
            from lib.podman_cleanup import clean_podman_images

            result = clean_podman_images(apply=args.apply)
            print(json.dumps(result, indent=2))
            return 0 if result["status"] in ("preview", "cleaned", "absent") else 1
        if args.agent_storage_command == "clean-go-build":
            from lib.go_cache_cleanup import clean_go_build_cache

            result = clean_go_build_cache(apply=args.apply)
            print(json.dumps(result, indent=2))
            return 0 if result["status"] in ("preview", "cleaned", "absent") else 1
        if args.agent_storage_command == "clean-generated":
            from lib.agent_generated_cleanup import clean_generated

            return clean_generated(args.repository, args.rule, policy_path=args.policy, apply=args.apply)
        if args.agent_storage_command == "project":
            from lib.agent_project_cleanup import inspect_project_cleanup

            result = inspect_project_cleanup(args.repository, policy_path=args.policy,
                                             max_entries=args.max_entries, timeout=args.timeout)
            print(json.dumps(result, indent=2))
            return 1 if result["evidence"] and result["evidence"]["issues"] else 0
        result = inspect_storage(paths=args.path, max_entries=args.max_entries, timeout=args.timeout)
        unavailable_selection = any(
            row["category"] == "selected" and row["status"] == "unavailable"
            for row in result["candidates"]
        )
        if getattr(args, "summary", False):
            result = _summarize_inventory(result)
        print(json.dumps(result, separators=(",", ":")) if getattr(args, "summary", False) else json.dumps(result, indent=2))
        return 1 if result["issues"] or unavailable_selection else 0
    except (OSError, ValueError) as exc:
        print(json.dumps({"read_only": not bool(getattr(args, "apply", False)), "error": str(exc)}))
        return 1
