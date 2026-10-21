"""On-demand, metadata-only root-filesystem estimates for the web panel.

Areas partition one scan; services and projects are overlapping views of it.
Incomplete rows contain lower bounds, including zero when nothing was observed.
An absent conventional path is a complete zero with a ``Not present`` issue.
Only fixed ``home/repos`` children are discovered as projects (64 measured,
30 returned). No file contents, Git configuration, or external commands are read.
"""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from datetime import datetime, timezone
import os
import re
import stat
import threading
import time

from lib.validation import validate_filesystem_path


_ROOT = "/"  # Private fixture seam; never supplied by an HTTP request.
_OPEN_DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_LOCAL_TYPES = frozenset(("ext2", "ext3", "ext4", "xfs", "btrfs", "f2fs",
                          "zfs", "bcachefs", "jfs", "reiserfs", "overlay"))
_MAX_DEPTH = 48
_MAX_PROJECTS = 64
_RETURN_PROJECTS = 30
_INCOMPLETE = "Coverage incomplete: "
_ABSENT = "Not present; measured as zero."
_UNSCANNED = "Unavailable: this path has not been scanned."
_AREA_PATHS = ("home", "root", "var/tmp", "var", "opt", "usr", "tmp", "srv", "etc", "boot")


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _within(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix.rstrip("/") + "/")


def _validated_home(home: str) -> str:
    if not isinstance(home, str):
        raise ValueError("Home must be an absolute, normalized directory path")
    validate_filesystem_path(home)
    if not os.path.isabs(home) or os.path.normpath(home) != home or home.startswith("//"):
        raise ValueError("Home must be an absolute, normalized directory path")
    if not _within(home, _ROOT):
        raise ValueError("Home must be inside the diagnostic root")
    return home


def _mounts() -> dict[str, str]:
    """Read bounded kernel mount metadata, exposing neither sources nor options."""
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


def _open_root() -> int:
    """Anchor every component, including fixture ancestors, without symlinks."""
    descriptor = os.open("/", _OPEN_DIRECTORY)
    try:
        for component in _ROOT.strip("/").split("/") if _ROOT != "/" else ():
            child = os.open(component, _OPEN_DIRECTORY, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _row(identifier: str, label: str, path: str) -> dict:
    return {"id": identifier, "label": label, "path": path,
            "allocated_bytes": 0, "apparent_bytes": 0, "files": 0,
            "complete": False, "issues": [_UNSCANNED]}


def _service_rows(home: str) -> list[dict]:
    # Matching always respects '/' boundaries; the longest prefix owns each entry.
    system = (
        ("docker", "Docker storage", "var/lib/docker"),
        ("containers", "System container storage", "var/lib/containers/storage"),
        ("containerd", "Containerd storage", "var/lib/containerd"),
        ("basaltwater-install", "Basaltwater installation", "opt/basaltwater"),
        ("basaltwater-state", "Basaltwater system state", "var/lib/basaltwater"),
        ("basaltwater-logs", "Basaltwater system logs", "var/log/basaltwater"),
        ("system-cache", "System package caches", "var/cache"),
    )
    user = (
        ("rootless-containers", "Rootless container storage", ".local/share/containers/storage"),
        ("rootless-docker", "Rootless Docker storage", ".local/share/docker"),
        ("basaltwater-data", "Basaltwater installations and data", ".local/share/basaltwater"),
        ("basaltwater-user-state", "Basaltwater user state and logs", ".local/state/basaltwater"),
        ("basaltwater-config", "Basaltwater configuration metadata", ".config/basaltwater"),
        ("t3", "T3 installations, worktrees and sessions", ".t3"),
        ("codex", "Codex installations and sessions", ".codex"),
        ("agents", "Agent installations and skills", ".agents"),
        ("tool-cache", "User tool and package caches", ".cache"),
        ("npm", "npm package cache", ".npm"),
        ("pnpm", "pnpm package store", ".local/share/pnpm/store"),
        ("cargo-registry", "Cargo registry cache", ".cargo/registry"),
        ("cargo-git", "Cargo Git cache", ".cargo/git"),
        ("go-modules", "Go module cache", "go/pkg/mod"),
    )
    return ([_row(identifier, label, os.path.join(_ROOT, path)) for identifier, label, path in system]
            + [_row(identifier, label, os.path.join(home, path)) for identifier, label, path in user])


class _LimitReached(Exception):
    pass


class _Scan:
    def __init__(self, home: str, report: dict, deadline: float, mounts: dict[str, str]) -> None:
        self.home, self.report, self.deadline, self.mounts = home, report, deadline, mounts
        self.rows = [*report["areas"], *report["services"]]
        self.seen: set[tuple[int, int]] = set()
        self.done: set[str] = set()
        self.uncertain: set[str] = set()
        self.skipped: set[str] = set()
        self.project_paths: set[str] = set()
        self.device = 0
        # Prefer fixed projects and service roots before walking the rest of home/OS.
        preferred = [os.path.join(home, "repos"), *[row["path"] for row in report["services"]]]
        self.full_roots = set(preferred)
        targets = [*preferred, home, *[row["path"] for row in report["areas"]]]
        self.priority: dict[str, list[str]] = {}
        for target in targets:
            current = _ROOT
            for component in os.path.relpath(target, _ROOT).split(os.sep):
                if component == ".":
                    continue
                names = self.priority.setdefault(current, [])
                if component not in names:
                    names.append(component)
                current = os.path.join(current, component)

    def budget(self) -> None:
        if (self.report["entries_scanned"] >= self.report["limits"]["entry_limit"]
                or time.monotonic() >= self.deadline):
            raise _LimitReached

    def issue(self, message: str) -> None:
        if message not in self.report["issues"]:
            self.report["issues"].append(message)

    def area(self, path: str) -> dict:
        return next((row for row in self.report["areas"][:-1] if _within(path, row["path"])),
                    self.report["areas"][-1])

    def affected(self, path: str) -> list[dict]:
        area = self.area(path)
        return [row for row in self.rows if row is area or
                (not row["id"].startswith("area:") and _within(path, row["path"])) or
                (_within(row["path"], path) and row["id"] not in self.done)]

    def problem(self, path: str, message: str) -> None:
        self.issue(_INCOMPLETE + message)
        for row in self.affected(path):
            self.uncertain.add(row["id"])
            row["complete"] = False
            row["issues"] = [item for item in row["issues"] if item != _UNSCANNED]
            if message not in row["issues"] and len(row["issues"]) < 8:
                row["issues"].append(message)

    def absent(self, path: str) -> None:
        for row in self.rows:
            if _within(row["path"], path) and row["id"] not in self.uncertain:
                row.update(complete=True, issues=[_ABSENT])
                self.done.add(row["id"])

    def finish(self, path: str) -> None:
        for row in self.rows:
            if row["path"] == path:
                row["complete"] = row["id"] not in self.uncertain
                row["issues"] = [item for item in row["issues"] if item != _UNSCANNED]
                self.done.add(row["id"])

    def account(self, path: str, info: os.stat_result) -> None:
        key = (info.st_dev, info.st_ino)
        if key in self.seen:
            return
        self.seen.add(key)
        allocated, apparent = max(0, info.st_blocks) * 512, max(0, info.st_size)
        files = int(stat.S_ISREG(info.st_mode))
        self.report["scanned_allocated_bytes"] += allocated
        self.report["scanned_apparent_bytes"] += apparent
        self.report["files_scanned"] += files
        service = max((row for row in self.report["services"] if _within(path, row["path"])),
                      key=lambda row: len(row["path"]), default=None)
        project = next((row for row in self.report["projects"] if _within(path, row["path"])), None)
        for row in (self.area(path), service, project):
            if row is not None:
                row["allocated_bytes"] += allocated
                row["apparent_bytes"] += apparent
                row["files"] += files

    def project(self, path: str, name: str) -> None:
        if os.path.dirname(path) != os.path.join(self.home, "repos") or path in self.project_paths:
            return
        self.project_paths.add(path)
        if len(self.report["projects"]) < _MAX_PROJECTS:
            row = _row("project:" + name, name, path)
            self.report["projects"].append(row)
            self.rows.append(row)
        else:
            self.issue(_INCOMPLETE + "Project discovery limit reached; additional project rows omitted.")

    def omitted_mount(self, path: str) -> None:
        self.problem(path, "Nested mount or other device omitted from root-filesystem coverage.")
        if _within(self.home, path):
            self.issue(_INCOMPLETE + "Home is on a nested mount or another device; its bytes are omitted.")

    def visit(self, descriptor: int, name: str, path: str, depth: int, *,
              listed: bool = False, priority_only: bool = False) -> None:
        if path in self.skipped:
            return
        self.budget()
        self.report["entries_scanned"] += 1
        # Do not even stat known mountpoints: a remote/FUSE stat can block.
        if path in self.mounts:
            self.skipped.add(path)
            self.project(path, name)
            self.omitted_mount(path)
            return
        try:
            info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        except FileNotFoundError:
            self.skipped.add(path)
            # Only fixed priority names may be absent without racing a listing.
            if listed:
                self.problem(path, "Entry disappeared during scan.")
            else:
                self.absent(path)
            return
        except OSError:
            self.skipped.add(path)
            self.problem(path, "Metadata unavailable or changed during scan.")
            return
        if stat.S_ISDIR(info.st_mode):
            self.project(path, name)
        if info.st_dev != self.device:
            self.skipped.add(path)
            self.omitted_mount(path)
            return
        if stat.S_ISLNK(info.st_mode) or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
            self.skipped.add(path)
            if path in self.full_roots or path in self.priority or any(_within(row["path"], path) for row in self.rows):
                self.problem(path, "Conventional directory is a symlink or special file; unavailable.")
            return
        if stat.S_ISREG(info.st_mode):
            self.skipped.add(path)
            if path in self.full_roots or path in self.priority or any(_within(row["path"], path) for row in self.rows):
                self.problem(path, "Conventional directory is a regular file; directory coverage unavailable.")
            self.account(path, info)
            return
        try:
            child = os.open(name, _OPEN_DIRECTORY, dir_fd=descriptor)
            try:
                opened = os.fstat(child)
                if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                    self.skipped.add(path)
                    self.problem(path, "Directory changed during scan.")
                    return
                self.account(path, opened)
                if depth >= _MAX_DEPTH:
                    self.skipped.add(path)
                    self.problem(path, "Directory depth limit reached.")
                else:
                    self.walk(child, path, depth, priority_only=priority_only)
            finally:
                os.close(child)
        except OSError:
            self.skipped.add(path)
            self.problem(path, "Directory unreadable or changed during scan.")

    def walk(self, descriptor: int, path: str, depth: int, *, priority_only: bool = False) -> None:
        # First visit only fixed ancestor chains, scanning projects/services in full.
        # Then revisit their anchors for the general walk, skipping finished subtrees.
        priority_only = priority_only and path not in self.full_roots
        names = self.priority.get(path, ())
        try:
            for name in names:
                self.visit(descriptor, name, os.path.join(path, name), depth + 1, priority_only=priority_only)
            if priority_only:
                return
            with os.scandir(descriptor) as entries:
                for entry in entries:
                    self.budget()
                    if entry.name not in names:
                        self.visit(descriptor, entry.name, os.path.join(path, entry.name), depth + 1, listed=True)
        except _LimitReached:
            self.problem(path, "Shared time or entry limit reached; sizes are lower bounds.")
            raise
        except OSError:
            self.skipped.add(path)
            self.problem(path, "Directory listing unavailable or changed during scan.")
        else:
            self.skipped.add(path)
            self.finish(path)


def collect_storage_usage(home: str, *, duration_seconds: float = 15.0,
                          entry_limit: int = 200000) -> dict:
    """Estimate fixed local portions with global time/entry/depth and inode limits.

    Duration must be finite and in (0, 60], entries in [1, 2000000]. The time
    budget is cooperative between local metadata calls; it cannot cancel a
    blocked kernel syscall. Mounts are from this process's namespace, so service
    sandbox mounts are deliberately omitted too. Ancestor metadata may be observed
    again between priority and general traversal; entries count these observations,
    including absent fixed names. Bytes/inodes are counted once. No privileges
    are needed.
    """
    home = _validated_home(home)
    if (type(duration_seconds) not in (int, float) or not 0 < duration_seconds <= 60
            or type(entry_limit) is not int
            or not 1 <= entry_limit <= 2000000):
        raise ValueError("Use a finite duration in (0, 60] seconds and 1-2000000 entries")
    started = time.monotonic()
    areas = [_row("area:" + path, "/" + path, os.path.join(_ROOT, path)) for path in _AREA_PATHS]
    areas.append(_row("area:other", "Root files and other directories", _ROOT))
    report = {"generated_at": _iso_now(), "duration_seconds": 0.0,
              "entries_scanned": 0, "files_scanned": 0,
              "scanned_allocated_bytes": 0, "scanned_apparent_bytes": 0,
              "root_filesystem": None, "areas": areas, "services": _service_rows(home),
              "projects": [], "issues": [
                  "Scope: regular files and directory metadata on the root device only; nested mounts, symlinks and special files are excluded.",
                  "Areas are disjoint; services and projects overlap those areas and must not be added to them.",
                  "Global device/inode deduplication attributes hardlinks to their first observed path; shared/reflink extents may still overlap.",
                  "Directory estimates need not match df: deleted-open files, sparse files, filesystem metadata, unreadable paths and concurrent changes affect coverage.",
                  "Incomplete rows are lower bounds, not true zero; complete absent paths are explicitly labelled.",
              ], "limits": {"duration_seconds": float(duration_seconds), "entry_limit": entry_limit}}
    try:
        mounts = _mounts()
        if mounts.get(_ROOT) not in _LOCAL_TYPES:
            report["issues"].append(_INCOMPLETE + "Root filesystem is unknown or unsupported; scan refused.")
            return report
        scan = _Scan(home, report, started + duration_seconds, mounts)
        descriptor = _open_root()
        try:
            scan.device = os.fstat(descriptor).st_dev
            try:
                capacity = os.fstatvfs(descriptor)
                unit = capacity.f_frsize or capacity.f_bsize
                report["root_filesystem"] = {
                    "total_bytes": capacity.f_blocks * unit,
                    "used_bytes": (capacity.f_blocks - capacity.f_bfree) * unit,
                    "available_bytes": capacity.f_bavail * unit,
                    "reserved_bytes": max(0, capacity.f_bfree - capacity.f_bavail) * unit,
                }
            except OSError:
                scan.issue(_INCOMPLETE + "Root filesystem capacity unavailable.")
            scan.budget()
            report["entries_scanned"] += 1
            scan.account(_ROOT, os.fstat(descriptor))
            try:
                scan.walk(descriptor, _ROOT, 0, priority_only=True)
                scan.walk(descriptor, _ROOT, 0)
            except _LimitReached:
                pass
        finally:
            os.close(descriptor)
        report["projects"].sort(key=lambda row: (-row["allocated_bytes"], row["path"]))
        if len(report["projects"]) > _RETURN_PROJECTS:
            scan.issue(_INCOMPLETE + "Only the largest 30 measured projects are returned; remaining project rows omitted.")
            report["projects"] = report["projects"][:_RETURN_PROJECTS]
    except _LimitReached:
        report["issues"].append(_INCOMPLETE + "Shared time or entry limit reached before scanning.")
    except (OSError, ValueError, IndexError):
        report["issues"].append(_INCOMPLETE + "Root metadata or mount inventory unavailable; scan coverage unavailable.")
    finally:
        report["duration_seconds"] = max(0.0, time.monotonic() - started)
    return report


def _complete(report: dict) -> bool:
    return (report.get("root_filesystem") is not None
            and all(row.get("complete", False) for key in ("areas", "services", "projects")
                    for row in report.get(key, []))
            and not any(issue.startswith(_INCOMPLETE) for issue in report.get("issues", [])))


class StorageDiagnostics:
    """One daemon worker per manager; construction and snapshots never scan.

    The injected collector is called with ``home`` as its sole argument. A new
    start is allowed immediately after completion (no cooldown). Reruns retain
    the last report, including after a failed attempt. Snapshots are detached.
    """

    def __init__(self, home: str, *, collector: Callable[[str], dict] | None = None) -> None:
        self._home = _validated_home(home)
        self._collector = collector if collector is not None else collect_storage_usage
        self._lock = threading.Lock()
        self._snapshot = {"state": "idle", "started_at": None, "finished_at": None,
                          "message": "Ready. Diagnostics run only when requested.", "report": None}

    def snapshot(self) -> dict:
        """Return a detached snapshot without observing storage."""
        with self._lock:
            return deepcopy(self._snapshot)

    def start(self) -> bool:
        """Atomically start one background scan; return false while it runs."""
        with self._lock:
            if self._snapshot["state"] == "running":
                return False
            self._snapshot.update(state="running", started_at=_iso_now(), finished_at=None,
                                  message="Scanning root-filesystem metadata; the previous report remains available.")
            try:
                threading.Thread(target=self._run, name="storage-diagnostics", daemon=True).start()
            except Exception:
                self._snapshot.update(state="failed", finished_at=_iso_now(),
                                      message="Storage diagnostics could not start. Try again.")
                return False
            return True

    def _run(self) -> None:
        try:
            report = deepcopy(self._collector(self._home))
            complete = _complete(report)
        except Exception:
            with self._lock:
                self._snapshot.update(state="failed", finished_at=_iso_now(),
                                      message="Storage diagnostics failed. Check local access and try again.")
            return
        with self._lock:
            self._snapshot.update(state="complete" if complete else "partial", finished_at=_iso_now(),
                                  message=("Storage diagnostics complete." if complete else
                                           "Storage diagnostics partial; review coverage issues and lower bounds."),
                                  report=report)
