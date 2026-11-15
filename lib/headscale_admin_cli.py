"""Stdlib parser and private-input recovery CLI; runtime imports stay lazy."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import stat
import sys


def add_headscale_admin_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser("headscale-admin", help="Root local Headscale recovery")
    commands = parser.add_subparsers(dest="headscale_admin_action", required=True)
    for action in ("users-list", "users-create", "users-delete", "nodes-list", "approve", "expire", "revoke"):
        command = commands.add_parser(action)
        command.add_argument("--dry-run", action="store_true")
        if action == "users-create":
            command.add_argument("--name", required=True)
        if action in {"users-delete", "expire", "revoke"}:
            command.add_argument("--id", required=True, help="Exact reviewed native numeric ID")
        if action == "approve":
            command.add_argument("--user", required=True, help="Exact verified person's user name")
            inputs = command.add_mutually_exclusive_group()
            inputs.add_argument("--auth-id-stdin", action="store_true")
            inputs.add_argument("--auth-id-file", help="Private owned Auth-ID file; never the ID itself")
    return parser


def _auth_id(args) -> str:
    from lib.headscale_admin import AdminError
    from lib.setup_directories import check_directory_path
    from lib.validation import validate_filesystem_path

    if args.auth_id_file:
        validate_filesystem_path(args.auth_id_file, must_exist=True)
        check_directory_path(os.path.dirname(os.path.abspath(args.auth_id_file)))
        fd = os.open(args.auth_id_file, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "r") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1
                    or info.st_mode & 0o077 or info.st_size > 130):
                raise AdminError("Unsafe private Auth-ID input")
            return stream.read(130).rstrip("\n")
    if args.auth_id_stdin:
        if sys.stdin.isatty():
            raise AdminError("Use the hidden terminal prompt for private TTY input")
        return sys.stdin.read(130).rstrip("\n")
    if not sys.stdin.isatty():
        raise AdminError("Select private Auth-ID stdin or file input")
    return getpass.getpass("Verified exact Auth ID: ")


def run_headscale_admin_command(args: argparse.Namespace) -> int:
    from lib.headscale_admin import AdminError, execute_admin_action, validate_action

    try:
        action = {"approve": "register", "expire": "node_expire", "revoke": "node_delete"}.get(
            args.headscale_admin_action, args.headscale_admin_action.replace("-", "_"))
        parameters = {}
        if action == "users_create":
            parameters = {"name": args.name}
        elif action in {"users_delete", "node_expire", "node_delete"}:
            parameters = {"id": args.id}
        elif action == "register":
            parameters = {"user": args.user, "auth_id": _auth_id(args)}
        validate_action(action, parameters)
        if args.dry_run:
            # No native calls, lock creation, or credential echo.
            result = {"dry_run": True, "action": action,
                      "parameters": {k: v for k, v in parameters.items() if k != "auth_id"}}
        else:
            result = execute_admin_action(action, parameters)
        print(json.dumps(result))
        return 0
    except (AdminError, OSError, ValueError, EOFError):
        print("Headscale operation unavailable or unconfirmed; reconcile before retry", file=sys.stderr)
        return 1
