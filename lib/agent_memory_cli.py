"""CLI access to selected project memory without automatic context or secret dumps."""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys

from lib.agent_memory import AgentMemory, MAX_TEXT_BYTES, MemoryBusyError, MemoryConflictError
from lib.validation import validate_filesystem_path


def add_memory_parser(commands: argparse._SubParsersAction) -> None:
    parser = commands.add_parser("memory", help="Private project notes, todos and secret-file references")
    actions = parser.add_subparsers(dest="agent_memory_command", required=True)
    for action in ("scope", "list", "get", "put", "remove", "claim", "renew", "finish", "reopen"):
        command = actions.add_parser(action)
        command.add_argument("--project", default=".", help="Project directory; Git worktrees share memory")
        command.add_argument("--json", action="store_true", help="Pretty-print JSON results")
        if action not in ("scope", "list"):
            command.add_argument("name")
        if action in ("list", "get", "put", "remove"):
            command.add_argument("--kind", choices=("note", "todo", "secret"), default=None if action == "list" else "note")
        if action == "list":
            command.add_argument("--limit", type=int, default=20)
            command.add_argument("--query", default="", help="Match names or non-secret text without printing bodies")
        elif action == "put":
            command.add_argument("--file", required=True, help="UTF-8 file or - for stdin; secret kind registers a private file without reading it")
            command.add_argument("--if-revision", type=int, default=0, help="Observed revision; 0 creates only")
        elif action in ("remove", "reopen"):
            command.add_argument("--if-revision", type=int, required=True)
        elif action == "claim":
            command.add_argument("--owner", required=True, help="Unique cooperating-agent label, not an authenticated identity")
            command.add_argument("--lease-seconds", type=int, default=900)
        elif action in ("renew", "finish"):
            command.add_argument("--claim-token", required=True)
            if action == "renew":
                command.add_argument("--lease-seconds", type=int, default=900)
            else:
                command.add_argument("--status", choices=("open", "done", "blocked"), default="done")


def _read_text(path: str) -> str:
    if path == "-":
        source = getattr(sys.stdin, "buffer", sys.stdin)
        content = source.read(MAX_TEXT_BYTES + 1)
    else:
        path = os.path.abspath(os.path.expanduser(path))
        validate_filesystem_path(path, must_exist=True)
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ValueError("Memory input must be a regular UTF-8 file")
            with os.fdopen(descriptor, "rb") as source:
                descriptor = -1
                content = source.read(MAX_TEXT_BYTES + 1)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
    size = len(content.encode("utf-8")) if isinstance(content, str) else len(content)
    if size > MAX_TEXT_BYTES:
        raise ValueError("Memory text exceeds 16384 bytes")
    return content.decode("utf-8") if isinstance(content, bytes) else content


def run_memory_command(args: argparse.Namespace) -> int:
    try:
        memory = AgentMemory(args.project)
        action = args.agent_memory_command
        if action == "scope":
            result = {"scope": memory.scope, "identity": memory.identity, "state_file": str(memory.path)}
        elif action == "list":
            result = memory.list(kind=args.kind, limit=args.limit, query=args.query)
        elif action == "get":
            result = memory.get(args.kind, args.name)
        elif action == "put":
            if args.kind == "secret" and args.file == "-":
                raise ValueError("Secret entries register existing private files; secret values are not accepted on stdin")
            value = args.file if args.kind == "secret" else _read_text(args.file)
            result = memory.put(args.kind, args.name, value, revision=args.if_revision)
        elif action == "remove":
            result = memory.remove(args.kind, args.name, revision=args.if_revision)
        elif action == "claim":
            result = memory.claim(args.name, args.owner, seconds=args.lease_seconds)
        elif action == "renew":
            result = memory.complete_claim(args.name, args.claim_token, seconds=args.lease_seconds)
        elif action == "finish":
            result = memory.complete_claim(args.name, args.claim_token, status=args.status)
        else:
            result = memory.reopen(args.name, revision=args.if_revision)
        print(json.dumps({"ok": True, **result}, indent=2 if args.json else None, ensure_ascii=False))
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        issue = "conflict" if isinstance(exc, MemoryConflictError) else "busy" if isinstance(exc, MemoryBusyError) else "memory_error"
        print(json.dumps({"ok": False, "issue": issue, "error": str(exc)}, ensure_ascii=False))
        return 1
