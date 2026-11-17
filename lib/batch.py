"""Persistent, workspace-scoped command batches without transcript capture."""

from __future__ import annotations

import argparse
from collections.abc import Callable
import hashlib
import ipaddress
import os
from pathlib import Path
import sys
import time
import uuid

from lib.atomic_io import ensure_directory_durable, read_json_file, write_json_atomic
from lib.concurrency import resource_lock, ResourceBusyError, ResourceLockSafetyError
from lib.validation import validate_filesystem_path, validate_no_control_characters
from lib.validators import validate_host
from lib.workspace import get_workspace_dir


MAX_ENTRIES = 1000
_WRAPPED_ENV = "BASALTWATER_BATCH_WRAPPED"


def _text(value: str) -> str:
    validate_no_control_characters(value, "Batch text")
    if not value.strip() or len(value) > 1000:
        raise ValueError("Batch text must contain 1–1000 characters")
    return value


def _path() -> Path:
    name = _text(os.environ.get("BASALTWATER_BATCH", "default"))
    digest = hashlib.sha256(name.encode()).hexdigest()
    # Workspace aliases must use the same lock as their canonical location.
    directory = Path(os.path.realpath(Path(get_workspace_dir()) / "batches"))
    path = directory / f"{digest}.json"
    validate_filesystem_path(str(path))
    return path


def _target_label(value: str) -> str:
    if validate_host(value):
        return value
    try:
        # Provisioning accepts CIDR targets; SSH can also accept IPv6. Do not
        # retain arbitrary invalid host arguments, which may contain URL auth.
        ipaddress.ip_interface(value)
        if "%" not in value:
            return value
    except ValueError:
        pass
    return "[target omitted]"


def _identifier(value: object) -> bool:
    return (isinstance(value, str) and len(value) == 32
            and all(c in "0123456789abcdef" for c in value))


def _seconds(value: object) -> bool:
    # Comparisons also reject huge integers without overflowing float conversion.
    return type(value) in (int, float) and 0 <= value <= 10**12


def _private_directory(path: Path) -> None:
    if not path.exists():
        # Persist each new ancestor's directory entry before writing state.
        # Restrict the new batch directory before placing any reports in it.
        ensure_directory_durable(str(path))
        path.chmod(0o700)


def _read(path: Path) -> dict | None:
    try:
        # JSON escapes can use twelve bytes per non-BMP character. Accommodate
        # every permitted 1000-character label at the 1000-entry limit.
        data = read_json_file(str(path), max_bytes=16 * 1024 * 1024)
    except FileNotFoundError:
        return None
    except (ValueError, OSError) as exc:
        raise ValueError(f"Cannot read batch state {path}: {exc}") from exc
    if (not isinstance(data, dict)
            or set(data) != {"schema_version", "id", "title", "entries", "started", "ended"}
            or type(data["schema_version"]) is not int or data["schema_version"] != 1
            or not _identifier(data["id"])
            or not isinstance(data.get("title"), str)
            or not isinstance(data.get("entries"), list)
            or len(data["entries"]) > MAX_ENTRIES
            or not _seconds(data["started"])
            or (data["ended"] is not None and not _seconds(data["ended"]))):
        raise ValueError(f"Invalid or unsupported batch state: {path}; preserve it for inspection")
    identifiers = set()
    for entry in data["entries"]:
        if (not isinstance(entry, dict) or not isinstance(entry.get("label"), str)
                or set(entry) != {"id", "label", "kind", "started", "code", "seconds"}
                or not _identifier(entry["id"]) or entry["id"] in identifiers
                or not _seconds(entry["started"])
                or not isinstance(entry["kind"], str) or entry["kind"] not in {"command", "note"}
                or (entry.get("code") is not None and type(entry["code"]) is not int)
                or (entry["seconds"] is not None and not _seconds(entry["seconds"]))
                or ((entry["code"] is None) != (entry["seconds"] is None))
                or (entry["kind"] == "note" and entry["code"] is not None)):
            raise ValueError(f"Invalid batch entry: {path}; preserve it for inspection")
        identifiers.add(entry["id"])
        _text(entry["label"])
    _text(data["title"])
    return data


def _append(data: dict, label: str, kind: str) -> str:
    if len(data["entries"]) >= MAX_ENTRIES:
        raise ValueError("Batch has reached its 1000-entry limit; end it before continuing")
    entry_id = uuid.uuid4().hex
    data["entries"].append({"id": entry_id, "label": _text(label), "kind": kind,
                            "started": time.time(), "code": None, "seconds": None})
    return entry_id


def _summary(data: dict) -> int:
    commands = [e for e in data["entries"] if e["kind"] == "command"]
    passed = sum(e["code"] == 0 for e in commands)
    pending = sum(e["code"] is None for e in commands)
    failed = len(commands) - passed - pending
    elapsed = max(0, (data.get("ended") or time.time()) - data["started"])
    print(f"Batch: {data['title']} ({elapsed:.1f}s)")
    print(f"{passed} succeeded, {failed} failed, {pending} unfinished")
    for entry in data["entries"]:
        if entry["kind"] == "note":
            print(f"  Note: {entry['label']}")
        elif entry["code"] is None:
            print(f"  UNFINISHED  {entry['label']}")
        else:
            status = "PASS" if entry["code"] == 0 else f"FAIL ({entry['code']})"
            print(f"  {status}  {entry['label']}  ({entry['seconds'] or 0:.1f}s)")
    return int(bool(failed or pending))


def _tracked(
    path: Path, label: str, action: Callable[[], int], *, required: bool,
    batched_action: Callable[[], int] | None = None,
) -> int:
    # Record admission before any command mutation, and release the short lock
    # during execution so concurrent commands can join the same batch.
    try:
        with resource_lock("batch", str(path), wait=True):
            data = _read(path)
            if data is None or data.get("ended") is not None:
                if required:
                    raise ValueError("No active batch; run 'basaltw batch start' first")
                batch_id = entry_id = None
            else:
                batch_id = data["id"]
                entry_id = _append(data, label, "command")
                write_json_atomic(str(path), data)
    except (OSError, ValueError, ResourceBusyError, ResourceLockSafetyError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if entry_id is None:
        return action()
    started = time.monotonic()
    code = 1
    try:
        code = (batched_action or action)()
        return code
    except KeyboardInterrupt:
        code = 130
        raise
    except SystemExit as exc:
        code = exc.code if type(exc.code) is int else (0 if exc.code is None else 1)
        raise
    finally:
        try:
            with resource_lock("batch", str(path), wait=True):
                data = _read(path)
                if data is None or data["id"] != batch_id or data.get("ended") is not None:
                    raise ValueError("Batch was closed or replaced while this command ran")
                entry = next((e for e in data["entries"] if e["id"] == entry_id), None)
                if entry is None:
                    raise ValueError("Batch command record is missing")
                entry.update(code=code, seconds=max(0, time.monotonic() - started))
                write_json_atomic(str(path), data)
        except (OSError, ValueError, ResourceBusyError, ResourceLockSafetyError) as exc:
            # Preserve the command's exit status; its retained pending entry
            # prevents the batch from reporting an unrecorded success.
            print(f"Warning: unable to finish batch record: {exc}", file=sys.stderr)


def track_command(args: argparse.Namespace, action: Callable[[], int]) -> int:
    """Record only recognized command metadata, never argv or output."""
    try:
        label = f"basaltw {args.command}"
        if args.command in {"setup", "patch", "shares", "ssh", "logs"}:
            label += f" {_target_label(args.host)}"
        if getattr(args, "dry_run", False):
            label += " [dry run]"
        path = _path()
    except (OSError, ValueError, ResourceBusyError, ResourceLockSafetyError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if os.environ.get(_WRAPPED_ENV) == str(path):
        return action()
    # Avoid creating locks/directories for ordinary non-batch invocations.
    if not os.path.lexists(path):
        return action()
    batched_action = None
    if (args.command in {"refresh", "ssh"}
            or (args.command == "logs" and getattr(args, "follow", False))):
        # Keep the batch owner alive across commands that replace the CLI.
        batched_action = lambda: _run_external([
            sys.executable, str(Path(__file__).resolve().parents[1] / "basaltwater.py"),
            *sys.argv[1:],
        ], batch_path=path)
    return _tracked(path, label, action, required=False, batched_action=batched_action)


def _run_external(argv: list[str], *, batch_path: Path) -> int:
    from lib.remote_utils import run

    # The wrapper owns the result for the whole child tree, including exec.
    # Nested basaltw invocations must not leave duplicate/incomplete records.
    env = {**os.environ, _WRAPPED_ENV: str(batch_path)}
    try:
        result = run(argv, interactive=True, check=False, timeout=None,
                     env=env, display_cmd="batch command")
        return result.returncode if result.returncode >= 0 else 128 - result.returncode
    except OSError:
        print("Error: unable to launch external command", file=sys.stderr)
        return 127


def add_batch_subparser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("batch", help="Summarize a sequence of commands")
    commands = parser.add_subparsers(dest="batch_command", required=True)
    start = commands.add_parser("start", help="Begin a workspace batch")
    start.add_argument("title", nargs="?", default="Command batch")
    end = commands.add_parser("end", help="Close and summarize the batch")
    end.add_argument("--force", action="store_true", help="Close with unfinished commands")
    commands.add_parser("status", help="Show the latest batch without closing it")
    note = commands.add_parser("note", help="Add a non-secret summary note")
    note.add_argument("text")
    run = commands.add_parser("run", help="Run another tool and record its exit status")
    run.add_argument("--label", default="External command", help="Non-secret summary label")
    run.add_argument("argv", nargs=argparse.REMAINDER, help="-- COMMAND [ARG ...]")


def run_batch_command(args: argparse.Namespace) -> int:
    try:
        path = _path()
        if args.batch_command == "run":
            argv = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
            if not argv:
                raise ValueError("batch run requires -- COMMAND [ARG ...]")

            return _tracked(path, _text(args.label), lambda: _run_external(argv, batch_path=path), required=True)
        unfinished = False
        with resource_lock("batch", str(path), wait=True):
            data = _read(path)
            if args.batch_command == "start":
                title = _text(args.title)
                if data is not None and data.get("ended") is None:
                    raise ValueError("A batch is already active; inspect status and end it first")
                if data is not None:
                    archive = path.parent / "history" / f"{data['id']}.json"
                    _private_directory(archive.parent)
                    write_json_atomic(str(archive), data)
                data = {"schema_version": 1, "id": uuid.uuid4().hex, "title": title,
                        "started": time.time(), "ended": None, "entries": []}
                _private_directory(path.parent)
                write_json_atomic(str(path), data)
            elif args.batch_command == "status":
                if data is None:
                    raise ValueError("No batch found")
            else:
                if data is None or data.get("ended") is not None:
                    raise ValueError("No active batch; run 'basaltw batch start' first")
                if args.batch_command == "note":
                    _append(data, args.text, "note")
                else:
                    unfinished = not args.force and any(
                        e["kind"] == "command" and e["code"] is None for e in data["entries"]
                    )
                    if not unfinished:
                        data["ended"] = time.time()
                if not unfinished:
                    write_json_atomic(str(path), data)
        # Never hold the state lock while writing to a terminal, pipe or pager.
        if args.batch_command == "start":
            print(f"Started batch: {data['title']}")
            return 0
        if args.batch_command == "note":
            return 0
        result = _summary(data)
        if unfinished:
            raise ValueError("Unfinished commands; wait, or inspect outcomes before 'batch end --force'")
        return result
    except (OSError, ValueError, ResourceBusyError, ResourceLockSafetyError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
