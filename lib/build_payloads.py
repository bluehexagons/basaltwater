"""Nonblocking per-job payload protection on the stable worker lock inode.

Linux OFD record locks are independent of the worker's flock. Byte ranges do
not write or extend the file. A hash collision only makes unrelated jobs busy.
Never unlink or replace the worker lock while either store is in use.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import ctypes
import errno
import fcntl
import hashlib
import os

from lib.build_inputs import _identifier, _private_root
from lib.build_jobs import BuildJobStore


class _Flock(ctypes.Structure):
    _fields_ = [("type", ctypes.c_short), ("whence", ctypes.c_short),
                ("start", ctypes.c_longlong), ("length", ctypes.c_longlong),
                ("pid", ctypes.c_int)]


@contextmanager
def payload_lease(jobs: BuildJobStore, request_id: str, *, exclusive: bool) -> Iterator[None]:
    """Protect a job's bytes, before opening them, through response cleanup.

    Maintenance orders worker -> input -> exclusive payload -> short state
    snapshot. Readers never acquire worker/input locks while holding state.
    Closing this independent open description releases only its record lock,
    including on BaseException; it cannot release another reader's protection.
    """
    request_id = _identifier(request_id, "Request ID")
    if not hasattr(fcntl, "F_OFD_SETLK"):
        raise OSError("Build payload protection requires Linux OFD locks")
    with _private_root(jobs.parent) as directory:
        flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
        try:
            descriptor = os.open(jobs.worker_lock_name, flags | os.O_CREAT | os.O_EXCL,
                                 0o600, dir_fd=directory)
        except FileExistsError:
            descriptor = os.open(jobs.worker_lock_name, flags, dir_fd=directory)
        try:
            opened = jobs._check_private_file(descriptor, "payload lock")
            offset = int.from_bytes(hashlib.sha256(request_id.encode("ascii")).digest()[:7], "big")
            lock = _Flock(fcntl.F_WRLCK if exclusive else fcntl.F_RDLCK, os.SEEK_SET, offset, 1, 0)
            try:
                fcntl.fcntl(descriptor, fcntl.F_OFD_SETLK, bytes(lock))
            except OSError as error:
                if error.errno in {errno.EAGAIN, errno.EACCES}:
                    raise BlockingIOError(errno.EBUSY, "Build payload is busy") from error
                raise
            named = jobs._check_named_file(directory, jobs.worker_lock_name, "payload lock", required=True)
            if named is None or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino):
                raise ValueError("Build payload lock identity changed")
            os.fsync(descriptor)
            os.fsync(directory)
            yield
        finally:
            os.close(descriptor)
