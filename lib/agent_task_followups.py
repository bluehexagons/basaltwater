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
from lib.privilege_policy import load_policy


class ServiceApprovalPending(ValueError):
    """The proposal is valid but needs an operator decision or service result."""


class ServiceApprovalUnavailable(RuntimeError):
    """A broker operation failed while the task review must remain available."""


def _service_status(identifier: str) -> dict[str, Any]:
    try:
        return exchange({"action": "status", "id": identifier})
    except (OSError, ValueError, RuntimeError) as exc:
        raise ServiceApprovalUnavailable("Service approval status is unavailable. Check the existing request in the privilege portal before retrying; no task has been queued.") from exc


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


def _request_services(state: dict[str, Any], path: str, units: list[str], title: str) -> None:
    """Submit explicitly requested actions without replaying uncertain outcomes."""
    for unit in dict.fromkeys(units):
        previous = state["requests"].get(unit)
        if previous:
            if not previous.get("id"):
                raise ServiceApprovalPending("Service submission outcome is unknown. Review existing requests in the separate privilege portal before requesting again: " + unit)
            reply = _service_status(previous["id"])
            previous["state"] = reply.get("state", "unknown")
            write_json_atomic(path, state)
            if reply.get("state") not in {"failed", "expired", "denied", "cancelled", "invalidated"}:
                continue
        # General one-time command approval is a separate, supported broker
        # contract. Select it from protected policy before submission, never by
        # retrying a rejected request through a different interface.
        try:
            policy = load_policy()
        except (OSError, ValueError):
            policy = None
        operation = "command.run" if policy is not None and unit not in policy["services"] else "service.restart"
        parameters = _restart_parameters(unit, operation)
        state["requests"][unit] = {"state": "submission-uncertain", "operation": operation}
        write_json_atomic(path, state)
        try:
            reply = exchange({"action": "request", "operation": operation, "parameters": parameters,
                              "reason": "Task follow-up: " + title})
        except (FileNotFoundError, ConnectionRefusedError, PrivilegeRequestRejected) as exc:
            del state["requests"][unit]
            write_json_atomic(path, state)
            raise ServiceApprovalUnavailable("The privilege broker could not accept the restart request for " + unit
                + ". Check broker readiness and policy in Admin controls. Your task has not been queued.") from exc
        except (OSError, ValueError, RuntimeError) as exc:
            raise ServiceApprovalUnavailable("The service request submission was not confirmed. Check the privilege portal before requesting again; no task has been queued.") from exc
        state["requests"][unit] = {key: reply[key] for key in ("id", "state", "review_url") if key in reply}
        state["requests"][unit]["operation"] = operation
        write_json_atomic(path, state)


def _restart_parameters(unit: str, operation: str) -> dict[str, Any]:
    if operation == "service.restart":
        return {"unit": unit}
    if operation == "command.run":
        return {"argv": ["/usr/bin/systemctl", "--no-ask-password", "restart", "--", unit]}
    raise ValueError("Invalid service approval operation")


def decide_follow_up(manager: AgentTasks, identifier: str, run_id: str, decision: str, *,
                     settings: dict[str, Any] | None = None, run_now: bool = True, draft: bool = False) -> str | None:
    """Approval authorizes one task and confirms its displayed operator checklist.

    Service prerequisites execute via the separately authenticated broker before
    provider launch; policy and write grants are still validated. Drafts retain
    unresolved requirements. Interrupted submission is never blindly retried.
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
        if candidate.get("storage_operation", "none") not in {"none", "review"} and restarts:
            raise ValueError("Host cleanup cannot include service restarts")
        # Use the same exact-unit validation for edited reviews as reports.
        original = manager.contexts.receipt(identifier, run_id)["follow_up"]
        validate_follow_up({**original, "service_restarts": restarts})
        if set(state["requests"]) - set(restarts):
            raise ValueError("Service requests were already submitted for this proposal. Block it to cancel unclaimed requests, "
                             "then prepare a revised task; removing a unit here cannot withdraw its broker action.")
        if decision == "approve" and settings is None and follow_up_requires_review(original):
            raise ValueError("Review this older proposal before approval. Open Review follow-up task, "
                             "check its access and prerequisites, then select Approve once.")
        candidate.update(interval="once", continue_until_done=False, max_runs=1, start_at=0,
                         source_task_id=identifier, source_run_id=run_id)
        task = validate_task(candidate, manager.home)
        acknowledged = []
        if not draft:
            issues = task_policy_issues(task)
            if issues:
                raise ValueError("Resolve prerequisites before approval: " + "; ".join(issues))
            # The operator has just approved the displayed proposal or review
            # form. Do not require editing away its checklist to acknowledge it.
            # Keep the confirmation in the decision, leaving the source receipt
            # intact, and do not pass resolved blockers to the unattended runner.
            acknowledged = task["operator_requirements"]
            task["operator_requirements"] = []
        if decision == "request-privilege":
            if not restarts:
                raise ValueError("No service restarts were requested")
            _request_services(state, str(path), restarts, task["title"])
            return None
        missing = [unit for unit in dict.fromkeys(restarts) if unit not in state["requests"]]
        if missing and not draft:
            _request_services(state, str(path), missing, task["title"])
            raise ServiceApprovalPending("Open the service approval link below and authorize the exact action. "
                                         "After it finishes, select Check approval and launch. No task has been queued.")
        # Drafts must not lose these prerequisites and later bypass the check
        # through the ordinary Run control.
        waiting = []
        for unit in dict.fromkeys(restarts):
            request = state["requests"].get(unit, {})
            if "id" not in request:
                waiting.append(unit + ": " + request.get("state", "not requested"))
                continue
            reply = _service_status(request["id"])
            request["state"] = reply.get("state", "unknown")
            write_json_atomic(str(path), state)
            plan = reply.get("plan", {})
            if reply.get("state") != "succeeded":
                waiting.append(unit + ": " + request["state"])
            elif (plan.get("operation") != request.get("operation", "service.restart")
                  or plan.get("parameters") != _restart_parameters(unit, request.get("operation", "service.restart"))):
                raise ValueError("Service prerequisite has not succeeded with the exact requested plan: " + unit)
        if waiting:
            raise ServiceApprovalPending("Waiting for service approval or successful execution: " + "; ".join(waiting)
                + ". Review the status below. Failed or expired actions need an explicit new request; "
                  "uncertain actions need inspection in the privilege portal. No task has been queued.")
        child = manager.create(task, run_now=run_now, draft=draft,
                               request_key="follow-up-" + identifier + "-" + run_id)
        state.update(status="draft" if draft else "approved", task_id=child,
                     acknowledged_requirements=acknowledged)
        write_json_atomic(str(path), state)
        return child
