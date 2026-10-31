#!/usr/bin/env python3
"""One-shot, independently approved storage routine worker and public result."""

from __future__ import annotations

import argparse
import fcntl
import math
import os
import signal
import subprocess
import sys
import time

if __name__ == "__main__" and not __package__:
    sys.path.insert(0, "/opt/basaltwater")

from lib.atomic_io import read_json_file, remove_file_durable, write_json_atomic
from lib.machine_state import PERSISTENT_STATE_DIR as STATE_DIR, can_manage_system_services
from lib.privilege_execution import ENVIRONMENT
from lib.privilege_policy import protected_path
from lib.samba_accounts import HEX_ID
from lib.storage_routines import ROUTINE_STATE, ROUTINE_UNIT, validate_routine_action
from sync.routine_management import RoutineRejected, apply_routine_action

RESULTS = {"running", "succeeded", "warnings", "findings", "failed", "interrupted"}
FAILURES = {"", "storage_busy", "setup_recovery_required", "review_changed", "tools_unavailable",
            "execution_rejected", "routine_failed", "job_interrupted"}


def _payload(job_id: str) -> str:
    if not isinstance(job_id, str) or not HEX_ID.fullmatch(job_id):
        raise ValueError("Invalid routine job identity")
    return os.path.join(os.path.dirname(ROUTINE_STATE), "routine-payload-" + job_id + ".json")


def stage_routine_job(value: dict) -> None:
    value = validate_routine_action(value)
    path = _payload(value["job_id"])
    protected_path(os.path.dirname(path), directory=True)
    if os.path.lexists(path):
        raise ValueError("Routine payload already exists; inspect without retrying")
    write_json_atomic(path, value, mode=0o600)


def consume_routine_job(job_id: str) -> dict:
    path = _payload(job_id)
    protected_path(path)
    value = validate_routine_action(read_json_file(path, max_bytes=16384))
    if value["job_id"] != job_id:
        raise ValueError("Routine payload identity changed")
    remove_file_durable(path)
    return value


def _unit_state() -> str:
    try:
        result = subprocess.run(["/usr/bin/systemctl", "show", ROUTINE_UNIT, "--property=ActiveState", "--value"],
                                capture_output=True, text=True, timeout=2, check=False, env=ENVIRONMENT)
        if result.returncode == 0 and result.stdout.strip() in {"active", "activating", "deactivating", "inactive", "failed"}:
            return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return "unavailable"


def _record() -> dict:
    protected_path(os.path.dirname(ROUTINE_STATE), directory=True)
    if not os.path.lexists(ROUTINE_STATE):
        return {}
    protected_path(ROUTINE_STATE)
    value = read_json_file(ROUTINE_STATE, max_bytes=4096)
    if (not isinstance(value, dict) or set(value) != {"job_id", "status", "failure", "started", "finished"}
            or not isinstance(value["job_id"], str) or not HEX_ID.fullmatch(value["job_id"])
            or value["status"] not in RESULTS or value["failure"] not in FAILURES
            or any(type(value[key]) not in {int, float} or not math.isfinite(value[key])
                   for key in ("started", "finished") if key == "started" or value[key] is not None)):
        raise ValueError("Invalid routine job result")
    return value


def job_status() -> dict:
    try:
        before = _record()
        active = _unit_state()
        after = _record()
        if before.get("job_id") != after.get("job_id"):
            raise ValueError("Routine job changed during observation")
        if after.get("status") == "running" and active in {"inactive", "failed"}:
            after.update(status="interrupted", failure="job_interrupted")
        return {"unit_state": active, "latest": after,
                "blocked": os.path.lexists(STATE_DIR + "/setup-operation.json"),
                "available": active != "unavailable" and can_manage_system_services()}
    except (OSError, ValueError, RuntimeError, TypeError):
        return {"unit_state": "unavailable", "latest": {}, "blocked": True, "available": False}


def run_action(value: dict) -> int:
    value = validate_routine_action(value)
    if os.geteuid() != 0 or not can_manage_system_services():
        raise PermissionError("Routine jobs require a supported root service")
    protected_path(os.path.dirname(ROUTINE_STATE), directory=True)
    lock_path = ROUTINE_STATE + ".lock"
    if os.path.lexists(lock_path):
        protected_path(lock_path)
    with open(lock_path, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if _record().get("job_id") == value["job_id"]:
            raise ValueError("This routine job was already claimed")
        record = {"job_id": value["job_id"], "status": "running", "failure": "",
                  "started": time.time(), "finished": None}
        write_json_atomic(ROUTINE_STATE, record, mode=0o600)
        try:
            record["status"] = apply_routine_action(value)
            if record["status"] not in {"succeeded", "warnings", "findings"}:
                raise ValueError("Unexpected routine result")
        except RoutineRejected as exc:
            reason = str(exc)
            record.update(status="failed", failure=reason if reason in FAILURES else "execution_rejected")
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            record.update(status="failed", failure="execution_rejected")
        except BaseException:
            record.update(status="interrupted", failure="job_interrupted")
            raise
        finally:
            record["finished"] = time.time()
            write_json_atomic(ROUTINE_STATE, record, mode=0o600)
        return 0 if record["status"] in {"succeeded", "warnings"} else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job_id")
    args = parser.parse_args()
    os.umask(0o077)
    def interrupted(_signum, _frame):
        raise SystemExit(124)
    signal.signal(signal.SIGTERM, interrupted)
    return run_action(consume_routine_job(args.job_id))


if __name__ == "__main__":
    raise SystemExit(main())
