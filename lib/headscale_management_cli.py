"""Controller authority and tagged enrollment commands, with private transport."""

from __future__ import annotations

import argparse
import json
import sys


def add_headscale_management_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser("headscale-manage", help="Explicit SSH management and tagged enrollment")
    commands = parser.add_subparsers(dest="headscale_management_action", required=True)
    for action in ("bind", "show", "remove", "enroll", "inspect", "reconcile"):
        command = commands.add_parser(action)
        command.add_argument("reference", help="Saved server name or stable ID")
        command.add_argument("--workspace")
        command.add_argument("--dry-run", action="store_true")
        if action in {"bind", "enroll"}:
            prefix = "ssh" if action == "bind" else "target"
            command.add_argument("--" + prefix + "-host", required=True)
            command.add_argument("--" + prefix + "-key", required=True, help="Absolute private identity file path")
            command.add_argument("--" + prefix + "-known-hosts", required=True, help="Absolute existing pinned host-key file")
            command.add_argument("--" + prefix + "-port", type=int, default=22)
        if action == "enroll":
            command.add_argument("--tag", action="append", required=True, help="Explicit approved service tag; repeat")
        if action == "inspect":
            command.add_argument("--remote", action="store_true", help="Inspect safe native key/node metadata over authorized SSH")
        if action == "reconcile":
            command.add_argument("--expected-key-id", action="append", default=[])
            command.add_argument("--expected-node-id", action="append", default=[])
    return parser


def run_headscale_management_command(args: argparse.Namespace) -> int:
    from lib import headscale_management as management
    from lib.headscale_transport import SSHBinding

    try:
        action = args.headscale_management_action
        keywords = {"workspace": args.workspace}
        if action == "bind":
            ssh = SSHBinding(args.ssh_host, args.ssh_key, args.ssh_known_hosts, args.ssh_port)
            result = management.bind_management(args.reference, ssh, dry_run=args.dry_run, **keywords).to_dict()
        elif action == "show":
            result = management.resolve_management(args.reference, **keywords).to_dict()
        elif action == "remove":
            management.remove_management(args.reference, dry_run=args.dry_run, **keywords)
            result = {"removed": not args.dry_run, "dry_run": args.dry_run}
        elif action == "enroll":
            target = SSHBinding(args.target_host, args.target_key, args.target_known_hosts, args.target_port)
            result = management.enroll_machine(args.reference, target, args.tag, dry_run=args.dry_run, **keywords)
        elif action == "inspect":
            management.resolve_management(args.reference, **keywords)
            result = {"enrollment": management.inspect_enrollment(**keywords)}
            if args.remote and not args.dry_run:
                result["native"] = management.inspect_management(args.reference, **keywords)
        else:
            result = management.reconcile_enrollment(args.reference, args.expected_key_id, args.expected_node_id,
                                                     dry_run=args.dry_run, **keywords)
        print(json.dumps(result))
        return 0
    except (OSError, ValueError, RuntimeError, TypeError, KeyError):
        print("Headscale management unavailable or unconfirmed; inspect retained enrollment and native keys/nodes before retry", file=sys.stderr)
        return 1
