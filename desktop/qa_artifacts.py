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
    remedy = ("Create a dedicated parent directory owned by you with mode 0700 outside Git, "
              "or omit --output when optional for a private temporary bundle")
    try:
        parent = path.parent.stat()
    except FileNotFoundError as exc:
        raise ValueError(f"Evidence output parent directory does not exist. {remedy}") from exc
    except NotADirectoryError as exc:
        raise ValueError(f"Evidence output parent is not a directory. {remedy}") from exc
    if not stat.S_ISDIR(parent.st_mode):
        raise ValueError(f"Evidence output parent is not a directory. {remedy}")
    if parent.st_uid != os.getuid():
        raise ValueError(f"Evidence output parent is not owned by the current account. {remedy}")
    if parent.st_mode & 0o077:
        raise ValueError(f"Evidence output parent is not private (group/other permissions are set). {remedy}")
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
    directory = os.open(f"/proc/{pid}", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if os.fstat(directory).st_uid != os.getuid():
            raise ValueError("Application must belong to the current account")
        # Read through the pinned directory so PID reuse cannot mix ownership and start ticks.
        fd = os.open("stat", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
        with os.fdopen(fd, "rb") as info:
            # comm can contain spaces and parentheses. Fields after its last ')' start at 3.
            _, separator, tail = info.read(8192).rpartition(b")")
            fields = tail.split()
    finally:
        os.close(directory)
    if not separator or len(fields) < 20 or not fields[19].isdigit() or fields[0] == b"Z":
        raise ValueError("Application process identity is unavailable")
    return {"pid": pid, "start_ticks": fields[19].decode("ascii")}


def parse_process_identities(selected: list[list[str]]) -> list[tuple[int, str]]:
    if len(selected) > 16:
        raise ValueError("Inspect at most 16 exact process identities")
    identities = []
    for identity in selected:
        if (not isinstance(identity, list) or len(identity) != 2
                or any(not isinstance(n, str) or not re.fullmatch(r"[0-9]{1,24}", n) for n in identity)):
            raise ValueError("Processes require PID START_TICKS from a prior exact identity receipt")
        pid, ticks = identity
        if not 0 < int(pid) <= 2147483647:
            raise ValueError("Process PID is out of range")
        identities.append((int(pid), ticks))
    return identities


def observe_process_identity(pid: int, ticks: str) -> dict:
    resource = {"pid": pid, "expected_start_ticks": ticks, "normal_exit": "unknown"}
    try:
        current = process_identity(pid)
        resource.update(state="running-preserve" if current["start_ticks"] == ticks else "identity-replaced",
                        observed=current)
    except (FileNotFoundError, ProcessLookupError):
        resource["state"] = "identity-not-present"
    except (OSError, ValueError) as exc:
        resource.update(state="unavailable", detail=str(exc)[:300])
    return resource


def load_process_identity(path: str, *, boot_id: str | None) -> tuple[int, str]:
    from lib.atomic_io import read_json_file

    receipt = read_json_file(path, max_bytes=8192)
    if (not isinstance(receipt, dict) or receipt.get("kind") != "process-identity"
            or type(receipt.get("schema_version")) is not int or receipt["schema_version"] != 1
            or not isinstance(receipt.get("process"), dict)):
        raise ValueError("Expected a process.json from desktop evidence process")
    receipt_clock = receipt.get("clock")
    if not boot_id or not isinstance(receipt_clock, dict) or receipt_clock.get("boot_id") != boot_id:
        raise ValueError("Process receipt must come from the current Linux boot")
    pid, ticks = receipt["process"].get("pid"), receipt["process"].get("start_ticks")
    if type(pid) is not int or not 0 < pid <= 2147483647 or not isinstance(ticks, str) or not re.fullmatch(r"[0-9]{1,24}", ticks):
        raise ValueError("Process receipt has an invalid PID or start ticks")
    return pid, ticks


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
