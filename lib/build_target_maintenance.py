"""Stable, protected coordination between target requests and enrollment.

The lock inode is never replaced or removed. Its durable hold survives a failed
installer after flock is released; only a completed installer opens serving.
"""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import stat
from typing import Iterator

from lib.setup_directories import directory_fd
from lib.validation import validate_filesystem_path


LOCK_PATH = "/etc/basaltwater/build-target/.maintenance.lock"
_HELD = b"held\n"
_READY = b"ready\n"


def trusted_path(path: str, *, directory: bool, missing: bool = False) -> bool:
    """Check root control, canonical spelling and no-follow ancestors."""
    validate_filesystem_path(path)
    if not path.startswith("/") or path.startswith("//") or os.path.normpath(path) != path:
        raise ValueError("Target path must be canonical and absolute")
    for component in (*reversed(Path(path).parents), Path(path)):
        try:
            info = component.lstat()
        except FileNotFoundError:
            if missing:
                return False
            raise
        final = str(component) == path
        expected = stat.S_ISDIR(info.st_mode) if (directory or not final) else stat.S_ISREG(info.st_mode)
        # Root-owned sticky ancestors (e.g. /tmp in isolated test fixtures)
        # cannot have their root-owned children renamed by other users.
        sticky_parent = not final and stat.S_ISDIR(info.st_mode) and info.st_mode & stat.S_ISVTX
        if (not expected or stat.S_ISLNK(info.st_mode) or info.st_uid != 0
                or info.st_mode & 0o022 and not sticky_parent):
            raise ValueError("Target path is not controlled by root: " + str(component))
    return True


def read_protected(path: str, *, mode: int, maximum: int) -> bytes | None:
    """Read one bounded, single-link root:root file through its checked parent."""
    parent, name = os.path.split(path)
    if not trusted_path(parent, directory=True, missing=True):
        return None
    with directory_fd(parent) as parent_fd:
        try:
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                                 dir_fd=parent_fd)
        except FileNotFoundError:
            return None
        try:
            before = os.fstat(descriptor)
            if (not stat.S_ISREG(before.st_mode) or before.st_uid != 0 or before.st_gid != 0
                    or before.st_nlink != 1 or stat.S_IMODE(before.st_mode) != mode
                    or before.st_size > maximum):
                raise ValueError("Unsafe managed target file: " + path)
            with os.fdopen(os.dup(descriptor), "rb") as stream:
                raw = stream.read(maximum + 1)
            after = os.fstat(descriptor)
            named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            identity = lambda item: (item.st_dev, item.st_ino, item.st_mode, item.st_uid,
                                     item.st_gid, item.st_nlink, item.st_size,
                                     item.st_mtime_ns, item.st_ctime_ns)
            if (len(raw) > maximum or len(raw) != before.st_size
                    or identity(before) != identity(after) or identity(before) != identity(named)):
                raise ValueError("Managed target file changed while reading: " + path)
            return raw
        finally:
            os.close(descriptor)


def set_serving(descriptor: int, enabled: bool) -> None:
    """Persist the serving gate while holding the exclusive installer lease."""
    content = _READY if enabled else _HELD
    if os.pwrite(descriptor, content, 0) != len(content):
        raise OSError("Short target maintenance state write")
    os.ftruncate(descriptor, len(content))
    os.fsync(descriptor)


@contextmanager
def target_lease(*, installer: bool = False, create: bool = False) -> Iterator[int]:
    """Acquire immediately or refuse; requests never queue with old policy.

    ``create`` is only for the marker-scoped first installation. A missing lock
    on any later run is an error, never an invitation to create a new inode.
    """
    if create and not installer:
        raise ValueError("Only initial enrollment can create target coordination")
    parent, name = os.path.split(LOCK_PATH)
    trusted_path(parent, directory=True)
    parent_info = os.lstat(parent)
    if parent_info.st_gid != 0 or stat.S_IMODE(parent_info.st_mode) != 0o700:
        raise ValueError("Target coordination requires a private root directory")
    with directory_fd(parent) as parent_fd:
        flags = os.O_RDWR if installer else os.O_RDONLY
        flags |= os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
        if create:
            flags |= os.O_CREAT | os.O_EXCL
        descriptor = os.open(name, flags, 0o600, dir_fd=parent_fd)
        try:
            info = os.fstat(descriptor)
            named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_gid != 0
                    or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600
                    or (info.st_dev, info.st_ino) != (named.st_dev, named.st_ino)):
                raise ValueError("Unsafe target coordination file")
            try:
                fcntl.flock(descriptor, (fcntl.LOCK_EX if installer else fcntl.LOCK_SH) | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("Target operation or enrollment is active; retry later") from exc
            if create:
                set_serving(descriptor, False)
                os.fsync(parent_fd)
            state = os.pread(descriptor, 16, 0)
            if state not in {_HELD, _READY} or info.st_size > 16:
                raise ValueError("Invalid target coordination state")
            if not installer and state != _READY:
                raise RuntimeError("Target enrollment requires reconciliation or is disabled")
            yield descriptor
        finally:
            # Closing releases flock, including after errors. Never unlink it.
            os.close(descriptor)


@contextmanager
def target_maintenance() -> Iterator[None]:
    """Own an enabled target exclusively for a local administrative operation.

    Acquire before loading configuration and retain ownership through the local
    action and its response. This never creates a lock or changes enrollment or
    the durable serving gate. The action owns its own recovery evidence.
    """
    with target_lease(installer=True) as descriptor:
        if os.pread(descriptor, 16, 0) != _READY:
            raise RuntimeError("Target enrollment requires reconciliation or is disabled")
        yield


__all__ = ["target_lease", "target_maintenance"]
