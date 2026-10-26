"""Durable target-side admission for externally built deployments.

This store records intent and serializes activation. Authentication, target
policy, artifact verification, and runtime compatibility are caller duties.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
import copy
import fcntl
import hashlib
import math
import os
import re
import stat
import time
import uuid
from typing import Any

from lib.atomic_io import read_json_file, write_bytes_atomic
from lib.build_protocol import decode_document, encode_document, parse_build_request, request_digest
from lib.setup_directories import directory_fd
from lib.validation import validate_filesystem_path, validate_no_control_characters


MAX_OPERATIONS = 4096
MAX_STORE_BYTES = 16 * 1024 * 1024
MAX_RESULT_BYTES = 64 * 1024
MAX_TTL_SECONDS = 30 * 24 * 60 * 60
_MAX_GENERATION = 2**63 - 1
_ID = re.compile(r"[0-9a-f]{32}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
_OWNER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}")
_STATES = frozenset({"reserved", "cancelled", "activating", "uncertain", "completed"})
_INITIALIZED = b"basaltwater-build-admission-v1\n"
_RECORD_FIELDS = frozenset({
    "operation_id", "owner", "idempotency_key", "application", "generation",
    "created_at", "expires_at", "project", "request_id", "request_sha256",
    "source", "bindings_sha256", "state", "artifact_sha256", "result",
})


def _slug(value: object, label: str) -> str:
    if not isinstance(value, str) or _SLUG.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase application slug")
    return value


def _owner(value: object) -> str:
    if not isinstance(value, str) or _OWNER.fullmatch(value) is None:
        raise ValueError("Owner must be a safe label of 1–128 characters")
    validate_no_control_characters(value, "Owner")
    return value


def _hex(value: object, pattern: re.Pattern[str], label: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ValueError(f"{label} must be lowercase hexadecimal")
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


def _source(value: object) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {
        "revision", "snapshot_sha256", "manifest_sha256",
    }:
        raise ValueError("Admission source identity is invalid")
    revision = value["revision"]
    if not isinstance(revision, str) or re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", revision) is None:
        raise ValueError("Admission source revision is invalid")
    return {
        "revision": revision,
        "snapshot_sha256": _hex(value["snapshot_sha256"], _SHA256, "Source snapshot digest"),
        "manifest_sha256": _hex(value["manifest_sha256"], _SHA256, "Source manifest digest"),
    }


def _replay_identity(owner: str, key: str) -> str:
    return hashlib.sha256((owner + "\0" + key).encode("ascii")).hexdigest()


def _empty_state() -> dict[str, Any]:
    return {"schema": 1, "desired_generations": {}, "operations": {}, "replays": {}}


class DeploymentAdmissionStore:
    """A bounded, private store for deployment reservations and activation.

    ``path`` names the JSON state file inside an already-provisioned private
    directory. A stable sibling ``.lock`` file serializes each operation.
    """

    def __init__(self, path: str | os.PathLike[str], *, clock: Callable[[], float] | None = None):
        raw_path = os.fspath(path)
        if not isinstance(raw_path, str):
            raise ValueError("Admission state path must be text")
        validate_filesystem_path(raw_path, must_exist=False)
        self.path = os.path.abspath(raw_path)
        self.parent = os.path.dirname(self.path)
        self.name = os.path.basename(self.path)
        if not self.name or self.name in {".", ".."}:
            raise ValueError("Admission state path must name a file")
        self.lock_name = self.name + ".lock"
        if clock is not None and not callable(clock):
            raise ValueError("Admission clock must be callable")
        self._clock = time.time if clock is None else clock
        self._check_parent()

    def _check_parent(self) -> None:
        with directory_fd(self.parent) as descriptor:
            info = os.fstat(descriptor)
            mode = stat.S_IMODE(info.st_mode)
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
                    or mode & 0o077 or mode & 0o700 != 0o700):
                raise ValueError("Admission parent must be private and owned by the effective UID")

    def _now(self) -> float:
        value = self._clock()
        if type(value) not in (int, float) or value < 0:
            raise ValueError("Admission clock returned an invalid timestamp")
        try:
            converted = float(value)
        except OverflowError as exc:
            raise ValueError("Admission clock returned an invalid timestamp") from exc
        if not math.isfinite(converted):
            raise ValueError("Admission clock returned an invalid timestamp")
        return converted

    @staticmethod
    def _check_file(parent_fd: int, name: str, label: str) -> bool:
        try:
            info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return False
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600):
            raise ValueError(f"Admission {label} must be an owned regular 0600 file without links")
        return True

    @contextmanager
    def _locked(self) -> Iterator[dict[str, Any]]:
        """Hold global store ownership through state changes and activation."""
        self._check_parent()
        with directory_fd(self.parent) as parent_fd:
            parent_info = os.fstat(parent_fd)
            mode = stat.S_IMODE(parent_info.st_mode)
            if (parent_info.st_uid != os.geteuid() or mode & 0o077
                    or mode & 0o700 != 0o700):
                raise ValueError("Admission parent changed to an unsafe directory")
            flags = (os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK
                     | getattr(os, "O_CLOEXEC", 0))
            lock_fd = os.open(self.lock_name, flags, 0o600, dir_fd=parent_fd)
            try:
                self._check_file(parent_fd, self.lock_name, "lock")
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise RuntimeError("Deployment admission store is busy") from exc
                self._check_file(parent_fd, self.name, "state")
                initialized = os.pread(lock_fd, len(_INITIALIZED) + 1, 0)
                if initialized not in (b"", _INITIALIZED):
                    raise ValueError("Admission initialization witness is invalid")
                state = self._load(required=bool(initialized))
                if not initialized:
                    self._save(state)
                    if os.pwrite(lock_fd, _INITIALIZED, 0) != len(_INITIALIZED):
                        raise OSError("Admission initialization witness is incomplete")
                    os.fsync(lock_fd)
                yield state
            finally:
                os.close(lock_fd)

    def _load(self, *, required: bool = False) -> dict[str, Any]:
        try:
            document = read_json_file(self.path, max_bytes=MAX_STORE_BYTES)
        except FileNotFoundError:
            if required:
                raise ValueError("Initialized admission ledger is missing; restore it before continuing") from None
            return _empty_state()
        return self._validate_state(document)

    @staticmethod
    def _validate_record(operation_id: object, value: object) -> dict[str, Any]:
        if not isinstance(value, dict) or set(value) != _RECORD_FIELDS:
            raise ValueError("Admission store contains an invalid operation record")
        record = copy.deepcopy(value)
        record["operation_id"] = _hex(operation_id, _ID, "Operation ID")
        if record["operation_id"] != value["operation_id"]:
            raise ValueError("Admission operation key does not match its record")
        record["owner"] = _owner(record["owner"])
        record["idempotency_key"] = _hex(record["idempotency_key"], _ID, "Idempotency key")
        record["application"] = _slug(record["application"], "Application")
        generation = record["generation"]
        if type(generation) is not int or not 1 <= generation <= _MAX_GENERATION:
            raise ValueError("Admission generation is invalid")
        record["created_at"] = _timestamp(record["created_at"], "Admission creation time")
        record["expires_at"] = _timestamp(record["expires_at"], "Admission expiry")
        if record["expires_at"] <= record["created_at"]:
            raise ValueError("Admission expiry must follow its creation time")
        record["project"] = _slug(record["project"], "Project")
        record["request_id"] = _hex(record["request_id"], _ID, "Build request ID")
        if record["operation_id"] == record["request_id"]:
            raise ValueError("Operation ID must differ from the build request ID")
        record["request_sha256"] = _hex(record["request_sha256"], _SHA256, "Build request digest")
        record["source"] = _source(record["source"])
        record["bindings_sha256"] = _hex(record["bindings_sha256"], _SHA256, "Target bindings digest")
        if not isinstance(record["state"], str) or record["state"] not in _STATES:
            raise ValueError("Admission operation state is invalid")
        artifact = record["artifact_sha256"]
        if record["state"] in {"activating", "uncertain", "completed"}:
            record["artifact_sha256"] = _hex(artifact, _SHA256, "Artifact digest")
        elif artifact is not None:
            raise ValueError("Unstarted admission cannot bind an artifact")
        if record["state"] != "completed" and record["result"] is not None:
            raise ValueError("Incomplete admission cannot retain an activation result")
        try:
            encode_document(record["result"], maximum=MAX_RESULT_BYTES)
        except ValueError as exc:
            raise ValueError("Admission activation result is invalid or oversized") from exc
        return record

    @classmethod
    def _validate_state(cls, value: object) -> dict[str, Any]:
        if (not isinstance(value, dict)
                or set(value) != {"schema", "desired_generations", "operations", "replays"}
                or type(value["schema"]) is not int or value["schema"] != 1):
            raise ValueError("Admission store has an unsupported or invalid schema")
        desired = value["desired_generations"]
        operations = value["operations"]
        replays = value["replays"]
        if not isinstance(desired, dict) or not isinstance(operations, dict) or not isinstance(replays, dict):
            raise ValueError("Admission store indexes must be objects")
        if len(operations) > MAX_OPERATIONS or len(replays) > MAX_OPERATIONS:
            raise ValueError("Admission store exceeds its operation limit")
        clean_desired: dict[str, int] = {}
        for application, generation in desired.items():
            application = _slug(application, "Application")
            if type(generation) is not int or not 1 <= generation <= _MAX_GENERATION:
                raise ValueError("Admission desired generation is invalid")
            clean_desired[application] = generation
        clean_operations: dict[str, dict[str, Any]] = {}
        expected_replays: dict[str, str] = {}
        generations: dict[str, set[int]] = {}
        for operation_id, raw_record in operations.items():
            record = cls._validate_record(operation_id, raw_record)
            app = record["application"]
            if app not in clean_desired or record["generation"] > clean_desired[app]:
                raise ValueError("Admission operation is ahead of its desired generation")
            app_generations = generations.setdefault(app, set())
            if record["generation"] in app_generations:
                raise ValueError("Admission store contains a duplicate application generation")
            app_generations.add(record["generation"])
            replay_id = _replay_identity(record["owner"], record["idempotency_key"])
            if replay_id in expected_replays:
                raise ValueError("Admission store contains a duplicate replay identity")
            expected_replays[replay_id] = record["operation_id"]
            clean_operations[record["operation_id"]] = record
        for application, latest in clean_desired.items():
            if latest > MAX_OPERATIONS:
                raise ValueError("Admission generation history exceeds its operation limit")
            if generations.get(application) != set(range(1, latest + 1)):
                raise ValueError("Admission generation history is incomplete")
        clean_replays: dict[str, str] = {}
        for replay_id, operation_id in replays.items():
            _hex(replay_id, _SHA256, "Replay identity")
            _hex(operation_id, _ID, "Replay operation ID")
            clean_replays[replay_id] = operation_id
        if clean_replays != expected_replays:
            raise ValueError("Admission replay index does not match retained operations")
        return {
            "schema": 1,
            "desired_generations": clean_desired,
            "operations": clean_operations,
            "replays": clean_replays,
        }

    def _save(self, state: dict[str, Any]) -> None:
        encoded = encode_document(state, maximum=MAX_STORE_BYTES - 1) + b"\n"
        write_bytes_atomic(self.path, encoded, mode=0o600)

    @staticmethod
    def _metadata(record: dict[str, Any], state: dict[str, Any], now: float) -> dict[str, Any]:
        result = copy.deepcopy(record)
        superseded = state["desired_generations"][record["application"]] != record["generation"]
        expired = now >= record["expires_at"]
        result["superseded"] = superseded
        result["expired"] = expired
        if record["state"] == "activating":
            effective = "uncertain"
        elif record["state"] != "reserved":
            effective = record["state"]
        elif superseded:
            effective = "superseded"
        elif expired:
            effective = "expired"
        else:
            effective = "reserved"
        result["effective_status"] = effective
        return result

    @staticmethod
    def _owned_record(state: dict[str, Any], operation_id: str, owner: str) -> dict[str, Any]:
        try:
            record = state["operations"][operation_id]
        except KeyError as exc:
            raise ValueError("Deployment admission was not found") from exc
        if record["owner"] != owner:
            raise PermissionError("Deployment admission is not owned by this principal")
        return record

    def reserve(
        self,
        application: str,
        request: object,
        *,
        owner: str,
        idempotency_key: str,
        bindings_sha256: str,
        ttl_seconds: int = 3600,
    ) -> dict[str, Any]:
        """Persist a new desired generation, or return an exact-key replay."""
        application = _slug(application, "Application")
        principal = _owner(owner)
        key = _hex(idempotency_key, _ID, "Idempotency key")
        bindings = _hex(bindings_sha256, _SHA256, "Target bindings digest")
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= MAX_TTL_SECONDS:
            raise ValueError("Admission TTL is outside supported limits")
        job = parse_build_request(request)
        digest = request_digest(job)
        replay_id = _replay_identity(principal, key)
        with self._locked() as state:
            now = self._now()
            expiry = now + ttl_seconds
            if not math.isfinite(expiry):
                raise ValueError("Admission expiry is outside supported limits")
            existing_id = state["replays"].get(replay_id)
            if existing_id is not None:
                record = state["operations"][existing_id]
                expected = (record["application"], record["project"], record["request_sha256"],
                            record["source"], record["bindings_sha256"])
                actual = (application, job["project"], digest, job["source"], bindings)
                if expected != actual:
                    raise ValueError("Idempotency key is already bound to different deployment intent")
                return self._metadata(record, state, now)
            if len(state["operations"]) >= MAX_OPERATIONS:
                raise ValueError("Admission store is full; reconcile or retain existing evidence")
            current = state["desired_generations"].get(application, 0)
            if current >= _MAX_GENERATION:
                raise ValueError("Application deployment generation is exhausted")
            generation = current + 1
            operation_id = uuid.uuid4().hex
            while operation_id == job["request_id"] or operation_id in state["operations"]:
                operation_id = uuid.uuid4().hex
            record = {
                "operation_id": operation_id,
                "owner": principal,
                "idempotency_key": key,
                "application": application,
                "generation": generation,
                "created_at": now,
                "expires_at": expiry,
                "project": job["project"],
                "request_id": job["request_id"],
                "request_sha256": digest,
                "source": job["source"],
                "bindings_sha256": bindings,
                "state": "reserved",
                "artifact_sha256": None,
                "result": None,
            }
            state["operations"][operation_id] = record
            state["desired_generations"][application] = generation
            state["replays"][replay_id] = operation_id
            self._save(state)
            return self._metadata(record, state, now)

    def status(self, operation_id: str, *, owner: str) -> dict[str, Any]:
        """Return detached owner-scoped admission metadata."""
        operation_id = _hex(operation_id, _ID, "Operation ID")
        principal = _owner(owner)
        with self._locked() as state:
            record = self._owned_record(state, operation_id, principal)
            return self._metadata(record, state, self._now())

    def cancel(self, operation_id: str, *, owner: str) -> dict[str, Any]:
        """Durably cancel an admission that has not started activation."""
        operation_id = _hex(operation_id, _ID, "Operation ID")
        principal = _owner(owner)
        with self._locked() as state:
            now = self._now()
            record = self._owned_record(state, operation_id, principal)
            if record["state"] == "cancelled":
                return self._metadata(record, state, now)
            if record["state"] != "reserved":
                raise ValueError("Activation has started and cannot be represented as undone")
            record["state"] = "cancelled"
            self._save(state)
            return self._metadata(record, state, now)

    def activate(
        self,
        operation_id: str,
        generation: int,
        request: object,
        artifact_sha256: str,
        *,
        owner: str,
        bindings_sha256: str,
        action: Callable[[], object],
    ) -> Any:
        """Run one trusted activation while holding generation ownership.

        The action has no arguments and must return bounded JSON. Its effects
        are caller-owned and cannot be rolled back by this admission record.
        """
        operation_id = _hex(operation_id, _ID, "Operation ID")
        principal = _owner(owner)
        artifact = _hex(artifact_sha256, _SHA256, "Artifact digest")
        bindings = _hex(bindings_sha256, _SHA256, "Target bindings digest")
        if type(generation) is not int or not 1 <= generation <= _MAX_GENERATION:
            raise ValueError("Activation generation is invalid")
        if not callable(action):
            raise ValueError("Activation action must be callable")
        job = parse_build_request(request)
        digest = request_digest(job)
        with self._locked() as state:
            record = self._owned_record(state, operation_id, principal)
            if record["generation"] != generation:
                raise ValueError("Activation generation does not match the admission")
            if (record["request_id"] != job["request_id"] or record["project"] != job["project"]
                    or record["request_sha256"] != digest or record["source"] != job["source"]):
                raise ValueError("Build request does not match the admitted identity")
            if record["bindings_sha256"] != bindings:
                raise ValueError("Target bindings do not match the admission")
            bound_artifact = record["artifact_sha256"]
            if bound_artifact is not None and bound_artifact != artifact:
                raise ValueError("Admission is already bound to a different artifact digest")
            if record["state"] == "completed":
                return copy.deepcopy(record["result"])
            if record["state"] in {"activating", "uncertain"}:
                raise RuntimeError("Activation outcome is uncertain and requires operator reconciliation")
            if record["state"] != "reserved":
                raise ValueError("Admission is cancelled and cannot activate")
            if state["desired_generations"].get(record["application"]) != generation:
                raise ValueError("Admission has been superseded by a newer desired generation")
            if self._now() >= record["expires_at"]:
                raise ValueError("Admission has expired")

            record["artifact_sha256"] = artifact
            record["state"] = "activating"
            self._save(state)
            try:
                # Loading and syncing the marker can consume the remaining TTL.
                # Once that marker is durable, any failed check retains the
                # bound digest and recovery guard, even if no action ran.
                if self._now() >= record["expires_at"]:
                    raise ValueError("Admission expired before activation could start")
                result = action()
                encoded_result = encode_document(result, maximum=MAX_RESULT_BYTES)
                # Decode through the strict parser so the stored result is detached JSON.
                stored_result = decode_document(encoded_result, maximum=MAX_RESULT_BYTES)
            except BaseException:
                record["state"] = "uncertain"
                try:
                    self._save(state)
                except BaseException:
                    # The already durable "activating" phase remains a replay guard.
                    pass
                raise

            record["state"] = "completed"
            record["result"] = stored_result
            self._save(state)
            return copy.deepcopy(stored_result)
