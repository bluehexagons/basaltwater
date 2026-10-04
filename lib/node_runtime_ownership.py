"""Keep explicit and legacy NVM installs outside automatic runtime cleanup."""

from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import re
import stat
from typing import Iterator

from lib.atomic_io import read_json_file, write_json_atomic
from lib.validation import validate_filesystem_path


MARKER = ".basaltwater-runtime.json"
LATEST_UPDATE_ENV = "BASALTWATER_NODE_UPDATE_LATEST"
_VERSION = re.compile(r"v?[0-9]{1,5}\.[0-9]{1,5}\.[0-9]{1,5}", re.ASCII)


def runtime_directory(nvm_dir: str | Path, version: str) -> Path:
    if not isinstance(version, str) or not _VERSION.fullmatch(version):
        raise ValueError("Runtime ownership requires an exact stable Node version")
    root = Path(nvm_dir).absolute()
    validate_filesystem_path(str(root))
    directory = root / "versions/node" / ("v" + version.removeprefix("v"))
    for parent in (directory, *directory.parents):
        if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
            raise ValueError("Runtime ownership requires regular NVM directories")
        if parent.is_relative_to(root) and parent.exists():
            info = parent.stat()
            if info.st_uid != os.getuid() or info.st_mode & 0o002:
                raise ValueError("NVM runtime directories must be owned by you and not world-writable")
    return directory


@contextmanager
def runtime_lock(nvm_dir: str | Path) -> Iterator[None]:
    """Serialize project protection and automatic removal in this NVM root."""
    import fcntl

    root = runtime_directory(nvm_dir, "0.0.0").parents[2]
    descriptor = os.open(root / ".basaltwater-runtime.lock",
                         os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("Unsafe Node runtime ownership lock")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("Another Node runtime maintenance/protection operation is active; retry later") from exc
        yield
    finally:
        os.close(descriptor)


def runtime_owner(nvm_dir: str | Path, version: str) -> str | None:
    """Missing, unsafe, or invalid provenance never authorizes cleanup."""
    try:
        directory = runtime_directory(nvm_dir, version)
        marker = directory / MARKER
        info = marker.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            return None
        record = read_json_file(str(marker), max_bytes=1024)
        if (not isinstance(record, dict) or set(record) != {"schema_version", "version", "owner"}
                or type(record["schema_version"]) is not int or record["schema_version"] != 1
                or record["version"] != directory.name or not isinstance(record["owner"], str)
                or record["owner"] not in {"automatic", "project"}):
            return None
        return record["owner"]
    except (OSError, ValueError, UnicodeError):
        return None


def mark_runtime(nvm_dir: str | Path, version: str, *, owner: str) -> None:
    if not isinstance(owner, str) or owner not in {"automatic", "project"}:
        raise ValueError("Invalid Node runtime owner")
    with runtime_lock(nvm_dir):
        directory = runtime_directory(nvm_dir, version)
        if not (directory / "bin/node").is_file():
            raise ValueError("Cannot record ownership of an absent Node runtime")
        marker = directory / MARKER
        existing = runtime_owner(nvm_dir, version)
        if os.path.lexists(marker) and existing is None:
            raise ValueError("Unsafe or invalid Node runtime ownership marker; repair it explicitly")
        if owner == "automatic" and existing == "project":
            return
        write_json_atomic(str(marker), {
            "schema_version": 1, "version": directory.name, "owner": owner,
        }, mode=0o600)
