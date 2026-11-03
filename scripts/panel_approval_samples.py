"""Synthetic approval plans shared by development surfaces; no executor."""

from __future__ import annotations

import time
import uuid

from lib.privilege_policy import digest, operation_plan


def admin_preview(action: str) -> dict:
    policy = {"version": 1, "machine": "c" * 32, "origin": "http://127.0.0.1:8765",
              "requester_uid": 1000, "ttl_seconds": 3600, "services": {}, "reboot": "approve", "commands": "approve"}
    plan = operation_plan(policy, 1000, "admin.run", {"action": action})
    return {"plan": plan, "digest": digest(plan)}


def admin_request(action: str, state: str) -> dict:
    plan = admin_preview(action)["plan"]
    now = time.time()
    return {"id": uuid.uuid4().hex, "digest": digest(plan), "state": state, "action": action,
            "created": now, "expires": now + 3600, "plan": plan,
            "reason": "Synthetic administrator request; no host action runs.", "execution": None}


def privilege_request(operation: str, parameters: dict, state: str) -> dict:
    plan = {"machine": "c" * 32, "uid": 1000, "operation": operation, "parameters": parameters,
            "argv": parameters.get("argv", []) if operation == "command.run" else [],
            "effect": "Simulated privilege request. No host commands or background jobs run.",
            "mode": "approve", "policy_digest": "d" * 64}
    now = time.time()
    return {"id": uuid.uuid4().hex, "digest": digest(plan), "state": state, "action": operation,
            "created": now, "expires": now + 3600, "plan": plan,
            "reason": "Synthetic development request.", "execution": None}
