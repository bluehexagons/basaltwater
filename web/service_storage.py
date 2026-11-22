"""Mount-safe traversal for service-owned storage on Linux."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import os
from pathlib import Path
import re
import stat

from lib.setup_directories import directory_fd
from lib.validation import validate_filesystem_path


class StorageBoundaryError(RuntimeError):
    """The service tree cannot be kept on its expected mount."""


def reject_nested_mounts(path: Path, *, include_root: bool = False) -> None:
    """Refuse mounted descendants, including file mounts and same-device binds."""
    validate_filesystem_path(str(path))
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Expected an absolute service storage path: {path}")
    root = Path("/", *path.parts[1:])
    try:
        with open("/proc/self/mountinfo", encoding="utf-8", errors="surrogateescape") as table:
            found_root = False
            for line in table:
                fields = line.split()
                if len(fields) < 10 or "-" not in fields[6:]:
                    raise ValueError("Invalid mount table")
                raw = re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), fields[4])
                mount = Path(raw)
                if not mount.is_absolute() or ".." in mount.parts:
                    raise ValueError("Invalid mount target")
                found_root |= mount == Path("/")
                if (include_root or mount != root) and mount.is_relative_to(root):
                    raise StorageBoundaryError(f"Nested mount in service storage {path}: {mount}")
            if not found_root:
                raise ValueError("Mount table has no root entry")
    except (OSError, ValueError) as exc:
        raise StorageBoundaryError(f"Cannot inspect nested mounts in service storage {path}") from exc


def mount_id(fd: int) -> int:
    try:
        with open(f"/proc/self/fdinfo/{fd}", encoding="ascii") as info:
            for line in info:
                if line.startswith("mnt_id:"):
                    identity = int(line.partition(":")[2])
                    if identity > 0:
                        return identity
    except (OSError, ValueError):
        pass
    raise StorageBoundaryError("Cannot identify the mount of an open service storage entry")


class StorageTree:
    def __init__(self, fd: int, identity: int, path: Path):
        self.fd = fd
        self.identity = identity
        self.path = path
        self.metadata(fd)

    def metadata(self, fd: int) -> os.stat_result:
        if mount_id(fd) != self.identity:
            raise StorageBoundaryError(f"Mount boundary crossed in service storage {self.path}")
        return os.fstat(fd)

    @contextmanager
    def child(self, parent: int, name: str) -> Iterator[tuple[int, os.stat_result]]:
        fd = os.open(name, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
        try:
            yield fd, self.metadata(fd)
        finally:
            os.close(fd)

    @contextmanager
    def directory(self, relative: Path, *, create: bool = False, mode: int = 0o755) -> Iterator[int]:
        """Open a directory beneath the pinned root without following links."""
        def descend(fd: int, parts: tuple[str, ...]) -> Iterator[int]:
            if not parts:
                yield fd
                return
            name, *remaining = parts
            if create:
                try:
                    os.mkdir(name, mode=0o755 if remaining else mode, dir_fd=fd)
                except FileExistsError:
                    pass
            with self.child(fd, name) as (child, info):
                if not stat.S_ISDIR(info.st_mode):
                    raise RuntimeError(f"Expected a directory in service storage: {relative}")
                yield from descend(child, tuple(remaining))

        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Expected a relative service storage directory")
        yield from descend(self.fd, relative.parts)

    def walk(
        self, *, bottom_up: bool = False,
    ) -> Iterator[tuple[Path, int, int, os.stat_result]]:
        """Yield each checked descriptor and its parent while both are open."""
        def visit(
            relative: Path, fd: int, parent: int, info: os.stat_result,
        ) -> Iterator[tuple[Path, int, int, os.stat_result]]:
            if not bottom_up:
                yield relative, fd, parent, info
            if stat.S_ISDIR(info.st_mode):
                with os.scandir(f"/proc/self/fd/{fd}") as entries:
                    for entry in entries:
                        with self.child(fd, entry.name) as (child, child_info):
                            yield from visit(relative / entry.name, child, fd, child_info)
            if bottom_up:
                yield relative, fd, parent, info

        yield from visit(Path("."), self.fd, self.fd, self.metadata(self.fd))


@contextmanager
def storage_tree(
    path: Path, *, backing: Path | None = None, create: bool = False, mode: int = 0o755,
) -> Iterator[StorageTree]:
    """Pin the service root and enforce its backing mount throughout a walk."""
    reject_nested_mounts(path)
    path = Path("/", *path.parts[1:])
    if backing is None:
        backing = path
        while not backing.exists():
            backing = backing.parent
    if not backing.is_absolute() or ".." in backing.parts:
        raise StorageBoundaryError(f"Invalid backing mount for service storage {path}")
    backing = Path("/", *backing.parts[1:])
    if not path.is_relative_to(backing):
        raise StorageBoundaryError(f"Invalid backing mount for service storage {path}")
    with directory_fd(str(backing)) as anchor:
        identity = mount_id(anchor)
        tree = StorageTree(anchor, identity, path)
        with tree.directory(path.relative_to(backing), create=create, mode=mode) as pinned:
            root = os.open(f"/proc/self/fd/{pinned}", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
            try:
                yield StorageTree(root, identity, path)
            finally:
                os.close(root)
