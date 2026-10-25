"""Unprivileged client for requesting and observing broker operations."""

from __future__ import annotations

import argparse
import json
import socket
import sys
import time

from lib.privilege_policy import DEFAULT_REQUEST_TTL_SECONDS, MAX_MESSAGE, MAX_REQUEST_TTL_SECONDS, REQUEST_SOCKET, canonical
from lib.admin_status import FAILURE_LABELS, STAGE_LABELS, sanitize_admin_status


class PrivilegeRequestRejected(ValueError):
    """The broker definitively rejected a request before accepting work."""


class PrivilegeRequestUnconfirmed(ValueError):
    """The broker response did not establish whether a request was accepted."""


class PrivilegeBrokerUnavailable(ConnectionError):
    """The request socket could not be connected to before sending a request."""


def exchange(message: dict, path: str = REQUEST_SOCKET) -> dict:
    data = (canonical(message) + "\n").encode()
    if len(data) > MAX_MESSAGE:
        raise ValueError("Request exceeds size limit")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(5)
        try:
            connection.connect(path)
        except (FileNotFoundError, ConnectionRefusedError) as exc:
            raise PrivilegeBrokerUnavailable(str(exc)) from exc
        connection.sendall(data)
        with connection.makefile("rb") as stream:
            raw = stream.readline(MAX_MESSAGE + 1)
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
    return response["result"]


def add_privilege_parser(commands: argparse._SubParsersAction) -> None:
    parser = commands.add_parser("privilege", help="Request a privileged operation for browser approval")
    actions = parser.add_subparsers(dest="privilege_command", required=True)
    request = actions.add_parser("request")
    request.add_argument("operation", choices=("command.run", "service.restart", "system.reboot"))
    request.add_argument("--unit", help="Exact administrator-registered service name")
    request.add_argument("--command", dest="privilege_argv", nargs=argparse.REMAINDER,
                         help="Exact command and arguments; place after --command")
    request.add_argument("--reason", required=True, help="Explain why the operation is needed")
    request.add_argument("--json", action="store_true")
    for name in ("status", "wait", "cancel"):
        sub = actions.add_parser(name)
        sub.add_argument("request_id")
        sub.add_argument("--json", action="store_true")
        if name == "wait":
            sub.add_argument("--timeout", type=int, default=DEFAULT_REQUEST_TTL_SECONDS)
    password = actions.add_parser("password-hash", help="Create an approval password hash on the administrator's device")
    password.add_argument("--username", required=True)
    status = actions.add_parser("admin-status", help="Read owned maintenance requests and safe job diagnostics")
    status.add_argument("--json", action="store_true")


def _admin_status(args: argparse.Namespace) -> int:
    try:
        result = sanitize_admin_status(exchange({"action": "admin-status"}))
    except (OSError, ValueError, RuntimeError):
        print(json.dumps({"ok": False, "issue": "maintenance_status_unavailable",
                          "remediation": "Check broker access/version and existing request status before submitting work."}))
        return 1
    if args.json:
        print(json.dumps(result, indent=2))
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
    return 1 if result["unit_state"] == "unavailable" or result["latest"].get("status") in {"failed", "interrupted", "unavailable"} else 0


def run_privilege_command(args: argparse.Namespace) -> int:
    if args.privilege_command == "admin-status":
        return _admin_status(args)
    try:
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
            result = exchange({"action": "request", "operation": args.operation,
                               "parameters": ({"unit": args.unit} if args.unit else {"argv": args.privilege_argv} if args.privilege_argv else {}), "reason": args.reason})
        elif args.privilege_command == "cancel":
            result = exchange({"action": "cancel", "id": args.request_id})
        else:
            timeout = getattr(args, "timeout", 0)
            if not 0 <= timeout <= MAX_REQUEST_TTL_SECONDS:
                raise ValueError(f"Timeout must be between 0 and {MAX_REQUEST_TTL_SECONDS} seconds")
            deadline = time.monotonic() + timeout
            while True:
                result = exchange({"action": "status", "id": args.request_id})
                if result["state"] not in {"pending", "approved", "executing"} or time.monotonic() >= deadline:
                    break
                time.sleep(2)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"{result['id']}: {result['state']}")
            if result.get("review_url"):
                print("Review on your own device: " + result["review_url"])
        if args.privilege_command == "cancel":
            return 0 if result["state"] in {"cancelled", "expired", "denied", "invalidated"} else 1
        if result["state"] in {"cancelled", "denied", "failed", "uncertain", "expired", "invalidated"}:
            return 1
        if args.privilege_command == "wait" and result["state"] in {"pending", "approved", "executing"}:
            return 2
        return 0
    except (OSError, ValueError, EOFError) as exc:
        print(f"Privilege request failed: {exc}", file=sys.stderr)
        return 1
