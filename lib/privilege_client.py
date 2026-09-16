"""Unprivileged client for requesting and observing broker operations."""

from __future__ import annotations

import argparse
import json
import socket
import sys
import time

from lib.privilege_policy import MAX_MESSAGE, REQUEST_SOCKET, canonical


def exchange(message: dict, path: str = REQUEST_SOCKET) -> dict:
    data = (canonical(message) + "\n").encode()
    if len(data) > MAX_MESSAGE:
        raise ValueError("Request exceeds size limit")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(5)
        connection.connect(path)
        connection.sendall(data)
        with connection.makefile("rb") as stream:
            raw = stream.readline(MAX_MESSAGE + 1)
    if len(raw) > MAX_MESSAGE or not raw.endswith(b"\n"):
        raise ValueError("Invalid broker response")
    response = json.loads(raw)
    if response.get("ok") is not True:
        raise ValueError(response.get("error", "Broker rejected request"))
    return response["result"]


def add_privilege_parser(commands: argparse._SubParsersAction) -> None:
    parser = commands.add_parser("privilege", help="Request a privileged operation for browser approval")
    actions = parser.add_subparsers(dest="privilege_command", required=True)
    request = actions.add_parser("request")
    request.add_argument("operation", choices=("service.restart", "system.reboot"))
    request.add_argument("--unit", help="Exact administrator-registered service name")
    request.add_argument("--reason", required=True, help="Explain why the operation is needed")
    request.add_argument("--json", action="store_true")
    for name in ("status", "wait"):
        sub = actions.add_parser(name)
        sub.add_argument("request_id")
        sub.add_argument("--json", action="store_true")
        if name == "wait":
            sub.add_argument("--timeout", type=int, default=300)
    password = actions.add_parser("password-hash", help="Create an approval password hash on the administrator's device")
    password.add_argument("--username", required=True)


def run_privilege_command(args: argparse.Namespace) -> int:
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
            result = exchange({"action": "request", "operation": args.operation,
                               "parameters": {"unit": args.unit} if args.unit else {}, "reason": args.reason})
        else:
            timeout = getattr(args, "timeout", 0)
            if not 0 <= timeout <= 900:
                raise ValueError("Timeout must be between 0 and 900 seconds")
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
        if result["state"] in {"denied", "failed", "uncertain", "expired", "invalidated"}:
            return 1
        if args.privilege_command == "wait" and result["state"] in {"pending", "approved", "executing"}:
            return 2
        return 0
    except (OSError, ValueError, EOFError) as exc:
        print(f"Privilege request failed: {exc}", file=sys.stderr)
        return 1
