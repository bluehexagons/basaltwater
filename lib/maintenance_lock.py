"""Serialize scheduled mutations with target setup on the same machine."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import fcntl
import os
import stat

from lib.validation import validate_filesystem_path


SETUP_LOCK_FILE = "/run/lock/basaltwater-setup.lock"


@contextmanager
def maintenance_lock() -> Iterator[bool]:
    """Try the setup lock without waiting; retain its inode after release."""
    validate_filesystem_path(SETUP_LOCK_FILE, must_exist=False)
    descriptor = os.open(
        SETUP_LOCK_FILE,
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
        0o600,
    )
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
            raise ValueError("Unsafe target setup lock file")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
        else:
            yield True
    finally:
        os.close(descriptor)
