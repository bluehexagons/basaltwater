"""Account-scoped advisory reservations for cooperating workstation commands."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import sys
from typing import Iterator
import uuid

from lib.agent_workspace import _effective_home, _managed_root
from lib.atomic_io import read_json_file, write_json_atomic
from lib.remote_utils import is_dry_run
from lib.streamed_process import run_streamed
from lib.validation import validate_filesystem_path, validate_no_control_characters, validate_positive_integer


def resource_keys(resources: list[str], outputs: list[str]) -> list[str]:
    """Canonicalize output roots; named resources are shared by explicit agreement."""
    keys = []
    for name in resources:
        validate_no_control_characters(name, "Resource")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", name) or name.startswith("build:"):
            raise ValueError("Resource names require 1–128 letters, digits, '.', '_', ':' or '-'; build: is reserved")
        keys.append(name)
    for output in outputs:
        validate_filesystem_path(output)
        keys.append("build:" + os.path.realpath(os.path.abspath(os.path.expanduser(output))))
    if not keys or len(keys) > 32:
        raise ValueError("Select from 1 through 32 resources or build output roots")
    return sorted(set(keys))


def _label(value: str, name: str) -> str:
    validate_no_control_characters(value, name)
    if not value.strip() or len(value) > 128:
        raise ValueError(f"{name} must contain 1–128 characters")
    return value


def _root() -> Path:
    home = _effective_home()
    root = Path(_managed_root(home, os.path.join(home, ".local/state/basaltwater/reservations"), create=True))
    if root.stat().st_mode & 0o077:
        raise ValueError("Reservation state must be private (mode 0700)")
    return root


def _open_lock(path: Path) -> int:
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, 0o600)
    info = os.fstat(descriptor)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
        os.close(descriptor)
        raise ValueError("Unsafe reservation lock")
    return descriptor


def _paths(root: Path, key: str) -> tuple[Path, Path]:
    digest = hashlib.sha256(key.encode()).hexdigest()
    return root / (digest + ".lock"), root / (digest + ".json")


def _record(path: Path) -> dict | None:
    try:
        value = read_json_file(str(path), max_bytes=1024 * 1024)
    except FileNotFoundError:
        return None
    if (not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] != 1
            or not isinstance(value.get("owner"), str) or not isinstance(value.get("run_id"), str)):
        raise ValueError("Invalid reservation metadata; preserve it for inspection")
    _label(value["owner"], "Recorded owner")
    _label(value["run_id"], "Recorded run ID")
    return value


@contextmanager
def reserve(keys: list[str], *, owner: str, run_id: str) -> Iterator[tuple[dict, tuple[int, ...]]]:
    """Acquire all keys or none, without waiting, deleting locks or stealing owners.

    Pass the descriptors to the supervised child. Closing the parent's copies
    does not release a reservation while that child retains its copies.
    """
    _label(owner, "Owner")
    _label(run_id, "Run ID")
    if not keys or len(keys) > 32:
        raise ValueError("Select from 1 through 32 reservation keys")
    root = _root()
    descriptors = []
    try:
        for key in sorted(set(keys)):
            lock, metadata = _paths(root, key)
            descriptor = _open_lock(lock)
            descriptors.append(descriptor)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                holder = _record(metadata)
                detail = f"owner={holder.get('owner')!r}, run_id={holder.get('run_id')!r}" if holder else "owner metadata pending"
                raise RuntimeError(f"Resource {key!r} is reserved ({detail}); wait for that command to finish") from exc
        record = {"version": 1, "owner": owner, "run_id": run_id, "pid": os.getpid(),
                  "started_at": datetime.now(timezone.utc).isoformat(), "resources": sorted(set(keys))}
        for key in keys:
            _, metadata = _paths(root, key)
            _record(metadata)  # Preserve corrupt prior evidence instead of silently replacing it.
            write_json_atomic(str(metadata), {**record, "resource": key})
        yield record, tuple(descriptors)
    finally:
        # Never unlink or explicitly unlock a live inode: inherited descriptors
        # can outlive this supervisor after SIGKILL. The kernel owns release.
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def reservation_status(keys: list[str]) -> list[dict]:
    root = _root()
    results = []
    for key in keys:
        lock, metadata = _paths(root, key)
        descriptor = _open_lock(lock)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                active = False
            except BlockingIOError:
                active = True
            results.append({"resource": key, "active": active, "last_reservation": _record(metadata)})
        finally:
            os.close(descriptor)
    return results


@contextmanager
def termination_cleanup() -> Iterator[None]:
    """Turn SIGTERM into cancellation so the command group is reaped first."""
    def terminate(signum, frame):
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGTERM, terminate)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


def add_reservations_parser(commands: argparse._SubParsersAction) -> None:
    parser = commands.add_parser("reservations", help="Advisory workstation resource reservations")
    actions = parser.add_subparsers(dest="reservation_command", required=True)
    status = actions.add_parser("status", help="Inspect selected locks and last owner metadata")
    execute = actions.add_parser("run", help="Reserve resources for one foreground command")
    for action in (status, execute):
        action.add_argument("--resource", action="append", default=[], help="Shared name, for example gpu:all (repeatable)")
        action.add_argument("--build-output", action="append", default=[], help="Canonical shared build output root (repeatable)")
    status.add_argument("--json", action="store_true")
    execute.add_argument("--owner", required=True, help="Agent or task owner label")
    execute.add_argument("--run-id", default=None, help="Run identity; default: generated UUID")
    execute.add_argument("--timeout", type=validate_positive_integer, default=3600)
    execute.add_argument("reservation_argv", nargs=argparse.REMAINDER, metavar="COMMAND")


def run_reservations_command(args: argparse.Namespace) -> int:
    try:
        keys = resource_keys(args.resource, args.build_output)
        if args.reservation_command == "status":
            print(json.dumps(reservation_status(keys), indent=2))
            return 0
        command = list(args.reservation_argv)
        if command and command[0] == "--":
            command.pop(0)
        if not command or not command[0] or not 1 <= args.timeout <= 86400:
            raise ValueError("Supply a command after -- and a timeout from 1 through 86400 seconds")
        for argument in command:
            validate_no_control_characters(argument, "Command argument")
        if is_dry_run():
            raise ValueError("Reservation run requires execution")
        run_id = args.run_id if args.run_id is not None else uuid.uuid4().hex
        with termination_cleanup(), reserve(keys, owner=args.owner, run_id=run_id) as (record, descriptors):
            print(json.dumps({"reservation": record}), file=sys.stderr, flush=True)
            code = run_streamed(command, timeout=args.timeout, pass_fds=descriptors, cleanup_group_on_exit=True,
                                on_output=lambda chunk: print(chunk, end="", flush=True))
        return code if code >= 0 else 128 - code
    except TimeoutError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 124
    except KeyboardInterrupt:
        return 130
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
