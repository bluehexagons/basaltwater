"""Serialize scheduled mutations with target setup on the same machine."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
import fcntl
import os
import stat
from uuid import UUID

from lib.validation import validate_filesystem_path


SETUP_LOCK_FILE = "/run/lock/basaltwater-setup.lock"
_BUILD_SLOT = ContextVar("basaltwater_maintenance_slot", default=None)
_REBOOT_PREFIX = b"basaltwater-reboot:"
_EXTERNAL_POWER_PREFIX = b"basaltwater-external-power:"


def _local_boot_id() -> str:
    with open("/proc/sys/kernel/random/boot_id", encoding="ascii") as handle:
        value = handle.read().strip()
    if str(UUID(value)) != value:
        raise ValueError("Invalid local boot ID")
    return value


def retain_reboot_drain(*, external: bool = False) -> None:
    """Persist reboot intent while maintenance owns idle build capacity."""
    if _BUILD_SLOT.get() is None:
        return  # This machine has no installed build coordinator.
    from lib.build_coordinator import _directory

    boot_id = _local_boot_id()
    with _directory() as directory:
        descriptor = os.open("drain", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=directory)
        try:
            prefix = _EXTERNAL_POWER_PREFIX if external else _REBOOT_PREFIX
            os.write(descriptor, prefix + boot_id.encode("ascii") + b"\n")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.fsync(directory)


def recover_reboot_drain(*, cancel: bool = False) -> bool:
    """Recover only our reboot drain; never clear an interrupted build lease.

    Caller owns the setup lock. Cancellation must confirm shutdown -c before
    calling with cancel=True. Same-boot intent otherwise remains drained.
    """
    from lib.build_coordinator import LOCK, _directory, open_slot

    if not os.path.lexists(LOCK):
        return False
    slot = open_slot()
    try:
        try:
            fcntl.flock(slot, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        if os.fstat(slot).st_size:
            return False
        with _directory() as directory:
            try:
                descriptor = os.open("drain", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                     dir_fd=directory)
            except FileNotFoundError:
                return False
            try:
                info = os.fstat(descriptor)
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0
                        or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600):
                    raise ValueError("Unsafe reboot drain")
                content = os.read(descriptor, 128)
            finally:
                os.close(descriptor)
            prefix = next((value for value in (_REBOOT_PREFIX, _EXTERNAL_POWER_PREFIX)
                           if content.startswith(value)), None)
            if prefix is None:
                return False
            recorded = content[len(prefix):].decode("ascii").strip()
            if (str(UUID(recorded)) != recorded
                    or content != prefix + recorded.encode("ascii") + b"\n"
                    or info.st_size != len(content)):
                raise ValueError("Invalid reboot drain boot ID")
            # shutdown -c cannot cancel a provider's external power request.
            if cancel and prefix == _EXTERNAL_POWER_PREFIX:
                return False
            if not cancel and recorded == _local_boot_id():
                return False
            current = os.stat("drain", dir_fd=directory, follow_symlinks=False)
            if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
                raise ValueError("Reboot drain changed during recovery")
            os.unlink("drain", dir_fd=directory)
            os.fsync(directory)
            return True
    finally:
        os.close(slot)


@contextmanager
def maintenance_lock(*, build_slot: bool = True) -> Iterator[bool]:
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
            # Package updates and automatic restarts must also respect jobs
            # accepted outside setup, including Actions workers and webhooks.
            from lib.build_coordinator import LOCK, CoordinatorBusy, host_slot

            with ExitStack() as stack:
                token = None
                if build_slot and os.path.lexists(LOCK):
                    recover_reboot_drain()
                    try:
                        slot = stack.enter_context(host_slot())
                    except CoordinatorBusy:
                        yield False
                        return
                    token = _BUILD_SLOT.set(slot)
                try:
                    yield True
                finally:
                    if token is not None:
                        _BUILD_SLOT.reset(token)
    finally:
        os.close(descriptor)
