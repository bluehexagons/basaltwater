"""Private, bounded evidence files and a common local observation clock."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import stat
import tempfile
import time

from lib.validation import validate_filesystem_path

MAX_MEDIA_BYTES = 128 * 1024 * 1024


def stamp() -> dict:
    return {"at": datetime.now(timezone.utc).isoformat(), "monotonic_ns": time.monotonic_ns()}


def clock() -> dict:
    try:
        value = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        value = ""
    return {"boot_id": value if re.fullmatch(r"[0-9a-f-]{36}", value) else None,
            "monotonic_scope": "linux-boot"}


def new_bundle(output: str | None = None) -> Path:
    if output is None:
        return Path(tempfile.mkdtemp(prefix="basaltwater-qa-", dir="/tmp"))
    validate_filesystem_path(output)
    path = Path(output).absolute()
    if path.exists() or path.is_symlink():
        raise FileExistsError("Evidence output already exists")
    if path != path.resolve():
        raise ValueError("Evidence output must not traverse symlinks or parent components")
    if any((parent / ".git").exists() for parent in (path.parent, *path.parents)):
        raise ValueError("Keep evidence outside Git worktrees")
    parent = path.parent.stat()
    if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.getuid() or parent.st_mode & 0o077:
        raise ValueError("Evidence output requires an existing private owned parent directory")
    path.mkdir(mode=0o700)
    return path


def file_digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def snapshot(source: str, destination: Path) -> dict:
    """Pin a regular input and copy it, refusing changes during the bounded read."""
    validate_filesystem_path(source, must_exist=True)
    path = Path(source).absolute()
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as original:
        before = os.fstat(original.fileno())
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= MAX_MEDIA_BYTES:
            raise ValueError("Evidence input must be a regular file of 1 byte to 128 MiB")
        digest, count = hashlib.sha256(), 0
        with destination.open("xb") as copy:
            os.fchmod(copy.fileno(), 0o600)
            while chunk := original.read(1024 * 1024):
                count += len(chunk)
                if count > before.st_size:
                    raise ValueError("Evidence input grew while copying")
                digest.update(chunk)
                copy.write(chunk)
        after = os.fstat(original.fileno())
        if (count != before.st_size or
                (before.st_mtime_ns, before.st_ctime_ns) != (after.st_mtime_ns, after.st_ctime_ns)):
            raise ValueError("Evidence input changed while copying")
    return {"artifact": destination.name, "sha256": digest.hexdigest(), "bytes": count}


def process_identity(pid: int) -> dict:
    if type(pid) is not int or not 0 < pid <= 2147483647:
        raise ValueError("A positive application PID is required")
    root = Path(f"/proc/{pid}")
    if root.stat().st_uid != os.getuid():
        raise ValueError("Application must belong to the current account")
    # comm can contain spaces and parentheses. Fields after its LAST ')' start at 3.
    fields = (root / "stat").read_text()[:8192].rsplit(")", 1)[1].split()
    if len(fields) < 20 or not fields[19].isdigit() or fields[0] == "Z":
        raise ValueError("Application process identity is unavailable")
    return {"pid": pid, "start_ticks": fields[19]}


def process_snapshot(pid: int, output: str | None = None) -> dict:
    """Retain a selected live identity before a context change or normal quit."""
    from lib.atomic_io import write_json_atomic

    identity = process_identity(pid)
    data = {"schema_version": 1, "kind": "process-identity", "clock": clock(),
            "observed": stamp(), "process": identity, "input_authority": False,
            "normal_exit": "unknown",
            "limit": "Point-in-time, current-account Linux identity only; not application ownership, continued liveness or normal-exit proof. Compare start ticks only on the same boot."}
    folder = new_bundle(output)
    artifact = folder / "process.json"
    write_json_atomic(str(artifact), data, mode=0o600)
    return {"artifact": str(artifact), **data}
