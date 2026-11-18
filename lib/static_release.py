"""Prepare an independent release for ordinary manifests with static output."""

from __future__ import annotations

import os
from pathlib import Path
import stat

from lib.setup_directories import directory_fd
from lib.validation import validate_filesystem_path


def _copy_regular(descriptor: int, info: os.stat_result, destination: Path) -> None:
    # O_PATH pinned the inode without opening a device or blocking on a FIFO.
    # Reopen that regular inode for data, never its build-controlled name.
    with open(f"/proc/self/fd/{descriptor}", "rb") as source, destination.open("xb") as target:
        remaining = info.st_size
        while remaining:
            chunk = source.read(min(remaining, 1024 * 1024))
            if not chunk:
                raise ValueError("Release file shrank during snapshot")
            target.write(chunk)
            remaining -= len(chunk)
        if os.fstat(descriptor).st_ctime_ns != info.st_ctime_ns:
            raise ValueError("Release file changed during snapshot")
        target.flush()
        os.fchmod(target.fileno(), 0o755 if info.st_mode & 0o111 else 0o644)
        os.utime(target.fileno(), ns=(info.st_atime_ns, info.st_mtime_ns))


def snapshot_static_release(workspace: str, staging_path: str, outputs: list[str]) -> None:
    """Copy to fresh, private staging, preserving mixed-component release paths.

    Only the normalized Nginx output trees and their ancestors must be link-free.
    Other components and build tools can have symlinks elsewhere in the release.
    """
    destination_root = Path(staging_path)
    public_roots: set[Path] = set()
    required_directories: set[Path] = set()
    for output in outputs:
        validate_filesystem_path(output)
        path = Path(os.path.normpath(output))
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("Static output must stay inside the release")
        public_roots.add(path)
        required_directories.update((path, *path.parents))

    def copy_directory(source: int, relative: Path, public: bool) -> None:
        before = os.fstat(source)
        for name in os.listdir(source):
            child = relative / name
            destination = destination_root / child
            descriptor = os.open(name, os.O_PATH | os.O_NOFOLLOW, dir_fd=source)
            try:
                info = os.fstat(descriptor)
                if stat.S_ISDIR(info.st_mode):
                    destination.mkdir(mode=0o755)
                    directory = os.open(".", os.O_RDONLY | os.O_DIRECTORY, dir_fd=descriptor)
                    try:
                        copy_directory(directory, child, public or child in public_roots)
                    finally:
                        os.close(directory)
                elif child in required_directories:
                    raise ValueError(f"Static output requires a real directory at {child}")
                elif stat.S_ISREG(info.st_mode):
                    # Non-public package caches may hard-link the builder's own files.
                    if info.st_nlink != 1 and (public or info.st_uid != build_uid):
                        raise ValueError(f"Unsafe hard link in release at {child}")
                    _copy_regular(descriptor, info, destination)
                elif stat.S_ISLNK(info.st_mode) and not public:
                    destination.symlink_to(os.readlink("", dir_fd=descriptor))
                else:
                    raise ValueError(f"Unsafe link or special file in release at {child}")
            finally:
                os.close(descriptor)
        if os.fstat(source).st_ctime_ns != before.st_ctime_ns:
            raise ValueError(f"Release directory changed during snapshot at {relative}")

    with directory_fd(workspace) as source:
        build_uid = os.fstat(source).st_uid
        copy_directory(source, Path("."), Path(".") in public_roots)
