"""Controller entry points for managed HomeBox observation and recovery."""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess

from lib.cache import load_setup_command
from lib.ssh_utils import build_ssh_command, ssh_batch_mode
from lib.validation import validate_filesystem_path
from lib.validators import validate_host, validate_username


def add_homebox_subparser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("homebox", help="Inspect, back up, or restore managed HomeBox")
    commands = parser.add_subparsers(dest="homebox_command", required=True)
    for action in ("health", "backup", "restore"):
        command = commands.add_parser(action)
        command.add_argument("host")
        command.add_argument("--username")
        command.add_argument("-k", "--key", dest="ssh_key")
        command.add_argument("--workspace")
        command.add_argument("--json", action="store_true")
        if action != "health":
            command.add_argument("path", help="Absolute archive path on the target")
            command.add_argument("--dry-run", action="store_true")
        if action == "restore":
            command.add_argument("--yes", action="store_true", help="Replace current inventory from this backup")


def run_homebox_command(args: argparse.Namespace) -> int:
    try:
        if not validate_host(args.host):
            raise ValueError("Invalid HomeBox SSH host")
        cached = load_setup_command(args.host)
        username = args.username or (cached.username if cached else "root")
        key = args.ssh_key or (cached.ssh_key if cached else None)
        if not validate_username(username):
            raise ValueError("Invalid HomeBox SSH username")
        if key:
            validate_filesystem_path(key)
        action = args.homebox_command
        command = ["python3", "-m", "web.homebox_steps", action]
        if action != "health":
            validate_filesystem_path(args.path)
            if not args.path.startswith("/"):
                raise ValueError("Backup archive path must be absolute")
            command.append(args.path)
            if args.dry_run:
                print(json.dumps({"action": action, "host": args.host, "path": args.path, "dry_run": True}))
                return 0
        if action == "restore":
            if not args.yes:
                raise ValueError("Restore replaces current inventory; pass --yes")
            command.append("--yes")
        if username != "root":
            command = ["sudo", "-n", *command]
        ssh = build_ssh_command(args.host, username, key, batch_mode=ssh_batch_mode(),
                                remote_command="cd /opt/basaltwater && " + shlex.join(command))
        result = subprocess.run(ssh, capture_output=True, text=True, check=False,
                                timeout=90 if action == "health" else 3600)
        try:
            value = json.loads(result.stdout)
        except ValueError as exc:
            raise RuntimeError("HomeBox returned no valid result; check SSH access and installed basaltwater version") from exc
        if not isinstance(value, dict):
            raise RuntimeError("Invalid HomeBox result")
        if args.json:
            print(json.dumps(value, indent=2, sort_keys=True))
        elif action == "health":
            print(f"HomeBox on {args.host}: {'healthy' if value.get('healthy') else 'UNHEALTHY'}")
            for name in ("version", "status", "url", "data_path", "error"):
                if name in value:
                    print(f"  {name}: {value[name]}")
            automatic = value.get("automatic_update")
            if isinstance(automatic, dict) and automatic.get("configured"):
                timer = automatic.get("timer", {})
                check = automatic.get("check", {})
                timer_ok = isinstance(timer, dict) and timer.get("active") and timer.get("scheduled")
                check_ok = isinstance(check, dict) and not check.get("stale") and check.get("successful")
                print(f"  automatic update timer: {'ok' if timer_ok else 'FAILED'}")
                if isinstance(check, dict) and check.get("status") == "pending":
                    check_status = "pending"
                else:
                    check_status = "ok" if check_ok else "FAILED"
                print(f"  automatic update check: {check_status}")
        else:
            print(value.get("error") or f"HomeBox {action} completed: {args.path}")
        return 0 if result.returncode == 0 else 1
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print(f"Error: {exc}")
        return 1
