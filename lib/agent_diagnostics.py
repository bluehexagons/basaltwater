"""Read-only CLI access to bounded, redacted service diagnostics."""

from __future__ import annotations

import argparse
import json
import subprocess

from common.service_tools.web_panel_diagnostics import (
    PRIORITIES,
    SOURCES,
    WINDOWS,
    DiagnosticQuery,
    collect_diagnostics,
)


def add_diagnostics_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register only the collector's fixed selectors; output is always JSON."""
    parser = subparsers.add_parser(
        "diagnostics", help="Collect bounded, redacted local service evidence as JSON",
    )
    parser.add_argument("--service", required=True, choices=SOURCES)
    parser.add_argument("--window", default="24h", choices=WINDOWS)
    parser.add_argument("--priority", default="4", choices=PRIORITIES)


def run_diagnostics_command(args: argparse.Namespace) -> int:
    """Emit structured evidence; exit 1 for collection issues, not service health."""
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
    print(json.dumps(result, indent=2))
    return 1 if result["issues"] else 0
