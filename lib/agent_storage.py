"""Conservative retention helpers for managed coding-agent installations."""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import time
from dataclasses import dataclass

from lib.validation import validate_filesystem_path


_CODEX_RELEASE_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*")
_T3_ROTATED_LOG_PATTERN = re.compile(r".+\.(?:log|ndjson)\.[1-9][0-9]*$")
_MAX_CODEX_MANIFEST_BYTES = 64 * 1024


@dataclass(frozen=True)
class CodexRelease:
    """A standalone Codex release whose vendor layout was validated."""

    name: str
    path: str
    mtime: float
    device: int
    inode: int


@dataclass(frozen=True)
class CodexCleanupResult:
    """Outcome of one standalone Codex release reconciliation."""

    found: tuple[str, ...] = ()
    retained: tuple[str, ...] = ()
    active: tuple[str, ...] = ()
    selected: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class RotatedLog:
    """One regular numbered log rotation eligible for bounded cleanup."""

    path: str
    size_bytes: int
    mtime: float
    device: int
    inode: int


@dataclass(frozen=True)
class T3LogCleanupResult:
    """Outcome of one T3 numbered-log reconciliation."""

    found_count: int = 0
    found_bytes: int = 0
    selected: tuple[str, ...] = ()
    selected_bytes: int = 0
    removed: tuple[str, ...] = ()
    removed_bytes: int = 0
    errors: tuple[str, ...] = ()


def _validate_user_directory_chain(home: str, path: str, uid: int) -> None:
    """Validate an existing, user-owned directory chain beneath ``home``."""
    validate_filesystem_path(home, must_exist=True)
    validate_filesystem_path(path, must_exist=True)
    absolute_home = os.path.abspath(home)
    absolute_path = os.path.abspath(path)
    if absolute_path == absolute_home:
        raise ValueError("managed path is the user home")
    if os.path.commonpath((absolute_home, absolute_path)) != absolute_home:
        raise ValueError("managed path is outside the user home")

    current = absolute_home
    relative_parts = os.path.relpath(absolute_path, absolute_home).split(os.path.sep)
    for part in ("", *relative_parts):
        if part:
            current = os.path.join(current, part)
        current_stat = os.lstat(current)
        if stat.S_ISLNK(current_stat.st_mode):
            raise ValueError(f"managed path contains a symbolic link: {current}")
        if not stat.S_ISDIR(current_stat.st_mode):
            raise ValueError(f"managed path component is not a directory: {current}")
        if current_stat.st_uid != uid:
            raise ValueError(f"managed path is not owned by uid {uid}: {current}")


def _validate_codex_release(entry: os.DirEntry[str], uid: int) -> CodexRelease:
    """Return a release only when its expected standalone layout is intact."""
    entry_stat = entry.stat(follow_symlinks=False)
    if not stat.S_ISDIR(entry_stat.st_mode) or entry.is_symlink():
        raise ValueError("release entry is not a regular directory")
    if entry_stat.st_uid != uid:
        raise ValueError(f"release entry is not owned by uid {uid}")

    manifest_path = os.path.join(entry.path, "codex-package.json")
    manifest_stat = os.lstat(manifest_path)
    if not stat.S_ISREG(manifest_stat.st_mode) or manifest_stat.st_uid != uid:
        raise ValueError("release manifest is not a user-owned regular file")
    if manifest_stat.st_size > _MAX_CODEX_MANIFEST_BYTES:
        raise ValueError("release manifest is unexpectedly large")
    with open(manifest_path, encoding="utf-8") as manifest_file:
        manifest = json.load(manifest_file)

    if not isinstance(manifest, dict):
        raise ValueError("release manifest is not an object")
    version = manifest.get("version")
    target = manifest.get("target")
    if (
        manifest.get("layoutVersion") != 1
        or manifest.get("variant") != "codex"
        or manifest.get("entrypoint") != "bin/codex"
        or not isinstance(version, str)
        or not _CODEX_RELEASE_COMPONENT.fullmatch(version)
        or not isinstance(target, str)
        or not _CODEX_RELEASE_COMPONENT.fullmatch(target)
        or entry.name != f"{version}-{target}"
    ):
        raise ValueError("release manifest does not match the supported Codex layout")

    executable_path = os.path.join(entry.path, "bin", "codex")
    _validate_user_directory_chain(entry.path, os.path.dirname(executable_path), uid)
    executable_stat = os.lstat(executable_path)
    if (
        not stat.S_ISREG(executable_stat.st_mode)
        or executable_stat.st_uid != uid
        or executable_stat.st_mode & 0o111 == 0
    ):
        raise ValueError("release entrypoint is not a user-owned executable")

    return CodexRelease(
        name=entry.name,
        path=entry.path,
        mtime=entry_stat.st_mtime,
        device=entry_stat.st_dev,
        inode=entry_stat.st_ino,
    )


def _resolve_current_release(
    standalone_root: str,
    releases: dict[str, CodexRelease],
    uid: int,
) -> str:
    """Resolve the vendor-managed ``current`` link to a validated release."""
    current_path = os.path.join(standalone_root, "current")
    current_stat = os.lstat(current_path)
    if not stat.S_ISLNK(current_stat.st_mode) or current_stat.st_uid != uid:
        raise ValueError("Codex current entry is not a user-owned symbolic link")
    resolved = os.path.realpath(current_path)
    releases_root = os.path.realpath(os.path.join(standalone_root, "releases"))
    if os.path.dirname(resolved) != releases_root:
        raise ValueError("Codex current link does not target a direct release")
    current_name = os.path.basename(resolved)
    release = releases.get(current_name)
    if release is None or os.path.realpath(release.path) != resolved:
        raise ValueError("Codex current link does not target a validated release")
    return current_name


def codex_package_for_executable(home: str, executable: str) -> str | None:
    """Recognize a validated standalone package, including our rollback copy."""
    resolved = os.path.realpath(executable)
    package = os.path.dirname(os.path.dirname(resolved))
    if resolved != os.path.join(package, "bin", "codex"):
        return None
    releases = os.path.join(home, ".codex", "packages", "standalone", "releases")
    backups = os.path.join(home, ".local", "state", "basaltwater", "agent-backups")
    parent = os.path.dirname(package)
    if parent != releases and not (
        os.path.dirname(parent) == backups
        and os.path.basename(parent).startswith("codex-package-")
    ):
        return None
    try:
        uid = os.stat(home).st_uid
        _validate_user_directory_chain(home, package, uid)
        with os.scandir(parent) as entries:
            for entry in entries:
                if entry.path == package:
                    return _validate_codex_release(entry, uid).path
    except (OSError, ValueError):
        return None
    return None


def codex_update_supported(home: str, executable: str) -> bool:
    """Keep package-manager links and version-manager shims with their owner."""
    launcher = os.path.join(home, ".local", "bin", "codex")
    if os.path.abspath(executable) != launcher:
        return False
    if codex_package_for_executable(home, executable) is not None:
        return True
    # Legacy installations used a single file at the canonical user launcher.
    # Also accept the legacy rollback file produced by Basaltwater.
    return os.path.realpath(executable) in (
        launcher,
        os.path.join(home, ".local", "state", "basaltwater", "agent-backups", "codex.previous"),
    )


def prune_codex_update_backups(home: str, keep: str, *, proc_root: str = "/proc") -> None:
    """Retain the latest package snapshot, the selected CLI, and running copies."""
    root = os.path.join(home, ".local", "state", "basaltwater", "agent-backups")
    uid = os.stat(home).st_uid
    _validate_user_directory_chain(home, root, uid)
    selected = os.path.realpath(os.path.join(home, ".local", "bin", "codex"))
    with os.scandir(root) as entries:
        for entry in entries:
            if not entry.name.startswith("codex-package-") or entry.is_symlink():
                continue
            if not entry.is_dir(follow_symlinks=False):
                continue
            with os.scandir(entry.path) as contents:
                children = list(contents)
            if len(children) != 1:
                continue
            binary = os.path.join(children[0].path, "bin", "codex")
            package = codex_package_for_executable(home, binary)
            if package is None or package == os.path.dirname(os.path.dirname(keep)) or selected == binary:
                continue
            release = _validate_codex_release(children[0], uid)
            if _active_codex_releases({release.name: release}, uid, proc_root):
                continue
            shutil.rmtree(entry.path)


def _active_codex_releases(
    releases: dict[str, CodexRelease],
    uid: int,
    proc_root: str,
) -> set[str]:
    """Find validated releases currently executing for the target account."""
    active: set[str] = set()
    with os.scandir(proc_root) as processes:
        for process in processes:
            if not process.name.isdigit():
                continue
            try:
                if process.stat(follow_symlinks=False).st_uid != uid:
                    continue
                executable = os.path.realpath(os.readlink(os.path.join(process.path, "exe")))
            except OSError:
                # Process exit races and inaccessible kernel tasks are expected.
                continue
            for release in releases.values():
                try:
                    inside_release = (
                        os.path.commonpath((release.path, executable)) == release.path
                    )
                except ValueError:
                    inside_release = False
                if inside_release:
                    active.add(release.name)
                    break
    return active


def cleanup_codex_standalone_releases(
    home: str,
    uid: int,
    *,
    dry_run: bool,
    keep_rollback: int = 1,
    proc_root: str = "/proc",
) -> CodexCleanupResult:
    """Keep current, rollback, and active Codex releases; prune validated extras."""
    if keep_rollback < 0:
        raise ValueError("keep_rollback must not be negative")
    standalone_root = os.path.join(home, ".codex", "packages", "standalone")
    releases_root = os.path.join(standalone_root, "releases")
    if not os.path.lexists(standalone_root):
        return CodexCleanupResult()

    errors: list[str] = []
    skipped: list[str] = []
    releases: dict[str, CodexRelease] = {}
    try:
        _validate_user_directory_chain(home, releases_root, uid)
        with os.scandir(releases_root) as entries:
            for entry in entries:
                try:
                    release = _validate_codex_release(entry, uid)
                except (OSError, ValueError, json.JSONDecodeError):
                    skipped.append(entry.name)
                    continue
                releases[release.name] = release
    except (OSError, ValueError) as exc:
        return CodexCleanupResult(errors=(f"Codex release inventory: {exc}",))

    found = tuple(sorted(releases))
    try:
        current = _resolve_current_release(standalone_root, releases, uid)
        current_link_stat = os.lstat(os.path.join(standalone_root, "current"))
    except (OSError, ValueError) as exc:
        return CodexCleanupResult(
            found=found,
            retained=found,
            skipped=tuple(sorted(skipped)),
            errors=(f"Codex current release: {exc}",),
        )
    try:
        active = _active_codex_releases(releases, uid, proc_root)
    except OSError as exc:
        return CodexCleanupResult(
            found=found,
            retained=found,
            skipped=tuple(sorted(skipped)),
            errors=(f"Codex process inventory: {exc}",),
        )

    retained = {current, *active}
    rollback_candidates = sorted(
        (release for release in releases.values() if release.name not in retained),
        key=lambda release: (release.mtime, release.name),
        reverse=True,
    )
    retained.update(release.name for release in rollback_candidates[:keep_rollback])
    selected = tuple(sorted(set(releases) - retained))
    if dry_run or not selected:
        return CodexCleanupResult(
            found=found,
            retained=tuple(sorted(retained)),
            active=tuple(sorted(active)),
            selected=selected,
            skipped=tuple(sorted(skipped)),
            errors=tuple(errors),
        )

    removed: list[str] = []
    for name in selected:
        release = releases[name]
        try:
            latest_link_stat = os.lstat(os.path.join(standalone_root, "current"))
            if (
                latest_link_stat.st_dev != current_link_stat.st_dev
                or latest_link_stat.st_ino != current_link_stat.st_ino
            ):
                errors.append("Codex current release changed during cleanup")
                break
            if _resolve_current_release(standalone_root, releases, uid) == name:
                retained.add(name)
                continue
            if name in _active_codex_releases({name: release}, uid, proc_root):
                retained.add(name)
                continue
            current_stat = os.lstat(release.path)
            if (
                not stat.S_ISDIR(current_stat.st_mode)
                or current_stat.st_dev != release.device
                or current_stat.st_ino != release.inode
            ):
                raise OSError("release changed during cleanup")
            shutil.rmtree(release.path)
            removed.append(name)
        except (OSError, ValueError) as exc:
            errors.append(f"{release.path}: {exc}")

    return CodexCleanupResult(
        found=found,
        retained=tuple(sorted(retained)),
        active=tuple(sorted(active)),
        selected=selected,
        removed=tuple(sorted(removed)),
        skipped=tuple(sorted(skipped)),
        errors=tuple(errors),
    )


def _inventory_t3_rotated_logs(home: str, uid: int, base_dir: str | None) -> list[RotatedLog]:
    """Inventory only user-owned, regular numbered rotations without links."""
    log_root = os.path.join(base_dir or os.path.join(home, ".t3"), "userdata", "logs")
    if not os.path.lexists(log_root):
        return []
    _validate_user_directory_chain(home, log_root, uid)

    logs: list[RotatedLog] = []
    for current_root, directories, filenames in os.walk(
        log_root,
        topdown=True,
        followlinks=False,
    ):
        directories[:] = [
            name
            for name in directories
            if not os.path.islink(os.path.join(current_root, name))
        ]
        for filename in filenames:
            if not _T3_ROTATED_LOG_PATTERN.fullmatch(filename):
                continue
            path = os.path.join(current_root, filename)
            path_stat = os.lstat(path)
            if not stat.S_ISREG(path_stat.st_mode) or path_stat.st_uid != uid:
                continue
            logs.append(
                RotatedLog(
                    path=path,
                    size_bytes=path_stat.st_size,
                    mtime=path_stat.st_mtime,
                    device=path_stat.st_dev,
                    inode=path_stat.st_ino,
                )
            )
    return logs


def cleanup_t3_rotated_logs(
    home: str,
    uid: int,
    *,
    dry_run: bool,
    max_bytes: int,
    max_age_days: int,
    base_dir: str | None = None,
) -> T3LogCleanupResult:
    """Prune only numbered T3 rotations by age and total retained size."""
    try:
        logs = _inventory_t3_rotated_logs(home, uid, base_dir)
    except (OSError, ValueError) as exc:
        return T3LogCleanupResult(errors=(f"T3 rotated log inventory: {exc}",))
    if not logs:
        return T3LogCleanupResult()

    cutoff = time.time() - (max_age_days * 24 * 60 * 60)
    selected_paths = {entry.path for entry in logs if entry.mtime < cutoff}
    retained_bytes = sum(
        entry.size_bytes for entry in logs if entry.path not in selected_paths
    )
    if retained_bytes > max_bytes:
        for entry in sorted(logs, key=lambda candidate: candidate.mtime):
            if entry.path in selected_paths:
                continue
            selected_paths.add(entry.path)
            retained_bytes -= entry.size_bytes
            if retained_bytes <= max_bytes:
                break

    selected = tuple(entry.path for entry in logs if entry.path in selected_paths)
    selected_bytes = sum(
        entry.size_bytes for entry in logs if entry.path in selected_paths
    )
    if dry_run:
        return T3LogCleanupResult(
            found_count=len(logs),
            found_bytes=sum(entry.size_bytes for entry in logs),
            selected=selected,
            selected_bytes=selected_bytes,
        )

    removed: list[str] = []
    removed_bytes = 0
    errors: list[str] = []
    for entry in logs:
        if entry.path not in selected_paths:
            continue
        try:
            current_stat = os.lstat(entry.path)
            if (
                not stat.S_ISREG(current_stat.st_mode)
                or current_stat.st_uid != uid
                or current_stat.st_dev != entry.device
                or current_stat.st_ino != entry.inode
            ):
                raise OSError("rotated log changed during cleanup")
            os.unlink(entry.path)
            removed.append(entry.path)
            removed_bytes += entry.size_bytes
        except OSError as exc:
            errors.append(f"{entry.path}: {exc}")

    return T3LogCleanupResult(
        found_count=len(logs),
        found_bytes=sum(entry.size_bytes for entry in logs),
        selected=selected,
        selected_bytes=selected_bytes,
        removed=tuple(removed),
        removed_bytes=removed_bytes,
        errors=tuple(errors),
    )
