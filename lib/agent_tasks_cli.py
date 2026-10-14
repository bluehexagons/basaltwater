"""Local controls for durable tasks, independent workers and MCP clients."""

from __future__ import annotations

import argparse
import json
import sys

from lib.agent_task_api import TaskAPI
from lib.atomic_io import parse_json, read_json_file
from lib.validation import validate_filesystem_path


def add_tasks_parser(commands: argparse._SubParsersAction) -> None:
    parser = commands.add_parser("tasks", help="Manage persistent prompt tasks, goals and their worker")
    actions = parser.add_subparsers(dest="agent_tasks_command", required=True)
    for action in ("capabilities", "list"):
        command = actions.add_parser(action, help="Inspect task capabilities" if action == "capabilities" else "List saved tasks and recent runs")
        command.add_argument("--json", action="store_true")
        if action == "list":
            command.add_argument("--summary", action="store_true", help="Omit prompts, events and run output")
            command.add_argument("--history-limit", type=int, default=40, help="Include 0–40 recent runs")
    for action in ("show", "history"):
        command = actions.add_parser(action, help="Show task context" if action == "show" else "Read retained run receipts")
        command.add_argument("identifier", metavar="TASK_ID")
        if action == "history":
            command.add_argument("--limit", type=int, default=40)
        command.add_argument("--json", action="store_true")
    create = actions.add_parser("create", help="Save task settings from JSON (file or - for stdin)")
    create.add_argument("file", metavar="TASK_JSON", help="JSON file or - for stdin; maximum 64 KiB")
    disposition = create.add_mutually_exclusive_group()
    disposition.add_argument("--run-now", action="store_true", help="Queue work now and activate its repeat schedule")
    disposition.add_argument("--schedule", action="store_true", help="Activate work at start_at or after its first interval")
    disposition.add_argument("--draft", action="store_true", help="Save inactive settings (the default)")
    create.add_argument("--request-key", default="", help="Idempotent creation key for retried requests")
    create.add_argument("--json", action="store_true")
    update = actions.add_parser("update", help="Apply settings from JSON (file or - for stdin)")
    update.add_argument("identifier", metavar="TASK_ID")
    update.add_argument("file", metavar="TASK_JSON", help="JSON file or - for stdin; maximum 64 KiB")
    update.add_argument("--json", action="store_true")
    receipt = actions.add_parser("receipt", help="Read full retained evidence, including for a deleted task")
    receipt.add_argument("identifier", metavar="TASK_ID")
    receipt.add_argument("run_id", metavar="RUN_ID")
    receipt.add_argument("--json", action="store_true")
    for action in ("run", "pause", "resume", "cancel", "delete", "reset-context"):
        command = actions.add_parser(action, help={
            "run": "Queue one run", "pause": "Pause future runs", "resume": "Resume a paused schedule",
            "cancel": "Cancel queued or active work", "delete": "Remove a task, preserving context and workspaces",
            "reset-context": "Clear the checkpoint and conversation, preserving files",
        }[action])
        command.add_argument("identifier", metavar="TASK_ID")
        command.add_argument("--json", action="store_true")
    notify = actions.add_parser("notify", help="Wake enabled work with an external event")
    notify.add_argument("identifier", metavar="TASK_ID")
    notify.add_argument("message", help="Event evidence, not additional permissions")
    notify.add_argument("--key", default="", help="Deduplicate repeated deliveries")
    notify.add_argument("--json", action="store_true")
    worker = actions.add_parser("worker", help="Run the foreground scheduler independently of the panel")
    worker.add_argument("--once", action="store_true", help="Claim the worker lease, handle due work, then exit")
    mcp = actions.add_parser("mcp", help="Expose the task API over stdio MCP")
    mcp.add_argument("--allow-write", action="store_true", help="Expose mutations under the caller's existing task authority")


def run_tasks_command(args: argparse.Namespace) -> int:
    operation = args.agent_tasks_command
    if operation == "worker":
        from lib.agent_task_worker import run_worker

        return run_worker(once=args.once)
    if operation == "mcp":
        from lib.agent_tasks_mcp import serve

        return serve(allow_write=args.allow_write)
    try:
        arguments = {}
        if operation in {"create", "update"}:
            if args.file == "-":
                source = getattr(sys.stdin, "buffer", sys.stdin)
                content = source.read(64 * 1024 + 1)
                size = len(content.encode("utf-8")) if isinstance(content, str) else len(content)
                if size > 64 * 1024:
                    raise ValueError("Task JSON exceeds 65536 bytes")
                settings = parse_json(content)
            else:
                validate_filesystem_path(args.file, must_exist=True)
                settings = read_json_file(args.file, max_bytes=64 * 1024)
            if not isinstance(settings, dict):
                raise ValueError("Task JSON must contain an object")
            arguments["task"] = settings
        if operation == "create":
            arguments.update(run_now=args.run_now, draft=not (args.run_now or args.schedule), request_key=args.request_key)
        if hasattr(args, "identifier"):
            arguments["id"] = args.identifier
        if operation == "history":
            arguments["limit"] = args.limit
        if operation == "list":
            arguments.update(summary=args.summary, history_limit=args.history_limit)
        if operation == "receipt":
            arguments["run_id"] = args.run_id
        if operation == "notify":
            arguments.update(message=args.message, key=args.key)
        if operation in {"run", "pause", "resume", "cancel", "delete", "reset-context"}:
            arguments["action"], operation = operation, "action"
        result = TaskAPI().call(operation, arguments)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        if getattr(args, "json", False):
            print(json.dumps({"ok": False, "error": str(exc)}))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return 1
