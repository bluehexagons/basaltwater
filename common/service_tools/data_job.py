#!/usr/bin/env python3
"""Supervised, approved single-file recovery independent of the web panel."""

from __future__ import annotations

import argparse
from collections.abc import Iterator
from contextlib import contextmanager
import fcntl
import os
import secrets
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

if __name__ == "__main__" and not __package__:
    sys.path.insert(0, "/opt/basaltwater")

from lib.atomic_io import read_json_file, remove_file_durable, write_json_atomic
from lib.data_actions import DATA_STATE, DATA_UNIT, HEX_ID, finding_revision, validate_data_action
from lib.machine_state import PERSISTENT_STATE_DIR as STATE_DIR, can_manage_system_services, load_setup_config
from lib.operation_state import OperationStateStore
from lib.privilege_execution import ENVIRONMENT
from lib.privilege_policy import protected_path
from lib.runtime_config import RuntimeConfig
from sync.service_tools.scrub_findings import ADVICE, Findings, file_identity
from sync.service_tools.scrub_manage import execute, selected_jobs
from sync.service_tools.storage_ops import LOCK_FILE, OperationLock

RESULTS = {"running", "succeeded", "findings", "failed", "interrupted"}
FAILURES = {"", "storage_busy", "setup_recovery_required", "finding_changed", "file_changed", "identity_unavailable", "execution_rejected", "job_interrupted"}


class DataJobRejected(ValueError):
    """A safe, fixed reason for refusing a stale or conflicting operation."""


def _setup_blocked() -> bool:
    try:
        os.lstat(STATE_DIR + "/setup-operation.json")
    except FileNotFoundError:
        return False
    return True


@contextmanager
def _setup_owner() -> Iterator[None]:
    protected_path(STATE_DIR, directory=True)
    marker = STATE_DIR + "/setup-operation.json"
    for path in (marker, marker + ".lock"):
        if os.path.lexists(path):
            protected_path(path)
    with OperationStateStore(marker).ownership() as owner:
        if owner.load() is not None:
            raise DataJobRejected("setup_recovery_required")
        yield


def _payload_path(job_id: str) -> str:
    if not isinstance(job_id, str) or not HEX_ID.fullmatch(job_id):
        raise ValueError("Invalid data job identity")
    return os.path.join(os.path.dirname(DATA_STATE), "data-payload-" + job_id + ".json")


def stage_data_job(parameters: dict) -> None:
    """Stage only an already-claimed plan; user paths never enter systemd argv."""
    parameters = validate_data_action(parameters)
    path = _payload_path(parameters["job_id"])
    protected_path(os.path.dirname(path), directory=True)
    try:
        os.lstat(path)
    except FileNotFoundError:
        pass
    else:
        raise ValueError("Data job payload already exists; inspect before retrying")
    write_json_atomic(path, parameters, mode=0o600)


def consume_data_job(job_id: str) -> dict:
    path = _payload_path(job_id)
    protected_path(path)
    parameters = validate_data_action(read_json_file(path, max_bytes=16384))
    if parameters["job_id"] != job_id:
        raise ValueError("Data job payload identity mismatch")
    remove_file_durable(path)
    return parameters


def _unit_state() -> str:
    try:
        result = subprocess.run(["/usr/bin/systemctl", "show", DATA_UNIT, "--property=ActiveState", "--value"],
                                capture_output=True, text=True, timeout=2, check=False, env=ENVIRONMENT)
        if result.returncode == 0 and result.stdout.strip() in {"active", "activating", "deactivating", "inactive", "failed"}:
            return result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return "unavailable"


def _record() -> dict:
    protected_path(os.path.dirname(DATA_STATE), directory=True)
    try:
        os.lstat(DATA_STATE)
    except FileNotFoundError:
        return {}
    protected_path(DATA_STATE)
    value = read_json_file(DATA_STATE, max_bytes=16384)
    if not isinstance(value, dict) or set(value) != {"parameters", "status", "category", "failure", "started", "finished"}:
        raise ValueError("Invalid data job result")
    validate_data_action(value["parameters"])
    if (value["status"] not in RESULTS or value["category"] not in {"", "healthy", *ADVICE}
            or value["failure"] not in FAILURES
            or type(value["started"]) not in {int, float}
            or value["finished"] is not None and type(value["finished"]) not in {int, float}):
        raise ValueError("Invalid data job metadata")
    return value


def job_status() -> dict:
    """Read only fixed local metadata and unit state, never NAS files or evidence."""
    try:
        before = _record()
        active = _unit_state()
        after = _record()
        if before.get("parameters", {}).get("job_id") != after.get("parameters", {}).get("job_id"):
            raise ValueError("Data job changed during observation")
        if after.get("status") == "running" and active in {"inactive", "failed"}:
            after.update(status="interrupted", failure="job_interrupted")
        return {"unit_state": active, "latest": after,
                "blocked": _setup_blocked(),
                "available": active != "unavailable" and can_manage_system_services()}
    except (OSError, ValueError, RuntimeError, TypeError):
        return {"unit_state": "unavailable", "latest": {}, "blocked": True, "available": False}


def recover(parameters: dict, saved: dict) -> tuple[dict, int]:
    """Match a configured job, reject stale evidence, then use the CLI's guards."""
    parameters = validate_data_action(parameters)
    config = RuntimeConfig.from_dict(saved)
    args = SimpleNamespace(action=parameters["action"], directory=parameters["directory"],
                           database=parameters["database"], file=parameters["file"],
                           backup=parameters["backup"] or None, yes=True)
    jobs = selected_jobs(config, args)
    directory, database, _redundancy = jobs[0]
    report = Findings(directory, database)
    relative = os.path.relpath(parameters["file"], directory)
    entry = report.data["files"].get(relative, {})
    if entry.get("state") != "open" or not secrets.compare_digest(finding_revision(entry), parameters["finding"]):
        raise DataJobRejected("finding_changed")
    if parameters["action"] != "verify":
        if "file_identity" not in entry:
            raise DataJobRejected("identity_unavailable")
        if entry["file_identity"] != file_identity(parameters["file"]):
            raise DataJobRejected("file_changed")
        args.reviewed_identity = entry["file_identity"]
    return execute(args, config)


def run_action(parameters: dict) -> int:
    parameters = validate_data_action(parameters)
    if os.geteuid() != 0 or not can_manage_system_services():
        raise PermissionError("Data jobs require a root service on a supported host")
    protected_path(os.path.dirname(DATA_STATE), directory=True)
    lock_path = DATA_STATE + ".lock"
    if os.path.lexists(lock_path):
        protected_path(lock_path)
    with open(lock_path, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        previous = _record()
        if previous.get("parameters", {}).get("job_id") == parameters["job_id"]:
            raise ValueError("This data job was already claimed")
        if os.path.lexists(DATA_STATE):
            protected_path(DATA_STATE)
        value = {"parameters": parameters, "status": "running", "started": time.time(),
                 "finished": None, "category": "", "failure": ""}
        write_json_atomic(DATA_STATE, value, mode=0o600)
        try:
            # Match routine edits: setup ownership precedes the storage lock,
            # and spans fresh configuration loading through the entire recovery.
            with _setup_owner():
                with OperationLock(LOCK_FILE) as storage_lock:
                    if not storage_lock.acquire():
                        raise DataJobRejected("storage_busy")
                    saved = load_setup_config()
                    if saved is None:
                        raise ValueError("Saved setup unavailable")
                    result, code = recover(parameters, saved)
            category = result.get("category")
            if category not in {"healthy", *ADVICE}:
                raise ValueError("Unexpected recovery result")
            value.update(status="succeeded" if code == 0 and category == "healthy" else "findings", category=category)
        except DataJobRejected as exc:
            value.update(status="failed", failure=str(exc))
        except (OSError, ValueError, RuntimeError):
            value.update(status="failed", failure="execution_rejected")
        except BaseException:
            value.update(status="interrupted", failure="job_interrupted")
            raise
        finally:
            value["finished"] = time.time()
            write_json_atomic(DATA_STATE, value, mode=0o600)
        return 0 if value["status"] == "succeeded" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job_id")
    args = parser.parse_args()
    os.umask(0o077)
    def interrupted(_signum, _frame):
        raise SystemExit(1)
    signal.signal(signal.SIGTERM, interrupted)
    return run_action(consume_data_job(args.job_id))


if __name__ == "__main__":
    raise SystemExit(main())
