"""Root local DNS parser/handler integration; private explicit request files."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys


def add_headscale_dns_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser("headscale-dns", help="Explicit root DNS publication/recovery")
    parser.add_argument("dns_action", choices=("publish", "withdraw", "inspect", "reconcile"))
    parser.add_argument("--input", help="Private root-owned JSON request and SSH bindings")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def run_headscale_dns_command(args: argparse.Namespace) -> int:
    from lib.atomic_io import parse_json
    from lib.headscale_dns_binding import parse_dns_binding
    from lib.headscale_dns_peer import DNSRemote
    from lib.headscale_dns_publish import inspect_dns, publish_dns, reconcile_dns, withdraw_dns
    from lib.headscale_server import regular_bytes

    try:
        if os.geteuid() != 0:
            raise ValueError("Root required")
        if args.dns_action == "inspect":
            if args.input:
                raise ValueError()
            result = {"dry_run": True} if args.dry_run else inspect_dns()
        else:
            if not args.input:
                raise ValueError()
            path = Path(args.input)
            if path.lstat().st_mode & 0o077 or path.stat().st_size > 1024 * 1024:
                raise ValueError()
            p = parse_json(regular_bytes(path))
            fields = {
                "publish": {"binding", "dns_remote", "peer", "peer_remote", "expected", "old_remote"},
                "withdraw": {"expected", "dns_remote", "peer", "peer_remote"},
                "reconcile": {"expected_journal", "keep", "peer", "peer_remote", "new_remote", "old_remote"},
            }[args.dns_action]
            if type(p) is not dict or set(p) != fields:
                raise ValueError()
            for key in fields & {"binding", "peer", "expected"}:
                if p[key] is not None:
                    p[key] = parse_dns_binding(p[key])
            for key in fields & {"dns_remote", "peer_remote", "new_remote", "old_remote"}:
                if p[key] is not None:
                    if type(p[key]) is not dict or set(p[key]) != {"host", "key", "known_hosts", "user"}:
                        raise ValueError()
                    p[key] = DNSRemote(**p[key])
            result = {"publish": publish_dns, "withdraw": withdraw_dns,
                      "reconcile": reconcile_dns}[args.dns_action](**p, dry_run=args.dry_run)
        print(json.dumps(result))
        return 0
    except Exception:
        print("DNS operation unavailable or unconfirmed; inspect private recovery state", file=sys.stderr)
        return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(required=True)
    add_headscale_dns_parser(commands)
    return run_headscale_dns_command(parser.parse_args(["headscale-dns", *sys.argv[1:]]))


if __name__ == "__main__":
    raise SystemExit(main())
