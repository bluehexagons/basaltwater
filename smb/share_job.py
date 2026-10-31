#!/usr/bin/env python3
"""Supervise an approved share change independently of the browser process."""

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
from lib.machine_state import STATE_DIR, can_manage_system_services
from lib.operation_state import OperationStateStore
from lib.privilege_execution import ENVIRONMENT
from lib.privilege_policy import protected_path
from lib.samba_accounts import HEX_ID
from lib.samba_shares import SHARE_STATE, SHARE_UNIT, share_name, validate_share_action
from smb.share_management import apply_reviewed_share_update

RESULTS = {"running", "succeeded", "failed", "interrupted"}


def _payload_path(job_id: str) -> str:
    if not isinstance(job_id, str) or not HEX_ID.fullmatch(job_id):
        raise ValueError("Invalid share job identity")
    return os.path.join(os.path.dirname(SHARE_STATE), "samba-payload-" + job_id + ".json")


def stage_share_job(parameters: dict) -> None:
    parameters = validate_share_action(parameters)
    path = _payload_path(parameters["job_id"])
    protected_path(os.path.dirname(path), directory=True)
    if os.path.lexists(path):
        raise ValueError("Share job payload exists; inspect before retrying")
    write_json_atomic(path, parameters, mode=0o600)


def consume_share_job(job_id: str) -> dict:
    path = _payload_path(job_id)
    protected_path(path)
    parameters = validate_share_action(read_json_file(path, max_bytes=16384))
    if parameters["job_id"] != job_id:
        raise ValueError("Share payload identity mismatch")
    remove_file_durable(path)
    return parameters


def _record() -> dict:
    protected_path(os.path.dirname(SHARE_STATE), directory=True)
    try:
        os.lstat(SHARE_STATE)
    except FileNotFoundError:
        return {}
    protected_path(SHARE_STATE)
    value = read_json_file(SHARE_STATE, max_bytes=16384)
    if (not isinstance(value, dict) or set(value) != {"parameters", "status", "started", "finished"}
            or not isinstance(value["status"], str) or value["status"] not in RESULTS
            or type(value["started"]) not in {int, float} or not math.isfinite(value["started"])
            or value["finished"] is not None and (type(value["finished"]) not in {int, float} or not math.isfinite(value["finished"]))):
        raise ValueError("Invalid share job result")
    validate_share_action(value["parameters"])
    return value


def job_status() -> dict:
    """Observe fixed local metadata only; do not inspect accounts or shares."""
    try:
        before = _record()
        result = subprocess.run(["/usr/bin/systemctl", "show", SHARE_UNIT, "--property=ActiveState", "--value"],
                                capture_output=True, text=True, timeout=2, check=False, env=ENVIRONMENT)
        unit = result.stdout.strip()
        if result.returncode or unit not in {"active", "activating", "deactivating", "inactive", "failed"}:
            raise ValueError("Share unit status unavailable")
        after = _record()
        if before.get("parameters", {}).get("job_id") != after.get("parameters", {}).get("job_id"):
            raise ValueError("Share job changed during observation")
        latest = {}
        if after:
            latest = {key: after[key] for key in ("status", "started", "finished")}
            latest.update(job_id=after["parameters"]["job_id"], name=share_name(after["parameters"]))
            if latest["status"] == "running" and unit in {"inactive", "failed"}:
                latest["status"] = "interrupted"
        protected_path(STATE_DIR, directory=True)
        marker = STATE_DIR + "/setup-operation.json"
        if os.path.lexists(marker):
            protected_path(marker)
        blocked = OperationStateStore(marker).load() is not None
        return {"available": can_manage_system_services(), "unit_state": unit, "latest": latest, "blocked": blocked}
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        return {"available": False, "unit_state": "unavailable", "latest": {}, "blocked": True}


def run_action(parameters: dict) -> int:
    parameters = validate_share_action(parameters)
    if os.geteuid() != 0 or not can_manage_system_services():
        raise PermissionError("Share jobs require a root service on a supported host")
    protected_path(os.path.dirname(SHARE_STATE), directory=True)
    lock_path = SHARE_STATE + ".lock"
    if os.path.lexists(lock_path):
        protected_path(lock_path)
    with open(lock_path, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if _record().get("parameters", {}).get("job_id") == parameters["job_id"]:
            raise ValueError("This share job was already claimed")
        value = {"parameters": parameters, "status": "running", "started": time.time(), "finished": None}
        write_json_atomic(SHARE_STATE, value, mode=0o600)
        try:
            apply_reviewed_share_update(parameters)
            value["status"] = "succeeded"
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            value["status"] = "failed"
        except BaseException:
            value["status"] = "interrupted"
            raise
        finally:
            value["finished"] = time.time()
            write_json_atomic(SHARE_STATE, value, mode=0o600)
        return 0 if value["status"] == "succeeded" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job_id")
    args = parser.parse_args()
    os.umask(0o077)
    def interrupted(_signum, _frame):
        raise SystemExit(124)
    signal.signal(signal.SIGTERM, interrupted)
    return run_action(consume_share_job(args.job_id))


if __name__ == "__main__":
    raise SystemExit(main())
