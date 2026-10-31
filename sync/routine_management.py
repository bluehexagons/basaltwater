"""Fresh, serialized storage declaration changes and selected guarded execution."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
import secrets
import shutil
from types import SimpleNamespace

from lib.atomic_io import ensure_directory_durable, write_json_atomic
from lib.machine_state import SETUP_CONFIG_FILE, STATE_DIR, can_manage_system_services, load_setup_config
from lib.operation_state import OperationStateStore
from lib.privilege_policy import protected_path
from lib.runtime_config import RuntimeConfig
from lib.storage_controls import CONTROLS_FILE, load_paused
from lib.storage_routines import SPEC_FIELDS, configured_routines, routine_identity, validate_routine_action
from sync.service_tools.storage_ops import (
    LOCK_FILE, STATE_FILE, OperationLock, execute_storage_operations,
    get_parity_op_id, get_scrub_op_id, get_sync_op_id, load_last_run,
)
from sync.storage_ops_steps import create_storage_ops_service


class RoutineRejected(ValueError):
    """A fixed public reason safe to return without private tool output."""


def _identity(path: str) -> tuple[int, ...]:
    protected_path(path)
    info = os.stat(path, follow_symlinks=False)
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _revision() -> str:
    controls = _identity(CONTROLS_FILE) if os.path.lexists(CONTROLS_FILE) else None
    return hashlib.sha256(json.dumps([_identity(SETUP_CONFIG_FILE), controls]).encode()).hexdigest()


@contextmanager
def _owner():
    protected_path(STATE_DIR, directory=True)
    marker = STATE_DIR + "/setup-operation.json"
    for path in (marker, marker + ".lock"):
        if os.path.lexists(path):
            protected_path(path)
    with OperationStateStore(marker).ownership() as store:
        if store.load() is not None:
            raise RoutineRejected("setup_recovery_required")
        stamp = _identity(SETUP_CONFIG_FILE)
        saved = load_setup_config()
        if saved is None or stamp != _identity(SETUP_CONFIG_FILE):
            raise RoutineRejected("review_changed")
        yield store, saved


def collect_routine_snapshot(saved: dict) -> dict:
    """Only local config/pause metadata, never NAS paths or private reports."""
    try:
        with _owner() as (_store, fresh):
            if fresh != saved:
                raise RoutineRejected("review_changed")
            revision = _revision()
            rows = configured_routines(saved, load_paused())
            history = _history(saved, rows)
            if revision != _revision():
                raise RoutineRejected("review_changed")
            return {"available": True, "revision": revision, "routines": rows, "history": history}
    except (OSError, ValueError, RuntimeError):
        return {"available": False}


def _history(saved: dict, rows: list[dict]) -> dict:
    try:
        if os.path.lexists(STATE_FILE):
            protected_path(STATE_FILE)
        state = load_last_run()
        attempts, result = state.get("_attempts", {}), {}
        def retry(op_id):
            entry = attempts.get(op_id)
            return {key: entry[key] for key in ("failed_at", "retry_after", "failures")} if entry else None
        for row in rows:
            kind = row["kind"]
            identifier = routine_identity(kind, row["spec"])
            raw = next(spec for spec in saved[SPEC_FIELDS[kind]] if routine_identity(kind, spec) == identifier)
            op_id = (get_scrub_op_id if kind == "scrub" else get_sync_op_id)(*raw[:2])
            parity_id = get_parity_op_id(*raw[:2]) if kind == "scrub" else None
            result[identifier] = {"cadence_at": state.get(op_id), "retry": retry(op_id),
                                  "parity_at": state.get(parity_id), "parity_retry": retry(parity_id)}
        return {"available": True, "routines": result}
    except (OSError, ValueError, RuntimeError):
        return {"available": False}


def _proposal(saved: dict, value: dict) -> tuple[dict, set[str]]:
    before, after = value["before"], value["after"]
    updated = dict(saved)
    # Preserve untouched declarations verbatim, including relative scrub
    # database spelling used by their existing cadence/history keys.
    if before is None or after is None or before["spec"] != after["spec"]:
        kind = (after or before)["kind"]
        field = SPEC_FIELDS[kind]
        specs = list(saved.get(field) or [])
        if before is None:
            specs.append(after["spec"])
        else:
            identifier = routine_identity(before["kind"], before["spec"])
            index = next(index for index, spec in enumerate(specs) if routine_identity(kind, spec) == identifier)
            if after is None:
                specs.pop(index)
            else:
                specs[index] = after["spec"]
        updated[field] = specs or None
    paused = load_paused()
    if before is not None:
        paused.discard(routine_identity(before["kind"], before["spec"]))
    if after is not None and after["paused"]:
        paused.add(routine_identity(after["kind"], after["spec"]))
    rows = configured_routines(updated, paused)
    paused.intersection_update(routine_identity(row["kind"], row["spec"]) for row in rows)
    # Validate the fresh target's mount declarations without touching storage.
    RuntimeConfig.from_dict(updated)
    return updated, paused


def _require_tools(row: dict) -> None:
    needs = {"par2"} if row["kind"] == "scrub" else {"rsync", "unshare"}
    if any(shutil.which(program, path="/usr/sbin:/usr/bin:/sbin:/bin") is None for program in needs):
        raise RoutineRejected("tools_unavailable")


def _save(store, saved: dict, value: dict) -> None:
    updated, paused = _proposal(saved, value)
    if len((json.dumps(updated, indent=2) + "\n").encode()) > 1024 * 1024:
        raise ValueError("Updated setup exceeds its read limit")
    after, before = value["after"], value["before"]
    if after is not None and not after["paused"] and (
        before is None or before["paused"] or before["spec"] != after["spec"]
    ):
        _require_tools(after)
    parent = os.path.dirname(CONTROLS_FILE)
    if not os.path.lexists(parent):
        protected_path(os.path.dirname(parent), directory=True)
        ensure_directory_durable(parent)
    protected_path(parent, directory=True)
    record = store.begin("storage_routines", "localhost", "saving", context={"routine": value})
    try:
        if not secrets.compare_digest(value["revision"], _revision()):
            raise RoutineRejected("review_changed")
        write_json_atomic(CONTROLS_FILE, {"version": 1, "paused": sorted(paused)}, mode=0o600, uid=0, gid=0)
        if updated != saved:
            write_json_atomic(SETUP_CONFIG_FILE, updated, mode=0o600, uid=0, gid=0)
        store.transition(record.operation_id, "activating_timer")
        # Install/refresh only fixed service units. Never call setup's initial
        # sync or parity builders here, and never start storage-ops.service.
        create_storage_ops_service(SimpleNamespace(**{
            field: updated.get(field) or [] for field in SPEC_FIELDS.values()}))
        store.complete(record.operation_id)
    except BaseException as exc:
        try:
            current = store.load()
            if current is not None:
                store.transition(record.operation_id, current.phase, status="recovery_required",
                                 context={**record.context, "error_type": type(exc).__name__})
        except (OSError, ValueError):
            pass
        raise


def apply_routine_action(value: dict) -> str:
    value = validate_routine_action(value)
    if os.geteuid() != 0 or not can_manage_system_services():
        raise PermissionError("Storage routine management requires a supported root service")
    with _owner() as (store, saved):
        if not secrets.compare_digest(value["revision"], _revision()):
            raise RoutineRejected("review_changed")
        rows = configured_routines(saved, load_paused())
        if value["before"] is not None and value["before"] not in rows:
            raise RoutineRejected("review_changed")
        with OperationLock(LOCK_FILE) as storage_lock:
            if not storage_lock.acquire():
                raise RoutineRejected("storage_busy")
            if value["action"] == "change":
                _save(store, saved, value)
                return "succeeded"
            _require_tools(value["before"])
            result = execute_storage_operations(selection=value["before"])
            operations = [*result["syncs"], *result["scrubs"], *result["parity_updates"]]
            if len(operations) != 1:
                raise RoutineRejected("execution_rejected")
            if result["success"]:
                return "warnings" if operations[0].get("warning") else "succeeded"
            if value["before"]["kind"] == "scrub" and operations[0].get("completed"):
                return "findings"
            raise RoutineRejected("routine_failed")
