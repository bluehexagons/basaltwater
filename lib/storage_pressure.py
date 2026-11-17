"""Bounded low-space reclamation of logs, downloads and unused build runtimes."""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess

from lib.agent_storage_inventory import inspect_storage
from lib.cleanup_paths import process_uses_path, remove_checked_path
from lib.validation import validate_filesystem_path

SYSTEM_STORAGE_PATHS = ("/var/cache/apt/archives", "/var/log/journal", "/run/log/journal",
                        "/tmp", "/var/tmp", "/var/lib/systemd/coredump")


def clean_system_storage(*, target: int, apply: bool, requirements: dict[str, dict] | None = None) -> list[dict]:
    from lib.managed_storage import filesystem, needs_reclamation

    result = []
    candidates = [
        ("apt-downloads", "/var/cache/apt/archives",
         ["apt-get", "-o", "Dir::Cache::archives=/var/cache/apt/archives", "clean"]),
    ]
    for path in ("/var/log/journal", "/run/log/journal"):
        if os.path.isdir(path):
            candidates.append(("archived-journals", path,
                ["journalctl", f"--directory={path}", "--rotate", "--vacuum-size=64M", "--vacuum-time=7d"]))
    # Keep the OS administrator's age and exclusion policy. In particular,
    # --remove/--purge would discard files regardless of their retention age.
    for path in ("/tmp", "/var/tmp", "/var/lib/systemd/coredump"):
        if os.path.isdir(path):
            candidates.append(("expired-system-files", path,
                ["systemd-tmpfiles", "--clean", f"--prefix={path}"]))
    for category, path, command in candidates:
        try:
            needed = needs_reclamation(filesystem(path), target=target, requirements=requirements)
        except (OSError, ValueError) as exc:
            result.append({"category": category, "path": path, "status": "failed", "reason": str(exc)[-2000:]})
            continue
        if not needed:
            continue
        row = {"category": category, "path": path, "status": "preview", "command": command}
        result.append(row)
        if apply:
            try:
                executable = shutil.which(command[0])
                if not executable:
                    raise OSError("Cleanup tool is unavailable")
                outcome = subprocess.run([executable, *command[1:]], capture_output=True, text=True,
                                         timeout=120, check=False)
                row["status"] = "cleaned" if outcome.returncode == 0 else "failed"
                if outcome.returncode:
                    row["reason"] = outcome.stderr[-2000:]
            except (OSError, subprocess.SubprocessError) as exc:
                row.update(status="failed", reason=str(exc)[-2000:])
    return result


def _process_uids() -> set[int]:
    result = set()
    with os.scandir("/proc") as entries:
        for index, entry in enumerate(entries):
            if index >= 32768:
                raise ValueError("Process inventory exceeds limit")
            if entry.name.isdigit():
                try:
                    result.add(entry.stat(follow_symlinks=False).st_uid)
                except FileNotFoundError:
                    continue
    return result


def _kept_versions(root: Path, versions: list[Path]) -> set[str]:
    """Keep newest in each major and all exact/partial alias selections."""
    ordered = sorted(versions, key=lambda path: tuple(map(int, path.name[1:].split("."))), reverse=True)
    keep = set()
    majors = set()
    for version in ordered:
        major = version.name.split(".")[0]
        if major not in majors:
            majors.add(major)
            keep.add(version.name)
    aliases = root / "alias"
    count = 0
    if aliases.is_symlink():
        raise ValueError("Unsafe Node alias directory")
    if aliases.exists():
        def fail_scan(error):
            raise error
        for directory, dirs, files in os.walk(aliases, followlinks=False, onerror=fail_scan):
            for name in dirs + files:
                count += 1
                path = Path(directory) / name
                if count > 128 or path.is_symlink():
                    raise ValueError("Alias inventory is unsafe or too large")
            for name in files:
                path = Path(directory) / name
                if not stat.S_ISREG(path.lstat().st_mode) or path.stat().st_size > 1024:
                    raise ValueError("Invalid Node alias")
                value = path.read_text().strip().removeprefix("v")
                if re.fullmatch(r"\d+(?:\.\d+){0,2}", value):
                    match = next((v.name for v in ordered if v.name[1:] == value or v.name[1:].startswith(value + ".")), None)
                    if match:
                        keep.add(match)
    return keep


def clean_build_versions(*, target: int, apply: bool, requirements: dict[str, dict] | None = None) -> list[dict]:
    from lib.managed_storage import BUILD_HOMES, accounts, activity, filesystem, needs_reclamation, safe_cache_path

    result = []
    for account in accounts([]):
        home = account.pw_dir
        if not account.pw_name.startswith("build-") or home != f"{BUILD_HOMES}/{account.pw_name}":
            continue
        root = Path(home) / ".nvm"
        descriptor = None
        try:
            if not root.exists() or not needs_reclamation(filesystem(str(root)), target=target, requirements=requirements):
                continue
            if activity(account.pw_uid):
                continue
            validate_filesystem_path(str(root), must_exist=True)
            if not safe_cache_path(str(root), home, account.pw_uid):
                raise ValueError("Unsafe NVM root")
            lock = root / ".basaltwater-runtime.lock"
            if apply:
                try:
                    descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600)
                    os.fchown(descriptor, account.pw_uid, account.pw_gid)
                except FileExistsError:
                    descriptor = os.open(lock, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
                info = os.fstat(descriptor)
                if not stat.S_ISREG(info.st_mode) or info.st_uid != account.pw_uid or info.st_mode & 0o077 or info.st_nlink != 1:
                    raise ValueError("Unsafe Node runtime lock")
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            parent = root / "versions/node"
            if not parent.exists():
                continue
            if not safe_cache_path(str(parent), home, account.pw_uid):
                raise ValueError("Unsafe Node versions directory")
            versions = []
            for index, path in enumerate(parent.iterdir()):
                if index >= 128:
                    raise ValueError("Node version inventory exceeds limit")
                if re.fullmatch(r"v[0-9]{1,5}\.[0-9]{1,5}\.[0-9]{1,5}", path.name):
                    if not safe_cache_path(str(path), home, account.pw_uid):
                        raise ValueError("Unsafe installed Node version")
                    versions.append(path)
            keep = _kept_versions(root, versions)
            uids = _process_uids()
            for version in sorted(versions, key=lambda p: tuple(map(int, p.name[1:].split(".")))):
                if version.name in keep or not needs_reclamation(filesystem(str(version)), target=target, requirements=requirements):
                    continue
                row = {"path": str(version), "status": "deferred"}
                result.append(row)
                if not safe_cache_path(str(version), home, account.pw_uid):
                    row["reason"] = "Version directory changed or is unsafe"
                    continue
                identity = version.lstat()
                measured = inspect_storage(home=home, paths=[str(version)], timeout=10)
                if measured["issues"] or len(measured["candidates"]) != 1:
                    row["reason"] = "Version inventory is unavailable or incomplete"
                    continue
                scan = measured["candidates"][0]
                if (scan["status"] != "measured" or set(scan["issues"]) - {"symlinks_skipped"}
                        or scan["other_owner_entries"] or scan["hardlinked_files"] or scan["is_mountpoint"]):
                    row["reason"] = "Version inventory is unsafe or incomplete"
                    continue
                def in_use():
                    return bool(activity(account.pw_uid)) or any(
                        process_uses_path(str(version), uid=uid, include_open_files=True) for uid in uids | _process_uids())
                if remove_checked_path(str(version), identity, uid=account.pw_uid,
                                       in_use=in_use, dry_run=not apply):
                    row["status"] = "cleaned" if apply else "preview"
                else:
                    row["reason"] = "Version is in use or process visibility is incomplete"
        except (OSError, ValueError) as exc:
            result.append({"path": str(root), "status": "deferred_or_failed", "reason": str(exc)[-2000:]})
        finally:
            if descriptor is not None:
                os.close(descriptor)
    return result
