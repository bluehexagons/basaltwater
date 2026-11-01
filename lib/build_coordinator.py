"""Protected, bounded host capacity shared by build producers."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import fcntl
import grp
import os
import stat
import time

from lib.remote_utils import run
from lib.setup_directories import directory_fd

DIRECTORY = "/var/lib/basaltwater-build-coordinator"
GROUP = "basaltwater-builds"
LOCK = DIRECTORY + "/slot.lock"
WAIT_SECONDS = 60
RECOVERY_SERVICE = "basaltwater-build-capacity.service"
_MAINTENANCE = ContextVar("basaltwater_build_maintenance", default=False)


class CoordinatorBusy(RuntimeError):
    """Capacity is held or explicitly drained; queued work must remain queued."""


def _directory():
    return directory_fd(DIRECTORY)


def open_slot() -> int:
    """Open the existing immutable lock inode; never create it as a worker."""
    with _directory() as directory:
        info = os.fstat(directory)
        if info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o755:
            raise ValueError("Unsafe build coordinator directory")
        descriptor = os.open("slot.lock", os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1
                or info.st_gid != grp.getgrnam(GROUP).gr_gid or stat.S_IMODE(info.st_mode) != 0o660):
            raise ValueError("Unsafe build coordinator lock")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def ensure_coordinator() -> None:
    """Install root-owned capacity state without replacing an active inode."""
    if os.geteuid() != 0:
        raise PermissionError("Coordinator installation requires root")
    try:
        group = grp.getgrnam(GROUP)
    except KeyError:
        run(["groupadd", "--system", GROUP])
        group = grp.getgrnam(GROUP)
    with directory_fd(DIRECTORY, create=True, mode=0o755) as directory:
        info = os.fstat(directory)
        if info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError("Unsafe build coordinator directory")
        os.fchmod(directory, 0o755)
        descriptor = os.open("slot.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                             0o660, dir_fd=directory)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1:
                raise ValueError("Unsafe build coordinator lock")
            os.fchown(descriptor, 0, group.gr_gid)
            os.fchmod(descriptor, 0o660)
        finally:
            os.close(descriptor)
    from lib.unit_transaction import replace_units

    replace_units({RECOVERY_SERVICE: """[Unit]
Description=Recover confirmed reboot drain before Basaltwater builds
After=local-fs.target

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 -I /opt/basaltwater/common/service_tools/build_capacity_recovery.py
User=root
Group=root
WorkingDirectory=/
UMask=0077
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
"""}, activate=(RECOVERY_SERVICE,))


def drained() -> bool:
    """Any drain entry, including malformed state, refuses admission."""
    with _directory() as directory:
        try:
            os.stat("drain", dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            return False
        return True


@contextmanager
def host_slot(*, blocking: bool = False):
    """Acquire one heavy-job slot, with at most sixty seconds of waiting."""
    descriptor = open_slot()
    deadline = time.monotonic() + (WAIT_SECONDS if blocking else 0)
    try:
        while True:
            if drained():
                raise CoordinatorBusy("Build host is drained")
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise CoordinatorBusy("Build host capacity unavailable") from None
                time.sleep(0.2)
        if drained():
            raise CoordinatorBusy("Build host is drained")
        if os.fstat(descriptor).st_size:
            raise ValueError("Build capacity requires administrator recovery")
        mark_slot(descriptor)
        yield descriptor
        clear_slot(descriptor)
    finally:
        os.close(descriptor)


def mark_slot(descriptor: int) -> None:
    """Persist uncertainty before work: a process death must not free capacity."""
    os.pwrite(descriptor, b"Build lease interrupted; inspect processes before clearing this marker\n", 0)
    os.fsync(descriptor)


def clear_slot(descriptor: int) -> None:
    """Release durable capacity only after the producer confirms cleanup."""
    os.ftruncate(descriptor, 0)
    os.fsync(descriptor)


@contextmanager
def maintenance(*, release_drain: bool = True):
    """Drain before capacity; runtime activation retains it until setup finishes."""
    if os.geteuid() != 0:
        raise PermissionError("Maintenance requires root")
    if _MAINTENANCE.get():
        yield
        return
    descriptor = open_slot()
    try:
        with _directory() as directory:
            drain = os.open("drain", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                            0o600, dir_fd=directory)
            info = os.fstat(drain)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600:
                os.close(drain)
                raise ValueError("Unsafe build drain marker")
            os.close(drain)
            if info.st_size:
                raise ValueError("Pending reboot or uncertain drain requires cancellation or boot recovery before setup")
            os.fsync(directory)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise CoordinatorBusy("Build host is active; drain retained; retry maintenance after work stops") from None
        if os.fstat(descriptor).st_size:
            raise ValueError("Build capacity requires administrator recovery; drain retained")
        with _directory() as directory:
            if os.stat("drain", dir_fd=directory, follow_symlinks=False).st_size:
                raise ValueError("Pending reboot or uncertain drain requires recovery before setup")
        token = _MAINTENANCE.set(True)
        try:
            yield
        finally:
            _MAINTENANCE.reset(token)
        if release_drain:
            with _directory() as directory:
                os.unlink("drain", dir_fd=directory)
                os.fsync(directory)
    finally:
        os.close(descriptor)
