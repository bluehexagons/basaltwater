"""Explicit, account-owned write grants and operator prerequisites for tasks."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from lib.validation import validate_filesystem_path


def text_requirements(value: object) -> list[str]:
    if (not isinstance(value, list) or len(value) > 8 or any(
            not isinstance(item, str) or not item.strip() or len(item.encode()) > 500
            or any(ord(char) < 32 or ord(char) == 127 for char in item) for item in value)):
        raise ValueError("Operator prerequisites require up to eight short lines")
    return list(dict.fromkeys(item.strip() for item in value))


def writable_directories(value: object, home: str, *, check: bool = True) -> list[str]:
    """Never turn a proposed path into a broad home or credential-store grant."""
    if not isinstance(value, list) or len(value) > 8:
        raise ValueError("Select at most eight additional writable directories")
    result = []
    protected = (".ssh", ".codex", ".agents", ".config", ".local/state/basaltwater")
    for path in value:
        if not isinstance(path, str):
            raise ValueError("Writable directories must be paths")
        if path.startswith("~/"):
            path = os.path.join(home, path[2:])
        validate_filesystem_path(path, must_exist=check)
        if (not os.path.isabs(path) or os.path.normpath(path) != path
                or path == home or os.path.commonpath((path, home)) != home):
            raise ValueError("Additional writable directories must be specific directories inside the account's home")
        for name in protected:
            target = os.path.join(home, name)
            if os.path.commonpath((path, target)) in (path, target):
                raise ValueError("Task write grants cannot include credentials, agent configuration, or control state")
        if check:
            current = Path(home)
            for part in Path(path).relative_to(home).parts:
                current /= part
                info = current.lstat()
                if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
                    raise ValueError("Writable directories must be owned, unshared directories without symlinks")
        result.append(path)
    return list(dict.fromkeys(result))
