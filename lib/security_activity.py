"""Track managed setup windows so audit notifications can exclude expected changes."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

from lib.atomic_io import write_json_atomic
from lib.machine_state import STATE_DIR
from lib.operation_state import OperationRecord
from lib.types import JSONDict
from lib.validation import validate_filesystem_path


SETUP_ACTIVITY_FILE = os.path.join(STATE_DIR, "security-setup-activity.json")
_ACTIVITY_SCHEMA_VERSION = 1
_MAX_ACTIVE_WINDOW = timedelta(hours=24)


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def record_setup_activity(record: OperationRecord, status: str) -> None:
    """Record the bounds of an basaltwater setup that may change audited files."""
    if status not in {"in_progress", "succeeded", "failed"}:
        raise ValueError(f"Unsupported setup activity status: {status}")
    timestamp = _timestamp()
    started_at = timestamp
    if status != "in_progress":
        try:
            with open(SETUP_ACTIVITY_FILE, encoding="utf-8") as file_obj:
                existing = json.load(file_obj)
        except (OSError, json.JSONDecodeError):
            existing = None
        if (
            isinstance(existing, dict)
            and existing.get("operation_id") == record.operation_id
            and isinstance(existing.get("started_at"), str)
        ):
            started_at = existing["started_at"]
    payload: JSONDict = {
        "schema_version": _ACTIVITY_SCHEMA_VERSION,
        "operation_id": record.operation_id,
        "operation_type": record.operation_type,
        "status": status,
        "started_at": started_at,
        "updated_at": timestamp,
    }
    if status != "in_progress":
        payload["finished_at"] = payload["updated_at"]
    validate_filesystem_path(SETUP_ACTIVITY_FILE, must_exist=False)
    write_json_atomic(SETUP_ACTIVITY_FILE, payload, mode=0o600, sort_keys=True)


def _parse_local_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


def managed_setup_audit_window(
    since: datetime,
    now: datetime,
) -> tuple[datetime, datetime] | None:
    """Return an overlapping, bounded managed-setup audit exclusion window."""
    try:
        if os.path.islink(SETUP_ACTIVITY_FILE):
            return None
        with open(SETUP_ACTIVITY_FILE, encoding="utf-8") as file_obj:
            payload = json.load(file_obj)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("schema_version") != _ACTIVITY_SCHEMA_VERSION:
        return None
    if payload.get("operation_type") != "target_setup":
        return None

    started_at = _parse_local_timestamp(payload.get("started_at"))
    if (
        started_at is None
        or started_at > now + timedelta(seconds=5)
        or started_at < now - _MAX_ACTIVE_WINDOW
    ):
        return None
    status = payload.get("status")
    if status == "in_progress":
        cutoff = now
    elif status in {"succeeded", "failed"}:
        cutoff = _parse_local_timestamp(payload.get("finished_at"))
        if cutoff is None or cutoff > now + timedelta(seconds=5):
            return None
    else:
        return None
    if cutoff < since:
        return None
    return started_at, cutoff
