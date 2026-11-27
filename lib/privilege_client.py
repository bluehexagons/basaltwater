"""Unprivileged client for requesting and observing broker operations."""

from __future__ import annotations

import argparse
import json
import re
import socket
import sys
import time

from lib.privilege_policy import (
    DEFAULT_REQUEST_TTL_SECONDS, ID_PATTERN, MAX_MESSAGE, MAX_REQUEST_TTL_SECONDS,
    REQUEST_SOCKET, canonical,
)
from lib.admin_status import FAILURE_LABELS, STAGE_LABELS, sanitize_admin_status
from lib.privilege_execution import MAX_RUNTIME_SECONDS, validate_runtime
from lib.tool_results import exception_issue, exit_status, failure, from_report, success, write_result


class PrivilegeRequestRejected(ValueError):
    """The broker definitively rejected a request before accepting work."""


class PrivilegeRequestUnconfirmed(ValueError):
    """The broker response did not establish whether a request was accepted."""


class PrivilegeBrokerUnavailable(ConnectionError):
    """The request socket could not be connected to before sending a request."""


def exchange(message: dict, path: str = REQUEST_SOCKET, *, timeout: float | None = None) -> dict:
    data = (canonical(message) + "\n").encode()
    if len(data) > MAX_MESSAGE:
        raise ValueError("Request exceeds size limit")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        planning = message.get("operation") == "software.install" or message.get("action") == "software-claim"
        connection.settimeout(timeout if timeout is not None else 55 if planning else 5)
        try:
            connection.connect(path)
        except (FileNotFoundError, ConnectionRefusedError) as exc:
            raise PrivilegeBrokerUnavailable(str(exc)) from exc
        connection.sendall(data)
        with connection.makefile("rb") as stream:
            raw = stream.readline(MAX_MESSAGE + 1)
    if not raw and message.get("action") == "status":
        raise ConnectionResetError("The broker closed the status connection")
    if len(raw) > MAX_MESSAGE or not raw.endswith(b"\n"):
        raise PrivilegeRequestUnconfirmed("Invalid broker response framing; request acceptance is unknown")
    try:
        response = json.loads(raw)
    except (ValueError, RecursionError) as exc:
        raise PrivilegeRequestUnconfirmed("Malformed broker response; request acceptance is unknown") from exc
    if not isinstance(response, dict):
        raise PrivilegeRequestUnconfirmed("Invalid broker response; request acceptance is unknown")
    if response.get("ok") is False:
        error = response.get("error")
        detail = error if isinstance(error, str) and error else "Broker returned an invalid error response"
        if (message.get("action") == "request" and response.get("code") == "request_rejected"
                and set(response) == {"ok", "code", "error"} and isinstance(error, str) and error):
            raise PrivilegeRequestRejected(error)
        raise PrivilegeRequestUnconfirmed("Broker did not confirm request acceptance: " + detail)
    if response.get("ok") is not True or not isinstance(response.get("result"), dict):
        raise PrivilegeRequestUnconfirmed("Invalid broker result; request acceptance is unknown")
    result = response["result"]
    action = message.get("action")
    if action == "request":
        request_id = result.get("id")
        if not isinstance(request_id, str) or not ID_PATTERN.fullmatch(request_id):
            raise PrivilegeRequestUnconfirmed(
                "Broker response did not confirm a valid request ID; request acceptance is unknown"
            )
    elif action in {"status", "cancel", "decide", "software-claim", "software-saved"}:
        request_id = result.get("id")
        if (not isinstance(request_id, str) or not ID_PATTERN.fullmatch(request_id)
                or request_id != message.get("id")):
            raise PrivilegeRequestUnconfirmed(
                "Broker response did not confirm the requested ID; outcome is unknown"
            )
    return result


def _wait_for_request(request_id: str, until: str, timeout: int) -> dict:
    if not ID_PATTERN.fullmatch(request_id):
        raise ValueError("Invalid request ID")
    if not 0 <= timeout <= MAX_REQUEST_TTL_SECONDS + MAX_RUNTIME_SECONDS:
        raise ValueError("Wait timeout must be between zero and seven days plus six hours")
    message = {"action": "status", "id": request_id}
    waiting = {"pending"} if until == "decision" else {
        "pending", "approved", "awaiting_controller", "saving", "ready", "executing",
    }
    if timeout == 0:
        result = exchange(message)
        if result["state"] not in waiting:
            return result
    else:
        deadline = time.monotonic() + timeout
        delay = 1
        result = {"id": request_id, "state": "unknown"}
        while (remaining := deadline - time.monotonic()) > 0:
            try:
                result = exchange(message, timeout=min(5, remaining))
            except (ConnectionError, TimeoutError):
                # A service restart or slow broker must not discard the receipt.
                pass
            else:
                if result["state"] not in waiting:
                    return result
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(delay, remaining))
            delay = min(delay * 2, 15)
    return {**result, "ok": False, "issue": "wait_timeout",
            "error": "No further outcome was observed within the local wait budget"}


def duration(value: str) -> int:
    match = re.fullmatch(r"([0-9]{1,8})([smhd]?)", value)
    if not match:
        raise argparse.ArgumentTypeError("Use seconds or a duration such as 15m, 4h, or 2d")
    return int(match[1]) * {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}[match[2]]


def add_privilege_parser(commands: argparse._SubParsersAction) -> None:
    parser = commands.add_parser("privilege", help="Request a privileged operation for browser approval")
    actions = parser.add_subparsers(dest="privilege_command", required=True)
    listing = actions.add_parser("list", help="Find your active and recent requests, including a lost request ID")
    listing.add_argument("--json", action="store_true")
    request = actions.add_parser("request")
    request.add_argument("operation", choices=("command.run", "service.restart", "system.reboot"))
    request.add_argument("--unit", help="Exact administrator-registered service name")
    request.add_argument("--command", dest="privilege_argv", nargs=argparse.REMAINDER,
                         help="Exact command and arguments; place after --command")
    request.add_argument("--reason", required=True, help="Explain why the operation is needed")
    request.add_argument("--review-window", type=duration, help="Time available for approval, e.g. 2d")
    request.add_argument("--runtime", type=duration, help="Execution budget for command.run, e.g. 30m")
    request.add_argument("--json", action="store_true")
    for name in ("status", "wait", "cancel"):
        sub = actions.add_parser(name)
        sub.add_argument("request_id")
        sub.add_argument("--json", action="store_true")
        if name == "wait":
            sub.add_argument("--timeout", type=duration, default=DEFAULT_REQUEST_TTL_SECONDS,
                             help="Local observation budget, default 24h. Use 0 for an immediate check.")
            sub.add_argument("--until", choices=("decision", "completion"), default="completion")
    password = actions.add_parser("password-hash", help="Create an approval password hash on the administrator's device")
    password.add_argument("--username", required=True)
    status = actions.add_parser("admin-status", help="Read owned maintenance requests and safe job diagnostics")
    status.add_argument("--json", action="store_true")


def _admin_status(args: argparse.Namespace) -> int:
    try:
        result = sanitize_admin_status(exchange({"action": "admin-status"}))
    except (OSError, ValueError, RuntimeError):
        error = "Check broker access and existing request status before submitting work."
        if args.json:
            return write_result(failure("maintenance_status_unavailable", error))
        print(error, file=sys.stderr)
        return 1
    if args.json:
        if result["unit_state"] == "unavailable":
            return write_result(from_report(result, ok=False, issue="maintenance_status_unavailable",
                                            error="The maintenance service status could not be checked"))
        return write_result(success(**result))
    else:
        print("Maintenance service: " + result["unit_state"])
        latest = result["latest"]
        if latest:
            print(f"Latest job: {latest.get('action', 'unavailable')} ({latest['status']})")
            print("Stage: " + STAGE_LABELS.get(latest.get("stage"), "Unavailable"))
            print("Command index: " + str(latest.get("command_index") or "Unavailable"))
            print("Failure: " + FAILURE_LABELS.get(latest.get("failure"), "Unavailable"))
            if setup := latest.get("setup_context"):
                print("Setup phase: " + setup["phase"])
                print("Setup stage: " + (setup["setup_stage"] or "Unavailable"))
                print("Setup step: " + str(setup["step_index"] or "Unavailable"))
                print("Error class: " + (setup["error_type"] or "Unavailable"))
        for request in result["requests"]:
            print(f"{request['id']}: {request['state']} ({request['action']})")
    return 1 if result["unit_state"] == "unavailable" else 0


def run_privilege_command(args: argparse.Namespace) -> int:
    action = args.privilege_command
    if action == "admin-status":
        return _admin_status(args)
    sent = False
    try:
        if args.privilege_command == "list":
            records = exchange({"action": "owned-requests"})["requests"]
            if args.json:
                return write_result(success(requests=records))
            else:
                for row in records:
                    print(f'{row["id"]}  {row["state"]}  {row["operation"]} {row["target"]}'.rstrip())
                if not records:
                    print("No recent privilege requests.")
            return 0
        if args.privilege_command == "password-hash":
            from lib.privilege_auth import password_record
            import getpass

            password = getpass.getpass("New approval password: ")
            if password != getpass.getpass("Repeat approval password: "):
                raise ValueError("Passwords do not match")
            print(json.dumps(password_record(args.username, password)))
            return 0
        if args.privilege_command == "request":
            if (args.operation == "service.restart") != bool(args.unit):
                raise ValueError("--unit is required only for service.restart")
            if (args.operation == "command.run") != bool(args.privilege_argv):
                raise ValueError("--command is required only for command.run")
            if args.operation != "command.run" and args.privilege_argv:
                raise ValueError("--command is required only for command.run")
            parameters = {"unit": args.unit} if args.unit else {"argv": args.privilege_argv} if args.privilege_argv else {}
            message = {"action": "request", "operation": args.operation, "parameters": parameters, "reason": args.reason}
            if args.review_window is not None:
                if not 30 <= args.review_window <= MAX_REQUEST_TTL_SECONDS:
                    raise ValueError("Review window must be between 30 seconds and 7 days")
                message["review_seconds"] = args.review_window
            if args.runtime is not None:
                if args.operation != "command.run":
                    raise ValueError("--runtime is available for command.run")
                parameters["runtime_seconds"] = validate_runtime(args.runtime)
            sent = True
            result = exchange(message)
        elif args.privilege_command == "cancel":
            if not ID_PATTERN.fullmatch(args.request_id):
                raise ValueError("Invalid request ID")
            sent = True
            result = exchange({"action": "cancel", "id": args.request_id})
        elif args.privilege_command == "wait":
            result = _wait_for_request(args.request_id, args.until, args.timeout)
        else:
            if not ID_PATTERN.fullmatch(args.request_id):
                raise ValueError("Invalid request ID")
            result = exchange({"action": "status", "id": args.request_id})
        state = result["state"]
        ok = True
        if action == "cancel":
            ok = state in {"cancelled", "denied", "expired", "invalidated"}
            cancellation = "confirmed" if state == "cancelled" else "already_settled" if ok else "not_confirmed"
            result = {**result, "cancellation": cancellation}
            if not ok:
                result.update(stage="cancel", effect="none")
        if action == "wait":
            decision_states = {"approved", "awaiting_controller", "saving", "ready", "executing",
                               "dispatched", "succeeded", "failed", "uncertain"}
            ok = result.get("issue") != "wait_timeout" and state in (decision_states if args.until == "decision"
                                                                    else {"succeeded", "dispatched"})
        result = from_report(result, ok=ok, issue="request_milestone_failed",
                             error=f"The request is {state}. Inspect this request's status before acting again.")
        if args.json:
            return write_result(result)
        else:
            print(f"{result['id']}: {result['state']}")
            if result.get("review_url"):
                print("Review on your own device: " + result["review_url"])
        return exit_status(result)
    except (OSError, ValueError, EOFError) as exc:
        issue = ("listing_unavailable" if action == "list" else
                 "request_unconfirmed" if isinstance(exc, PrivilegeRequestUnconfirmed) else
                 "request_rejected" if isinstance(exc, PrivilegeRequestRejected) else exception_issue(exc))
        fields = {}
        if action in {"request", "cancel"}:
            fields.update(stage=action, effect="none" if not sent or isinstance(
                exc, (PrivilegeRequestRejected, PrivilegeBrokerUnavailable)) else "unknown")
        if action in {"status", "wait", "cancel"} and ID_PATTERN.fullmatch(args.request_id):
            fields.update(id=args.request_id, state="unknown")
        result = failure(issue, str(exc), **fields)
        if getattr(args, "json", False):
            return write_result(result)
        print(f"Privilege request failed: {exc}", file=sys.stderr)
        return exit_status(result)
