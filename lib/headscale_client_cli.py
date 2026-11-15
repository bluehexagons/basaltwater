"""Explicit local native client recovery; no controller auto-enrollment."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
import re
import sys


def add_headscale_client_parser(subparsers) -> argparse.ArgumentParser:
    """Register local lifecycle commands without loading native runtime code."""
    parser = subparsers.add_parser("headscale-client", help="Root local Headscale client lifecycle")
    commands = parser.add_subparsers(dest="headscale_client_action", required=True)
    selection = commands.add_parser("reconcile-selection", help="Reconcile saved controller binding after native migration")
    selection.add_argument("host", help="Exact cached setup host")
    selection.add_argument("--expected-server-id", required=True)
    selection.add_argument("--expected-url", required=True)
    selection.add_argument("--server", required=True, help="New saved server name or stable ID")
    selection.add_argument("--new-url", required=True)
    selection.add_argument("--node-id", required=True, help="Exact new observed native node ID")
    selection.add_argument("--tag", action="append", default=[], help="Exact expected service tags; omit for personal client")
    selection.add_argument("--target-host", required=True)
    selection.add_argument("--target-key", required=True)
    selection.add_argument("--target-known-hosts", required=True)
    selection.add_argument("--target-port", type=int, default=22)
    selection.add_argument("--workspace")
    selection.add_argument("--dry-run", action="store_true")
    for action in ("status", "enroll-personal", "enroll-machine", "reconcile", "adopt",
                   "disable", "leave", "migrate", "abandon"):
        command = commands.add_parser(action)
        command.add_argument("--url", required=True, help="Reviewed current HTTPS control endpoint")
        command.add_argument("--dry-run", action="store_true")
        if action in {"adopt", "leave", "migrate"}:
            command.add_argument("--node-id", required=True, help="Exact reviewed native node ID")
        if action == "migrate":
            command.add_argument("--new-url", required=True, help="Explicit new HTTPS endpoint")
        if action == "enroll-machine":
            command.add_argument("--key-file", required=True, help="Existing private transient key file")
            command.add_argument("--approved-tag", action="append", required=True,
                                 help="Explicitly authorized service tag; repeat")
        if action == "abandon":
            command.add_argument("--confirm", action="store_true", required=True,
                                 help="Confirm remote request/key reconciliation and abandonment")
    return parser


def run_headscale_client_command(args: argparse.Namespace) -> int:
    """Dispatch to guarded native APIs, suppressing private/native diagnostics."""
    from lib.headscale_config import validate_headscale_url
    from lib.validation import validate_filesystem_path

    try:
        action = args.headscale_client_action
        if action == "reconcile-selection":
            from lib.headscale_selection import reconcile_saved_selection
            from lib.headscale_transport import SSHBinding

            reconcile_saved_selection(
                args.host, expected_server_id=args.expected_server_id, expected_url=args.expected_url,
                server=args.server, new_url=args.new_url, node_id=args.node_id, tags=args.tag,
                target=SSHBinding(args.target_host, args.target_key, args.target_known_hosts, args.target_port),
                workspace=args.workspace, dry_run=args.dry_run,
            )
            print(json.dumps({"action": action, "dry_run": args.dry_run}))
            return 0
        url = validate_headscale_url(args.url)
        if action in {"adopt", "leave", "migrate"}:
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9:_.-]{0,127}", args.node_id):
                raise ValueError("Invalid node ID")
        if action == "migrate":
            new_url = validate_headscale_url(args.new_url)
            if new_url == url:
                raise ValueError("Migration requires a new endpoint")
        if action == "enroll-machine":
            validate_filesystem_path(args.key_file, must_exist=True)
            tags = tuple(args.approved_tag)
            if not tags or len(tags) > 64 or len(set(tags)) != len(tags) or any(
                not re.fullmatch(r"tag:[a-zA-Z][a-zA-Z0-9-]{0,62}", tag) for tag in tags
            ):
                raise ValueError("Invalid approved service tags")
        if action == "abandon" and args.confirm is not True:
            raise ValueError("Explicit confirmation required")
        if args.dry_run:
            print(json.dumps({"dry_run": True, "action": action, "url": url}))
            return 0
        if os.geteuid() != 0:
            raise ValueError("Root required")
        from lib import headscale_client as client

        if action == "status":
            result = client.observe_client(url, require_running=False)
        elif action == "enroll-personal":
            if not all(os.isatty(fd) for fd in (0, 1, 2)):
                raise ValueError("Private terminal required")
            result = client.enroll_client(url, interactive=True)
        elif action == "enroll-machine":
            result = client.enroll_client(url, key_file=args.key_file, expected_tags=tags)
        elif action == "reconcile":
            result = client.reconcile(url)
        elif action == "adopt":
            result = client.adopt_client(url, expected_node_id=args.node_id)
        elif action == "disable":
            result = client.disable_client(url)
        elif action == "leave":
            result = client.leave_client(url, expected_node_id=args.node_id)
        elif action == "migrate":
            result = client.migrate_client(url, new_url, expected_node_id=args.node_id)
        elif action == "abandon":
            result = client.abandon_enrollment(url, confirm=args.confirm)
        else:
            raise ValueError("Unknown client action")
        print(json.dumps(asdict(result) if result is not None else {"action": action, "confirmed": True}))
        return 0
    except (OSError, ValueError, RuntimeError, TypeError, KeyError, EOFError, KeyboardInterrupt):
        # Native output and input values can contain credentials. Never echo
        # exception text, even when failure originated below the native adapter.
        print("Client operation unavailable or unconfirmed; preserve state and reconcile before retry",
              file=sys.stderr)
        return 1
