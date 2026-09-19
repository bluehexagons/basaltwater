"""Process-level locks for controller-side infrastructure operations."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import os
import stat
import tempfile
from collections.abc import Iterator

from lib.validation import validate_no_control_characters


class ResourceBusyError(RuntimeError):
    """Raised when another process owns a requested controller resource."""


def _lock_root() -> str:
    path = os.path.join(tempfile.gettempdir(), f"basaltwater-locks-{os.getuid()}")
    os.makedirs(path, mode=0o700, exist_ok=True)
    info = os.lstat(path)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RuntimeError(f"Unsafe basaltwater lock directory: {path}")
    return path


def resource_lock_path(namespace: str, resource: str) -> str:
    """Return the private lock path for a logical controller resource."""
    validate_no_control_characters(namespace, "Lock namespace")
    validate_no_control_characters(resource, "Lock resource")
    if not namespace or not resource:
        raise ValueError("Lock namespace and resource must not be empty")
    safe_namespace = "".join(
        character if character.isalnum() or character in "._-" else "_"
        for character in namespace
    )[:40]
    digest = hashlib.sha256(resource.encode("utf-8")).hexdigest()
    return os.path.join(_lock_root(), f"{safe_namespace}-{digest}.lock")


@contextmanager
def resource_lock(
    namespace: str,
    resource: str,
    *,
    wait: bool = False,
) -> Iterator[None]:
    """Own an advisory lock for one resource until the context exits."""
    path = resource_lock_path(namespace, resource)
    descriptor = os.open(
        path,
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
        0o600,
    )
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise RuntimeError(f"Unsafe basaltwater lock file: {path}")
        operation = fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB)
        try:
            fcntl.flock(descriptor, operation)
        except BlockingIOError as exc:
            raise ResourceBusyError(
                f"Another basaltwater process is already operating on {resource}"
            ) from exc
        yield
    finally:
        os.close(descriptor)
