"""Crash-safe helpers for small persistent text and JSON files."""

from __future__ import annotations

import json
import math
import os
import stat
import tempfile

from lib.types import JSON
from lib.validation import validate_filesystem_path


def _json_object(pairs: list[tuple[str, JSON]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON contains duplicate object keys")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError("JSON contains a non-finite number")


def _finite_json_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("JSON contains a non-finite number")
    return number


def read_json_file(path: str, *, max_bytes: int = 1024 * 1024) -> JSON:
    """Read bounded JSON from a regular file without following the final symlink."""
    validate_filesystem_path(path)
    if type(max_bytes) is not int or max_bytes <= 0:
        raise ValueError('JSON read limit must be a positive integer')
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError(f'JSON path must be a regular file: {path}')
        with os.fdopen(descriptor, 'rb') as stream:
            descriptor = -1
            content = stream.read(max_bytes + 1)
        if len(content) > max_bytes:
            raise ValueError(f'JSON file exceeds {max_bytes} bytes: {path}')
        try:
            return json.loads(
                content.decode('utf-8'), object_pairs_hook=_json_object,
                parse_constant=_reject_json_constant, parse_float=_finite_json_float,
            )
        except RecursionError as exc:
            raise ValueError(f'JSON nesting is too deep: {path}') from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def write_text_atomic(
    path: str, content: str, *, mode: int = 0o600,
    uid: int = -1, gid: int = -1,
) -> None:
    """Write text using a same-directory temporary file and atomic replace.

    The temporary file is flushed and fsynced before replacement, and the
    containing directory is synced after replacement so an interrupted write
    cannot leave a partial target file behind.
    """

    target_path = os.path.abspath(path)
    parent_dir = os.path.dirname(target_path)
    os.makedirs(parent_dir, exist_ok=True)

    file_descriptor, temporary_path = tempfile.mkstemp(
        dir=parent_dir,
        prefix=f".{os.path.basename(target_path)}-",
        text=True,
    )
    descriptor_open = True
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as file_obj:
            descriptor_open = False
            file_obj.write(content)
            file_obj.flush()
            if uid != -1 or gid != -1:
                os.fchown(file_obj.fileno(), uid, gid)
            os.fchmod(file_obj.fileno(), mode)
            os.fsync(file_obj.fileno())
        os.replace(temporary_path, target_path)
        _fsync_directory(parent_dir)
    finally:
        if descriptor_open:
            os.close(file_descriptor)
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass


def write_json_atomic(
    path: str,
    value: JSON,
    *,
    mode: int = 0o600,
    sort_keys: bool = False,
    indent: int | None = 2,
    uid: int = -1,
    gid: int = -1,
) -> None:
    """Serialize JSON and persist it through :func:`write_text_atomic`."""

    content = json.dumps(value, indent=indent, sort_keys=sort_keys, allow_nan=False) + "\n"
    write_text_atomic(path, content, mode=mode, uid=uid, gid=gid)


def remove_file_durable(path: str) -> bool:
    """Remove one file and sync its directory so deletion survives a crash."""

    target_path = os.path.abspath(path)
    try:
        os.unlink(target_path)
    except FileNotFoundError:
        return False
    _fsync_directory(os.path.dirname(target_path))
    return True


def rename_path_durable(source: str, destination: str) -> None:
    """Rename an artifact and sync both parent directories before proceeding.

    A sync failure can occur after the rename has taken effect. Operation owners
    must reconcile the recorded paths before attempting recovery.
    """
    validate_filesystem_path(source)
    validate_filesystem_path(destination)
    os.rename(source, destination)
    for parent in dict.fromkeys((
        os.path.dirname(os.path.abspath(source)),
        os.path.dirname(os.path.abspath(destination)),
    )):
        _fsync_directory(parent)


def fsync_tree(path: str) -> None:
    """Flush release contents and directories before discarding its backup.

    Links created by build tooling are flushed with their parent directory;
    their targets are never followed. Special files are refused without waiting
    on a FIFO. The caller owns the tree and must have finished writing it.
    """
    validate_filesystem_path(path, must_exist=True)
    if os.path.islink(path) or not os.path.isdir(path):
        raise ValueError(f"Release tree must be a directory, not a link: {path}")

    def walk_error(error):
        raise error

    for current, _directories, files in os.walk(path, topdown=False, followlinks=False, onerror=walk_error):
        for name in files:
            candidate = os.path.join(current, name)
            if stat.S_ISLNK(os.lstat(candidate).st_mode):
                continue
            descriptor = os.open(candidate, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                    raise ValueError(f"Release file must be regular: {candidate}")
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        descriptor = os.open(current, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _fsync_directory(path: str) -> None:
    """Flush directory metadata after an atomic replacement."""

    directory_descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_descriptor)
    finally:
        os.close(directory_descriptor)
