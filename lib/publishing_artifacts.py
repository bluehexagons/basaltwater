"""Completed build records and bounded snapshots of regular-file artifacts."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import stat

from lib.atomic_io import read_json_file, write_json_atomic
from lib.publishing_store import digest, identifier, private_directory
from lib.validation import validate_filesystem_path

MAX_FILES = 10000
MAX_BYTES = 30 * 1024 ** 3
_PRIVATE_NAMES = {".git", ".ssh", ".config", ".env", "butler_creds", "config.vdf", "loginusers.vdf", "ssfn"}


def checked_directory(value: str) -> Path:
    validate_filesystem_path(value, must_exist=True)
    path = Path(value).absolute()
    for ancestor in (*reversed(path.parents), path):
        if ancestor.is_symlink():
            raise ValueError("Artifact directories cannot contain linked ancestors")
    if not path.is_dir():
        raise ValueError("Artifact must be a directory")
    return path


def relative_path(value: object) -> str:
    if not isinstance(value, str) or not value or value == ".":
        raise ValueError("Select an artifact subdirectory")
    validate_filesystem_path(value, must_exist=False)
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or any(part in _PRIVATE_NAMES for part in path.parts):
        raise ValueError("Artifact path must stay inside the project and exclude private files")
    return path.as_posix()


def scan(source: Path, destination: Path | None = None) -> list[dict]:
    """Walk with directory descriptors so swaps cannot follow symlinks."""
    entries = []
    total = 0
    for directory, dirs, files, root_fd in os.fwalk(source, follow_symlinks=False):
        for name in dirs + files:
            if (name.lower() in _PRIVATE_NAMES or name.lower().startswith((".env.", "ssfn"))
                    or name.lower().endswith((".pem", ".key"))):
                raise ValueError("Artifact includes a credential or private-state path")
            info = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
            if not stat.S_ISDIR(info.st_mode) and not stat.S_ISREG(info.st_mode):
                raise ValueError("Artifact contains a link or special file")
        for name in sorted(files):
            relative = (Path(directory).relative_to(source) / name).as_posix()
            validate_filesystem_path(relative, must_exist=False)
            if len(relative.encode()) > 1024:
                raise ValueError("Artifact paths exceed the 1024-byte limit")
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=root_fd)
            with os.fdopen(fd, "rb") as stream:
                before = os.fstat(stream.fileno())
                if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                    raise ValueError("Artifacts cannot contain hard links or special files")
                total += before.st_size
                if total > MAX_BYTES or len(entries) >= MAX_FILES:
                    raise ValueError("Artifact exceeds the file or byte limit")
                sha = hashlib.sha256()
                output = None
                if destination:
                    target = destination / relative
                    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    output = target.open("xb")
                try:
                    count = 0
                    while chunk := stream.read(1024 * 1024):
                        count += len(chunk)
                        if count > before.st_size:
                            raise ValueError("Artifact changed during preparation")
                        sha.update(chunk)
                        if output:
                            output.write(chunk)
                    after = os.fstat(stream.fileno())
                    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns) or count != before.st_size:
                        raise ValueError("Artifact changed during preparation")
                    if output:
                        output.flush()
                        os.fsync(output.fileno())
                        os.fchmod(output.fileno(), 0o700 if before.st_mode & 0o111 else 0o600)
                finally:
                    if output:
                        output.close()
                entries.append({"path": relative, "size": count, "sha256": sha.hexdigest(), "executable": bool(before.st_mode & 0o111)})
    if not entries:
        raise ValueError("Artifact is empty")
    return sorted(entries, key=lambda entry: entry["path"])


def complete_record(repository: str, path: str, build_id: str, record_path: str) -> dict:
    """Build systems call this only after their export is complete."""
    root = checked_directory(repository)
    path = relative_path(path)
    record_path = relative_path(record_path)
    source = checked_directory(str(root / path))
    if (root / record_path).is_relative_to(source):
        raise ValueError("Completion record must be outside the artifact")
    record = {"version": 1, "path": path, "build_id": identifier(build_id), "digest": digest(scan(source))}
    target = root / record_path
    for ancestor in (*reversed(target.parents), target):
        if ancestor.is_symlink():
            raise ValueError("Completion record cannot follow links")
    write_json_atomic(str(target), record, mode=0o600)
    return record


def snapshot(repository: str, record_path: str, destination: Path) -> dict:
    root = checked_directory(repository)
    record_path = relative_path(record_path)
    target = root / record_path
    for ancestor in (*reversed(target.parents), target):
        if ancestor.is_symlink():
            raise ValueError("Completion record cannot follow links")
    record = read_json_file(str(target), max_bytes=4096)
    if not isinstance(record, dict) or set(record) != {"version", "path", "build_id", "digest"} or type(record["version"]) is not int or record["version"] != 1:
        raise ValueError("Invalid version-1 completion record")
    identifier(record["build_id"])
    source = checked_directory(str(root / relative_path(record["path"])))
    private_directory(destination)
    try:
        entries = scan(source, destination)
        if digest(entries) != record["digest"]:
            raise ValueError("Completed artifact digest no longer matches")
        return {**record, "entries": entries, "snapshot": str(destination)}
    except BaseException:
        shutil.rmtree(destination)
        raise
