"""Redacted local support snapshots for basaltwater agent VMs."""

from __future__ import annotations

import argparse
import json
import os
import platform
import pwd
import stat
from datetime import datetime, timezone
from typing import Optional

from lib.atomic_io import write_json_atomic
from lib.installation_info import build_setup_snapshot_metadata
from lib.types import JSONDict
from lib.validation import validate_filesystem_path


_INSTALLATION_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BROWSER_LAUNCHER_FEATURES = (
    "browser_selection",
    "private_evidence",
    "bounded_evidence",
    "coordinate_input",
    "webgl_settle_delay",
)
_BROWSER_WORKFLOW_SKILLS = frozenset(
    (
        "basaltwater-browser-testing",
        "basaltwater-playwright-testing",
        "basaltwater-t3-preview-testing",
    )
)
_BROWSER_ISSUES = frozenset(
    (
        "launchers_missing",
        "launchers_unsafe",
        "managed_defaults_stale",
        "mcp_browser_selection_missing",
        "registration_missing",
        "smoke_test_failed",
        "stale_processes",
        "workflow_skill_missing_or_stale",
    )
)
_BROWSER_REMEDIATIONS = frozenset(
    (
        "inspect_launcher_security_then_rerun_saved_setup",
        "rerun_setup_with_browser_automation",
        "rerun_saved_setup",
        "inspect_browser_runtime",
        "restart_agent_sessions",
    )
)
_DEVELOPMENT_ISSUES = frozenset(
    (
        "godot_unusable",
        "go_unusable",
        "gofmt_missing",
        "go_c_compiler_missing",
        "node_default_missing",
        "node_npm_missing",
        "node_pnpm_missing",
    )
)
_DEVELOPMENT_TOOLCHAIN_FIELDS = {
    "godot": ("installed", "healthy", "version", "export_templates", "web_templates"),
    "go": ("installed", "healthy", "version", "gofmt", "cgo_enabled", "c_compiler"),
    "node": ("installed", "healthy", "version", "npm", "pnpm", "corepack"),
}


def _effective_home() -> str:
    try:
        return pwd.getpwuid(os.geteuid()).pw_dir
    except KeyError as exc:
        raise RuntimeError("Could not resolve the current agent user's home") from exc


def _sanitize_browser_processes(value: object) -> JSONDict:
    processes = value if isinstance(value, dict) else {}
    sanitized: JSONDict = {}
    for key in ("total", "stale"):
        count = processes.get(key)
        if type(count) is int and count >= 0:
            sanitized[key] = count
    if isinstance(processes.get("inspected"), bool):
        sanitized["inspected"] = processes["inspected"]
    return sanitized


def _expand_home_path(path: str, home: str) -> str:
    if path == "~":
        return home
    if path.startswith(f"~{os.path.sep}"):
        return os.path.join(home, path[2:])
    return path


def _t3_log_summary(home: str) -> JSONDict:
    root = os.path.join(home, ".t3", "userdata", "logs")
    summary = {
        "available": False,
        "current_files": 0,
        "rotated_files": 0,
        "total_bytes": 0,
        "newest_mtime": None,
    }
    if os.path.islink(root) or not os.path.isdir(root):
        return summary
    newest: float | None = None
    for current, directories, filenames in os.walk(root, followlinks=False):
        directories[:] = [
            name
            for name in directories
            if not os.path.islink(os.path.join(current, name))
        ]
        for filename in filenames:
            path = os.path.join(current, filename)
            try:
                details = os.lstat(path)
            except OSError:
                continue
            if not stat.S_ISREG(details.st_mode):
                continue
            summary["total_bytes"] = int(summary["total_bytes"]) + details.st_size
            if filename.rsplit(".", 1)[-1].isdigit():
                summary["rotated_files"] = int(summary["rotated_files"]) + 1
            else:
                summary["current_files"] = int(summary["current_files"]) + 1
            newest = details.st_mtime if newest is None else max(newest, details.st_mtime)
    summary["available"] = True
    summary["newest_mtime"] = (
        datetime.fromtimestamp(newest, timezone.utc).isoformat().replace("+00:00", "Z")
        if newest is not None
        else None
    )
    return summary


def build_agent_support_bundle(
    home: Optional[str] = None,
    *,
    browser_smoke: bool = False,
) -> JSONDict:
    """Compose diagnostic results while omitting paths, identities, and log text."""
    from lib.agent_cli import (
        DEFAULT_DOCTOR_TOOLS,
        inspect_agent_tools,
        inspect_browser_automation,
        inspect_development_readiness,
        inspect_host_readiness,
        inspect_t3code,
    )

    user_home = os.path.abspath(home or _effective_home())
    installation = build_setup_snapshot_metadata(_INSTALLATION_ROOT)
    tools = inspect_agent_tools(list(DEFAULT_DOCTOR_TOOLS), home=user_home)
    host = inspect_host_readiness(user_home)
    t3code = inspect_t3code(user_home)
    browser = inspect_browser_automation(user_home, run_smoke=browser_smoke)
    development = inspect_development_readiness(user_home)
    raw_launcher_features = browser.get("launcher_features")
    launcher_features = (
        raw_launcher_features if isinstance(raw_launcher_features, dict) else {}
    )
    raw_browser_issues = browser.get("issues")
    browser_issues = (
        [
            issue
            for issue in raw_browser_issues
            if isinstance(issue, str) and issue in _BROWSER_ISSUES
        ]
        if isinstance(raw_browser_issues, list)
        else []
    )
    browser_remediation = browser.get("remediation")
    raw_browser_workflow_skills = browser.get("workflow_skills")
    browser_workflow_skills = (
        [
            skill_name
            for skill_name in raw_browser_workflow_skills
            if isinstance(skill_name, str)
            and skill_name in _BROWSER_WORKFLOW_SKILLS
        ]
        if isinstance(raw_browser_workflow_skills, list)
        else []
    )
    raw_development_toolchains = development.get("toolchains")
    development_toolchains = (
        raw_development_toolchains
        if isinstance(raw_development_toolchains, dict)
        else {}
    )
    raw_development_issues = development.get("issues")
    development_issues = (
        [
            issue
            for issue in raw_development_issues
            if isinstance(issue, str) and issue in _DEVELOPMENT_ISSUES
        ]
        if isinstance(raw_development_issues, list)
        else []
    )
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "basaltwater": {
            "version": installation["version"],
            "commit": installation.get("commit"),
            "dirty": installation.get("dirty"),
        },
        "system": {
            "platform": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "tools": [
            {
                key: record.get(key)
                for key in (
                    "tool",
                    "installed",
                    "version",
                    "credential",
                    "credential_healthy",
                    "credential_status",
                )
                if key in record
            }
            for record in tools
        ],
        "host": {
            "healthy": host["healthy"],
            "status": host["status"],
            "memory": host["memory"],
            "disk": {
                key: value
                for key, value in host["disk"].items()
                if key != "path"
            },
            "agent_storage": {
                "size_bytes": host["agent_storage"]["size_bytes"],
                "codex_release_count": host["agent_storage"]["codex_release_count"],
            },
            "t3_service": host["t3_service"],
            "maintenance": host["maintenance"],
            "maintenance_hold": host["maintenance_hold"],
            "reboot_pending": host["reboot_pending"],
            "warnings": host["warnings"],
            "errors": host["errors"],
        },
        "t3code": {
            "healthy": t3code["healthy"],
            "checks": t3code["checks"],
            "version": t3code["version"],
        },
        "browser": {
            "installed": browser["installed"],
            "launchers_secure": browser.get("launchers_secure") is True,
            "launcher_features": {
                feature: launcher_features.get(feature) is True
                for feature in _BROWSER_LAUNCHER_FEATURES
                if feature in launcher_features
            },
            "managed_defaults": browser.get("managed_defaults") is True,
            "running_processes": _sanitize_browser_processes(
                browser.get("running_processes")
            ),
            "registrations": browser["registrations"],
            "workflow_skills": browser_workflow_skills,
            "workflow_skill_ready": browser.get("workflow_skill_ready") is True,
            "configured": browser["configured"],
            "smoke_test": browser["smoke_test"],
            "healthy": browser["healthy"],
            "issues": browser_issues,
            "remediation": (
                browser_remediation
                if isinstance(browser_remediation, str)
                and browser_remediation in _BROWSER_REMEDIATIONS
                else None
            ),
        },
        "development": {
            "installed": development.get("installed") is True,
            "healthy": development.get("healthy") is True,
            "issues": development_issues,
            "toolchains": {
                name: {
                    field: toolchain.get(field)
                    for field in fields
                    if field in toolchain
                }
                for name, fields in _DEVELOPMENT_TOOLCHAIN_FIELDS.items()
                if isinstance(
                    toolchain := development_toolchains.get(name),
                    dict,
                )
            },
        },
        "t3_logs": _t3_log_summary(user_home),
        "privacy": {
            "log_contents_included": False,
            "repository_contents_included": False,
            "installation_branch_included": False,
            "credential_contents_included": False,
            "user_identity_included": False,
        },
    }


def write_agent_support_bundle(bundle: JSONDict, output: str, home: str) -> str:
    """Write a new private support snapshot below the current user's home."""
    user_home = os.path.realpath(os.path.abspath(home))
    path = os.path.abspath(_expand_home_path(output, user_home))
    validate_filesystem_path(path, must_exist=False)
    try:
        contained = os.path.commonpath((os.path.realpath(path), user_home)) == user_home
    except ValueError:
        contained = False
    if not contained or path == user_home:
        raise ValueError("Support bundle output must remain below the current user's home")
    current = user_home
    for component in os.path.relpath(path, user_home).split(os.path.sep)[:-1]:
        current = os.path.join(current, component)
        if os.path.lexists(current) and os.path.islink(current):
            raise ValueError(f"Support bundle path contains a symbolic link: {current}")
    parent = os.path.dirname(path)
    if os.path.islink(parent) or not os.path.isdir(parent):
        raise ValueError(f"Support bundle parent must be an existing directory: {parent}")
    if os.path.lexists(path):
        raise ValueError(f"Support bundle output already exists: {path}")
    write_json_atomic(path, bundle, mode=0o600, sort_keys=True)
    return path


def run_agent_support_command(args: argparse.Namespace) -> int:
    """Build and optionally save a redacted local support snapshot."""
    try:
        home = os.path.abspath(_effective_home())
        bundle = build_agent_support_bundle(
            home,
            browser_smoke=args.browser_smoke,
        )
        if args.output:
            path = write_agent_support_bundle(bundle, args.output, home)
            print(path)
        else:
            print(json.dumps(bundle, indent=2, sort_keys=True))
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Error: {exc}")
        return 1
    return 0
