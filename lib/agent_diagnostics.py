"""Read-only CLI access to bounded, redacted service diagnostics."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess

from common.service_tools.web_panel_diagnostics import (
    PRIORITIES,
    SOURCES,
    WINDOWS,
    DiagnosticQuery,
    collect_diagnostics,
)
from lib.setup_directories import directory_fd
from lib.types import JSONDict
from lib.validation import validate_filesystem_path


def add_diagnostics_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register fixed selectors and optional private evidence output."""
    parser = subparsers.add_parser(
        "diagnostics", help="Collect bounded, redacted local service evidence as JSON",
    )
    parser.add_argument("--service", required=True, choices=SOURCES)
    parser.add_argument("--window", default="24h", choices=WINDOWS)
    parser.add_argument("--priority", default="4", choices=PRIORITIES)
    parser.add_argument("--summary", action="store_true", help="Omit events from stdout; requires --output")
    parser.add_argument("--output", metavar="PATH", help="Save full JSON to a new private file in an existing directory")


def _write_evidence(result: JSONDict, output: str) -> str:
    """Publish complete private JSON without following links or replacing files."""
    validate_filesystem_path(output)
    # Reject traversal before normalizing so a symlink/.. cannot disappear.
    if ".." in output.split(os.sep):
        raise ValueError("Evidence path must not contain '..'")
    path = os.path.abspath(output)
    content = json.dumps(result, indent=2) + "\n"
    with directory_fd(os.path.dirname(path)) as parent:
        temporary = ".diagnostics-" + secrets.token_hex(16)
        descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600, dir_fd=parent,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            # Atomic publication with no replacement; write_json_atomic replaces
            # existing files and therefore cannot implement this contract.
            os.link(temporary, os.path.basename(path), src_dir_fd=parent, dst_dir_fd=parent,
                    follow_symlinks=False)
            os.fsync(parent)
        finally:
            os.unlink(temporary, dir_fd=parent)
    return path


def _summarize(result: JSONDict, args: argparse.Namespace, path: str) -> JSONDict:
    counts: dict[str, int] = {}
    for event in result["events"]:
        priority = event["priority"] if event["priority"] in ("0", "1", "2", "3", "4", "5", "6", "7") else "unknown"
        counts[priority] = counts.get(priority, 0) + 1
    return {
        "properties": result["properties"],
        "issues": result["issues"],
        "query": {"service": args.service, "window": args.window, "priority": args.priority},
        "event_count": len(result["events"]),
        "priority_counts": counts,
        "evidence_path": path,
        "events_omitted_from_stdout": True,
        "qualification": (
            "Counts cover only returned events: newest first, at most 100; each query is limited "
            "to five seconds and 64 KiB. Filters, permissions and journal retention can hide "
            "entries even without collection issues. Empty events or exit 0 do not prove service health."
        ),
    }


def run_diagnostics_command(args: argparse.Namespace) -> int:
    """Emit structured evidence; exit 1 for collection issues, not service health."""
    summary = getattr(args, "summary", False)
    output = getattr(args, "output", None)
    if summary and output is None:
        print(json.dumps({"properties": {}, "events": [], "issues": [
            "--summary requires --output PATH; no collection was run.",
        ]}, indent=2))
        return 2
    if args.service not in SOURCES or args.window not in WINDOWS or args.priority not in PRIORITIES:
        result = {"properties": {}, "events": [], "issues": ["Invalid diagnostic selector."]}
    else:
        try:
            evidence = collect_diagnostics(DiagnosticQuery(
                service=args.service, window=args.window, priority=args.priority, load=True,
            ))
            result = {key: evidence[key] for key in ("properties", "events", "issues")}
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
            # Exception text can contain command output, paths or credentials.
            result = {"properties": {}, "events": [], "issues": ["Diagnostic collection could not complete."]}
    path = None
    if output is not None:
        try:
            path = _write_evidence(result, output)
        except (OSError, RuntimeError, ValueError):
            # Keep all evidence on stdout when saving fails, including in summary
            # mode. Never echo exception text or an unvalidated output path.
            result["issues"] = [*result["issues"], "Full diagnostic evidence could not be saved; events remain on stdout."]
    code = 1 if result["issues"] else 0
    print(json.dumps(_summarize(result, args, path) if summary and path else result, indent=2))
    return code
