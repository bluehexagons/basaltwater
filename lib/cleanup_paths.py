"""Identity-checked disposal for paths selected by existing cleanup policies."""

from __future__ import annotations

import os
import secrets
import shutil
import stat
from typing import Callable

from lib.validation import validate_filesystem_path


class CleanupPathError(OSError):
    """A failed disposal retains its private recovery path for local logging."""

    def __init__(self, message: str, recovery_path: str) -> None:
        super().__init__(message)
        self.recovery_path = recovery_path


def _identity(info: os.stat_result) -> tuple:
    return (info.st_dev, info.st_ino, info.st_uid, info.st_mode, info.st_mtime_ns)


def _open_parent(path: str, uid: int) -> int:
    """Anchor through non-linked directories owned by root or the selected UID."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    descriptor = os.open("/", flags)
    try:
        for component in os.path.dirname(path).split(os.sep)[1:]:
            if not component:
                continue
            child = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
            info = os.fstat(descriptor)
            if info.st_uid not in (0, uid) or info.st_mode & 0o002 and not info.st_mode & stat.S_ISVTX:
                raise OSError("Cleanup parent is not owner-controlled")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def remove_checked_path(path: str, expected: os.stat_result, *, uid: int,
                        in_use: Callable[[], bool], dry_run: bool = False,
                        on_isolated: Callable[[str], None] | None = None) -> bool:
    """Recheck identity/use, then dispose only the isolated inventoried entry.

    A recreated original pathname is never revisited. Failed or mismatched
    disposal is retained privately for explicit recovery, not retried or pruned.
    """
    validate_filesystem_path(path)
    if not os.path.isabs(path) or os.path.normpath(path) == "/":
        raise OSError("Cleanup path must be absolute and below a directory")
    path = os.path.normpath(path)
    if expected.st_uid != uid or not any(test(expected.st_mode) for test in (stat.S_ISDIR, stat.S_ISREG, stat.S_ISLNK)):
        raise OSError("Cleanup entry ownership or type is unsafe")
    parent = _open_parent(path, uid)
    disposal = -1
    moved = False
    name = os.path.basename(path)
    private_name = ".basaltwater-dispose-" + secrets.token_hex(16)
    recovery_path = os.path.join(os.path.dirname(path), private_name, "entry")
    try:
        if in_use():
            return False
        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if _identity(current) != _identity(expected):
            raise OSError("Cleanup entry changed since inventory")
        if dry_run:
            return True
        os.mkdir(private_name, mode=0o700, dir_fd=parent)
        disposal = os.open(private_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
        private_info = os.fstat(disposal)
        if private_info.st_uid != os.geteuid() or private_info.st_mode & 0o077:
            raise OSError("Disposal directory ownership or permissions changed")
        os.rename(name, "entry", src_dir_fd=parent, dst_dir_fd=disposal)
        moved = True
        current = os.stat("entry", dir_fd=disposal, follow_symlinks=False)
        if _identity(current) != _identity(expected):
            raise OSError("Cleanup entry changed during isolation; retained for recovery")
        if on_isolated is not None:
            on_isolated(recovery_path)
        if stat.S_ISDIR(current.st_mode):
            shutil.rmtree("entry", dir_fd=disposal)
        else:
            os.unlink("entry", dir_fd=disposal)
        return True
    except OSError as exc:
        if moved:
            raise CleanupPathError(str(exc), recovery_path) from exc
        raise
    finally:
        if disposal >= 0:
            try:
                current = os.stat(private_name, dir_fd=parent, follow_symlinks=False)
                opened = os.fstat(disposal)
                if (current.st_dev, current.st_ino) == (opened.st_dev, opened.st_ino) and opened.st_uid == os.geteuid() and not os.listdir(disposal):
                    os.rmdir(private_name, dir_fd=parent)
            except OSError:
                pass
            os.close(disposal)
        os.close(parent)


def process_uses_path(path: str, proc_root: str = "/proc", *, uid: int | None = None,
                      include_open_files: bool = False,
                      on_incomplete: Callable[[], None] | None = None) -> bool:
    """Check owner process paths; optionally include bounded open descriptors.

    Descriptor inspection fails closed on denied access unless a caller handles
    that coverage limit explicitly. More than 4096 FDs always defers cleanup.
    This remains a snapshot, not an exclusion lock for future work.
    """
    current_uid = os.getuid() if uid is None else uid
    absolute_path = os.path.abspath(path)
    encoded_path = os.fsencode(absolute_path)
    try:
        process_entries = os.scandir(proc_root)
    except OSError:
        return True
    with process_entries:
        for entry in process_entries:
            if not entry.name.isdigit():
                continue
            try:
                if entry.stat(follow_symlinks=False).st_uid != current_uid:
                    continue
            except (FileNotFoundError, ProcessLookupError):
                continue
            except OSError:
                return True
            # Non-dumpable daemons can hide cwd/exe without occupying every
            # cache. Inspect their readable argv without exposing it in reports.
            for link in ("cwd", "exe"):
                try:
                    target = os.readlink(os.path.join(entry.path, link))
                    if os.path.commonpath((os.path.abspath(target), absolute_path)) == absolute_path:
                        return True
                except (OSError, ValueError):
                    pass
            try:
                with open(os.path.join(entry.path, "cmdline"), "rb") as handle:
                    arguments = handle.read(65537)
                if len(arguments) > 65536:
                    return True
                for argument in arguments.split(b"\0"):
                    for value in (argument, argument.split(b"=", 1)[-1]):
                        if value == encoded_path or value.startswith(encoded_path + os.sep.encode()):
                            return True
            except (FileNotFoundError, ProcessLookupError):
                continue
            except OSError:
                return True
            if include_open_files:
                try:
                    with os.scandir(os.path.join(entry.path, "fd")) as descriptors:
                        for index, descriptor in enumerate(descriptors):
                            if index >= 4096:
                                return True
                            try:
                                target = os.readlink(descriptor.path).removesuffix(" (deleted)")
                            except (FileNotFoundError, ProcessLookupError):
                                continue
                            if target.startswith("/") and os.path.commonpath((target, absolute_path)) == absolute_path:
                                return True
                except (FileNotFoundError, ProcessLookupError):
                    continue
                except PermissionError:
                    # Non-dumpable account daemons also hide cwd/exe. A caller
                    # may expose this limitation rather than declare every
                    # unrelated directory active; never silently claim coverage.
                    if on_incomplete is None:
                        return True
                    on_incomplete()
                except (OSError, ValueError):
                    return True
    return False
