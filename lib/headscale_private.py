"""Small private documents shared by Headscale controller recovery paths."""

from __future__ import annotations

import json
import os
import stat

from lib.atomic_io import parse_json, write_json_atomic
from lib.setup_directories import check_directory_path, directory_fd
from lib.validation import validate_filesystem_path

LIMIT = 64 * 1024


def read_private(path: str) -> dict | None:
    validate_filesystem_path(path)
    check_directory_path(os.path.dirname(path))
    try:
        with directory_fd(os.path.dirname(path)) as parent:
            info = os.fstat(parent)
            if info.st_uid != os.geteuid() or info.st_mode & 0o077:
                raise ValueError("Unsafe private Headscale state directory")
            fd = os.open(os.path.basename(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "rb") as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1
                or info.st_mode & 0o077 or info.st_size > LIMIT):
            raise ValueError("Unsafe private Headscale state file")
        content = source.read(LIMIT + 1)
    if len(content) > LIMIT:
        raise ValueError("Private Headscale state exceeds limit")
    document = parse_json(content.decode())
    if type(document) is not dict:
        raise ValueError("Invalid private Headscale state")
    return document


def write_private(path: str, value: dict) -> None:
    if len(json.dumps(value).encode()) > LIMIT:
        raise ValueError("Private Headscale state exceeds limit")
    with directory_fd(os.path.dirname(path), create=True, mode=0o700) as parent:
        info = os.fstat(parent)
        if info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError("Unsafe private Headscale state directory")
        write_json_atomic(path, value, mode=0o600)
