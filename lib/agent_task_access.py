"""Explicit, account-owned write grants and operator prerequisites for tasks."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from lib.validation import validate_filesystem_path


_PROTECTED_HOME_PATHS = (".ssh", ".codex", ".agents", ".config", ".local/state/basaltwater")


def _overlaps(first: str, second: str) -> bool:
    try:
        return os.path.commonpath((first, second)) in (first, second)
    except ValueError:
        return False


def _overlaps_protected_home_path(path: str, home: str) -> bool:
    """Check literal and resolved paths so symlink aliases cannot cross a protected boundary."""
    candidates = {os.path.abspath(path), os.path.realpath(path)}
    for relative in _PROTECTED_HOME_PATHS:
        protected = os.path.join(home, relative)
        protected_paths = {protected, os.path.realpath(protected)}
        if any(_overlaps(candidate, target)
               for candidate in candidates for target in protected_paths):
            return True
    return False


def validate_existing_workspace(directory: str, home: str) -> None:
    """Require a specific existing project directory outside protected home trees."""
    if os.path.abspath(directory) == os.path.abspath(home):
        raise ValueError("Workspace changes require a specific project directory, not the account's home")
    if _overlaps_protected_home_path(directory, home):
        raise ValueError("Workspace directory cannot overlap credentials, configuration, or task control state")


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
    for path in value:
        if not isinstance(path, str):
            raise ValueError("Writable directories must be paths")
        if path.startswith("~/"):
            path = os.path.join(home, path[2:])
        validate_filesystem_path(path, must_exist=check)
        if (not os.path.isabs(path) or os.path.normpath(path) != path
                or path == home or os.path.commonpath((path, home)) != home):
            raise ValueError("Additional writable directories must be specific directories inside the account's home")
        if _overlaps_protected_home_path(path, home):
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
