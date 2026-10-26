"""Bounded typed dispatcher for the internal shared build-host protocol.

The caller supplies the authenticated principal from trusted transport state.
This module never starts a worker and never accepts paths, commands, or owner
labels as dispatcher fields. The transport owner must supervise blocking reads
and writes with its own deadline.
"""

from __future__ import annotations

from collections.abc import Callable
import hashlib
import os
import re
import stat
from typing import Any, BinaryIO

from lib.build_host_policy import (
    SUPPORTED_BUILD_RUNTIMES,
    authorize_build_request,
    capabilities_for_principal,
)
from lib.build_jobs import BuildJobStore
from lib.build_protocol import (
    MAX_ARTIFACT_BYTES,
    MAX_DESCRIPTOR_BYTES,
    MAX_REQUEST_BYTES,
    decode_document,
    encode_document,
    parse_build_request,
)
from lib.build_source import MAX_SNAPSHOT_BYTES
from lib.validation import validate_filesystem_path, validate_no_control_characters


MAX_HEADER_LINE_BYTES = MAX_REQUEST_BYTES + 4096
MAX_RESPONSE_LINE_BYTES = 64 * 1024
MAX_LOG_CHUNK_BYTES = 64 * 1024
MAX_LOG_BYTES = 16 * 1024**2
MAX_ARTIFACT_BUNDLE_BYTES = MAX_ARTIFACT_BYTES + MAX_DESCRIPTOR_BYTES + 1024
_HEADER_OVERHEAD_BYTES = MAX_HEADER_LINE_BYTES - MAX_REQUEST_BYTES
_REQUEST_ID = re.compile(r"[0-9a-f]{32}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_SAFE_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._+()\-]{0,255}")
_DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
_READ = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
_ARTIFACT_IDENTITY_FIELDS = frozenset({
    "sha256", "size", "descriptor_sha256", "request_id", "request_sha256",
    "project", "source", "platform",
})


def _fixed_response(*, error: str | None = None, result: object = None,
                    payload_bytes: int = 0) -> bytes:
    if error is None:
        value = {"schema": 1, "ok": True, "result": result, "payload_bytes": payload_bytes}
    else:
        value = {"schema": 1, "ok": False, "error": error, "payload_bytes": 0}
    encoded = encode_document(value, maximum=MAX_RESPONSE_LINE_BYTES - 1) + b"\n"
    if len(encoded) > MAX_RESPONSE_LINE_BYTES:
        raise ValueError("Build host response exceeds its byte limit")
    return encoded


def _write_all(stream: BinaryIO, value: bytes | memoryview) -> None:
    remaining = memoryview(value)
    while remaining:
        written = stream.write(remaining)
        if type(written) is not int or written <= 0:
            raise OSError("Build host response write did not complete")
        remaining = remaining[written:]


def _request_id(value: object) -> str:
    if not isinstance(value, str) or _REQUEST_ID.fullmatch(value) is None:
        raise ValueError("Invalid build request ID")
    return value


def _private_root(path: str) -> int:
    """Open an owned 0700 root after checking every path ancestor."""
    validate_filesystem_path(path, must_exist=True)
    if (not os.path.isabs(path) or os.path.normpath(path) != path or path == "/"
            or any(char.isspace() for char in path) or any(char in path for char in "%:")):
        raise ValueError("Build storage root is not a supported private path")

    trusted_owners = {0, os.geteuid()}
    descriptor = os.open("/", _DIRECTORY)
    parent_info = os.fstat(descriptor)
    try:
        if not stat.S_ISDIR(parent_info.st_mode) or parent_info.st_uid not in trusted_owners:
            raise ValueError("Build storage ancestry is not trusted")
        for part in path.split("/")[1:]:
            child = os.open(part, _DIRECTORY, dir_fd=descriptor)
            try:
                info = os.fstat(child)
                if not stat.S_ISDIR(info.st_mode) or info.st_uid not in trusted_owners:
                    raise ValueError("Build storage ancestry is not trusted")
                if stat.S_IMODE(parent_info.st_mode) & 0o022:
                    sticky_protection = (
                        parent_info.st_mode & stat.S_ISVTX
                        and parent_info.st_uid in trusted_owners
                        and info.st_uid in trusted_owners
                    )
                    if not sticky_protection:
                        raise ValueError("Build storage ancestry is writable by other users")
            except Exception:
                os.close(child)
                raise
            os.close(descriptor)
            descriptor = child
            parent_info = info

        info = os.fstat(descriptor)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError("Build storage root must be private and owned by this process")
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _file_identity(info: os.stat_result) -> tuple[int, int, int, int, int, int, int, int]:
    return (
        info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink,
        info.st_size, info.st_mtime_ns, info.st_ctime_ns,
    )


def _check_private_file(info: os.stat_result, *, maximum_size: int) -> None:
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600
            or not 0 <= info.st_size <= maximum_size):
        raise ValueError("Build storage file has an unsafe type, owner, mode, link count, or size")


def _open_private_file(root_fd: int, name: str, *, maximum_size: int,
                       allow_growth: bool = False) -> tuple[int, os.stat_result]:
    named = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
    _check_private_file(named, maximum_size=maximum_size)
    descriptor = os.open(name, _READ, dir_fd=root_fd)
    try:
        opened = os.fstat(descriptor)
        _check_private_file(opened, maximum_size=maximum_size)
        same_inode = (named.st_dev, named.st_ino) == (opened.st_dev, opened.st_ino)
        stable = _file_identity(named) == _file_identity(opened)
        if not same_inode or (not stable and (not allow_growth or opened.st_size < named.st_size)):
            raise ValueError("Build storage file changed while being opened")
        return descriptor, opened
    except Exception:
        os.close(descriptor)
        raise


def _assert_same_named_file(root_fd: int, name: str, expected: os.stat_result,
                            *, maximum_size: int) -> os.stat_result:
    named = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
    _check_private_file(named, maximum_size=maximum_size)
    if _file_identity(named) != _file_identity(expected):
        raise OSError("Build storage file identity changed")
    return named


def _assert_log_name(root_fd: int, name: str, expected: os.stat_result,
                     *, maximum_size: int) -> os.stat_result:
    named = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
    _check_private_file(named, maximum_size=maximum_size)
    if ((named.st_dev, named.st_ino) != (expected.st_dev, expected.st_ino)
            or named.st_size < expected.st_size):
        raise OSError("Build log identity changed")
    return named


def _require_eof(stream: BinaryIO) -> None:
    trailing = stream.read(1)
    if not isinstance(trailing, bytes) or trailing:
        raise ValueError("Unexpected trailing build protocol payload")


class _ArtifactPayload:
    """Pinned descriptor used for one already-verified artifact transfer."""

    def __init__(self, root_fd: int, descriptor: int, name: str, size: int,
                 digest: str, identity: os.stat_result) -> None:
        self.root_fd = root_fd
        self.descriptor = descriptor
        self.name = name
        self.size = size
        self.digest = digest
        self.identity = identity

    def close(self) -> None:
        for descriptor in (self.descriptor, self.root_fd):
            try:
                os.close(descriptor)
            except OSError:
                pass

    def verify_identity(self) -> None:
        info = os.fstat(self.descriptor)
        _check_private_file(info, maximum_size=MAX_ARTIFACT_BUNDLE_BYTES)
        if _file_identity(info) != _file_identity(self.identity):
            raise OSError("Build artifact identity changed")
        _assert_same_named_file(
            self.root_fd, self.name, self.identity, maximum_size=MAX_ARTIFACT_BUNDLE_BYTES,
        )

    def stream(self, output_stream: BinaryIO) -> None:
        self.verify_identity()
        transferred = hashlib.sha256()
        offset = 0
        while offset < self.size:
            block = os.pread(self.descriptor, min(MAX_LOG_CHUNK_BYTES, self.size - offset), offset)
            if not block:
                raise OSError("Build artifact ended before its stored size")
            transferred.update(block)
            _write_all(output_stream, block)
            offset += len(block)
        self.verify_identity()
        if transferred.hexdigest() != self.digest:
            raise OSError("Build artifact changed during transfer")


class BuildHostService:
    """Dispatch one strict request for a principal authenticated by transport."""

    def __init__(self, *, jobs: BuildJobStore, load_policy: Callable[[], object],
                 admit_source: Callable[..., dict[str, Any]],
                 tool_versions: Callable[[], dict[str, str]],
                 logs_root: str, artifacts_root: str) -> None:
        if not isinstance(jobs, BuildJobStore):
            raise ValueError("A build job store is required")
        for callback in (load_policy, admit_source, tool_versions):
            if not callable(callback):
                raise ValueError("Build host callbacks must be callable")
        if not isinstance(logs_root, str) or not isinstance(artifacts_root, str):
            raise ValueError("Build storage roots must be text paths")
        self.jobs = jobs
        self.load_policy = load_policy
        self.admit_source = admit_source
        self.tool_versions = tool_versions
        self.logs_root = logs_root
        self.artifacts_root = artifacts_root

    def serve_once(self, *, principal: str, input_stream: BinaryIO,
                   output_stream: BinaryIO) -> None:
        """Read one bounded header line and dispatch it; payload reads stay typed."""
        try:
            header = input_stream.readline(MAX_HEADER_LINE_BYTES + 1)
            if not isinstance(header, bytes):
                raise ValueError("Build protocol input must be binary")
        except ValueError:
            _write_all(output_stream, _fixed_response(error="rejected"))
            output_stream.flush()
            return
        except PermissionError:
            _write_all(output_stream, _fixed_response(error="rejected"))
            output_stream.flush()
            return
        except OSError:
            _write_all(output_stream, _fixed_response(error="unavailable"))
            output_stream.flush()
            return
        self.handle(header, principal=principal, input_stream=input_stream,
                    output_stream=output_stream)

    def handle(self, header: bytes, *, principal: str, input_stream: BinaryIO,
               output_stream: BinaryIO) -> None:
        """Dispatch one raw JSON line and write its bounded JSON/binary reply.

        For non-submit actions, EOF is required before any lookup or mutation.
        Submit framing, exact source byte count, and EOF are owned by the
        injected ``admit_source`` callback. Callers must enforce I/O deadlines.
        """
        payload: _ArtifactPayload | None = None
        try:
            action, fields = self._parse_header(header)
            if action != "submit":
                _require_eof(input_stream)
            result, raw_payload, payload = self._dispatch(
                action, fields, principal=principal, input_stream=input_stream,
            )
            if payload is not None:
                payload.verify_identity()
                payload_bytes = payload.size
            else:
                payload_bytes = len(raw_payload) if raw_payload is not None else 0
            response = _fixed_response(
                result=result, payload_bytes=payload_bytes,
            )
        except PermissionError:
            if payload is not None:
                payload.close()
            _write_all(output_stream, _fixed_response(error="rejected"))
            output_stream.flush()
            return
        except (ValueError, RuntimeError):
            if payload is not None:
                payload.close()
            _write_all(output_stream, _fixed_response(error="rejected"))
            output_stream.flush()
            return
        except OSError:
            if payload is not None:
                payload.close()
            _write_all(output_stream, _fixed_response(error="unavailable"))
            output_stream.flush()
            return

        try:
            _write_all(output_stream, response)
            if payload is not None:
                payload.stream(output_stream)
            elif raw_payload is not None:
                _write_all(output_stream, raw_payload)
            output_stream.flush()
        finally:
            if payload is not None:
                payload.close()

    @staticmethod
    def _parse_header(header: bytes) -> tuple[str, dict[str, Any]]:
        if (not isinstance(header, bytes) or not header or len(header) > MAX_HEADER_LINE_BYTES
                or not header.endswith(b"\n") or b"\n" in header[:-1]
                or b"\r" in header):
            raise ValueError("Malformed build protocol header")
        document = decode_document(
            header[:-1], maximum=MAX_REQUEST_BYTES + _HEADER_OVERHEAD_BYTES,
        )
        if not isinstance(document, dict) or type(document.get("schema")) is not int \
                or document.get("schema") != 1:
            raise ValueError("Unsupported build protocol schema")
        action = document.get("action")
        fields_by_action = {
            "capabilities": {"schema", "action"},
            "submit": {"schema", "action", "request", "source_size"},
            "status": {"schema", "action", "request_id"},
            "cancel": {"schema", "action", "request_id"},
            "log": {"schema", "action", "request_id", "offset", "max_bytes"},
            "artifact": {"schema", "action", "request_id"},
        }
        if not isinstance(action, str) or action not in fields_by_action:
            raise ValueError("Unsupported build protocol action")
        if set(document) != fields_by_action[action]:
            raise ValueError("Build protocol header has missing or unsupported fields")

        fields = dict(document)
        if action == "submit":
            fields["request"] = parse_build_request(fields["request"])
            size = fields["source_size"]
            if type(size) is not int or not 1 <= size <= MAX_SNAPSHOT_BYTES:
                raise ValueError("Source snapshot size is outside supported limits")
        elif action in {"status", "cancel", "artifact", "log"}:
            fields["request_id"] = _request_id(fields["request_id"])
            if action == "log":
                offset = fields["offset"]
                max_bytes = fields["max_bytes"]
                if type(offset) is not int or not 0 <= offset <= MAX_LOG_BYTES:
                    raise ValueError("Log offset is outside supported limits")
                if type(max_bytes) is not int or not 0 <= max_bytes <= MAX_LOG_CHUNK_BYTES:
                    raise ValueError("Log chunk size is outside supported limits")
        return action, fields

    def _dispatch(self, action: str, fields: dict[str, Any], *, principal: str,
                  input_stream: BinaryIO) -> tuple[dict[str, Any], bytes | None, _ArtifactPayload | None]:
        if action == "capabilities":
            capabilities = capabilities_for_principal(self.load_policy(), principal=principal)
            versions = self._safe_tool_versions(self.tool_versions())
            return {**capabilities, "tool_versions": versions}, None, None

        if action == "submit":
            request = fields["request"]
            authorize_build_request(
                self.load_policy(), principal=principal, request=request, requested_runtimes=[],
            )
            receipt = self.admit_source(
                request, owner=principal, source=input_stream,
                source_size=fields["source_size"], jobs=self.jobs,
            )
            if not isinstance(receipt, dict):
                raise ValueError("Source admission returned an invalid receipt")
            return receipt, None, None

        request_id = fields["request_id"]
        status = self.jobs.status(request_id, owner=principal)
        self._authorize_project(principal, status)
        if action == "status":
            return status, None, None
        if action == "cancel":
            return self.jobs.cancel(request_id, owner=principal), None, None
        if action == "log":
            payload, metadata = self._read_log(status, request_id, fields["offset"],
                                               fields["max_bytes"])
            return metadata, payload, None
        if action == "artifact":
            payload = self._open_artifact(status, request_id)
            return {"sha256": payload.digest, "size": payload.size}, None, payload
        raise ValueError("Unsupported build protocol action")

    def _authorize_project(self, principal: str, status: object) -> None:
        if not isinstance(status, dict) or not isinstance(status.get("project"), str):
            raise ValueError("Build store returned invalid public status")
        capabilities = capabilities_for_principal(self.load_policy(), principal=principal)
        if status["project"] not in capabilities["projects"]:
            raise PermissionError("Current build project grant is unavailable")

    @staticmethod
    def _safe_tool_versions(value: object) -> dict[str, str]:
        if not isinstance(value, dict) or len(value) > 128:
            raise ValueError("Installed tool observations are invalid")
        result: dict[str, str] = {}
        for name in sorted(SUPPORTED_BUILD_RUNTIMES):
            version = value.get(name)
            if not isinstance(version, str) or len(version) > 256 \
                    or _SAFE_VERSION.fullmatch(version) is None:
                continue
            try:
                version.encode("utf-8", errors="strict")
                validate_no_control_characters(version, "Installed tool version")
            except (UnicodeError, ValueError):
                continue
            result[name] = version
        return result

    def _read_log(self, status: dict[str, Any], request_id: str, offset: int,
                  max_bytes: int) -> tuple[bytes, dict[str, Any]]:
        limits = status.get("limits")
        state = status.get("state")
        if (not isinstance(limits, dict) or type(limits.get("log_bytes")) is not int
                or not 1 <= limits["log_bytes"] <= MAX_LOG_BYTES
                or not isinstance(state, str)):
            raise ValueError("Build store returned invalid log metadata")
        if offset > limits["log_bytes"]:
            raise ValueError("Log offset exceeds this job's log limit")
        root_fd = _private_root(self.logs_root)
        try:
            try:
                descriptor, before = _open_private_file(
                    root_fd, request_id + ".log", maximum_size=limits["log_bytes"],
                    allow_growth=True,
                )
            except FileNotFoundError:
                if state not in {"queued", "running"}:
                    raise OSError("Build log is unavailable") from None
                return b"", {"offset": offset, "next_offset": offset, "waiting": True,
                             "complete": False}
            try:
                available = max(0, before.st_size - offset)
                payload = os.pread(descriptor, min(max_bytes, available), offset) if available else b""
                after = os.fstat(descriptor)
                _check_private_file(after, maximum_size=limits["log_bytes"])
                if ((after.st_dev, after.st_ino) != (before.st_dev, before.st_ino)
                        or after.st_size < before.st_size):
                    raise OSError("Build log changed while being read")
                _assert_log_name(
                    root_fd, request_id + ".log", after, maximum_size=limits["log_bytes"],
                )
            finally:
                os.close(descriptor)
        finally:
            os.close(root_fd)

        next_offset = offset + len(payload)
        active = state in {"queued", "running"}
        waiting = active and next_offset >= before.st_size
        complete = not active and next_offset >= before.st_size
        return payload, {"offset": offset, "next_offset": next_offset,
                         "waiting": waiting, "complete": complete}

    def _open_artifact(self, status: dict[str, Any], request_id: str) -> _ArtifactPayload:
        artifact = status.get("artifact")
        if status.get("state") != "succeeded" or not isinstance(artifact, dict) \
                or set(artifact) != _ARTIFACT_IDENTITY_FIELDS:
            raise ValueError("Build artifact is unavailable")
        digest = artifact.get("sha256")
        size = artifact.get("size")
        if (not isinstance(digest, str) or _SHA256.fullmatch(digest) is None
                or type(size) is not int or not 1 <= size <= MAX_ARTIFACT_BUNDLE_BYTES
                or artifact.get("request_id") != request_id
                or artifact.get("project") != status.get("project")
                or artifact.get("source") != status.get("source")
                or artifact.get("platform") != status.get("platform")
                or not isinstance(artifact.get("descriptor_sha256"), str)
                or _SHA256.fullmatch(artifact["descriptor_sha256"]) is None
                or not isinstance(artifact.get("request_sha256"), str)
                or _SHA256.fullmatch(artifact["request_sha256"]) is None):
            raise ValueError("Build store returned an invalid artifact identity")

        name = request_id + ".bundle"
        root_fd = _private_root(self.artifacts_root)
        descriptor = -1
        pinned_root = -1
        try:
            descriptor, before = _open_private_file(
                root_fd, name, maximum_size=MAX_ARTIFACT_BUNDLE_BYTES,
            )
            if before.st_size != size:
                raise ValueError("Build artifact size does not match its stored identity")
            pinned_root = os.dup(root_fd)
            calculated = hashlib.sha256()
            offset = 0
            while offset < size:
                block = os.pread(descriptor, min(MAX_LOG_CHUNK_BYTES, size - offset), offset)
                if not block:
                    raise OSError("Build artifact ended before its stored size")
                calculated.update(block)
                offset += len(block)
            after = os.fstat(descriptor)
            _check_private_file(after, maximum_size=MAX_ARTIFACT_BUNDLE_BYTES)
            if _file_identity(after) != _file_identity(before):
                raise OSError("Build artifact changed during verification")
            _assert_same_named_file(
                root_fd, name, before, maximum_size=MAX_ARTIFACT_BUNDLE_BYTES,
            )
            if calculated.hexdigest() != digest:
                raise ValueError("Build artifact digest does not match its stored identity")
            result = _ArtifactPayload(pinned_root, descriptor, name, size, digest, before)
            pinned_root = -1
            descriptor = -1
            return result
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if pinned_root >= 0:
                os.close(pinned_root)
            os.close(root_fd)


__all__ = ["BuildHostService", "MAX_HEADER_LINE_BYTES", "MAX_LOG_CHUNK_BYTES",
           "MAX_RESPONSE_LINE_BYTES"]
