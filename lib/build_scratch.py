"""Root-worker-owned, bounded ext4 scratch images for internal build jobs.

This adapter deliberately keeps uncertain state on disk. The build coordinator
must stop the complete worker process tree and snapshot artifacts before it
calls :meth:`ScratchHandle.release`.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
import stat
import subprocess
from typing import Any

from lib.setup_directories import directory_fd
from lib.validation import validate_filesystem_path


MIB = 1024 * 1024
MIN_SIZE_BYTES = 256 * MIB
MAX_SIZE_BYTES = 128 * 1024 * MIB
_REQUEST_ID = re.compile(r"[0-9a-f]{32}")
_SAFE_ABSOLUTE_PATH = re.compile(r"/(?:[A-Za-z0-9._+-]+/)*[A-Za-z0-9._+-]+")
_LOOP_SOURCE = re.compile(r"/dev/loop[0-9]+")
_MAJOR_MINOR = re.compile(r"([0-9]+):([0-9]+)")
_ROOT_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
_FILE_NOFOLLOW = os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
_MARKER_NAME = "identity.json"
_IMAGE_NAME = "scratch.ext4"
_MOUNT_NAME = "fs"
_MARKER_LIMIT = 4096
_ENVIRONMENT = {"LANG": "C", "PATH": "/usr/sbin:/usr/bin:/sbin:/bin"}
_FINDMNT_FIELDS = {"id", "source", "fstype", "target", "options", "maj:min"}


def _require_root() -> None:
    if os.geteuid() != 0:
        raise PermissionError("Build scratch storage is available only to the root worker")


def _canonical_root(value: str | os.PathLike[str]) -> str:
    raw = os.fspath(value)
    if not isinstance(raw, str):
        raise ValueError("Scratch root must be a text path")
    validate_filesystem_path(raw, must_exist=True)
    if (not os.path.isabs(raw) or os.path.normpath(raw) != raw or raw == "/"
            or _SAFE_ABSOLUTE_PATH.fullmatch(raw) is None):
        raise ValueError("Scratch root must be a canonical managed absolute path")
    return raw


def _directory_mode(info: os.stat_result, label: str, *, allow_traverse: bool = False) -> None:
    mode = stat.S_IMODE(info.st_mode)
    allowed_other = 0o011 if allow_traverse else 0
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
            or mode & 0o700 != 0o700 or mode & 0o7000
            or mode & 0o077 & ~allowed_other):
        raise ValueError(f"{label} must be a private root-owned directory")


def _regular_file(info: os.stat_result, label: str, *, expected_size: int | None = None) -> None:
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o600
            or (expected_size is not None and info.st_size != expected_size)):
        raise ValueError(f"Scratch {label} must be a root-owned regular 0600 file")


def _positive_integer(value: object, label: str) -> int:
    if type(value) is int and value > 0:
        return value
    if isinstance(value, str) and len(value) <= 20 and re.fullmatch(r"[1-9][0-9]*", value):
        try:
            return int(value)
        except ValueError:
            pass
    raise RuntimeError(f"findmnt returned an invalid {label}")


def _find_mount(target: str) -> dict[str, Any] | None:
    """Return one exact mountpoint observation; command or parse errors fail closed."""
    try:
        result = subprocess.run(
            ["/usr/bin/findmnt", "--json", "--mountpoint", target,
             "--output", "ID,SOURCE,FSTYPE,TARGET,OPTIONS,MAJ:MIN"],
            capture_output=True, text=True, env=_ENVIRONMENT, timeout=15, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("Scratch mount observation failed; retain evidence") from exc
    if not isinstance(result.stdout, str) or not isinstance(result.stderr, str):
        raise RuntimeError("findmnt returned invalid output; retain scratch evidence")
    if len(result.stdout) > 8192 or len(result.stderr) > 4096:
        raise RuntimeError("findmnt output is oversized; retain scratch evidence")
    if result.stderr.strip():
        raise RuntimeError("findmnt reported an error; retain scratch evidence")
    if result.returncode not in (0, 1):
        raise RuntimeError("findmnt failed; retain scratch evidence")
    if not result.stdout.strip():
        raise RuntimeError("findmnt returned no observation; retain scratch evidence")
    try:
        document = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise RuntimeError("findmnt returned malformed JSON; retain scratch evidence") from exc
    if not isinstance(document, dict) or set(document) != {"filesystems"}:
        raise RuntimeError("findmnt returned an invalid observation; retain scratch evidence")
    filesystems = document["filesystems"]
    if not isinstance(filesystems, list):
        raise RuntimeError("findmnt returned an invalid mount list; retain scratch evidence")
    if result.returncode == 1 and filesystems:
        raise RuntimeError("findmnt failed with a non-empty result; retain scratch evidence")
    if not filesystems:
        return None
    if result.returncode != 0 or len(filesystems) != 1:
        raise RuntimeError("findmnt returned an ambiguous mount result; retain scratch evidence")
    mount = filesystems[0]
    if not isinstance(mount, dict) or set(mount) != _FINDMNT_FIELDS:
        raise RuntimeError("findmnt returned incomplete mount identity; retain scratch evidence")
    if any(not isinstance(mount[key], str) for key in _FINDMNT_FIELDS - {"id"}):
        raise RuntimeError("findmnt returned invalid mount fields; retain scratch evidence")
    mount_id = _positive_integer(mount["id"], "mount ID")
    return {**mount, "id": mount_id}


def _device_numbers(source: str) -> tuple[int, int]:
    info = os.stat(source, follow_symlinks=False)
    if not stat.S_ISBLK(info.st_mode):
        raise RuntimeError("findmnt source is not a loop block device")
    return os.major(info.st_rdev), os.minor(info.st_rdev)


def _loop_backing_file(major: int, minor: int) -> str:
    path = f"/sys/dev/block/{major}:{minor}/loop/backing_file"
    with open(path, "r", encoding="utf-8", errors="strict") as source:
        value = source.read(4097)
    if len(value) > 4096 or not value.endswith("\n"):
        raise RuntimeError("Loop backing-file identity is unavailable")
    return value[:-1]


def _source_matches(mount: dict[str, Any], image_path: str) -> bool:
    source = mount["source"]
    if source == image_path:
        return True
    if _LOOP_SOURCE.fullmatch(source) is None:
        return False
    match = _MAJOR_MINOR.fullmatch(mount["maj:min"])
    if match is None:
        return False
    major, minor = int(match.group(1)), int(match.group(2))
    return _device_numbers(source) == (major, minor) and _loop_backing_file(major, minor) == image_path


def _capacity_bytes(mount_path: str, size_bytes: int) -> int:
    try:
        usage = os.statvfs(mount_path)
    except OSError as exc:
        raise RuntimeError("Scratch filesystem capacity is unavailable") from exc
    blocks, fragment_size = usage.f_blocks, usage.f_frsize
    if (type(blocks) is not int or type(fragment_size) is not int
            or blocks <= 0 or fragment_size <= 0):
        raise RuntimeError("Scratch filesystem capacity is invalid")
    capacity = blocks * fragment_size
    if capacity <= 0 or capacity > size_bytes:
        raise RuntimeError("Observed scratch capacity exceeds the image bound")
    return capacity


def _observe_owned_mount(
    mount_path: str,
    image_path: str,
    size_bytes: int,
    *,
    expected_mount_id: int | None = None,
    expected_capacity: int | None = None,
) -> tuple[int, int]:
    mount = _find_mount(mount_path)
    if mount is None:
        raise RuntimeError("Owned scratch mount is absent; retain evidence for reconciliation")
    options = set(mount["options"].split(","))
    if (mount["target"] != mount_path or mount["fstype"] != "ext4"
            or not {"rw", "nodev", "nosuid"}.issubset(options)
            or not _source_matches(mount, image_path)):
        raise RuntimeError("Scratch mount identity or safety options changed; retain evidence")
    mount_id = mount["id"]
    if expected_mount_id is not None and mount_id != expected_mount_id:
        raise RuntimeError("Scratch mount ID changed; retain evidence for reconciliation")
    capacity = _capacity_bytes(mount_path, size_bytes)
    if expected_capacity is not None and capacity != expected_capacity:
        raise RuntimeError("Scratch filesystem capacity changed; retain evidence")
    return mount_id, capacity


def _marker_bytes(marker: dict[str, Any]) -> bytes:
    encoded = json.dumps(marker, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return (encoded + "\n").encode("ascii")


def _write_all(descriptor: int, content: bytes) -> None:
    view = memoryview(content)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("Short write while saving scratch identity")
        view = view[written:]


def _create_marker(request_fd: int, marker: dict[str, Any]) -> None:
    content = _marker_bytes(marker)
    if len(content) > _MARKER_LIMIT:
        raise ValueError("Scratch identity marker is oversized")
    descriptor = os.open(
        _MARKER_NAME,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | _FILE_NOFOLLOW,
        0o600,
        dir_fd=request_fd,
    )
    try:
        os.fchmod(descriptor, 0o600)
        _regular_file(os.fstat(descriptor), "identity marker")
        _write_all(descriptor, content)
        os.fsync(descriptor)
        os.fsync(request_fd)
    finally:
        os.close(descriptor)


def _marker_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate scratch identity key")
        result[key] = value
    return result


def _read_marker(request_fd: int) -> dict[str, Any]:
    descriptor = os.open(_MARKER_NAME, os.O_RDONLY | _FILE_NOFOLLOW | os.O_NONBLOCK,
                         dir_fd=request_fd)
    try:
        _regular_file(os.fstat(descriptor), "identity marker")
        with os.fdopen(os.dup(descriptor), "rb") as source:
            raw = source.read(_MARKER_LIMIT + 1)
        if len(raw) > _MARKER_LIMIT:
            raise ValueError("Scratch identity marker is oversized")
        try:
            marker = json.loads(raw, object_pairs_hook=_marker_object)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("Scratch identity marker is malformed") from exc
        if not isinstance(marker, dict):
            raise ValueError("Scratch identity marker is invalid")
        return marker
    finally:
        os.close(descriptor)


_MARKER_FIELDS = {
    "schema", "request_id", "root_path", "root_dev", "root_ino",
    "request_dev", "request_ino", "image_path", "image_dev", "image_ino",
    "image_size_bytes", "mount_path", "mountpoint_dev", "mountpoint_ino",
}


def _validate_marker(
    marker: dict[str, Any],
    *,
    root_path: str,
    request_id: str,
    root_info: os.stat_result,
    request_info: os.stat_result,
    size_bytes: int,
) -> None:
    if set(marker) != _MARKER_FIELDS:
        raise ValueError("Scratch identity marker has an unsupported shape")
    expected_strings = {
        "request_id": request_id,
        "root_path": root_path,
        "image_path": f"{root_path}/{request_id}/{_IMAGE_NAME}",
        "mount_path": f"{root_path}/{request_id}/{_MOUNT_NAME}",
    }
    if type(marker["schema"]) is not int or marker["schema"] != 1:
        raise ValueError("Scratch identity marker schema is unsupported")
    if any(marker[key] != value for key, value in expected_strings.items()):
        raise ValueError("Scratch identity marker paths do not match the request")
    expected_numbers = {
        "root_dev": root_info.st_dev,
        "root_ino": root_info.st_ino,
        "request_dev": request_info.st_dev,
        "request_ino": request_info.st_ino,
        "image_size_bytes": size_bytes,
    }
    for key, value in expected_numbers.items():
        if type(marker[key]) is not int or marker[key] != value:
            raise ValueError("Scratch identity marker does not match its directory")
    for key in ("image_dev", "image_ino", "mountpoint_dev", "mountpoint_ino"):
        if type(marker[key]) is not int or marker[key] < 0:
            raise ValueError("Scratch identity marker has invalid file identity")


def _verify_image(request_fd: int, marker: dict[str, Any]) -> os.stat_result:
    info = os.stat(_IMAGE_NAME, dir_fd=request_fd, follow_symlinks=False)
    _regular_file(info, "image", expected_size=marker["image_size_bytes"])
    if (info.st_dev, info.st_ino) != (marker["image_dev"], marker["image_ino"]):
        raise ValueError("Scratch image identity changed; retain evidence")
    return info


def _verify_mountpoint(request_fd: int, marker: dict[str, Any]) -> os.stat_result:
    descriptor = os.open(_MOUNT_NAME, _ROOT_FLAGS, dir_fd=request_fd)
    try:
        info = os.fstat(descriptor)
        _directory_mode(info, "Scratch mountpoint")
        if (info.st_dev, info.st_ino) != (marker["mountpoint_dev"], marker["mountpoint_ino"]):
            raise ValueError("Scratch mountpoint identity changed; retain evidence")
        return info
    finally:
        os.close(descriptor)


def _run_native(argv: list[str], *, timeout: int, failure: str) -> None:
    try:
        result = subprocess.run(argv, capture_output=True, text=True, env=_ENVIRONMENT,
                                timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"{failure}; retain scratch evidence for reconciliation") from exc
    if result.returncode != 0:
        raise RuntimeError(f"{failure}; retain scratch evidence for reconciliation")


@dataclass(frozen=True)
class ScratchHandle:
    """An owned mount. Release only after the caller has stopped and snapshotted work."""

    request_id: str
    mount_path: str
    size_bytes: int
    capacity_bytes: int
    _root_path: str
    _mount_id: int

    def release(self, *, work_stopped: bool = False) -> None:
        """Unmount once and delete private state only after exact verification."""
        if work_stopped is not True:
            raise ValueError("Scratch release requires confirmed stopped work")
        _release(self)


def create_scratch(
    root: str | os.PathLike[str],
    request_id: str,
    *,
    size_bytes: int,
) -> ScratchHandle:
    """Create and verify a fresh root-worker ext4 image for one build request.

    Any error after the request directory is created leaves its marker and
    whatever image or mount state exists for operator reconciliation.
    """
    _require_root()
    if not isinstance(request_id, str) or _REQUEST_ID.fullmatch(request_id) is None:
        raise ValueError("Build request ID must be 32 lowercase hexadecimal digits")
    if (type(size_bytes) is not int or not MIN_SIZE_BYTES <= size_bytes <= MAX_SIZE_BYTES
            or size_bytes % MIB != 0):
        raise ValueError("Scratch image size must be MiB-aligned between 256 MiB and 128 GiB")
    root_path = _canonical_root(root)
    request_path = f"{root_path}/{request_id}"
    image_path = f"{request_path}/{_IMAGE_NAME}"
    mount_path = f"{request_path}/{_MOUNT_NAME}"

    with directory_fd(root_path) as root_fd:
        root_info = os.fstat(root_fd)
        _directory_mode(root_info, "Scratch root")
        if stat.S_IMODE(root_info.st_mode) != 0o700:
            raise ValueError("Scratch root must have mode 0700")
        try:
            os.stat(request_id, dir_fd=root_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise FileExistsError("Build request scratch state already exists; do not retry")
        os.mkdir(request_id, mode=0o700, dir_fd=root_fd)
        os.fsync(root_fd)
        request_fd = os.open(request_id, _ROOT_FLAGS, dir_fd=root_fd)
        try:
            os.fchmod(request_fd, 0o700)
            request_info = os.fstat(request_fd)
            _directory_mode(request_info, "Scratch request directory")
            os.mkdir(_MOUNT_NAME, mode=0o700, dir_fd=request_fd)
            os.fsync(request_fd)
            mountpoint_fd = os.open(_MOUNT_NAME, _ROOT_FLAGS, dir_fd=request_fd)
            try:
                os.fchmod(mountpoint_fd, 0o700)
                mountpoint_info = os.fstat(mountpoint_fd)
                _directory_mode(mountpoint_info, "Scratch mountpoint")
            finally:
                os.close(mountpoint_fd)

            image_fd = os.open(
                _IMAGE_NAME,
                os.O_RDWR | os.O_CREAT | os.O_EXCL | _FILE_NOFOLLOW,
                0o600,
                dir_fd=request_fd,
            )
            try:
                os.fchmod(image_fd, 0o600)
                os.ftruncate(image_fd, size_bytes)
                os.fsync(image_fd)
                image_info = os.fstat(image_fd)
                _regular_file(image_info, "image", expected_size=size_bytes)
            finally:
                os.close(image_fd)

            marker: dict[str, Any] = {
                "schema": 1,
                "request_id": request_id,
                "root_path": root_path,
                "root_dev": root_info.st_dev,
                "root_ino": root_info.st_ino,
                "request_dev": request_info.st_dev,
                "request_ino": request_info.st_ino,
                "image_path": image_path,
                "image_dev": image_info.st_dev,
                "image_ino": image_info.st_ino,
                "image_size_bytes": size_bytes,
                "mount_path": mount_path,
                "mountpoint_dev": mountpoint_info.st_dev,
                "mountpoint_ino": mountpoint_info.st_ino,
            }
            _create_marker(request_fd, marker)
            _validate_marker(marker, root_path=root_path, request_id=request_id,
                             root_info=root_info, request_info=request_info, size_bytes=size_bytes)
            _verify_image(request_fd, marker)
            _verify_mountpoint(request_fd, marker)
            if _find_mount(mount_path) is not None:
                raise RuntimeError("Scratch mountpoint is already in use; retain evidence")

            _run_native(
                ["/usr/sbin/mkfs.ext4", "-F", "-q", "-m", "0", "-E",
                 "root_owner=0:0,root_perms=0700", image_path],
                timeout=300,
                failure="ext4 formatting failed",
            )
            _verify_image(request_fd, marker)
            _verify_mountpoint(request_fd, marker)
            if _read_marker(request_fd) != marker:
                raise ValueError("Scratch identity marker changed; retain evidence")
            if _find_mount(mount_path) is not None:
                raise RuntimeError("Scratch mountpoint became occupied; retain evidence")
            _run_native(
                ["/usr/bin/mount", "--types", "ext4", "--options", "loop,nodev,nosuid",
                 "--source", image_path, "--target", mount_path],
                timeout=60,
                failure="ext4 mount is unconfirmed",
            )
            _verify_image(request_fd, marker)
            if _read_marker(request_fd) != marker:
                raise ValueError("Scratch identity marker changed; retain evidence")
            mount_id, capacity = _observe_owned_mount(mount_path, image_path, size_bytes)
            return ScratchHandle(request_id, mount_path, size_bytes, capacity, root_path, mount_id)
        finally:
            os.close(request_fd)


def _empty_directory(parent_fd: int, name: str) -> os.stat_result:
    descriptor = os.open(name, _ROOT_FLAGS, dir_fd=parent_fd)
    try:
        info = os.fstat(descriptor)
        _directory_mode(info, "Scratch mountpoint")
        if os.listdir(descriptor):
            raise RuntimeError("Unmounted scratch mountpoint is not empty; retain evidence")
        return info
    finally:
        os.close(descriptor)


def _release(handle: ScratchHandle) -> None:
    _require_root()
    if (not isinstance(handle.request_id, str)
            or _REQUEST_ID.fullmatch(handle.request_id) is None
            or type(handle.size_bytes) is not int
            or type(handle.capacity_bytes) is not int
            or type(handle._mount_id) is not int or handle._mount_id <= 0):
        raise ValueError("Scratch handle is invalid")
    root_path = _canonical_root(handle._root_path)
    request_path = f"{root_path}/{handle.request_id}"
    image_path = f"{request_path}/{_IMAGE_NAME}"
    mount_path = f"{request_path}/{_MOUNT_NAME}"
    if handle.mount_path != mount_path:
        raise ValueError("Scratch handle mount path does not match its request")

    with directory_fd(root_path) as root_fd:
        root_info = os.fstat(root_fd)
        _directory_mode(root_info, "Scratch root", allow_traverse=True)
        request_fd = os.open(handle.request_id, _ROOT_FLAGS, dir_fd=root_fd)
        try:
            request_info = os.fstat(request_fd)
            _directory_mode(request_info, "Scratch request directory", allow_traverse=True)
            marker = _read_marker(request_fd)
            _validate_marker(marker, root_path=root_path, request_id=handle.request_id,
                             root_info=root_info, request_info=request_info,
                             size_bytes=handle.size_bytes)
            if marker["mount_path"] != handle.mount_path:
                raise ValueError("Scratch handle does not match retained mount identity")
            _verify_image(request_fd, marker)
            mountpoint_id, capacity = _observe_owned_mount(
                mount_path, image_path, handle.size_bytes,
                expected_mount_id=handle._mount_id,
                expected_capacity=handle.capacity_bytes,
            )
            if mountpoint_id != handle._mount_id or capacity != handle.capacity_bytes:
                raise RuntimeError("Scratch mount changed before release; retain evidence")

            _run_native(["/usr/bin/umount", "--", mount_path], timeout=60,
                        failure="Scratch unmount is busy or unconfirmed")
            if _find_mount(mount_path) is not None:
                raise RuntimeError("Scratch mount remains after unmount; retain evidence")
            if _read_marker(request_fd) != marker:
                raise ValueError("Scratch identity marker changed; retain evidence")
            _verify_image(request_fd, marker)
            mountpoint_info = _empty_directory(request_fd, _MOUNT_NAME)
            if (mountpoint_info.st_dev, mountpoint_info.st_ino) != (
                    marker["mountpoint_dev"], marker["mountpoint_ino"]):
                raise RuntimeError("Scratch mountpoint identity changed; retain evidence")
            if set(os.listdir(request_fd)) != {_MARKER_NAME, _IMAGE_NAME, _MOUNT_NAME}:
                raise RuntimeError("Scratch request directory has unexpected entries; retain evidence")

            os.unlink(_IMAGE_NAME, dir_fd=request_fd)
            os.fsync(request_fd)
            os.rmdir(_MOUNT_NAME, dir_fd=request_fd)
            os.fsync(request_fd)
            if os.listdir(request_fd) != [_MARKER_NAME]:
                raise RuntimeError("Scratch identity state changed during release; retain evidence")
            os.unlink(_MARKER_NAME, dir_fd=request_fd)
            os.fsync(request_fd)
        finally:
            os.close(request_fd)
        os.rmdir(handle.request_id, dir_fd=root_fd)
        os.fsync(root_fd)


__all__ = ["ScratchHandle", "create_scratch", "MIN_SIZE_BYTES", "MAX_SIZE_BYTES", "MIB"]
