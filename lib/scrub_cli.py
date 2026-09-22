"""Controller commands for inspecting and remediating NAS integrity findings."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

from lib.cache import load_setup_command
from lib.ssh_utils import build_ssh_command, shell_join, ssh_process_timeout
from lib.validation import validate_filesystem_path
from lib.validators import validate_host, validate_username

ACTIONS = ("status", "inspect", "verify", "repair", "restore", "accept")
MUTATIONS = ("repair", "restore", "accept")


def add_target_arguments(parser: argparse.ArgumentParser, action: str) -> None:
    parser.add_argument("--directory", help="Select a configured scrub root (absolute target path)")
    parser.add_argument("--database", help="Disambiguate a scrub database (absolute target path)")
    parser.add_argument("--json", action="store_true", help="Print the report as JSON")
    if action == "status":
        parser.add_argument("--all", action="store_true", help="Include resolved findings")
    else:
        parser.add_argument("--file", required=True, help="Absolute protected file path on the target host")
    if action == "restore":
        parser.add_argument("--from", dest="backup", required=True, help="Backup file on the target host")
    if action in MUTATIONS:
        parser.add_argument("--yes", action="store_true", help="Confirm this single-file change; retain original data and parity")


def add_scrub_subparser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("scrub", help="Inspect and remediate persistent file integrity findings")
    actions = parser.add_subparsers(dest="scrub_action", required=True)
    for action in ACTIONS:
        child = actions.add_parser(action)
        child.add_argument("host")
        child.add_argument("--key", "-i", dest="ssh_key")
        child.add_argument("--username", "-u", default="root", help="SSH administrator (default: root)")
        add_target_arguments(child, action)


def run_scrub_command(args: argparse.Namespace) -> int:
    try:
        if not validate_host(args.host) or not validate_username(args.username):
            raise ValueError("Invalid host or SSH username")
        command = ["python3", "/opt/basaltwater/sync/service_tools/scrub_manage.py", args.scrub_action]
        for option in ("directory", "database", "file", "backup"):
            value = getattr(args, option, None)
            if value:
                validate_filesystem_path(value)
                if not os.path.isabs(value):
                    raise ValueError(f"--{option} must be an absolute path on the target host")
                command.extend(["--from" if option == "backup" else f"--{option}", value])
        for flag in ("json", "all"):
            if getattr(args, flag, False):
                command.append(f"--{flag}")
        if args.scrub_action in MUTATIONS:
            if not args.yes:
                if not sys.stdin.isatty():
                    raise ValueError("Remediation requires --yes in non-interactive use")
                warning = "Accept current content as the new baseline" if args.scrub_action == "accept" else args.scrub_action.capitalize()
                answer = input(f"{warning}: {args.host}:{args.file}? Originals and parity will be retained. [y/N] ")
                if answer.lower() != "y":
                    return 1
            command.append("--yes")
        if args.host in {"localhost", "127.0.0.1", "::1"}:
            from sync.service_tools.scrub_manage import main
            return main(command[2:])
        saved = load_setup_command(args.host)
        key = args.ssh_key or (saved.ssh_key if saved else None)
        if key:
            key = os.path.abspath(os.path.expanduser(key))
            validate_filesystem_path(key)
        if args.username != "root":
            command = ["sudo", "-n", *command]
        ssh = build_ssh_command(args.host, args.username, key,
                                remote_command=shell_join(["timeout", "14400", *command]))
        return subprocess.run(ssh, check=False, timeout=ssh_process_timeout(14460)).returncode
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
