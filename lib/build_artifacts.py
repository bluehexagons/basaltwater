"""Broker-owned build output snapshots and verified, bounded release bundles.

The caller must stop build processes before packaging and keep its private
artifact parent inaccessible to the build identity. No project archive is
trusted. Files are copied through no-follow descriptors before inventorying.
Authentication and platform/runtime authorization belong to the caller.
"""

from __future__ import annotations

import hashlib
import ctypes
import os
import secrets
import shutil
import stat
from contextlib import contextmanager
from typing import Any, BinaryIO, Iterator

from lib.build_protocol import (
    CONTROL_NAMES,
    MAX_DESCRIPTOR_BYTES,
    decode_document,
    encode_document,
    parse_build_request,
    request_digest,
    validate_artifact_for_request,
    validate_artifact_path,
)
from lib.validation import validate_filesystem_path


MAGIC = b"BASALTWATER-ARTIFACT\x00\x01\n"
_CHUNK = 1024 * 1024
_DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_READ = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK


@contextmanager
def _directory(path: str) -> Iterator[int]:
    """Open every path component without following symbolic links."""
    validate_filesystem_path(path)
    descriptor = os.open("/", _DIRECTORY)
    try:
        for part in os.path.abspath(path).split("/")[1:]:
            if part:
                child = os.open(part, _DIRECTORY, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


@contextmanager
def _parent(path: str) -> Iterator[tuple[int, str]]:
    """Require a private broker/controller-owned publication directory."""
    validate_filesystem_path(path)
    absolute = os.path.abspath(path)
    parent, name = os.path.split(absolute)
    if not name:
        raise ValueError("Artifact destination must name a new entry")
    with _directory(parent) as descriptor:
        info = os.fstat(descriptor)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise ValueError("Artifact parent must be private and owned by the broker")
        try:
            os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise FileExistsError("Artifact destination already exists")
        yield descriptor, name


@contextmanager
def _temporary_tree(parent: int) -> Iterator[tuple[str, int]]:
    name = ".artifact-" + secrets.token_hex(16)
    os.mkdir(name, mode=0o700, dir_fd=parent)
    descriptor = os.open(name, _DIRECTORY, dir_fd=parent)
    try:
        yield name, descriptor
    finally:
        os.close(descriptor)
        try:
            shutil.rmtree(name, dir_fd=parent)
        except FileNotFoundError:
            pass


@contextmanager
def _relative_parent(root: int, path: str, *, create: bool = False) -> Iterator[tuple[int, str]]:
    validate_artifact_path(path)
    parts = path.split("/")
    descriptor = os.dup(root)
    try:
        for part in parts[:-1]:
            if create:
                try:
                    os.mkdir(part, mode=0o755, dir_fd=descriptor)
                except FileExistsError:
                    pass
            child = os.open(part, _DIRECTORY, dir_fd=descriptor)
            if create:
                os.fchmod(child, 0o755)
            os.close(descriptor)
            descriptor = child
        yield descriptor, parts[-1]
    finally:
        os.close(descriptor)


@contextmanager
def _regular(root: int, path: str) -> Iterator[BinaryIO]:
    with _relative_parent(root, path) as (parent, name):
        descriptor = os.open(name, _READ, dir_fd=parent)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("Artifact entry must be a regular file")
        stream = os.fdopen(descriptor, "rb")
    except BaseException:
        os.close(descriptor)
        raise
    with stream:
        yield stream


def _copy_file(source: int, destination: int, name: str, budget: dict[str, int]) -> None:
    descriptor = os.open(name, _READ, dir_fd=source)
    with os.fdopen(descriptor, "rb") as incoming:
        before = os.fstat(incoming.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ValueError("Build exports must be regular files without hard links")
        if before.st_size > budget["bytes"]:
            raise ValueError("Build exports exceed the admitted byte limit")
        output = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=destination)
        with os.fdopen(output, "wb") as outgoing:
            remaining = before.st_size
            while remaining:
                content = incoming.read(min(_CHUNK, remaining))
                if not content:
                    raise ValueError("Build export changed during snapshot")
                outgoing.write(content)
                remaining -= len(content)
            if incoming.read(1):
                raise ValueError("Build export grew during snapshot")
            after = os.fstat(incoming.fileno())
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                    after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise ValueError("Build export changed during snapshot")
            os.fchmod(outgoing.fileno(), 0o755 if before.st_mode & 0o111 else 0o644)
        budget["bytes"] -= before.st_size


def _snapshot(source: int, destination: int, exports: list[str], limits: dict) -> list[str]:
    """Copy selected outputs into private storage; never traverse a job link."""
    seen: set[str] = set()
    budget = {"bytes": limits["artifact_bytes"]}

    def copy(parent: int, target: int, name: str, path: str, *, recurse: bool) -> None:
        validate_artifact_path(path)
        info = os.stat(name, dir_fd=parent, follow_symlinks=False)
        directory = stat.S_ISDIR(info.st_mode)
        if not directory and not stat.S_ISREG(info.st_mode):
            raise ValueError("Build export contains a link or special file")
        if path not in seen:
            if len(seen) >= limits["artifact_entries"]:
                raise ValueError("Build exports exceed the admitted entry limit")
            if directory:
                os.mkdir(name, mode=0o755, dir_fd=target)
            else:
                _copy_file(parent, target, name, budget)
            seen.add(path)
        if directory and recurse:
            child = os.open(name, _DIRECTORY, dir_fd=parent)
            output = os.open(name, _DIRECTORY, dir_fd=target)
            try:
                with os.scandir(child) as entries:
                    for entry in entries:
                        if entry.name.casefold() not in CONTROL_NAMES:
                            copy(child, output, entry.name, path + "/" + entry.name, recurse=True)
            finally:
                os.close(output)
                os.close(child)

    for export in exports:
        if export == ".":
            with os.scandir(source) as entries:
                for entry in entries:
                    if entry.name.casefold() not in CONTROL_NAMES:
                        copy(source, destination, entry.name, entry.name, recurse=True)
            continue
        parts = export.split("/")
        parent, target = os.dup(source), os.dup(destination)
        try:
            for index, name in enumerate(parts):
                path = "/".join(parts[:index + 1])
                copy(parent, target, name, path, recurse=index == len(parts) - 1)
                if index != len(parts) - 1:
                    child = os.open(name, _DIRECTORY, dir_fd=parent)
                    os.close(parent)
                    parent = child
                    child = os.open(name, _DIRECTORY, dir_fd=target)
                    os.close(target)
                    target = child
        finally:
            os.close(parent)
            os.close(target)
    return sorted(seen)


def _inventory(root: int, paths: list[str]) -> list[dict[str, Any]]:
    entries = []
    for path in paths:
        with _relative_parent(root, path) as (parent, name):
            info = os.stat(name, dir_fd=parent, follow_symlinks=False)
        directory = stat.S_ISDIR(info.st_mode)
        digest = hashlib.sha256()
        if not directory:
            with _regular(root, path) as stream:
                while content := stream.read(_CHUNK):
                    digest.update(content)
        entries.append({"path": path, "kind": "directory" if directory else "file",
                        "size": 0 if directory else info.st_size,
                        "sha256": "" if directory else digest.hexdigest(),
                        "executable": False if directory else bool(info.st_mode & 0o111)})
    return entries


def _publish_tree(parent: int, source: str, destination: str) -> None:
    """Atomically refuse any existing destination, including a racing creator.

    This is the Debian adapter. Platforms without renameat2 must provide their
    own no-replace primitive; an unsafe replace fallback is never used.
    """
    library = ctypes.CDLL(None, use_errno=True)
    rename = getattr(library, "renameat2", None)
    if rename is None:
        raise NotImplementedError("Artifact publication requires no-replace rename support")
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(parent, os.fsencode(source), parent, os.fsencode(destination), 1) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), destination)


def create_artifact(workspace: str, request: object, destination: str, *,
                    toolchains: dict[str, str] | None = None) -> dict[str, Any]:
    """Publish a new bundle from stopped build output, without replacing a file.

    The result describes the bundle digest, byte count and verified descriptor.
    A failure after publication may leave the complete file; callers reconcile
    their durable operation rather than blindly replacing it on retry.
    """
    job = parse_build_request(request)
    with _parent(destination) as (parent, name), _temporary_tree(parent) as (_, snapshot):
        with _directory(workspace) as source:
            paths = _snapshot(source, snapshot,
                              [path for item in job["components"] for path in item["artifacts"]],
                              job["limits"])
        descriptor = validate_artifact_for_request({
            "schema": 1, "request_id": job["request_id"], "project": job["project"],
            "request_sha256": request_digest(job), "source": job["source"],
            "platform": job["platform"], "components": [
                {key: item[key] for key in ("name", "type", "artifacts")}
                for item in job["components"]],
            "toolchains": toolchains or {}, "entries": _inventory(snapshot, paths),
        }, job)
        encoded = encode_document(descriptor, maximum=MAX_DESCRIPTOR_BYTES)
        temporary = ".bundle-" + secrets.token_hex(16)
        descriptor_fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                0o600, dir_fd=parent)
        digest, size = hashlib.sha256(), 0
        try:
            with os.fdopen(descriptor_fd, "wb") as stream:
                def write(content):
                    nonlocal size
                    stream.write(content)
                    digest.update(content)
                    size += len(content)
                write(MAGIC + len(encoded).to_bytes(8, "big") + encoded)
                for entry in descriptor["entries"]:
                    if entry["kind"] == "file":
                        with _regular(snapshot, entry["path"]) as incoming:
                            while content := incoming.read(_CHUNK):
                                write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.link(temporary, name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
            os.fsync(parent)
        finally:
            os.unlink(temporary, dir_fd=parent)
        return {"sha256": digest.hexdigest(), "size": size, "descriptor": descriptor}


def unpack_artifact(bundle: str, request: object, destination: str, *,
                    expected_sha256: str) -> dict[str, Any]:
    """Verify every byte into a new private tree before publishing that tree."""
    if (not isinstance(expected_sha256, str) or len(expected_sha256) != 64
            or any(char not in "0123456789abcdef" for char in expected_sha256)):
        raise ValueError("An expected artifact digest is required")
    job = parse_build_request(request)
    with _parent(destination) as (parent, name), _temporary_tree(parent) as (temporary, root):
        bundle_parent, bundle_name = os.path.split(os.path.abspath(bundle))
        with _directory(bundle_parent) as incoming_parent, _regular(incoming_parent, bundle_name) as stream:
            digest = hashlib.sha256()

            def read(size):
                content = stream.read(size)
                digest.update(content)
                if len(content) != size:
                    raise ValueError("Truncated artifact bundle")
                return content

            if read(len(MAGIC)) != MAGIC:
                raise ValueError("Unsupported artifact bundle format")
            length = int.from_bytes(read(8), "big")
            if not 1 <= length <= MAX_DESCRIPTOR_BYTES:
                raise ValueError("Artifact descriptor exceeds its byte limit")
            descriptor = validate_artifact_for_request(
                decode_document(read(length), maximum=MAX_DESCRIPTOR_BYTES), job)
            for entry in descriptor["entries"]:
                with _relative_parent(root, entry["path"], create=True) as (directory, leaf):
                    if entry["kind"] == "directory":
                        try:
                            os.mkdir(leaf, mode=0o755, dir_fd=directory)
                        except FileExistsError:
                            pass
                        child = os.open(leaf, _DIRECTORY, dir_fd=directory)
                        try:
                            os.fchmod(child, 0o755)
                        finally:
                            os.close(child)
                        continue
                    output = os.open(leaf, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                     0o600, dir_fd=directory)
                    file_digest, remaining = hashlib.sha256(), entry["size"]
                    with os.fdopen(output, "wb") as outgoing:
                        while remaining:
                            content = read(min(_CHUNK, remaining))
                            outgoing.write(content)
                            file_digest.update(content)
                            remaining -= len(content)
                        if file_digest.hexdigest() != entry["sha256"]:
                            raise ValueError("Artifact file checksum mismatch")
                        os.fchmod(outgoing.fileno(), 0o755 if entry["executable"] else 0o644)
                        outgoing.flush()
                        os.fsync(outgoing.fileno())
            if stream.read(1):
                raise ValueError("Unexpected bytes after artifact inventory")
            if digest.hexdigest() != expected_sha256:
                raise ValueError("Artifact bundle checksum mismatch")
        # Sync directory entries bottom-up while retaining private root mode.
        for entry in reversed(descriptor["entries"]):
            if entry["kind"] == "directory":
                with _relative_parent(root, entry["path"]) as (directory, leaf):
                    child = os.open(leaf, _DIRECTORY, dir_fd=directory)
                    try:
                        os.fsync(child)
                    finally:
                        os.close(child)
        os.fsync(root)
        _publish_tree(parent, temporary, name)
        os.fsync(parent)
        return descriptor
