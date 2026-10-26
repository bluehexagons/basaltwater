"""Bounded controller-side SSH client for the internal build-host protocol.

This module sends only typed ``basaltwater-build-v1`` requests. SSH trust is
provided by an explicit known-hosts file, and all remote work remains owned by
the durable build host after the transport exits.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import os
import re
import selectors
import signal
import stat
import subprocess
import time
from typing import Any, Callable

from lib.build_host_service import (
    MAX_HEADER_LINE_BYTES,
    MAX_LOG_CHUNK_BYTES,
    MAX_RESPONSE_LINE_BYTES,
)
from lib.build_inputs import _rename_noreplace
from lib.build_jobs import MAX_OPERATIONS
from lib.build_protocol import (
    DEFAULT_LIMITS,
    MAX_ARTIFACT_BYTES,
    MAX_DESCRIPTOR_BYTES,
    decode_document,
    encode_document,
    parse_build_request,
)
from lib.build_source import MAX_SNAPSHOT_BYTES
from lib.validation import validate_filesystem_path
from lib.validators import validate_host, validate_username


REMOTE_COMMAND = "basaltwater-build-v1"
MAX_TIMEOUT_SECONDS = 900
MAX_LOG_BYTES = 16 * 1024**2
MAX_ARTIFACT_BUNDLE_BYTES = MAX_ARTIFACT_BYTES + MAX_DESCRIPTOR_BYTES + 1024
MAX_TRANSFER_CHUNK_BYTES = 64 * 1024
MAX_STDERR_CAPTURE_BYTES = 64 * 1024
_ID = re.compile(r"[0-9a-f]{32}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_PROJECT = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
_TOKEN = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._+()/\-]{0,255}")
_RUNTIMES = frozenset({"go", "node", "python"})
_JOB_FIELDS = frozenset({
    "request_id", "sequence", "project", "source", "platform", "limits",
    "state", "cancel_requested", "created_at", "expires_at", "started_at",
    "finished_at", "artifact", "reason",
})
_PUBLIC_ARTIFACT_FIELDS = frozenset({
    "sha256", "size", "descriptor_sha256", "request_id", "request_sha256",
    "project", "source", "platform",
})
_LIMIT_BOUNDS = {
    "timeout_seconds": (1, 4 * 3600),
    "memory_bytes": (64 * 1024**2, 64 * 1024**3),
    "cpu_percent": (1, 6400),
    "scratch_bytes": (256 * 1024**2, 128 * 1024**3),
    "artifact_bytes": (1, MAX_ARTIFACT_BYTES),
    "artifact_entries": (1, 100_000),
    "log_bytes": (1, MAX_LOG_BYTES),
}
_DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
_READ = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
_CLOEXEC = getattr(os, "O_CLOEXEC", 0)


class BuildClientError(RuntimeError):
    """A local client failure that does not expose remote details."""

    def __init__(self, message: str, *, transport_started: bool = False) -> None:
        super().__init__(message)
        self.transport_started = transport_started


class BuildHostProtocolError(BuildClientError):
    """The remote endpoint returned a malformed or incomplete wire response."""


class BuildHostTransportError(BuildClientError):
    """The bounded SSH transport did not complete successfully."""


class BuildHostRemoteError(BuildClientError):
    """The build service returned one of its fixed error codes."""

    def __init__(self, code: str) -> None:
        super().__init__(f"Build host returned {code}", transport_started=True)
        self.code = code


class BuildSubmissionUnconfirmed(BuildClientError):
    """A submit transport outcome that may have followed durable admission."""

    def __init__(self, request_id: str, cause: Exception) -> None:
        code = cause.code if isinstance(cause, BuildHostRemoteError) else None
        description = f"Build submission {request_id} is unconfirmed; query status and reuse this same request ID and inputs"
        if code is not None:
            description += f" (remote code: {code})"
        super().__init__(description, transport_started=True)
        self.request_id = request_id
        self.error_code = code


class ArtifactPublicationUncertain(BuildClientError):
    """A verified artifact was published but its directory sync failed."""

    def __init__(self, destination: str, expected_sha256: str, expected_size: int) -> None:
        super().__init__(
            f"Artifact was verified and published at {destination}, "
            "but its directory sync failed; "
            f"reconcile size={expected_size} and sha256={expected_sha256} before retrying, "
            "and do not overwrite the destination",
        )
        self.destination = destination
        self.expected_sha256 = expected_sha256
        self.expected_size = expected_size


@dataclass(frozen=True)
class BuildLogChunk:
    """One raw, bounded log response and its continuation metadata."""

    data: bytes
    offset: int
    next_offset: int
    waiting: bool
    complete: bool


@dataclass(frozen=True)
class _SourceFile:
    path: str
    descriptor: int
    size: int
    digest: str
    identity: tuple[int, int, int, int, int, int, int, int]


@dataclass(frozen=True)
class _Reply:
    result: dict[str, Any] | None
    payload_bytes: int
    error: str | None


def _file_identity(info: os.stat_result) -> tuple[int, int, int, int, int, int, int, int]:
    return (
        info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink,
        info.st_size, info.st_mtime_ns, info.st_ctime_ns,
    )


def _absolute_path(value: object, label: str) -> str:
    try:
        path = os.fspath(value)
    except TypeError as exc:
        raise ValueError(f"{label} must be a filesystem path") from exc
    if not isinstance(path, str) or not path:
        raise ValueError(f"{label} must be a text path")
    validate_filesystem_path(path)
    if not os.path.isabs(path) or os.path.normpath(path) != path:
        raise ValueError(f"{label} must be a canonical absolute path")
    return path


def _open_trusted_file(path: str, label: str, *, private: bool) -> tuple[int, tuple[int, ...]]:
    """Open an explicit credential file without following its final symlink."""
    descriptor = os.open(path, _READ)
    try:
        info = os.fstat(descriptor)
        allowed_owners = {os.geteuid()} if private else {0, os.geteuid()}
        if (not stat.S_ISREG(info.st_mode) or info.st_uid not in allowed_owners
                or info.st_nlink != 1 or (stat.S_IMODE(info.st_mode) & 0o077 if private
                                          else stat.S_IMODE(info.st_mode) & 0o022)):
            raise ValueError(f"{label} must be a trusted regular file with safe permissions")
        return descriptor, _file_identity(info)
    except BaseException:
        os.close(descriptor)
        raise


def _verify_credential(descriptor: int, expected: tuple[int, ...], label: str) -> None:
    try:
        current = _file_identity(os.fstat(descriptor))
    except OSError as exc:
        raise BuildHostTransportError(f"Configured {label} is no longer available") from exc
    if current != expected:
        raise BuildHostTransportError(f"Configured {label} changed after client creation")


def _open_private_directory(path: str) -> int:
    """Open a private output parent after checking its no-follow ancestry."""
    validate_filesystem_path(path, must_exist=True)
    if not os.path.isabs(path) or os.path.normpath(path) != path or path == "/":
        raise ValueError("Artifact destination parent must be a canonical absolute path")

    trusted_owners = {0, os.geteuid()}
    descriptor = os.open("/", _DIRECTORY)
    try:
        parent_info = os.fstat(descriptor)
        if not stat.S_ISDIR(parent_info.st_mode) or parent_info.st_uid not in trusted_owners:
            raise ValueError("Artifact destination ancestry is not trusted")
        for part in path.split("/")[1:]:
            child = os.open(part, _DIRECTORY, dir_fd=descriptor)
            try:
                info = os.fstat(child)
                if not stat.S_ISDIR(info.st_mode) or info.st_uid not in trusted_owners:
                    raise ValueError("Artifact destination ancestry is not trusted")
                if stat.S_IMODE(parent_info.st_mode) & 0o022:
                    sticky_protection = (
                        parent_info.st_mode & stat.S_ISVTX
                        and parent_info.st_uid in trusted_owners
                        and info.st_uid in trusted_owners
                    )
                    if not sticky_protection:
                        raise ValueError("Artifact destination ancestry is writable by other users")
            except BaseException:
                os.close(child)
                raise
            os.close(descriptor)
            descriptor = child
            parent_info = info
        root_info = os.fstat(descriptor)
        if root_info.st_uid != os.geteuid() or stat.S_IMODE(root_info.st_mode) != 0o700:
            raise ValueError("Artifact destination parent must be owned mode 0700")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _private_temporary(parent_fd: int) -> tuple[str, int]:
    import secrets

    for _attempt in range(32):
        name = ".basaltwater-artifact-" + secrets.token_hex(16) + ".part"
        try:
            descriptor = os.open(
                name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | _CLOEXEC,
                0o600,
                dir_fd=parent_fd,
            )
            return name, descriptor
        except FileExistsError:
            continue
    raise FileExistsError("Could not allocate a private artifact temporary file")


def _request_id(value: object) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise ValueError("Request ID must be 32 lowercase hexadecimal characters")
    return value


def _digest(value: object, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _validate_platform(value: object) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {"os", "architecture", "baseline"}:
        raise ValueError("Invalid build platform")
    result: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(item, str) or _TOKEN.fullmatch(item) is None:
            raise ValueError("Invalid build platform")
        result[key] = item
    return result


def _validate_capabilities(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"schema", "projects", "tool_versions"}:
        raise ValueError("Invalid capabilities result")
    if type(value["schema"]) is not int or value["schema"] != 1:
        raise ValueError("Invalid capabilities schema")
    projects = value["projects"]
    versions = value["tool_versions"]
    if not isinstance(projects, dict) or len(projects) > 128:
        raise ValueError("Invalid capabilities project map")
    checked_projects: dict[str, Any] = {}
    for name, grant in projects.items():
        if not isinstance(name, str) or _PROJECT.fullmatch(name) is None:
            raise ValueError("Invalid capabilities project name")
        if not isinstance(grant, dict) or set(grant) != {"platform", "runtimes", "limits"}:
            raise ValueError("Invalid capabilities grant")
        platform = _validate_platform(grant["platform"])
        runtimes = grant["runtimes"]
        if (not isinstance(runtimes, list) or len(runtimes) > 3
                or any(not isinstance(runtime, str) or runtime not in _RUNTIMES for runtime in runtimes)
                or runtimes != sorted(set(runtimes))):
            raise ValueError("Invalid capabilities runtimes")
        limits = _validate_limits(grant["limits"])
        checked_projects[name] = {"platform": platform, "runtimes": list(runtimes), "limits": limits}
    if not isinstance(versions, dict) or set(versions) - _RUNTIMES:
        raise ValueError("Invalid capabilities tool versions")
    checked_versions: dict[str, str] = {}
    for name, version in versions.items():
        if not isinstance(version, str) or _VERSION.fullmatch(version) is None:
            raise ValueError("Invalid capabilities tool version")
        checked_versions[name] = version
    return {"schema": 1, "projects": checked_projects, "tool_versions": checked_versions}


def _validate_limits(value: object) -> dict[str, int]:
    if not isinstance(value, dict) or set(value) != set(DEFAULT_LIMITS):
        raise ValueError("Invalid build resource limits")
    checked: dict[str, int] = {}
    for name, (minimum, maximum) in _LIMIT_BOUNDS.items():
        item = value[name]
        if type(item) is not int or not minimum <= item <= maximum:
            raise ValueError("Invalid build resource limits")
        checked[name] = item
    if checked["scratch_bytes"] % (1024**2):
        raise ValueError("Invalid build resource limits")
    return checked


def _validate_timestamp(value: object, label: str, *, optional: bool = False) -> int | float | None:
    if optional and value is None:
        return None
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError(f"Invalid build {label}")
    return value


def _validate_public_artifact(value: object, record: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _PUBLIC_ARTIFACT_FIELDS:
        raise ValueError("Invalid public build artifact identity")
    sha256 = _digest(value["sha256"], "Artifact digest")
    descriptor_sha256 = _digest(value["descriptor_sha256"], "Artifact descriptor digest")
    request_sha256 = _digest(value["request_sha256"], "Build request digest")
    size = value["size"]
    if (type(size) is not int or not 1 <= size <= min(
            MAX_ARTIFACT_BUNDLE_BYTES,
            record["limits"]["artifact_bytes"] + MAX_DESCRIPTOR_BYTES + 1024,
    )):
        raise ValueError("Invalid public build artifact size")
    if (value["request_id"] != record["request_id"]
            or value["project"] != record["project"]
            or value["source"] != record["source"]
            or value["platform"] != record["platform"]):
        raise ValueError("Public artifact identity does not match its job")
    return {
        "sha256": sha256, "size": size, "descriptor_sha256": descriptor_sha256,
        "request_id": record["request_id"], "request_sha256": request_sha256,
        "project": record["project"], "source": record["source"],
        "platform": record["platform"],
    }


def _validate_job_record(value: object, request_id: str,
                         expected_request: dict[str, Any] | None = None) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _JOB_FIELDS:
        raise ValueError("Invalid public build job result")
    if _request_id(value["request_id"]) != request_id:
        raise ValueError("Build host returned a different request ID")
    sequence = value["sequence"]
    if type(sequence) is not int or not 1 <= sequence <= MAX_OPERATIONS:
        raise ValueError("Invalid build submission sequence")
    project = value["project"]
    if not isinstance(project, str) or _PROJECT.fullmatch(project) is None:
        raise ValueError("Invalid public build project")
    source = value["source"]
    if (not isinstance(source, dict)
            or set(source) != {"revision", "snapshot_sha256", "manifest_sha256"}):
        raise ValueError("Invalid public build source identity")
    revision = source["revision"]
    if not isinstance(revision, str) or re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", revision) is None:
        raise ValueError("Invalid public build source revision")
    source_identity = {
        "revision": revision,
        "snapshot_sha256": _digest(source["snapshot_sha256"], "Source snapshot digest"),
        "manifest_sha256": _digest(source["manifest_sha256"], "Manifest digest"),
    }
    platform = _validate_platform(value["platform"])
    limits = _validate_limits(value["limits"])
    probe = {
        "schema": 1,
        "request_id": request_id,
        "project": project,
        "source": source_identity,
        "platform": platform,
        "components": [{
            "name": "client-check", "type": "static", "artifacts": ["index.html"],
            "commands": [], "env": {},
        }],
        "limits": limits,
    }
    parse_build_request(probe)
    if expected_request is not None:
        for key, actual in (("project", project), ("source", source_identity),
                            ("platform", platform), ("limits", limits)):
            if actual != expected_request[key]:
                raise ValueError("Build host returned a different submitted identity")

    state = value["state"]
    states = {"queued", "running", "succeeded", "failed", "cancelled", "expired", "uncertain"}
    if not isinstance(state, str) or state not in states or type(value["cancel_requested"]) is not bool:
        raise ValueError("Invalid public build state")
    created_at = _validate_timestamp(value["created_at"], "creation time")
    expires_at = _validate_timestamp(value["expires_at"], "expiry")
    started_at = _validate_timestamp(value["started_at"], "start time", optional=True)
    finished_at = _validate_timestamp(value["finished_at"], "finish time", optional=True)
    if expires_at <= created_at:
        raise ValueError("Invalid public build expiry")
    if started_at is not None and (started_at < created_at or started_at >= expires_at):
        raise ValueError("Invalid public build start time")
    if finished_at is not None and (finished_at < created_at
                                    or (started_at is not None and finished_at < started_at)):
        raise ValueError("Invalid public build finish time")

    artifact_value = value["artifact"]
    reason = value["reason"]
    expected_reasons = {
        "failed": "build_failed", "cancelled": "cancelled",
        "expired": "queue_expired", "uncertain": "worker_interrupted",
    }
    if state == "queued":
        if (started_at is not None or finished_at is not None or artifact_value is not None
                or reason is not None or value["cancel_requested"]):
            raise ValueError("Invalid queued build metadata")
    elif state == "running":
        if started_at is None or finished_at is not None or artifact_value is not None or reason is not None:
            raise ValueError("Invalid running build metadata")
    else:
        if finished_at is None:
            raise ValueError("Terminal build result has no finish time")
        if state == "succeeded":
            if artifact_value is None or value["cancel_requested"] or reason is not None:
                raise ValueError("Invalid successful build metadata")
        elif artifact_value is not None or reason != expected_reasons[state]:
            raise ValueError("Invalid terminal build metadata")
        if state == "expired" and (
                started_at is not None or value["cancel_requested"]
                or finished_at < expires_at):
            raise ValueError("Invalid expired build metadata")
        if state == "cancelled" and not value["cancel_requested"]:
            raise ValueError("Invalid cancellation metadata")
        if state not in {"cancelled", "expired"} and started_at is None:
            raise ValueError("Terminal build result has no start time")
    if state == "succeeded":
        artifact = _validate_public_artifact(artifact_value, {
            "request_id": request_id, "project": project, "source": source_identity,
            "platform": platform, "limits": limits,
        })
    else:
        artifact = None
    if reason is not None and (not isinstance(reason, str) or reason != expected_reasons.get(state)):
        raise ValueError("Invalid public build reason")
    return {
        "request_id": request_id, "sequence": sequence, "project": project,
        "source": source_identity, "platform": platform, "limits": limits,
        "state": state, "cancel_requested": value["cancel_requested"],
        "created_at": created_at, "expires_at": expires_at, "started_at": started_at,
        "finished_at": finished_at, "artifact": artifact, "reason": reason,
    }


def _validate_log_result(value: object, offset: int,
                         requested_bytes: int, payload_bytes: int) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"offset", "next_offset", "waiting", "complete"}:
        raise ValueError("Invalid build log metadata")
    next_offset = value["next_offset"]
    waiting = value["waiting"]
    complete = value["complete"]
    if (type(value["offset"]) is not int or value["offset"] != offset
            or type(next_offset) is not int or next_offset != offset + payload_bytes
            or not 0 <= next_offset <= MAX_LOG_BYTES
            or type(waiting) is not bool or type(complete) is not bool
            or waiting and complete or payload_bytes > requested_bytes):
        raise ValueError("Invalid build log offsets or state")
    return {"offset": offset, "next_offset": next_offset,
            "waiting": waiting, "complete": complete}


def _validate_reply(line: bytes, *, action: str, fields: dict[str, Any],
                    payload_limit: int,
                    expected_artifact: tuple[str, int] | None = None) -> _Reply:
    try:
        if not line.endswith(b"\n") or b"\n" in line[:-1] or b"\r" in line:
            raise ValueError
        value = decode_document(line[:-1], maximum=MAX_RESPONSE_LINE_BYTES - 1)
        if not isinstance(value, dict):
            raise ValueError
        if value.get("schema") != 1 or type(value.get("schema")) is not int:
            raise ValueError
        if value.get("ok") is False:
            if set(value) != {"schema", "ok", "error", "payload_bytes"}:
                raise ValueError
            error = value["error"]
            payload_bytes = value["payload_bytes"]
            if error not in {"rejected", "unavailable"} or type(payload_bytes) is not int or payload_bytes != 0:
                raise ValueError
            return _Reply(None, 0, error)
        if value.get("ok") is not True or set(value) != {
            "schema", "ok", "result", "payload_bytes",
        }:
            raise ValueError
        payload_bytes = value["payload_bytes"]
        if type(payload_bytes) is not int or not 0 <= payload_bytes <= payload_limit:
            raise ValueError
        result = value["result"]
        if action == "capabilities":
            result = _validate_capabilities(result)
            if payload_bytes != 0:
                raise ValueError
        elif action == "submit":
            result = _validate_job_record(result, fields["request"]["request_id"], fields["request"])
            if payload_bytes != 0:
                raise ValueError
        elif action in {"status", "cancel"}:
            result = _validate_job_record(result, fields["request_id"])
            if payload_bytes != 0:
                raise ValueError
        elif action == "log":
            result = _validate_log_result(
                result, fields["offset"], fields["max_bytes"], payload_bytes,
            )
        elif action == "artifact":
            if not isinstance(result, dict) or set(result) != {"sha256", "size"}:
                raise ValueError
            digest = _digest(result["sha256"], "Artifact digest")
            size = result["size"]
            if (expected_artifact is None or type(size) is not int
                    or not 1 <= size <= MAX_ARTIFACT_BUNDLE_BYTES
                    or digest != expected_artifact[0] or size != expected_artifact[1]
                    or payload_bytes != size):
                raise ValueError
            result = {"sha256": digest, "size": size}
        else:
            raise ValueError
        return _Reply(result, payload_bytes, None)
    except (ValueError, TypeError, KeyError, RecursionError, UnicodeError, OverflowError):
        raise BuildHostProtocolError("Build host returned an invalid response", transport_started=True) from None


def _capture_stderr(capture: bytearray, block: bytes) -> None:
    """Retain only the first configured stderr bytes while callers drain all."""
    room = MAX_STDERR_CAPTURE_BYTES - len(capture)
    if room > 0:
        capture.extend(block[:room])


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    """Terminate only the local SSH process group and bound cleanup waits."""
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except OSError:
            try:
                process.terminate()
            except OSError:
                pass
        try:
            process.wait(timeout=0.25)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except OSError:
                try:
                    process.kill()
                except OSError:
                    pass
            try:
                process.wait(timeout=0.25)
            except subprocess.TimeoutExpired:
                pass


class BuildHostClient:
    """Explicitly configured, finite-timeout client for one build host.

    ``identity_file`` and ``known_hosts_file`` are opened once, without
    following their final symlink, and passed to the isolated SSH process by
    descriptor. Each method starts one transport; no operation is retried.
    """

    # Internal extension points shared with the typed target transport. Public
    # callers cannot select a remote command or a response parser per request.
    _remote_command = REMOTE_COMMAND
    _header_limit = MAX_HEADER_LINE_BYTES
    _parse_reply = staticmethod(_validate_reply)

    def __init__(
        self,
        *,
        host: str,
        username: str,
        port: int,
        identity_file: str | os.PathLike[str],
        known_hosts_file: str | os.PathLike[str],
        timeout: int | float,
    ) -> None:
        if not validate_host(host):
            raise ValueError("Invalid build host name or address")
        if not validate_username(username):
            raise ValueError("Invalid SSH username")
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("SSH port must be an integer from 1 to 65535")
        if (type(timeout) not in (int, float) or not math.isfinite(timeout)
                or not 0 < timeout <= MAX_TIMEOUT_SECONDS):
            raise ValueError(f"SSH timeout must be finite and between 0 and {MAX_TIMEOUT_SECONDS} seconds")
        self.host = host
        self.username = username
        self.port = port
        self.timeout = float(timeout)
        self.identity_file = _absolute_path(identity_file, "SSH identity file")
        self.known_hosts_file = _absolute_path(known_hosts_file, "SSH known-hosts file")
        self._identity_fd, self._identity_identity = _open_trusted_file(
            self.identity_file, "SSH identity file", private=True,
        )
        try:
            self._known_hosts_fd, self._known_hosts_identity = _open_trusted_file(
                self.known_hosts_file, "SSH known-hosts file", private=False,
            )
        except BaseException:
            os.close(self._identity_fd)
            raise
        self._closed = False

    def close(self) -> None:
        """Release the pinned local key and host-trust descriptors."""
        if self._closed:
            return
        self._closed = True
        for descriptor in (self._identity_fd, self._known_hosts_fd):
            try:
                os.close(descriptor)
            except OSError:
                pass

    def __enter__(self) -> BuildHostClient:
        if self._closed:
            raise ValueError("Build host client is closed")
        return self

    def __exit__(self, _kind: object, _value: object, _traceback: object) -> None:
        self.close()

    def capabilities(self) -> dict[str, Any]:
        """Return the authenticated principal's bounded project grants."""
        result, _payload = self._call("capabilities", {})
        return result

    def submit(self, request: object, source_path: str | os.PathLike[str]) -> dict[str, Any]:
        """Stream one pinned source snapshot for the caller-supplied request ID.

        Once SSH starts, every transport or server error is reported as
        :class:`BuildSubmissionUnconfirmed`. Query ``status()`` and reconcile
        with the same request ID and inputs; this method never chooses an ID or
        retries admission.
        """
        validated = parse_build_request(request)
        deadline = time.monotonic() + self.timeout
        source = self._open_source(
            source_path, validated["source"]["snapshot_sha256"], deadline=deadline,
        )
        try:
            try:
                result, _payload = self._call(
                    "submit", {"request": validated, "source_size": source.size},
                    source=source, deadline=deadline,
                )
                return result
            except BuildClientError as exc:
                if exc.transport_started:
                    raise BuildSubmissionUnconfirmed(validated["request_id"], exc) from exc
                raise
        finally:
            os.close(source.descriptor)

    def status(self, request_id: str) -> dict[str, Any]:
        """Return the strict public job receipt for this request ID."""
        request_id = _request_id(request_id)
        result, _payload = self._call("status", {"request_id": request_id})
        return result

    def cancel(self, request_id: str) -> dict[str, Any]:
        """Request durable cancellation of a queued or running build."""
        request_id = _request_id(request_id)
        result, _payload = self._call("cancel", {"request_id": request_id})
        return result

    def log(self, request_id: str, offset: int, max_bytes: int) -> BuildLogChunk:
        """Read one raw log range, capped at 64 KiB by the service contract."""
        request_id = _request_id(request_id)
        if type(offset) is not int or not 0 <= offset <= MAX_LOG_BYTES:
            raise ValueError("Log offset must be from zero through the 16 MiB limit")
        if type(max_bytes) is not int or not 0 <= max_bytes <= MAX_LOG_CHUNK_BYTES:
            raise ValueError("Log chunk size must be from zero through 64 KiB")
        result, data = self._call(
            "log", {"request_id": request_id, "offset": offset, "max_bytes": max_bytes},
            payload_limit=max_bytes,
        )
        return BuildLogChunk(
            data=data, offset=result["offset"], next_offset=result["next_offset"],
            waiting=result["waiting"], complete=result["complete"],
        )

    def download_artifact(
        self,
        request_id: str,
        destination: str | os.PathLike[str],
        expected_sha256: str,
        expected_size: int,
    ) -> str:
        """Stream, verify and no-replace publish a private artifact file."""
        request_id = _request_id(request_id)
        expected_sha256 = _digest(expected_sha256, "Expected artifact digest")
        if (type(expected_size) is not int
                or not 1 <= expected_size <= MAX_ARTIFACT_BUNDLE_BYTES):
            raise ValueError("Expected artifact size is outside the supported limit")
        target_path = _absolute_path(destination, "Artifact destination")
        parent_path, leaf = os.path.split(target_path)
        if not leaf or leaf in {".", ".."}:
            raise ValueError("Artifact destination must name a file")
        deadline = time.monotonic() + self.timeout
        parent_fd = _open_private_directory(parent_path)
        temporary_name: str | None = None
        temporary_fd = -1
        digest = hashlib.sha256()
        received = 0
        try:
            try:
                os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise FileExistsError("Artifact destination already exists")
            temporary_name, temporary_fd = _private_temporary(parent_fd)

            def write_payload(block: bytes) -> None:
                nonlocal received
                view = memoryview(block)
                while view:
                    written = os.write(temporary_fd, view)
                    if written <= 0:
                        raise OSError("Artifact temporary write made no progress")
                    digest.update(view[:written])
                    received += written
                    view = view[written:]

            self._call(
                "artifact",
                {"request_id": request_id},
                payload_limit=expected_size,
                payload_sink=write_payload,
                deadline=deadline,
                expected_artifact=(expected_sha256, expected_size),
            )
            if time.monotonic() >= deadline:
                raise BuildHostTransportError("Artifact transfer exceeded its finite timeout",
                                              transport_started=True)
            if received != expected_size or digest.hexdigest() != expected_sha256:
                raise BuildHostProtocolError("Downloaded artifact did not match its expected identity",
                                             transport_started=True)
            info = os.fstat(temporary_fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size != expected_size:
                raise BuildHostProtocolError("Downloaded artifact has an invalid temporary file",
                                             transport_started=True)
            os.fchmod(temporary_fd, 0o600)
            os.fsync(temporary_fd)
            if time.monotonic() >= deadline:
                raise BuildHostTransportError("Artifact publication exceeded its finite timeout",
                                              transport_started=True)
            os.close(temporary_fd)
            temporary_fd = -1
            _rename_noreplace(parent_fd, temporary_name, leaf)
            temporary_name = None
            try:
                os.fsync(parent_fd)
            except (OSError, KeyboardInterrupt) as exc:
                raise ArtifactPublicationUncertain(
                    target_path, expected_sha256, expected_size,
                ) from exc
            return target_path
        finally:
            if temporary_fd >= 0:
                os.close(temporary_fd)
            if temporary_name is not None:
                try:
                    os.unlink(temporary_name, dir_fd=parent_fd)
                except FileNotFoundError:
                    pass
            os.close(parent_fd)

    def _open_source(self, source_path: str | os.PathLike[str], expected_sha256: str,
                     *, deadline: float, maximum: int | None = None,
                     expected_size: int | None = None) -> _SourceFile:
        path = _absolute_path(source_path, "Build source snapshot")
        descriptor = os.open(path, _READ)
        try:
            before = os.fstat(descriptor)
            limit = MAX_SNAPSHOT_BYTES if maximum is None else maximum
            if not stat.S_ISREG(before.st_mode) or not 1 <= before.st_size <= limit:
                raise ValueError("Transfer source must be a non-empty regular file within its size limit")
            if expected_size is not None and before.st_size != expected_size:
                raise ValueError("Transfer source size does not match the declared size")
            named_before = os.stat(path, follow_symlinks=False)
            identity = _file_identity(before)
            if _file_identity(named_before) != identity:
                raise ValueError("Build source changed while being opened")
            calculated = hashlib.sha256()
            offset = 0
            while offset < before.st_size:
                if time.monotonic() >= deadline:
                    raise BuildHostTransportError("Build source verification exceeded the SSH timeout")
                block = os.read(descriptor, min(MAX_TRANSFER_CHUNK_BYTES, before.st_size - offset))
                if not block:
                    raise ValueError("Build source ended before its declared size")
                calculated.update(block)
                offset += len(block)
            if os.read(descriptor, 1):
                raise ValueError("Transfer source exceeds its declared size")
            if time.monotonic() >= deadline:
                raise BuildHostTransportError("Build source verification exceeded the SSH timeout")
            after = os.fstat(descriptor)
            named_after = os.stat(path, follow_symlinks=False)
            if (_file_identity(after) != identity or _file_identity(named_after) != identity
                    or calculated.hexdigest() != expected_sha256):
                raise ValueError("Build source identity or SHA-256 does not match the request")
            os.lseek(descriptor, 0, os.SEEK_SET)
            return _SourceFile(path, descriptor, before.st_size, expected_sha256, identity)
        except BaseException:
            os.close(descriptor)
            raise

    def _ssh_arguments(self, identity_fd: int, known_hosts_fd: int) -> list[str]:
        controller_pid = os.getpid()
        identity = f"/proc/{controller_pid}/fd/{identity_fd}"
        known_hosts = f"/proc/{controller_pid}/fd/{known_hosts_fd}"
        connect_timeout = max(1, min(60, math.ceil(self.timeout)))
        return [
            "/usr/bin/ssh", "-F", "/dev/null", "-p", str(self.port),
            "-o", "IdentityFile=none", "-i", identity,
            "-o", f"UserKnownHostsFile={known_hosts}",
            "-o", "GlobalKnownHostsFile=/dev/null",
            "-o", "StrictHostKeyChecking=yes", "-o", "UpdateHostKeys=no",
            "-o", "VerifyHostKeyDNS=no", "-o", "CanonicalizeHostname=no",
            "-o", "BatchMode=yes", "-o", "PasswordAuthentication=no",
            "-o", "KbdInteractiveAuthentication=no", "-o", "PreferredAuthentications=publickey",
            "-o", "IdentitiesOnly=yes", "-o", "IdentityAgent=none",
            "-o", "CertificateFile=none", "-o", "ForwardAgent=no",
            "-o", "RequestTTY=no", "-o", "ClearAllForwardings=yes",
            "-o", "ProxyCommand=none", "-o", "ProxyJump=none",
            "-o", "ControlMaster=no", "-o", "ControlPath=none", "-o", "ControlPersist=no",
            "-o", "PermitLocalCommand=no", "-o", "ConnectionAttempts=1",
            "-o", f"ConnectTimeout={connect_timeout}",
            "-o", "LogLevel=ERROR", "-T", "-a", "--",
            f"{self.username}@{self.host}", self._remote_command,
        ]

    def _call(
        self,
        action: str,
        fields: dict[str, Any],
        *,
        source: _SourceFile | None = None,
        payload_limit: int = 0,
        payload_sink: Callable[[bytes], None] | None = None,
        deadline: float | None = None,
        expected_artifact: tuple[str, int] | None = None,
        on_start: Callable[[], None] | None = None,
    ) -> tuple[dict[str, Any], bytes]:
        if self._closed:
            raise ValueError("Build host client is closed")
        self._verify_credentials()
        document = {"schema": 1, "action": action, **fields}
        header = encode_document(document, maximum=self._header_limit - 1) + b"\n"
        if len(header) > self._header_limit:
            raise ValueError("Build request header exceeds the service limit")

        if deadline is None:
            deadline = time.monotonic() + self.timeout
        process: subprocess.Popen[bytes] | None = None
        selector: selectors.BaseSelector | None = None
        started = False
        identity_fd = -1
        known_hosts_fd = -1
        stderr_capture = bytearray()
        stdout_header = bytearray()
        collected_payload = bytearray()
        reply: _Reply | None = None
        received_payload = 0
        stdout_eof = False
        stderr_eof = False
        stdin_open = False
        header_offset = 0
        source_bytes_read = 0
        source_digest = hashlib.sha256()
        pending = b""
        pending_offset = 0
        input_error: Exception | None = None

        def close_stdin() -> None:
            nonlocal stdin_open
            if process is None or not stdin_open:
                return
            try:
                selector.unregister(process.stdin)
            except (KeyError, OSError, ValueError):
                pass
            try:
                process.stdin.close()
            except OSError:
                pass
            stdin_open = False

        def check_source_identity() -> None:
            if source is None:
                return
            current = _file_identity(os.fstat(source.descriptor))
            named = _file_identity(os.stat(source.path, follow_symlinks=False))
            if current != source.identity or named != source.identity:
                raise OSError("Build source changed while it was being sent")
            if source_digest.hexdigest() != source.digest:
                raise OSError("Build source digest changed while it was being sent")

        def consume_payload(data: bytes) -> None:
            nonlocal received_payload
            if reply is None:
                raise BuildHostProtocolError("Build host sent data before its response line",
                                             transport_started=True)
            remaining = reply.payload_bytes - received_payload
            if len(data) > remaining:
                raise BuildHostProtocolError("Build host sent excess response payload",
                                             transport_started=True)
            if not data:
                return
            if payload_sink is not None:
                payload_sink(data)
            elif action == "log":
                collected_payload.extend(data)
            received_payload += len(data)

        def consume_stdout(data: bytes) -> None:
            nonlocal reply
            if reply is None:
                boundary = data.find(b"\n")
                if boundary < 0:
                    stdout_header.extend(data)
                    if len(stdout_header) > MAX_RESPONSE_LINE_BYTES - 1:
                        raise BuildHostProtocolError("Build host response line exceeds 64 KiB",
                                                     transport_started=True)
                    return
                line_size = len(stdout_header) + boundary + 1
                if line_size > MAX_RESPONSE_LINE_BYTES:
                    raise BuildHostProtocolError("Build host response line exceeds 64 KiB",
                                                 transport_started=True)
                line = bytes(stdout_header) + data[:boundary + 1]
                if not line.endswith(b"\n"):
                    raise BuildHostProtocolError("Build host returned a malformed response line",
                                                 transport_started=True)
                reply = self._parse_reply(line, action=action, fields=fields,
                                         payload_limit=payload_limit,
                                         expected_artifact=expected_artifact)
                if reply.error is not None:
                    close_stdin()
                rest = data[boundary + 1:]
                if rest:
                    consume_payload(rest)
                return
            consume_payload(data)

        def fill_input() -> bytes | None:
            nonlocal header_offset, source_bytes_read, source_digest, pending
            if header_offset < len(header):
                block = header[header_offset:header_offset + MAX_TRANSFER_CHUNK_BYTES]
                header_offset += len(block)
                return block
            if source is None:
                return None
            if source_bytes_read < source.size:
                block = os.read(source.descriptor,
                                min(MAX_TRANSFER_CHUNK_BYTES, source.size - source_bytes_read))
                if not block:
                    raise OSError("Build source ended during transfer")
                source_digest.update(block)
                source_bytes_read += len(block)
                return block
            if os.read(source.descriptor, 1):
                raise OSError("Transfer source exceeds its declared size")
            check_source_identity()
            return None

        try:
            if time.monotonic() >= deadline:
                raise TimeoutError
            identity_fd = os.dup(self._identity_fd)
            known_hosts_fd = os.dup(self._known_hosts_fd)
            _verify_credential(identity_fd, self._identity_identity, "SSH identity file")
            _verify_credential(known_hosts_fd, self._known_hosts_identity, "SSH known-hosts file")
            if source is not None:
                current_source = _file_identity(os.fstat(source.descriptor))
                named_source = _file_identity(os.stat(source.path, follow_symlinks=False))
                if current_source != source.identity or named_source != source.identity:
                    raise ValueError("Build source changed before SSH started")
            process = subprocess.Popen(
                self._ssh_arguments(identity_fd, known_hosts_fd),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, bufsize=0, close_fds=True,
                start_new_session=True, cwd="/", umask=0o077,
                env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C", "LC_ALL": "C"},
            )
            started = True
            if on_start is not None:
                on_start()
            if process.stdin is None or process.stdout is None or process.stderr is None:
                raise OSError("SSH process did not provide bounded pipes")
            for stream in (process.stdin, process.stdout, process.stderr):
                os.set_blocking(stream.fileno(), False)
            selector = selectors.DefaultSelector()
            selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
            selector.register(process.stdout, selectors.EVENT_READ, "stdout")
            selector.register(process.stderr, selectors.EVENT_READ, "stderr")
            stdin_open = True

            while True:
                if time.monotonic() >= deadline:
                    raise TimeoutError
                if (stdout_eof and stderr_eof and not stdin_open
                        and process.poll() is not None):
                    break
                remaining_time = max(0.0, deadline - time.monotonic())
                events = selector.select(min(0.2, remaining_time))
                for key, _mask in events:
                    kind = key.data
                    if kind == "stdin":
                        if not stdin_open:
                            continue
                        try:
                            if pending_offset >= len(pending):
                                pending = fill_input() or b""
                                pending_offset = 0
                                if not pending:
                                    close_stdin()
                                    continue
                            written = os.write(
                                process.stdin.fileno(),
                                memoryview(pending)[pending_offset:pending_offset + MAX_TRANSFER_CHUNK_BYTES],
                            )
                            if written <= 0:
                                raise OSError("SSH input pipe made no progress")
                            pending_offset += written
                            if pending_offset >= len(pending):
                                pending = b""
                                pending_offset = 0
                        except (BrokenPipeError, OSError) as exc:
                            input_error = exc
                            close_stdin()
                    elif kind == "stdout":
                        block = os.read(process.stdout.fileno(), MAX_TRANSFER_CHUNK_BYTES)
                        if block:
                            consume_stdout(block)
                        else:
                            selector.unregister(process.stdout)
                            stdout_eof = True
                            if reply is None:
                                raise BuildHostProtocolError("Build host closed without a response line",
                                                             transport_started=True)
                            if received_payload != reply.payload_bytes:
                                raise BuildHostProtocolError("Build host truncated its response payload",
                                                             transport_started=True)
                    else:
                        block = os.read(process.stderr.fileno(), MAX_TRANSFER_CHUNK_BYTES)
                        if block:
                            _capture_stderr(stderr_capture, block)
                        else:
                            selector.unregister(process.stderr)
                            stderr_eof = True

            return_code = process.wait(timeout=max(0.01, deadline - time.monotonic()))
            if return_code != 0:
                raise BuildHostTransportError("SSH exited unsuccessfully", transport_started=True)
            if reply is None or received_payload != reply.payload_bytes:
                raise BuildHostProtocolError("Build host response was incomplete", transport_started=True)
            if reply.error is not None:
                raise BuildHostRemoteError(reply.error)
            if input_error is not None:
                raise BuildHostTransportError("SSH request input did not complete",
                                              transport_started=True) from None
            if source is not None and (source_bytes_read != source.size
                                       or source_digest.hexdigest() != source.digest):
                raise BuildHostTransportError("Build source transfer was incomplete",
                                              transport_started=True)
            assert reply.result is not None
            return reply.result, bytes(collected_payload)
        except BuildClientError:
            raise
        except KeyboardInterrupt as exc:
            if started or process is not None:
                raise BuildHostTransportError("SSH operation was interrupted",
                                              transport_started=True) from exc
            raise
        except TimeoutError:
            raise BuildHostTransportError("SSH operation exceeded its finite timeout",
                                          transport_started=started) from None
        except Exception as exc:
            raise BuildHostTransportError("SSH operation did not complete",
                                          transport_started=started) from exc
        finally:
            cleanup_error: BaseException | None = None

            def cleanup(callback: Callable[[], object], *, ignore_oserror: bool = False) -> None:
                nonlocal cleanup_error
                try:
                    callback()
                except BaseException as exc:
                    if not (ignore_oserror and isinstance(exc, OSError)) and cleanup_error is None:
                        cleanup_error = exc

            # An interrupt in one cleanup step must not skip the remaining
            # pipes or the credential duplicates that SSH may still be using.
            if selector is not None:
                cleanup(selector.close, ignore_oserror=True)
            if process is not None:
                def stop_running_process() -> None:
                    if process.poll() is None:
                        _stop_process(process)

                cleanup(stop_running_process)
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream is not None:
                        cleanup(stream.close, ignore_oserror=True)
            for descriptor in (identity_fd, known_hosts_fd):
                if descriptor >= 0:
                    cleanup(lambda: os.close(descriptor), ignore_oserror=True)
            if isinstance(cleanup_error, KeyboardInterrupt) and started:
                raise BuildHostTransportError("SSH cleanup was interrupted",
                                              transport_started=True) from cleanup_error
            if cleanup_error is not None:
                raise cleanup_error

    def _verify_credentials(self) -> None:
        _verify_credential(self._identity_fd, self._identity_identity, "SSH identity file")
        _verify_credential(self._known_hosts_fd, self._known_hosts_identity,
                           "SSH known-hosts file")


__all__ = [
    "ArtifactPublicationUncertain", "BuildClientError", "BuildHostClient",
    "BuildHostProtocolError",
    "BuildHostRemoteError", "BuildHostTransportError", "BuildLogChunk",
    "BuildSubmissionUnconfirmed", "MAX_ARTIFACT_BUNDLE_BYTES", "MAX_LOG_BYTES",
    "MAX_TIMEOUT_SECONDS", "REMOTE_COMMAND",
]
