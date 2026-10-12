"""Fixed maintenance diagnostics shared by the broker, CLI and panel."""

from __future__ import annotations

import math
import re

from lib.admin_actions import ADMIN_ACTIONS

JOB_ID_ENV = "BASALTWATER_MAINTENANCE_JOB_ID"
STAGE_LABELS = {"preflight": "Preflight", "source_verification": "Source verification", "command": "Command"}
FAILURE_LABELS = {
    "action_unavailable": "Action unavailable", "source_rejected": "Source rejected",
    "command_timeout": "Command timed out", "execution_unavailable": "Execution unavailable",
    "execution_rejected": "Execution rejected", "nonzero_exit": "Command exited unsuccessfully",
    "command_signal": "Command terminated by signal", "job_interrupted": "Job interrupted",
}
ERROR_TYPES = frozenset(("RuntimeError", "ValueError", "TypeError", "KeyError", "OSError",
    "PermissionError", "FileNotFoundError", "FileExistsError", "TimeoutError", "ConnectionError",
    "CalledProcessError", "CommandExecutionError", "OperationStateError"))
UNIT_STATES = frozenset(("active", "activating", "deactivating", "inactive", "failed", "unavailable"))
JOB_STATES = frozenset(("running", "succeeded", "failed", "interrupted", "unavailable"))
REQUEST_STATES = frozenset(("pending", "approved", "executing", "dispatched", "succeeded", "failed",
                           "uncertain", "expired", "denied", "invalidated", "cancelled"))


def valid_job_id(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{32}", value) is not None


def _index(value: object, maximum: int) -> int | None:
    return value if type(value) is int and 1 <= value <= maximum else None


def setup_details(value: object, job_id: object) -> dict | None:
    """Expose only a validated marker snapshot belonging to this exact job."""
    if (not isinstance(value, dict) or not valid_job_id(job_id) or value.get("job_id") != job_id
            or value.get("phase") not in ("applying", "finalizing", "recovery")):
        return None
    index, count = _index(value.get("step_index"), 4096), _index(value.get("step_count"), 4096)
    if (value.get("step_index") is not None and index is None
            or value.get("step_count") is not None and count is None
            or index is not None and count is not None and index > count):
        return None
    return {"job_id": job_id, "phase": value["phase"],
            "setup_stage": value.get("setup_stage") if value.get("setup_stage") in ("pre_steps", "steps") else None,
            "step_index": index, "step_count": count,
            "error_type": value.get("error_type") if isinstance(value.get("error_type"), str)
            and value["error_type"] in ERROR_TYPES else None}


def job_diagnostics(value: dict) -> dict:
    job_id = value.get("job_id") if valid_job_id(value.get("job_id")) else None
    return {"job_id": job_id,
            "stage": value.get("stage") if isinstance(value.get("stage"), str) and value["stage"] in STAGE_LABELS else None,
            "command_index": _index(value.get("command_index"), 32),
            "failure": value.get("failure") if isinstance(value.get("failure"), str) and value["failure"] in FAILURE_LABELS else None,
            "setup_context": setup_details(value.get("setup_context"), job_id)}


def sanitize_admin_status(value: object) -> dict:
    """Discard output, configuration, exception text and unknown reader fields."""
    if not isinstance(value, dict):
        raise ValueError("Invalid maintenance status")
    def timestamp(item: object) -> int | float | None:
        return item if type(item) in (int, float) and 0 <= item <= 10**12 and math.isfinite(item) else None
    latest = value.get("latest")
    if not latest:
        result = {}
    elif not isinstance(latest, dict) or latest.get("action") not in tuple(ADMIN_ACTIONS) or latest.get("status") not in tuple(JOB_STATES):
        result = {"status": "unavailable"}
    else:
        result = {"action": latest["action"], "status": latest["status"],
                  "started": timestamp(latest.get("started")), "finished": timestamp(latest.get("finished")),
                  "exit_code": latest.get("exit_code") if type(latest.get("exit_code")) is int else None,
                  **job_diagnostics(latest)}
    requests = []
    for row in value.get("requests", [])[:20] if isinstance(value.get("requests"), list) else []:
        if isinstance(row, dict) and valid_job_id(row.get("id")) and row.get("action") in tuple(ADMIN_ACTIONS):
            requests.append({"id": row["id"], "action": row["action"], "created": timestamp(row.get("created")),
                             "state": row.get("state") if row.get("state") in tuple(REQUEST_STATES) else "unavailable"})
    blocked = value.get("blocked") if isinstance(value.get("blocked"), dict) else {}
    return {"unit_state": value.get("unit_state") if value.get("unit_state") in tuple(UNIT_STATES) else "unavailable",
            "latest": result, "requests": requests, "blocked_actions": [name for name in ADMIN_ACTIONS if name in blocked]}
