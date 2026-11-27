"""Descriptor-anchored directory access for privileged service setup."""

from __future__ import annotations

from contextlib import contextmanager
import errno
import os
from collections.abc import Iterator

from lib.validation import validate_filesystem_path


@contextmanager
def directory_fd(path: str, *, create: bool = False, mode: int = 0o755,
                 exact_create_mode: bool = False) -> Iterator[int]:
    """Open every component without following links; optionally create missing ones.

    Mutations through the returned descriptor remain on the opened directory
    even if an unprivileged owner replaces its pathname during setup.
    Set exact_create_mode for public configuration that must remain readable
    under a private setup umask. Existing directory modes are preserved.
    Creation syncs every parent entry before yielding, including entries left
    by an interrupted earlier invocation, so subsequent state writes are durable.
    """
    validate_filesystem_path(path)
    if not os.path.isabs(path) or ".." in path.split(os.sep):
        raise ValueError(f"Setup directory must be absolute without '..': {path}")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    descriptor = os.open("/", flags)
    try:
        for component in path.split(os.sep):
            if not component or component == ".":
                continue
            created = False
            try:
                child = os.open(component, flags, dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    raise
                if exact_create_mode:
                    info = os.fstat(descriptor)
                    if info.st_uid != os.geteuid() or info.st_mode & 0o022:
                        raise ValueError(f"Unsafe parent for setup directory: {path}")
                try:
                    os.mkdir(component, mode=mode, dir_fd=descriptor)
                    created = True
                except FileExistsError:
                    pass
                child = os.open(component, flags, dir_fd=descriptor)
            parent, descriptor = descriptor, child
            try:
                if created and exact_create_mode:
                    os.fchmod(descriptor, mode)
                if create:
                    os.fsync(parent)
            finally:
                os.close(parent)
        yield descriptor
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise ValueError(f"Setup directory contains a symlink or non-directory: {path}") from exc
        raise
    finally:
        os.close(descriptor)


def check_directory_path(path: str) -> None:
    """Reject unsafe existing components before any setup mutation."""
    try:
        with directory_fd(path):
            pass
    except FileNotFoundError:
        pass
