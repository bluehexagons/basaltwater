"""Durable, single-host admission and worker leases for on-demand builds.

This module owns queued build identity and one host-wide worker slot. It does
not authenticate callers, authorize projects, check source readiness, supervise
commands, or execute builds. Callers pass an authenticated owner and keep the
worker lease until every supervised process and cleanup action has stopped.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
import copy
import errno
import fcntl
import hashlib
import math
import os
import re
import secrets
import stat
import threading
import time
from typing import Any

from lib.build_protocol import (
    MAX_ARTIFACT_BYTES,
    MAX_DESCRIPTOR_BYTES,
    decode_document,
    encode_document,
    parse_build_request,
    request_digest,
    validate_artifact_for_request,
)
from lib.setup_directories import directory_fd
from lib.validation import validate_filesystem_path, validate_no_control_characters


MAX_OPERATIONS = 4096
MAX_STORE_BYTES = 16 * 1024 * 1024
DEFAULT_QUEUE_TTL_SECONDS = 900
DEFAULT_MAX_QUEUE_LENGTH = 8
MAX_QUEUE_TTL_SECONDS = 7 * 24 * 60 * 60

_ID = re.compile(r"[0-9a-f]{32}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_OWNER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}")
_STATES = frozenset({"queued", "running", "succeeded", "failed", "cancelled", "expired", "uncertain"})
_REASONS = {
    "failed": "build_failed",
    "cancelled": "cancelled",
    "expired": "queue_expired",
    "uncertain": "worker_interrupted",
}
_RECORD_FIELDS = frozenset({
    "request_id", "owner", "request_sha256", "request", "sequence",
    "created_at", "expires_at", "state", "cancel_requested", "started_at",
    "finished_at", "lease_id", "artifact", "reason",
})
_ARTIFACT_FIELDS = frozenset({"sha256", "size", "descriptor"})
_ARTIFACT_IDENTITY_FIELDS = frozenset({
    "sha256", "size", "descriptor_sha256", "request_id", "request_sha256",
    "project", "source", "platform",
})
_ARTIFACT_SIZE_LIMIT = MAX_ARTIFACT_BYTES + MAX_DESCRIPTOR_BYTES + 1024
_INITIALIZED = b"basaltwater-build-jobs-v1\n"


def _identifier(value: object, label: str, pattern: re.Pattern[str] = _ID) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ValueError(f"{label} must be lowercase hexadecimal")
    return value


def _owner(value: object) -> str:
    if not isinstance(value, str) or _OWNER.fullmatch(value) is None:
        raise ValueError("Owner must be a safe label of 1–128 characters")
    validate_no_control_characters(value, "Owner")
    return value


def _timestamp(value: object, label: str) -> float:
    if type(value) not in (int, float) or value < 0:
        raise ValueError(f"{label} must be a finite non-negative timestamp")
    try:
        converted = float(value)
    except OverflowError as exc:
        raise ValueError(f"{label} must be a finite non-negative timestamp") from exc
    if not math.isfinite(converted):
        raise ValueError(f"{label} must be a finite non-negative timestamp")
    return converted


def _integer(value: object, minimum: int, maximum: int, label: str) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{label} is outside supported limits")
    return value


def _empty_state() -> dict[str, Any]:
    return {
        "schema": 1,
        "draining": False,
        "last_sequence": 0,
        "last_time": 0.0,
        "jobs": {},
    }


def _artifact_identity(value: object, request: dict[str, Any]) -> dict[str, Any]:
    """Verify an artifact descriptor and retain only its bounded identity."""
    if not isinstance(value, dict) or set(value) != _ARTIFACT_FIELDS:
        raise ValueError("A successful build requires digest, size, and descriptor")
    digest = _identifier(value["sha256"], "Artifact digest", _SHA256)
    size = _integer(value["size"], 1, _ARTIFACT_SIZE_LIMIT, "Artifact bundle size")
    descriptor = validate_artifact_for_request(value["descriptor"], request)
    descriptor_bytes = encode_document(descriptor, maximum=MAX_DESCRIPTOR_BYTES)
    return {
        "sha256": digest,
        "size": size,
        "descriptor_sha256": hashlib.sha256(descriptor_bytes).hexdigest(),
        "request_id": request["request_id"],
        "request_sha256": request_digest(request),
        "project": request["project"],
        "source": copy.deepcopy(request["source"]),
        "platform": copy.deepcopy(request["platform"]),
    }


def _validate_artifact_identity(value: object, request: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _ARTIFACT_IDENTITY_FIELDS:
        raise ValueError("Build store contains an invalid artifact identity")
    result = {
        "sha256": _identifier(value["sha256"], "Artifact digest", _SHA256),
        "size": _integer(value["size"], 1, _ARTIFACT_SIZE_LIMIT, "Artifact bundle size"),
        "descriptor_sha256": _identifier(value["descriptor_sha256"], "Descriptor digest", _SHA256),
        "request_id": _identifier(value["request_id"], "Request ID"),
        "request_sha256": _identifier(value["request_sha256"], "Request digest", _SHA256),
        "project": value["project"],
        "source": value["source"],
        "platform": value["platform"],
    }
    expected = {
        "request_id": request["request_id"],
        "request_sha256": request_digest(request),
        "project": request["project"],
        "source": request["source"],
        "platform": request["platform"],
    }
    if any(result[key] != expected[key] for key in expected):
        raise ValueError("Build store artifact identity does not match its request")
    return result


def _validate_record(key: object, value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _RECORD_FIELDS:
        raise ValueError("Build store contains an invalid job record")
    record = copy.deepcopy(value)
    record["request_id"] = _identifier(key, "Request ID")
    if record["request_id"] != value["request_id"]:
        raise ValueError("Build job key does not match its request ID")
    record["owner"] = _owner(record["owner"])
    request = parse_build_request(record["request"])
    if request["request_id"] != record["request_id"]:
        raise ValueError("Build job request ID does not match its record")
    record["request"] = request
    record["request_sha256"] = _identifier(record["request_sha256"], "Request digest", _SHA256)
    if record["request_sha256"] != request_digest(request):
        raise ValueError("Build job request digest does not match its request")
    record["sequence"] = _integer(record["sequence"], 1, MAX_OPERATIONS, "Submission sequence")
    record["created_at"] = _timestamp(record["created_at"], "Build creation time")
    record["expires_at"] = _timestamp(record["expires_at"], "Build expiry")
    if record["expires_at"] <= record["created_at"]:
        raise ValueError("Build expiry must follow its creation time")
    state = record["state"]
    if not isinstance(state, str) or state not in _STATES:
        raise ValueError("Build job state is invalid")
    if type(record["cancel_requested"]) is not bool:
        raise ValueError("Build cancellation flag is invalid")
    started = record["started_at"]
    finished = record["finished_at"]
    if started is not None:
        record["started_at"] = _timestamp(started, "Build start time")
        if record["started_at"] < record["created_at"]:
            raise ValueError("Build start time precedes submission")
        if record["started_at"] >= record["expires_at"]:
            raise ValueError("Build start time follows queue expiry")
    if finished is not None:
        record["finished_at"] = _timestamp(finished, "Build finish time")
        if record["finished_at"] < record["created_at"]:
            raise ValueError("Build finish time precedes submission")
    if (record["started_at"] is not None and record["finished_at"] is not None
            and record["finished_at"] < record["started_at"]):
        raise ValueError("Build finish time precedes its start")
    lease_id = record["lease_id"]
    if lease_id is not None:
        record["lease_id"] = _identifier(lease_id, "Worker lease ID")
    if state == "queued":
        if (started is not None or finished is not None or lease_id is not None
                or record["artifact"] is not None or record["reason"] is not None
                or record["cancel_requested"]):
            raise ValueError("Queued build job contains started or terminal metadata")
    elif state == "running":
        if (started is None or finished is not None or lease_id is None
                or record["artifact"] is not None or record["reason"] is not None):
            raise ValueError("Running build job contains invalid lease or terminal metadata")
    else:
        if finished is None or lease_id is not None:
            raise ValueError("Terminal build job is missing its finish time or retains a lease")
        expected_reason = _REASONS.get(state)
        if record["reason"] != expected_reason:
            raise ValueError("Terminal build job reason is invalid")
        if state == "succeeded":
            if started is None or record["artifact"] is None or record["cancel_requested"]:
                raise ValueError("Successful build job has invalid artifact or cancellation metadata")
            record["artifact"] = _validate_artifact_identity(record["artifact"], request)
        elif record["artifact"] is not None:
            raise ValueError("Unsuccessful build job cannot retain an artifact")
        elif state == "expired":
            if started is not None or record["cancel_requested"]:
                raise ValueError("Expired build job has invalid start or cancellation metadata")
        elif started is None:
            if state != "cancelled" or not record["cancel_requested"]:
                raise ValueError("Terminal build job is missing its start time")
        elif state == "cancelled" and not record["cancel_requested"]:
            raise ValueError("Cancelled running build was not cancellation-requested")
    return record


def _validate_state(value: object) -> dict[str, Any]:
    if (not isinstance(value, dict)
            or set(value) != {"schema", "draining", "last_sequence", "last_time", "jobs"}
            or type(value["schema"]) is not int or value["schema"] != 1):
        raise ValueError("Build store has an unsupported or invalid schema")
    if type(value["draining"]) is not bool:
        raise ValueError("Build store draining state is invalid")
    last_sequence = _integer(value["last_sequence"], 0, MAX_OPERATIONS, "Last submission sequence")
    last_time = _timestamp(value["last_time"], "Build store clock")
    jobs = value["jobs"]
    if not isinstance(jobs, dict) or len(jobs) > MAX_OPERATIONS:
        raise ValueError("Build store exceeds its operation limit")
    records = {key: _validate_record(key, record) for key, record in jobs.items()}
    sequences = [record["sequence"] for record in records.values()]
    if len(set(sequences)) != len(sequences) or set(sequences) != set(range(1, last_sequence + 1)):
        raise ValueError("Build store submission sequence is incomplete or duplicated")
    if last_sequence != len(records):
        raise ValueError("Build store sequence does not match retained operations")
    if any(record["created_at"] > last_time for record in records.values()):
        raise ValueError("Build store creation time is ahead of its durable clock")
    if any(record["expires_at"] <= last_time for record in records.values() if record["state"] == "queued"):
        raise ValueError("Build store contains queued work past its durable clock")
    if any(record["finished_at"] < record["expires_at"]
           for record in records.values() if record["state"] == "expired"):
        raise ValueError("Expired build job finished before its queue expiry")
    if any(record["started_at"] is not None and record["started_at"] > last_time
           for record in records.values()):
        raise ValueError("Build store start time is ahead of its durable clock")
    if any(record["finished_at"] is not None and record["finished_at"] > last_time
           for record in records.values()):
        raise ValueError("Build store finish time is ahead of its durable clock")
    if sum(record["state"] == "running" for record in records.values()) > 1:
        raise ValueError("Build store contains multiple active worker claims")
    return {
        "schema": 1,
        "draining": value["draining"],
        "last_sequence": last_sequence,
        "last_time": last_time,
        "jobs": records,
    }


class BuildJobStore:
    """A bounded private queue with a single exclusive worker lease.

    ``path`` names a JSON state file inside an existing private directory owned
    by the effective UID. The stable sibling locks are ``<name>.lock`` for
    short state operations and ``<name>.worker.lock`` for a worker lifetime.
    """

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        clock: Callable[[], float] | None = None,
        queue_ttl_seconds: int = DEFAULT_QUEUE_TTL_SECONDS,
        max_queue_length: int = DEFAULT_MAX_QUEUE_LENGTH,
    ) -> None:
        raw_path = os.fspath(path)
        if not isinstance(raw_path, str):
            raise ValueError("Build state path must be text")
        validate_filesystem_path(raw_path, must_exist=False)
        self.path = os.path.abspath(raw_path)
        self.parent = os.path.dirname(self.path)
        self.name = os.path.basename(self.path)
        if not self.name or self.name in {".", ".."}:
            raise ValueError("Build state path must name a file")
        self.lock_name = self.name + ".lock"
        self.worker_lock_name = self.name + ".worker.lock"
        if clock is not None and not callable(clock):
            raise ValueError("Build store clock must be callable")
        if type(queue_ttl_seconds) is not int or not 1 <= queue_ttl_seconds <= MAX_QUEUE_TTL_SECONDS:
            raise ValueError("Build queue TTL is outside supported limits")
        if type(max_queue_length) is not int or not 1 <= max_queue_length <= MAX_OPERATIONS:
            raise ValueError("Build queue length is outside supported limits")
        self._clock = time.time if clock is None else clock
        self.queue_ttl_seconds = queue_ttl_seconds
        self.max_queue_length = max_queue_length
        self._lease_authority = object()
        self._check_parent()

    def _check_parent(self) -> None:
        with directory_fd(self.parent) as descriptor:
            self._check_parent_fd(descriptor)

    @staticmethod
    def _check_parent_fd(descriptor: int) -> None:
        info = os.fstat(descriptor)
        mode = stat.S_IMODE(info.st_mode)
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
                or mode & 0o077 or mode & 0o700 != 0o700):
            raise ValueError("Build store parent must be private and owned by the effective UID")

    @staticmethod
    def _check_private_file(descriptor: int, label: str) -> os.stat_result:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600):
            raise ValueError(f"Build store {label} must be an owned regular 0600 file without links")
        return info

    @staticmethod
    def _check_named_file(parent_fd: int, name: str, label: str, *, required: bool) -> os.stat_result | None:
        try:
            info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            if required:
                raise ValueError(f"Build store {label} is missing") from None
            return None
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600):
            raise ValueError(f"Build store {label} must be an owned regular 0600 file without links")
        return info

    @contextmanager
    def _locked(self) -> Iterator[tuple[dict[str, Any], int]]:
        """Hold the short state lock and yield its directory-anchored state."""
        with directory_fd(self.parent) as parent_fd:
            self._check_parent_fd(parent_fd)
            flags = (os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK
                     | getattr(os, "O_CLOEXEC", 0))
            try:
                lock_fd = os.open(self.lock_name, flags, 0o600, dir_fd=parent_fd)
            except OSError as exc:
                if exc.errno == errno.ELOOP:
                    raise ValueError("Build store state lock must not be a symlink") from exc
                raise
            try:
                lock_info = self._check_private_file(lock_fd, "state lock")
                self._check_named_file(parent_fd, self.lock_name, "state lock", required=True)
                fcntl.flock(lock_fd, fcntl.LOCK_EX)
                self._check_parent_fd(parent_fd)
                current_lock = self._check_named_file(parent_fd, self.lock_name, "state lock", required=True)
                if current_lock is None or (current_lock.st_dev, current_lock.st_ino) != (
                        lock_info.st_dev, lock_info.st_ino):
                    raise ValueError("Build store state lock changed while opening")
                initialized = os.pread(lock_fd, len(_INITIALIZED) + 1, 0)
                if initialized not in (b"", _INITIALIZED):
                    raise ValueError("Build store initialization witness is invalid")
                state = self._load(parent_fd, required=bool(initialized))
                if not initialized:
                    # Persist an initial ledger before marking the stable lock.
                    # Once acknowledged, losing the ledger cannot reset replay
                    # protection or remove an interrupted running claim.
                    self._save(parent_fd, state)
                    if os.pwrite(lock_fd, _INITIALIZED, 0) != len(_INITIALIZED):
                        raise OSError("Build store initialization witness is incomplete")
                    os.fsync(lock_fd)
                yield state, parent_fd
            finally:
                os.close(lock_fd)

    def _load(self, parent_fd: int, *, required: bool = False) -> dict[str, Any]:
        try:
            descriptor = os.open(
                self.name,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0),
                dir_fd=parent_fd,
            )
        except FileNotFoundError:
            if required:
                raise ValueError("Initialized build ledger is missing; restore it before continuing") from None
            return _empty_state()
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise ValueError("Build store state must not be a symlink") from exc
            raise
        try:
            self._check_private_file(descriptor, "state")
            with os.fdopen(descriptor, "rb") as stream:
                descriptor = -1
                raw = stream.read(MAX_STORE_BYTES + 1)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if len(raw) > MAX_STORE_BYTES:
            raise ValueError("Build store exceeds its 16 MiB byte limit")
        return _validate_state(decode_document(raw, maximum=MAX_STORE_BYTES))

    def capacity_snapshot(self) -> tuple[dict[str, Any], int]:
        """Copy private validated state and encoded byte count without mutation.

        Trusted maintenance only. Requires an established exact state-lock
        witness and ledger; refuses contention without initializing, expiring,
        syncing or acquiring the worker lease. Call under the input lock when
        combining input and job accounting. The worker lock may not exist yet.
        """
        # Import at use time: input admission itself depends on this job store.
        from lib.build_inputs import _private_root

        with _private_root(self.parent) as parent_fd:
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
            descriptor = os.open(self.lock_name, flags, dir_fd=parent_fd)
            try:
                opened = self._check_private_file(descriptor, "state lock")
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise RuntimeError("Build job accounting is busy") from None
                current = self._check_named_file(parent_fd, self.lock_name, "state lock", required=True)
                if (current is None or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
                        or opened.st_size != len(_INITIALIZED)
                        or os.pread(descriptor, len(_INITIALIZED) + 1, 0) != _INITIALIZED):
                    raise ValueError("Build store initialization witness is invalid")
                # Only the ledger and its two documented locks belong here.
                with os.scandir(parent_fd) as entries:
                    for index, entry in enumerate(entries):
                        if index >= 3 or entry.name not in {self.name, self.lock_name, self.worker_lock_name}:
                            raise ValueError("Build store contains ambiguous accounting evidence")
                self._check_named_file(parent_fd, self.worker_lock_name, "worker lock", required=False)
                before = self._check_named_file(parent_fd, self.name, "state", required=True)
                state = self._load(parent_fd, required=True)
                after = self._check_named_file(parent_fd, self.name, "state", required=True)
                identity = lambda info: (info.st_dev, info.st_ino, info.st_size,
                                         info.st_mtime_ns, info.st_ctime_ns)
                if before is None or after is None or identity(before) != identity(after):
                    raise ValueError("Build store changed during capacity inspection")
                return state, before.st_size
            finally:
                os.close(descriptor)

    @contextmanager
    def _readonly_state(self) -> Iterator[dict[str, Any]]:
        """Read established job evidence under a nonblocking lock, without repair."""
        # Import at use time: input admission itself depends on this job store.
        from lib.build_inputs import _private_root

        with _private_root(self.parent) as parent_fd:
            try:
                descriptor = os.open(
                    self.lock_name,
                    os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0),
                    dir_fd=parent_fd,
                )
            except FileNotFoundError:
                raise ValueError("Build store state lock is missing") from None
            except OSError as exc:
                if exc.errno == errno.ELOOP:
                    raise ValueError("Build store state lock must not be a symlink") from exc
                raise
            try:
                opened = self._check_private_file(descriptor, "state lock")
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise RuntimeError("Build job state is busy") from None
                current = self._check_named_file(parent_fd, self.lock_name, "state lock", required=True)
                if (current is None or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
                        or opened.st_size != len(_INITIALIZED)
                        or os.pread(descriptor, len(_INITIALIZED) + 1, 0) != _INITIALIZED):
                    raise ValueError("Build store initialization witness is invalid")
                before = self._check_named_file(parent_fd, self.name, "state", required=True)
                state = self._load(parent_fd, required=True)
                after = self._check_named_file(parent_fd, self.name, "state", required=True)
                identity = lambda info: (info.st_dev, info.st_ino, info.st_size,
                                         info.st_mtime_ns, info.st_ctime_ns)
                if before is None or after is None or identity(before) != identity(after):
                    raise ValueError("Build store changed during read-only inspection")
                yield state
            finally:
                os.close(descriptor)

    def _save(self, parent_fd: int, state: dict[str, Any]) -> None:
        _validate_state(state)
        encoded = encode_document(state, maximum=MAX_STORE_BYTES - 1) + b"\n"
        temporary = f".{self.name}.{secrets.token_hex(16)}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open(temporary, flags, 0o600, dir_fd=parent_fd)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            self._check_named_file(parent_fd, self.name, "state", required=False)
            os.replace(temporary, self.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            os.fsync(parent_fd)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                os.unlink(temporary, dir_fd=parent_fd)
            except FileNotFoundError:
                pass

    def _now(self, state: dict[str, Any]) -> float:
        raw = _timestamp(self._clock(), "Build store clock")
        return max(raw, state["last_time"])

    @staticmethod
    def _touch(state: dict[str, Any], now: float) -> None:
        state["last_time"] = max(state["last_time"], now)

    @staticmethod
    def _expire_queued(state: dict[str, Any], now: float) -> bool:
        changed = False
        for record in state["jobs"].values():
            if record["state"] == "queued" and now >= record["expires_at"]:
                record["state"] = "expired"
                record["finished_at"] = now
                record["reason"] = _REASONS["expired"]
                changed = True
        return changed

    @staticmethod
    def _public(record: dict[str, Any]) -> dict[str, Any]:
        request = record["request"]
        return {
            "request_id": record["request_id"],
            "sequence": record["sequence"],
            "project": request["project"],
            "source": copy.deepcopy(request["source"]),
            "platform": copy.deepcopy(request["platform"]),
            "limits": copy.deepcopy(request["limits"]),
            "state": record["state"],
            "cancel_requested": record["cancel_requested"],
            "created_at": record["created_at"],
            "expires_at": record["expires_at"],
            "started_at": record["started_at"],
            "finished_at": record["finished_at"],
            "artifact": copy.deepcopy(record["artifact"]),
            "reason": record["reason"],
        }

    @staticmethod
    def _record(state: dict[str, Any], request_id: str) -> dict[str, Any]:
        try:
            return state["jobs"][request_id]
        except KeyError as exc:
            raise ValueError("Build job was not found") from exc

    def _owned_record(self, state: dict[str, Any], request_id: str, owner: str) -> dict[str, Any]:
        record = self._record(state, request_id)
        if record["owner"] != owner:
            raise PermissionError("Build job is not owned by this principal")
        return record

    def submit(self, request: object, *, owner: str) -> dict[str, Any]:
        """Durably admit a validated request or return its exact replay."""
        principal = _owner(owner)
        job = parse_build_request(request)
        digest = request_digest(job)
        with self._locked() as (state, parent_fd):
            existing = state["jobs"].get(job["request_id"])
            if existing is not None:
                if existing["owner"] != principal:
                    raise PermissionError("Build request ID is already owned by another principal")
                if existing["request_sha256"] != digest:
                    raise ValueError("Build request ID is already bound to different inputs")
                now = self._now(state)
                if self._expire_queued(state, now):
                    self._touch(state, now)
                    self._save(parent_fd, state)
                return self._public(existing)

            now = self._now(state)
            expired = self._expire_queued(state, now)
            if expired:
                self._touch(state, now)
                self._save(parent_fd, state)
            if state["draining"]:
                raise RuntimeError("Build host is draining and refuses new admissions")
            if len(state["jobs"]) >= MAX_OPERATIONS:
                raise ValueError("Build job store is full; retained operations cannot be pruned")
            queued = sum(record["state"] == "queued" for record in state["jobs"].values())
            if queued >= self.max_queue_length:
                raise ValueError("Build job queue is full")
            expiry = now + self.queue_ttl_seconds
            if not math.isfinite(expiry):
                raise ValueError("Build queue expiry is outside supported limits")
            sequence = state["last_sequence"] + 1
            record = {
                "request_id": job["request_id"],
                "owner": principal,
                "request_sha256": digest,
                "request": job,
                "sequence": sequence,
                "created_at": now,
                "expires_at": expiry,
                "state": "queued",
                "cancel_requested": False,
                "started_at": None,
                "finished_at": None,
                "lease_id": None,
                "artifact": None,
                "reason": None,
            }
            state["jobs"][job["request_id"]] = record
            state["last_sequence"] = sequence
            self._touch(state, now)
            self._save(parent_fd, state)
            return self._public(record)

    def status(self, request_id: str, *, owner: str) -> dict[str, Any]:
        """Return detached, owner-scoped status without commands or env values."""
        request_id = _identifier(request_id, "Request ID")
        principal = _owner(owner)
        with self._locked() as (state, parent_fd):
            record = self._owned_record(state, request_id, principal)
            now = self._now(state)
            if self._expire_queued(state, now):
                self._touch(state, now)
                self._save(parent_fd, state)
            return self._public(record)

    def read_for_retention(self, request_id: str, *, lease: WorkerLease) -> dict[str, Any]:
        """Return private evidence to trusted maintenance holding this worker lease.

        Copy under the short state lock; callers must hash files after returning.
        This never expires jobs, changes drain state, or supplies a public API.
        """
        request_id = _identifier(request_id, "Request ID")
        with self._lease_guard(lease):
            with self._locked() as (state, _parent_fd):
                return copy.deepcopy(self._record(state, request_id))

    def replay_terminal(self, request: object, *, owner: str, expected_job_sha256: str) -> dict[str, Any]:
        """Return an existing exact certain-terminal receipt; never create work.

        Pruned input admission uses this short snapshot before consuming replay
        bytes. It neither expires queued work nor hashes files under state lock.
        """
        principal = _owner(owner)
        job = parse_build_request(request)
        digest = request_digest(job)
        expected_job_sha256 = _identifier(expected_job_sha256, "Terminal job identity", _SHA256)
        with self._readonly_state() as state:
            record = self._owned_record(state, job["request_id"], principal)
            if record["request_sha256"] != digest:
                raise ValueError("Build request ID is already bound to different inputs")
            if record["state"] not in {"succeeded", "failed", "cancelled", "expired"}:
                raise ValueError("Build input replay requires an existing certain terminal job")
            if hashlib.sha256(encode_document(record, maximum=MAX_STORE_BYTES)).hexdigest() != expected_job_sha256:
                raise ValueError("Build input replay terminal job identity changed")
            return self._public(record)

    def cancel(self, request_id: str, *, owner: str) -> dict[str, Any]:
        """Cancel queued work or set a cooperative flag on running work."""
        request_id = _identifier(request_id, "Request ID")
        principal = _owner(owner)
        with self._locked() as (state, parent_fd):
            record = self._owned_record(state, request_id, principal)
            now = self._now(state)
            changed = self._expire_queued(state, now)
            if record["state"] == "queued":
                record["state"] = "cancelled"
                record["cancel_requested"] = True
                record["finished_at"] = now
                record["reason"] = _REASONS["cancelled"]
                changed = True
            elif record["state"] == "running" and not record["cancel_requested"]:
                record["cancel_requested"] = True
                changed = True
            if changed:
                self._touch(state, now)
                self._save(parent_fd, state)
            return self._public(record)

    def set_draining(self, draining: bool) -> bool:
        """Persist admission drain state; accepted queued jobs remain held."""
        if type(draining) is not bool:
            raise ValueError("Build drain state must be a boolean")
        with self._locked() as (state, parent_fd):
            now = self._now(state)
            changed = self._expire_queued(state, now)
            if state["draining"] != draining:
                state["draining"] = draining
                changed = True
            if changed:
                self._touch(state, now)
                self._save(parent_fd, state)
            return state["draining"]

    @contextmanager
    def worker(self) -> Iterator[WorkerLease]:
        """Yield the exclusive host worker lease until its context exits.

        Pass ``lease.fileno()`` to supervised subprocesses with ``pass_fds`` so
        descendants retain the slot if this process disappears. Lease methods
        remain restricted to the original trusted process.
        """
        with directory_fd(self.parent) as parent_fd:
            self._check_parent_fd(parent_fd)
            flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK
            try:
                descriptor = os.open(self.worker_lock_name, flags, 0o600, dir_fd=parent_fd)
            except OSError as exc:
                if exc.errno == errno.ELOOP:
                    raise ValueError("Build worker lock must not be a symlink") from exc
                raise
            try:
                info = self._check_private_file(descriptor, "worker lock")
                current = self._check_named_file(
                    parent_fd, self.worker_lock_name, "worker lock", required=True,
                )
                if current is None or (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
                    raise ValueError("Build worker lock changed while opening")
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise RuntimeError("Build worker slot is already leased") from exc
                os.set_inheritable(descriptor, True)
                lease = WorkerLease(
                    self, descriptor, secrets.token_hex(16), os.getpid(),
                    (info.st_dev, info.st_ino), self._lease_authority,
                )
                descriptor = -1
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
        try:
            yield lease
        finally:
            with lease._use_lock:
                lease._active = False
                try:
                    os.close(lease._descriptor)
                except OSError:
                    pass

    @contextmanager
    def _lease_guard(self, lease: WorkerLease) -> Iterator[None]:
        if not isinstance(lease, WorkerLease) or lease._store is not self:
            raise PermissionError("Build worker API requires this store's active lease")
        with lease._use_lock:
            self._verify_lease(lease, mutation=True)
            yield

    def _verify_lease(self, lease: WorkerLease, *, mutation: bool) -> None:
        if (not lease._active or lease._authority is not self._lease_authority
                or (mutation and lease._pid != os.getpid())):
            raise PermissionError("Build worker lease is inactive or belongs to another process")
        try:
            info = self._check_private_file(lease._descriptor, "worker lease")
        except OSError as exc:
            raise PermissionError("Build worker lease descriptor is no longer open") from exc
        if (info.st_dev, info.st_ino) != lease._lock_identity:
            raise PermissionError("Build worker lease identity changed")
        with directory_fd(self.parent) as parent_fd:
            self._check_parent_fd(parent_fd)
            current = self._check_named_file(parent_fd, self.worker_lock_name, "worker lock", required=True)
            if current is None or (current.st_dev, current.st_ino) != lease._lock_identity:
                raise PermissionError("Build worker lease lock file changed")
        if mutation:
            try:
                fcntl.flock(lease._descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise PermissionError("Build worker lease no longer owns the host slot") from exc

    def _claim(self, lease: WorkerLease) -> dict[str, Any] | None:
        with self._lease_guard(lease):
            with self._locked() as (state, parent_fd):
                now = self._now(state)
                changed = self._expire_queued(state, now)
                running = next((record for record in state["jobs"].values()
                                if record["state"] == "running"), None)
                if changed:
                    self._touch(state, now)
                    self._save(parent_fd, state)
                if running is not None:
                    if running["lease_id"] == lease._token:
                        raise RuntimeError("This worker lease already has an active claim")
                    raise RuntimeError(
                        "An interrupted running build must be resolved with "
                        "recover_interrupted before another claim"
                    )
                if state["draining"]:
                    return None
                queued = sorted(
                    (record for record in state["jobs"].values() if record["state"] == "queued"),
                    key=lambda record: record["sequence"],
                )
                if not queued:
                    return None
                record = queued[0]
                # Expiration was checked after loading the locked state, directly
                # before this durable running marker is written.
                if now >= record["expires_at"]:
                    record["state"] = "expired"
                    record["finished_at"] = now
                    record["reason"] = _REASONS["expired"]
                    self._touch(state, now)
                    self._save(parent_fd, state)
                    return None
                record["state"] = "running"
                record["started_at"] = now
                record["lease_id"] = lease._token
                self._touch(state, now)
                self._save(parent_fd, state)
                return {
                    "request_id": record["request_id"],
                    "owner": record["owner"],
                    "sequence": record["sequence"],
                    "request": copy.deepcopy(record["request"]),
                }

    def _finish(
        self,
        lease: WorkerLease,
        request_id: str,
        *,
        outcome: str,
        work_stopped: bool,
        artifact: object | None,
    ) -> dict[str, Any]:
        request_id = _identifier(request_id, "Request ID")
        if not isinstance(outcome, str) or outcome not in {"succeeded", "failed", "cancelled", "uncertain"}:
            raise ValueError("Build outcome must be succeeded, failed, cancelled, or uncertain")
        if type(work_stopped) is not bool or not work_stopped:
            raise ValueError("Caller must verify all supervised build work has stopped")
        if outcome == "succeeded" and artifact is None:
            raise ValueError("A successful build requires an artifact")
        if outcome != "succeeded" and artifact is not None:
            raise ValueError("Failed, cancelled, and uncertain builds cannot retain artifacts")

        with self._lease_guard(lease):
            identity = None
            if outcome == "succeeded":
                with self._locked() as (state, parent_fd):
                    record = self._record(state, request_id)
                    self._require_active_claim(record, lease)
                    now = self._now(state)
                    if self._expire_queued(state, now):
                        self._touch(state, now)
                        self._save(parent_fd, state)
                    if record["cancel_requested"]:
                        raise ValueError("A cancellation-requested build cannot finish as succeeded")
                    request = copy.deepcopy(record["request"])
                identity = _artifact_identity(artifact, request)

            with self._locked() as (state, parent_fd):
                record = self._record(state, request_id)
                self._require_active_claim(record, lease)
                now = self._now(state)
                self._expire_queued(state, now)
                if outcome == "succeeded" and record["cancel_requested"]:
                    raise ValueError("A cancellation-requested build cannot finish as succeeded")
                if outcome == "cancelled" and not record["cancel_requested"]:
                    raise ValueError(
                        "A running build can finish as cancelled only after cancellation is requested"
                    )
                record["state"] = outcome
                record["finished_at"] = now
                record["lease_id"] = None
                record["artifact"] = identity
                record["reason"] = _REASONS.get(outcome)
                self._touch(state, now)
                self._save(parent_fd, state)
                return self._public(record)

    @staticmethod
    def _require_active_claim(record: dict[str, Any], lease: WorkerLease) -> None:
        if record["state"] != "running" or record["lease_id"] != lease._token:
            raise PermissionError("Build request is not the active claim for this worker lease")

    def _recover_interrupted(
        self,
        lease: WorkerLease,
        request_id: str,
        *,
        cleanup: Callable[[str, dict[str, Any]], bool],
    ) -> dict[str, Any]:
        request_id = _identifier(request_id, "Request ID")
        if not callable(cleanup):
            raise ValueError("Recovery requires a trusted cleanup callback")
        with self._lease_guard(lease):
            with self._locked() as (state, parent_fd):
                record = self._record(state, request_id)
                if record["state"] != "running":
                    raise ValueError("Build request has no interrupted running claim")
                if record["lease_id"] == lease._token:
                    raise ValueError("The current worker lease cannot recover its own active claim")
                now = self._now(state)
                if self._expire_queued(state, now):
                    self._touch(state, now)
                    self._save(parent_fd, state)
                interrupted_lease = record["lease_id"]
                digest = record["request_sha256"]
                request = copy.deepcopy(record["request"])
            verified = cleanup(request_id, request)
            if verified is not True:
                raise RuntimeError("Recovery cleanup did not verify that all supervised work stopped")
            with self._locked() as (state, parent_fd):
                record = self._record(state, request_id)
                if (record["state"] != "running" or record["lease_id"] != interrupted_lease
                        or record["request_sha256"] != digest):
                    raise RuntimeError("Interrupted build identity changed during cleanup")
                now = self._now(state)
                self._expire_queued(state, now)
                record["state"] = "uncertain"
                record["finished_at"] = now
                record["lease_id"] = None
                record["artifact"] = None
                record["reason"] = _REASONS["uncertain"]
                self._touch(state, now)
                self._save(parent_fd, state)
                return self._public(record)


class WorkerLease:
    """A capability yielded by :meth:`BuildJobStore.worker`.

    Only its original process can use mutating methods. The inheritable file
    descriptor exists so supervised descendants keep the host slot after an
    abrupt parent exit; descendants should receive only the descriptor.
    """

    __slots__ = (
        "_store", "_descriptor", "_token", "_pid", "_lock_identity",
        "_authority", "_active", "_use_lock",
    )

    def __init__(
        self,
        store: BuildJobStore,
        descriptor: int,
        token: str,
        pid: int,
        lock_identity: tuple[int, int],
        authority: object,
    ) -> None:
        self._store = store
        self._descriptor = descriptor
        self._token = token
        self._pid = pid
        self._lock_identity = lock_identity
        self._authority = authority
        self._active = True
        self._use_lock = threading.RLock()

    def fileno(self) -> int:
        """Return the inheritable descriptor for supervised child processes."""
        with self._use_lock:
            self._store._verify_lease(self, mutation=False)
            return self._descriptor

    def claim(self) -> dict[str, Any] | None:
        """Persist and return the oldest unexpired queued request, if any."""
        return self._store._claim(self)

    def finish(
        self,
        request_id: str,
        *,
        outcome: str,
        work_stopped: bool,
        artifact: object | None = None,
    ) -> dict[str, Any]:
        """Record a terminal outcome after the caller confirms all work stopped."""
        return self._store._finish(
            self, request_id, outcome=outcome, work_stopped=work_stopped, artifact=artifact,
        )

    def recover_interrupted(
        self,
        request_id: str,
        *,
        cleanup: Callable[[str, dict[str, Any]], bool],
    ) -> dict[str, Any]:
        """Verify caller-owned cleanup and permanently mark an old claim uncertain."""
        return self._store._recover_interrupted(self, request_id, cleanup=cleanup)
