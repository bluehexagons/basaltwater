"""Crash-safe helpers for small persistent text and JSON files."""

from __future__ import annotations

import json
import os
import tempfile

from lib.types import JSON


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

    content = json.dumps(value, indent=indent, sort_keys=sort_keys) + "\n"
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


def _fsync_directory(path: str) -> None:
    """Flush directory metadata after an atomic replacement."""

    directory_descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_descriptor)
    finally:
        os.close(directory_descriptor)
