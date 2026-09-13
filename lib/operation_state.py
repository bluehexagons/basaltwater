"""Versioned durable markers for recoverable infrastructure operations."""

from __future__ import annotations

import json
import os
import uuid
import fcntl
import stat
import weakref
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Optional

from lib.atomic_io import read_json_file, remove_file_durable, write_json_atomic
from lib.types import JSONDict
from lib.validation import validate_filesystem_path, validate_no_control_characters


OPERATION_SCHEMA_VERSION = 1
OperationStatus = Literal["in_progress", "recovery_required"]


class OperationStateError(ValueError):
    """Raised when durable operation state is invalid or unsafe to replace."""


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_label(value: str, label: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string")
    validate_no_control_characters(value, label)


@dataclass(frozen=True)
class OperationRecord:
    """Serializable recovery state for one mutating operation."""

    schema_version: int
    operation_id: str
    operation_type: str
    resource: str
    phase: str
    status: OperationStatus
    started_at: str
    updated_at: str
    context: JSONDict

    def to_dict(self) -> JSONDict:
        return {
            "schema_version": self.schema_version,
            "operation_id": self.operation_id,
            "operation_type": self.operation_type,
            "resource": self.resource,
            "phase": self.phase,
            "status": self.status,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "context": self.context,
        }


def _required_string(payload: JSONDict, key: str, path: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise OperationStateError(f"Invalid operation marker {path}: {key} must be a string")
    return value


def _record_from_dict(payload: object, path: str) -> OperationRecord:
    if not isinstance(payload, dict):
        raise OperationStateError(f"Invalid operation marker {path}: expected a JSON object")
    version = payload.get("schema_version")
    if type(version) is not int or version != OPERATION_SCHEMA_VERSION:
        raise OperationStateError(
            f"Unsupported operation marker schema in {path}: {version!r}; "
            "move the marker aside for inspection before retrying"
        )
    status = payload.get("status")
    if not isinstance(status, str) or status not in {"in_progress", "recovery_required"}:
        raise OperationStateError(f"Invalid operation marker {path}: unknown status {status!r}")
    context = payload.get("context")
    if not isinstance(context, dict):
        raise OperationStateError(f"Invalid operation marker {path}: context must be an object")
    return OperationRecord(
        schema_version=OPERATION_SCHEMA_VERSION,
        operation_id=_required_string(payload, "operation_id", path),
        operation_type=_required_string(payload, "operation_type", path),
        resource=_required_string(payload, "resource", path),
        phase=_required_string(payload, "phase", path),
        status=status,
        started_at=_required_string(payload, "started_at", path),
        updated_at=_required_string(payload, "updated_at", path),
        context=context,
    )


class OperationStateStore:
    """Read and update one crash-safe operation marker."""

    def __init__(self, path: str):
        validate_filesystem_path(path, must_exist=False)
        self.path = os.path.abspath(path)
        self._lock_finalizer: weakref.finalize | None = None

    def _acquire(self) -> None:
        if self._lock_finalizer is not None and self._lock_finalizer.alive:
            return
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        lock_path = self.path + '.lock'
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise OperationStateError(f'Unsafe operation lock: {lock_path}')
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise OperationStateError(f'Operation is owned by another process: {self.path}') from exc
            self._lock_finalizer = weakref.finalize(self, os.close, descriptor)
        except BaseException:
            os.close(descriptor)
            raise

    def close(self) -> None:
        """Release ownership while retaining any marker for explicit recovery.

        The stable lock file is never removed. Kernel locks disappear on process
        exit; a leftover marker still blocks begin until recovered or inspected.
        """
        if self._lock_finalizer is not None:
            self._lock_finalizer()
            self._lock_finalizer = None

    def load(self) -> Optional[OperationRecord]:
        if not os.path.lexists(self.path):
            return None
        if os.path.islink(self.path):
            raise OperationStateError(
                f"Unsafe operation marker {self.path}: marker must not be a symlink"
            )
        try:
            payload = read_json_file(self.path)
        except (OSError, ValueError) as exc:
            raise OperationStateError(
                f"Invalid operation marker {self.path}: {exc}; "
                "move the marker aside for inspection before retrying"
            ) from exc
        return _record_from_dict(payload, self.path)

    def begin(
        self,
        operation_type: str,
        resource: str,
        phase: str,
        *,
        context: Optional[JSONDict] = None,
    ) -> OperationRecord:
        _validate_label(operation_type, "Operation type")
        _validate_label(resource, "Operation resource")
        _validate_label(phase, "Operation phase")
        self._acquire()
        existing = self.load()
        if existing is not None:
            raise OperationStateError(
                f"Unfinished {existing.operation_type} operation {existing.operation_id} "
                f"is recorded in {self.path} at phase {existing.phase}; recover or "
                "move the marker aside before retrying"
            )
        timestamp = _timestamp()
        record = OperationRecord(
            schema_version=OPERATION_SCHEMA_VERSION,
            operation_id=str(uuid.uuid4()),
            operation_type=operation_type,
            resource=resource,
            phase=phase,
            status="in_progress",
            started_at=timestamp,
            updated_at=timestamp,
            context=dict(context or {}),
        )
        self._write(record)
        return record

    def transition(
        self,
        operation_id: str,
        phase: str,
        *,
        status: OperationStatus = "in_progress",
        context: Optional[JSONDict] = None,
    ) -> OperationRecord:
        _validate_label(phase, "Operation phase")
        if status not in {"in_progress", "recovery_required"}:
            raise ValueError(f"Unsupported operation status: {status}")
        self._acquire()
        current = self._require_current(operation_id)
        record = OperationRecord(
            schema_version=current.schema_version,
            operation_id=current.operation_id,
            operation_type=current.operation_type,
            resource=current.resource,
            phase=phase,
            status=status,
            started_at=current.started_at,
            updated_at=_timestamp(),
            context=dict(context if context is not None else current.context),
        )
        self._write(record)
        return record

    def complete(self, operation_id: str) -> None:
        self._acquire()
        self._require_current(operation_id)
        remove_file_durable(self.path)
        self.close()

    def _require_current(self, operation_id: str) -> OperationRecord:
        current = self.load()
        if current is None:
            raise OperationStateError(f"Operation marker is missing: {self.path}")
        if current.operation_id != operation_id:
            raise OperationStateError(
                f"Operation marker {self.path} belongs to {current.operation_id}, "
                f"not {operation_id}"
            )
        return current

    def _write(self, record: OperationRecord) -> None:
        if len((json.dumps(record.to_dict(), indent=2) + '\n').encode('utf-8')) > 1024 * 1024:
            raise OperationStateError('Operation marker exceeds 1 MiB')
        write_json_atomic(self.path, record.to_dict(), mode=0o600, sort_keys=True)
