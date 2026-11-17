"""Resolve administrator-owned runtime environment files without reading values."""

from __future__ import annotations

from contextlib import contextmanager
import os
import re
import stat
from typing import Iterator

from lib.build_protocol import decode_document
from lib.validation import validate_filesystem_path, validate_no_control_characters


REGISTRY_PATH = "/etc/basaltwater/runtime-bindings.json"
MAX_REGISTRY_BYTES = 256 * 1024
_ROOT_UID = 0
_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")


def validate_runtime_binding(value: object) -> str:
    """Accept a literal binding ID, never a target path or template."""
    if not isinstance(value, str) or _NAME.fullmatch(value) is None:
        raise ValueError("runtime_binding must be a lowercase slug of 1–64 characters")
    validate_no_control_characters(value, "Runtime binding ID")
    return value


def _path(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Runtime binding paths must be canonical absolute paths")
    validate_filesystem_path(value, must_exist=False)
    if (len(value) > 4096 or re.fullmatch(r"/[A-Za-z0-9_./-]+", value) is None
            or value == "/" or value.startswith("//") or os.path.normpath(value) != value):
        raise ValueError("Runtime binding paths must be canonical absolute paths")
    return value


def parse_runtime_bindings(value: object) -> dict:
    """Validate target-local grants, scoped to an exact release root/component."""
    if (not isinstance(value, dict) or set(value) != {"schema", "bindings"}
            or type(value["schema"]) is not int or value["schema"] != 1
            or not isinstance(value["bindings"], dict) or len(value["bindings"]) > 256):
        raise ValueError("Invalid runtime binding registry")
    result = {}
    for name, grant in value["bindings"].items():
        validate_runtime_binding(name)
        if not isinstance(grant, dict) or set(grant) != {"deployment_root", "component", "env_file"}:
            raise ValueError("Invalid runtime binding grant")
        result[name] = {
            "deployment_root": _path(grant["deployment_root"]),
            "component": validate_runtime_binding(grant["component"]),
            "env_file": _path(grant["env_file"]),
        }
    return {"schema": 1, "bindings": result}


@contextmanager
def _private_file(path: str, *, metadata_only: bool = False) -> Iterator[int]:
    """Open through root-controlled no-follow directories, rejecting special files."""
    _path(path)
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    descriptor = os.open("/", directory_flags)
    trusted = {0, _ROOT_UID}
    try:
        for part in path.split("/")[1:-1]:
            parent = os.fstat(descriptor)
            if (parent.st_uid not in trusted
                    or parent.st_mode & 0o022 and not parent.st_mode & stat.S_ISVTX):
                raise ValueError("Runtime binding directory is not root-controlled")
            child = os.open(part, directory_flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        parent = os.fstat(descriptor)
        if parent.st_uid not in trusted or parent.st_mode & 0o022:
            raise ValueError("Runtime binding parent is not root-controlled")
        flags = os.O_PATH if metadata_only else os.O_RDONLY | os.O_NONBLOCK
        child = os.open(path.rsplit("/", 1)[1], flags | os.O_NOFOLLOW | os.O_CLOEXEC,
                        dir_fd=descriptor)
        try:
            info = os.fstat(child)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != _ROOT_UID
                    or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600):
                raise ValueError("Runtime binding requires a root-owned regular mode-0600 file")
            yield child
        finally:
            os.close(child)
    finally:
        os.close(descriptor)


def resolve_runtime_binding(binding: object, *, deployment_root: str, component: str) -> str:
    """Recheck current scope and file safety; return only the target-local path.

    No file is created, copied or changed. systemd reads the environment file as
    root when starting the service; the service/build users need no file access.
    """
    name = validate_runtime_binding(binding)
    with _private_file(REGISTRY_PATH) as descriptor:
        before = os.fstat(descriptor)
        if before.st_size > MAX_REGISTRY_BYTES:
            raise ValueError("Runtime binding registry exceeds its size limit")
        raw = bytearray()
        while len(raw) <= MAX_REGISTRY_BYTES:
            chunk = os.read(descriptor, min(65536, MAX_REGISTRY_BYTES + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
        after = os.fstat(descriptor)
        identity = lambda info: (info.st_dev, info.st_ino, info.st_uid, info.st_gid,
                                 info.st_mode, info.st_nlink, info.st_size,
                                 info.st_mtime_ns, info.st_ctime_ns)
        if len(raw) != before.st_size or identity(before) != identity(after):
            raise ValueError("Runtime binding registry changed during reading")
    registry = parse_runtime_bindings(decode_document(bytes(raw), maximum=MAX_REGISTRY_BYTES))
    grant = registry["bindings"].get(name)
    if (grant is None or grant["deployment_root"] != os.path.abspath(deployment_root)
            or grant["component"] != component):
        raise PermissionError("Runtime binding is missing, revoked or outside this component's scope")
    # Do not allow a release replacement or application data writer to replace
    # the privileged input, even if its current ownership happens to be safe.
    root = grant["deployment_root"]
    shared = os.path.join(os.path.dirname(root), ".basaltwater_shared")
    path = grant["env_file"]
    if any(os.path.commonpath((path, forbidden)) == forbidden for forbidden in (root, shared)):
        raise ValueError("Runtime binding file must be outside release and managed data trees")
    with _private_file(path, metadata_only=True):
        pass
    return path


def check_target_runtime_bindings(bindings: dict) -> None:
    """Check authorized IDs before target admission or retained activation."""
    for component in bindings["components"]:
        name = component.get("runtime", {}).get("runtime_binding")
        if name is not None:
            resolve_runtime_binding(name, deployment_root=bindings["deployment_root"],
                                    component=component["name"])
