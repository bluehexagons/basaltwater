"""Result objects shared by Basaltwater's machine-facing command adapters."""

from __future__ import annotations

import argparse
import json
import os
from typing import Any


def add_json_option(parser: argparse.ArgumentParser, *, default: bool = False) -> None:
    help_text = "Emit one JSON result object (the default)" if default else "Emit one JSON result object"
    parser.add_argument("--json", action="store_true", default=default, help=help_text)


def add_json_to_leaves(parser: argparse.ArgumentParser, *, default: bool = False) -> None:
    """Declare the JSON option on a command family whose leaves share a dispatcher."""
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for child in dict.fromkeys(action.choices.values()):
                add_json_to_leaves(child, default=default)
            return
    if not any("--json" in action.option_strings for action in parser._actions):
        add_json_option(parser, default=default)


def success(**fields: Any) -> dict[str, Any]:
    return {**fields, "schema_version": 1, "ok": True}


def failure(issue: str, error: str, **fields: Any) -> dict[str, Any]:
    return {**fields, "schema_version": 1, "ok": False, "issue": issue, "error": error}


def from_report(report: dict[str, Any], *, ok: bool, issue: str, error: str) -> dict[str, Any]:
    """Add operation status to a report while preserving its diagnostic details."""
    result = {**report, "schema_version": 1, "ok": bool(ok)}
    if not ok:
        result.update(issue=report.get("issue") or issue, error=report.get("error") or error)
    return result


def exception_issue(exc: Exception) -> str:
    if isinstance(exc, PermissionError):
        return "permission_denied"
    if isinstance(exc, TimeoutError):
        return "wait_timeout"
    if isinstance(exc, ValueError):
        return "invalid_input"
    return "operation_failed"


def exit_status(result: dict[str, Any]) -> int:
    if result["ok"]:
        return 0
    return {"invalid_input": 2, "wait_timeout": 124}.get(result["issue"], 1)


def write_result(result: dict[str, Any], *, flush: bool = False) -> int:
    print(json.dumps(result, allow_nan=False, separators=(",", ":")), flush=flush)
    return exit_status(result)


def artifact(kind: str, path: str | os.PathLike[str], media_type: str) -> dict[str, str]:
    return {"kind": kind, "path": os.path.abspath(path), "media_type": media_type}


def report_artifacts(
    report: dict[str, Any], fields: dict[str, tuple[str, str]],
) -> list[dict[str, str]]:
    """List completed local files named by the operation's report."""
    return [
        artifact(kind, path, media_type)
        for field, (kind, media_type) in fields.items()
        if isinstance(path := report.get(field), (str, os.PathLike)) and os.path.isfile(path)
    ]
