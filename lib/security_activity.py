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
_MAX_HISTORY = 63  # Together with the current attempt, retain at most 64 windows.


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def record_setup_activity(record: OperationRecord, status: str) -> None:
    """Keep bounded recent setup attempts, including repeated runs and failures."""
    if status not in {"in_progress", "succeeded", "failed"}:
        raise ValueError(f"Unsupported setup activity status: {status}")
    timestamp = _timestamp()
    started_at = timestamp
    try:
        with open(SETUP_ACTIVITY_FILE, encoding="utf-8") as file_obj:
            existing = json.load(file_obj)
    except (OSError, json.JSONDecodeError):
        existing = None
    history = []
    if isinstance(existing, dict):
        prior_history = existing.get("history", [])
        if isinstance(prior_history, list):
            history = [entry for entry in prior_history if isinstance(entry, dict)]
        if existing.get("operation_id") != record.operation_id or status == "in_progress":
            history.append({key: value for key, value in existing.items() if key != "history"})
    local_now = _parse_local_timestamp(timestamp)
    assert local_now is not None
    history = [
        entry for entry in history
        if _activity_window(entry, local_now - _MAX_ACTIVE_WINDOW, local_now) is not None
    ][-_MAX_HISTORY:]
    if status != "in_progress":
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
        "history": history,
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


def managed_setup_audit_windows(
    since: datetime,
    now: datetime,
) -> list[tuple[datetime, datetime]]:
    """Return separate recent setup windows without suppressing gaps between runs."""
    try:
        if os.path.islink(SETUP_ACTIVITY_FILE):
            return []
        with open(SETUP_ACTIVITY_FILE, encoding="utf-8") as file_obj:
            payload = json.load(file_obj)
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict):
        return []
    history = payload.get("history", [])
    entries = history[-_MAX_HISTORY:] if isinstance(history, list) else []
    return [
        window for entry in [*entries, payload]
        if isinstance(entry, dict)
        and (window := _activity_window(entry, since, now)) is not None
    ]


def _activity_window(
    payload: dict[str, object], since: datetime, now: datetime,
) -> tuple[datetime, datetime] | None:
    """Validate one attempt and account for ausearch's second precision."""
    if payload.get("schema_version") != _ACTIVITY_SCHEMA_VERSION:
        return None
    if payload.get("operation_type") != "target_setup":
        return None

    started_at = _parse_local_timestamp(payload.get("started_at"))
    if (
        started_at is None
        or started_at > now + timedelta(seconds=5)
        or started_at < since - _MAX_ACTIVE_WINDOW
    ):
        return None
    status = payload.get("status")
    if status == "in_progress":
        cutoff = min(now, started_at + _MAX_ACTIVE_WINDOW)
    elif status in {"succeeded", "failed"}:
        cutoff = _parse_local_timestamp(payload.get("finished_at"))
        if cutoff is None or cutoff > now + timedelta(seconds=5):
            return None
    else:
        return None
    if cutoff < since or cutoff < started_at or cutoff - started_at > _MAX_ACTIVE_WINDOW:
        return None
    return started_at.replace(microsecond=0), cutoff.replace(microsecond=0) + timedelta(seconds=1)
