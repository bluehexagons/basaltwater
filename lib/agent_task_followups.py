"""Operator decisions and exact service prerequisites for inert follow-ups."""

from __future__ import annotations

import fcntl
import os
import stat
from contextlib import contextmanager
from typing import Any

from lib.agent_task_context import private_json, validate_follow_up
from lib.agent_task_policy import task_policy_issues
from lib.agent_tasks import AgentTasks, validate_task
from lib.atomic_io import write_json_atomic
from lib.privilege_client import PrivilegeRequestRejected, exchange


def follow_up_requires_review(proposal: dict[str, Any]) -> bool:
    """Older proposals need an explicit access review, not a synthetic blocker."""
    return not {"writable_directories", "operator_requirements", "service_restarts"} <= set(proposal)


def follow_up_state(manager: AgentTasks, identifier: str, run_id: str) -> dict[str, Any]:
    """Read only local decision data; list rendering never polls the broker."""
    manager.contexts.receipt(identifier, run_id)  # Validate IDs and provenance first.
    path = manager.contexts.directory(identifier) / "runs" / (run_id + ".decision.json")
    try:
        value = private_json(path)
    except FileNotFoundError:
        return {"status": "pending", "requests": {}}
    if value.get("status") not in {"pending", "approved", "blocked", "draft"} or not isinstance(value.get("requests"), dict):
        raise RuntimeError("Invalid follow-up decision record")
    return value


def prepare_follow_up(manager: AgentTasks, identifier: str, run_id: str, *, review: bool = False) -> dict[str, Any]:
    receipt = manager.contexts.receipt(identifier, run_id)
    proposal = validate_follow_up(receipt.get("follow_up"))
    if proposal is None:
        raise ValueError("This run did not request a follow-up")
    settings = {key: value for key, value in proposal.items() if key not in {"reason", "service_restarts"}}
    settings.update(interval="once", max_runs=1, source_task_id=identifier, source_run_id=run_id)
    settings["prompt"] = (f"Follow-up to task {identifier}, run {run_id}. "
        "Recheck retained evidence; verify completed service prerequisites without repeating them.\n\n" + settings["prompt"])
    if review:
        # Receipt validation establishes the proposal's provenance and bounds.
        # Display invalid grants for correction; only submission validates them
        # as executable settings, after the operator has edited the form.
        defaults = validate_task({"title": proposal["title"], "prompt": settings["prompt"],
                                  "directory": manager.home, "mode": "inspect"}, manager.home)
        return {**defaults, **settings, "service_restarts": proposal.get("service_restarts", [])}
    return {**validate_task(settings, manager.home), "service_restarts": proposal.get("service_restarts", [])}


@contextmanager
def _decision_lock(manager: AgentTasks, identifier: str):
    path = manager.contexts.directory(identifier) / "follow-up.lock"
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077 or info.st_nlink != 1:
            raise RuntimeError("Unsafe follow-up lock")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def decide_follow_up(manager: AgentTasks, identifier: str, run_id: str, decision: str, *,
                     settings: dict[str, Any] | None = None, run_now: bool = True, draft: bool = False) -> str | None:
    """A click authorizes one task, never a sandbox bypass or broker approval.

    Service prerequisites execute via the separately authenticated broker before
    provider launch. Interrupted request submission is never blindly retried.
    """
    if decision not in {"approve", "block", "request-privilege"}:
        raise ValueError("Invalid follow-up decision")
    if decision == "block":
        if not isinstance(manager.contexts.receipt(identifier, run_id).get("follow_up"), dict):
            raise ValueError("This run did not request a follow-up")
        prepared = {}
    else:
        prepared = prepare_follow_up(manager, identifier, run_id, review=settings is not None)
    with _decision_lock(manager, identifier):
        state = follow_up_state(manager, identifier, run_id)
        if state["status"] in {"approved", "draft"}:
            if decision == "approve":
                return state["task_id"]  # Refresh/double-click is idempotent.
            raise ValueError("This proposal was already submitted; control its saved task instead")
        if state["status"] == "blocked":
            raise ValueError("This follow-up was blocked by the operator")
        path = manager.contexts.directory(identifier) / "runs" / (run_id + ".decision.json")
        if decision == "block":
            state["status"] = "blocked"
            write_json_atomic(str(path), state)
            for request in state["requests"].values():
                if request.get("id"):
                    try:
                        reply = exchange({"action": "cancel", "id": request["id"]})
                        request["state"] = reply["state"]
                    except (OSError, ValueError, RuntimeError):
                        request["state"] = "cancellation-unconfirmed"
            write_json_atomic(str(path), state)
            return None
        candidate = dict(prepared if settings is None else settings)
        restarts = candidate.pop("service_restarts", [])
        # Use the same exact-unit validation for edited reviews as reports.
        original = manager.contexts.receipt(identifier, run_id)["follow_up"]
        validate_follow_up({**original, "service_restarts": restarts})
        if decision == "approve" and settings is None and follow_up_requires_review(original):
            raise ValueError("Review this older proposal before approval. Open Review follow-up task, "
                             "check its access and prerequisites, then select Approve once.")
        if decision == "request-privilege":
            if not restarts:
                raise ValueError("No service restarts were requested")
            for unit in dict.fromkeys(restarts):
                previous = state["requests"].get(unit)
                if previous:
                    if not previous.get("id"):
                        raise ValueError("Service submission outcome is unknown. Review existing requests in the separate privilege portal before requesting again: " + unit)
                    reply = exchange({"action": "status", "id": previous["id"]})
                    previous["state"] = reply.get("state", "unknown")
                    write_json_atomic(str(path), state)
                    if reply.get("state") not in {"failed", "expired", "denied", "cancelled", "invalidated"}:
                        continue
                state["requests"][unit] = {"state": "submission-uncertain"}
                write_json_atomic(str(path), state)
                try:
                    reply = exchange({"action": "request", "operation": "service.restart", "parameters": {"unit": unit},
                                      "reason": "Task follow-up: " + prepared["title"]})
                except (FileNotFoundError, ConnectionRefusedError, PrivilegeRequestRejected):
                    del state["requests"][unit]
                    write_json_atomic(str(path), state)
                    raise
                state["requests"][unit] = {key: reply[key] for key in ("id", "state", "review_url") if key in reply}
                write_json_atomic(str(path), state)
            return None
        candidate.update(interval="once", continue_until_done=False, max_runs=1, start_at=0,
                         source_task_id=identifier, source_run_id=run_id)
        task = validate_task(candidate, manager.home)
        if not draft:
            issues = task_policy_issues(task) + task["operator_requirements"]
            if issues:
                raise ValueError("Resolve prerequisites before approval: " + "; ".join(issues))
        # Drafts must not lose these prerequisites and later bypass the check
        # through the ordinary Run control.
        for unit in dict.fromkeys(restarts):
            request = state["requests"].get(unit, {})
            if "id" not in request:
                raise ValueError("Request separate service approval before launching: " + unit)
            reply = exchange({"action": "status", "id": request["id"]})
            request["state"] = reply.get("state", "unknown")
            write_json_atomic(str(path), state)
            plan = reply.get("plan", {})
            if (reply.get("state") != "succeeded" or plan.get("operation") != "service.restart"
                    or plan.get("parameters") != {"unit": unit}):
                raise ValueError("Service prerequisite has not succeeded: " + unit)
        child = manager.create(task, run_now=run_now, draft=draft,
                               request_key="follow-up-" + identifier + "-" + run_id)
        state.update(status="draft" if draft else "approved", task_id=child)
        write_json_atomic(str(path), state)
        return child
