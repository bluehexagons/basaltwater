"""Redacted, durable readiness evidence for agent hosts."""

from __future__ import annotations

import json
import os
import re
import stat
from datetime import datetime, timezone
from typing import Any, Optional

from lib.atomic_io import write_json_atomic
from lib.types import JSONDict
from lib.validation import validate_filesystem_path


_READINESS_STATE_RELATIVE = os.path.join(
    ".local",
    "state",
    "basaltwater",
    "agent-readiness.json",
)
_BOOT_ID_PATH = "/proc/sys/kernel/random/boot_id"
_BOOT_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
_MAX_RECORD_BYTES = 256 * 1024
_TRIGGERS = frozenset(("agent_update", "manual"))
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


def _within(path: str, parent: str) -> bool:
    try:
        resolved_parent = os.path.realpath(parent)
        return (
            os.path.commonpath((os.path.realpath(path), resolved_parent))
            == resolved_parent
        )
    except ValueError:
        return False


def _state_path(home: str) -> str:
    user_home = os.path.abspath(os.path.expanduser(home))
    path = os.path.join(user_home, _READINESS_STATE_RELATIVE)
    validate_filesystem_path(path, must_exist=False)
    return path


def _boot_id(path: str = _BOOT_ID_PATH) -> Optional[str]:
    try:
        with open(path, encoding="ascii") as file_obj:
            value = file_obj.read(64).strip().lower()
    except (OSError, UnicodeError):
        return None
    return value if _BOOT_ID_RE.fullmatch(value) else None


def _safe_mapping(value: Any) -> JSONDict:
    return dict(value) if isinstance(value, dict) else {}


def _sanitize_browser_processes(value: Any) -> JSONDict:
    processes = _safe_mapping(value)
    sanitized: JSONDict = {}
    for key in ("total", "stale"):
        count = processes.get(key)
        if type(count) is int and count >= 0:
            sanitized[key] = count
    if isinstance(processes.get("inspected"), bool):
        sanitized["inspected"] = processes["inspected"]
    return sanitized


def _sanitize_credential_status(value: Any) -> JSONDict:
    status = _safe_mapping(value)
    return {
        key: status.get(key)
        for key in (
            "status",
            "auth_mode",
            "last_refresh",
            "last_refresh_age_seconds",
            "access_token_issued_at",
            "access_token_expires_at",
            "access_token_expired",
            "refresh_token_present",
            "api_key_present",
            "warnings",
        )
        if key in status
    }


def _sanitize_tool(record: JSONDict) -> JSONDict:
    sanitized = {
        key: record.get(key)
        for key in (
            "tool",
            "installed",
            "version",
            "credential",
            "credential_healthy",
        )
        if key in record
    }
    if "credential_status" in record:
        sanitized["credential_status"] = _sanitize_credential_status(
            record.get("credential_status")
        )
    if isinstance(record.get("required"), bool):
        sanitized["required"] = record["required"]
    return sanitized


def _sanitize_host(record: JSONDict) -> JSONDict:
    disk = _safe_mapping(record.get("disk"))
    storage = _safe_mapping(record.get("agent_storage"))
    return {
        "capability": "host",
        "healthy": record.get("healthy") is True,
        "status": record.get("status"),
        "memory": _safe_mapping(record.get("memory")),
        "disk": {key: value for key, value in disk.items() if key != "path"},
        "agent_storage": {
            "size_bytes": _safe_mapping(storage.get("size_bytes")),
            "codex_release_count": storage.get("codex_release_count"),
        },
        "t3_service": _safe_mapping(record.get("t3_service")),
        "maintenance": _safe_mapping(record.get("maintenance")),
        "maintenance_hold": _safe_mapping(record.get("maintenance_hold")),
        "reboot_pending": record.get("reboot_pending") is True,
        "warnings": list(record.get("warnings", []))
        if isinstance(record.get("warnings"), list)
        else [],
        "errors": list(record.get("errors", []))
        if isinstance(record.get("errors"), list)
        else [],
    }


def _sanitize_capability(record: JSONDict) -> Optional[JSONDict]:
    capability = record.get("capability")
    if capability == "host":
        return _sanitize_host(record)
    if capability == "t3code":
        return {
            "capability": "t3code",
            "healthy": record.get("healthy") is True,
            "checks": _safe_mapping(record.get("checks")),
            "version": record.get("version"),
            "fixes": list(record.get("fixes", []))
            if isinstance(record.get("fixes"), list)
            else [],
        }
    if capability == "browser":
        launcher_features = _safe_mapping(record.get("launcher_features"))
        running_processes = _sanitize_browser_processes(
            record.get("running_processes")
        )
        raw_issues = record.get("issues")
        issues = (
            [
                issue
                for issue in raw_issues
                if isinstance(issue, str) and issue in _BROWSER_ISSUES
            ]
            if isinstance(raw_issues, list)
            else []
        )
        remediation = record.get("remediation")
        raw_workflow_skills = record.get("workflow_skills")
        workflow_skills = (
            [
                skill_name
                for skill_name in raw_workflow_skills
                if isinstance(skill_name, str)
                and skill_name in _BROWSER_WORKFLOW_SKILLS
            ]
            if isinstance(raw_workflow_skills, list)
            else []
        )
        return {
            "capability": "browser",
            "healthy": record.get("healthy") is True,
            "installed": record.get("installed") is True,
            "launchers_secure": record.get("launchers_secure") is True,
            "launcher_features": {
                feature: launcher_features.get(feature) is True
                for feature in _BROWSER_LAUNCHER_FEATURES
                if feature in launcher_features
            },
            "managed_defaults": record.get("managed_defaults") is True,
            "running_processes": running_processes,
            "registrations": _safe_mapping(record.get("registrations")),
            "workflow_skills": workflow_skills,
            "workflow_skill_ready": record.get("workflow_skill_ready") is True,
            "configured": record.get("configured") is True,
            "smoke_test": record.get("smoke_test"),
            "issues": issues,
            "remediation": (
                remediation
                if isinstance(remediation, str)
                and remediation in _BROWSER_REMEDIATIONS
                else None
            ),
        }
    if capability == "development":
        raw_toolchains = _safe_mapping(record.get("toolchains"))
        raw_issues = record.get("issues")
        issues = (
            [
                issue
                for issue in raw_issues
                if isinstance(issue, str) and issue in _DEVELOPMENT_ISSUES
            ]
            if isinstance(raw_issues, list)
            else []
        )
        return {
            "capability": "development",
            "installed": record.get("installed") is True,
            "healthy": record.get("healthy") is True,
            "issues": issues,
            "toolchains": {
                name: {
                    field: toolchain.get(field)
                    for field in fields
                    if field in toolchain
                }
                for name, fields in _DEVELOPMENT_TOOLCHAIN_FIELDS.items()
                if isinstance(toolchain := raw_toolchains.get(name), dict)
            },
        }
    return None


def build_agent_readiness_record(
    tools: list[JSONDict],
    capabilities: list[JSONDict],
    *,
    trigger: str,
    now: Optional[datetime] = None,
    boot_id_path: str = _BOOT_ID_PATH,
) -> JSONDict:
    """Build one redacted record from already-collected doctor results."""
    if trigger not in _TRIGGERS:
        raise ValueError(f"Unsupported agent readiness trigger: {trigger}")
    sanitized_tools = [_sanitize_tool(record) for record in tools]
    sanitized_capabilities = [
        sanitized
        for record in capabilities
        if (sanitized := _sanitize_capability(record)) is not None
    ]
    tools_healthy = all(
        record.get("required") is False
        or (
            record.get("installed") is True
            and record.get("credential_healthy") is not False
        )
        for record in sanitized_tools
    )
    capabilities_healthy = all(
        record.get("healthy") is True for record in sanitized_capabilities
    )
    recorded_at = now or datetime.now(timezone.utc)
    if recorded_at.tzinfo is None:
        raise ValueError("Agent readiness time must include a timezone")
    return {
        "schema_version": 1,
        "recorded_at": recorded_at.astimezone(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "trigger": trigger,
        "boot_id": _boot_id(boot_id_path),
        "healthy": tools_healthy and capabilities_healthy,
        "tools": sanitized_tools,
        "capabilities": sanitized_capabilities,
        "privacy": {
            "paths_included": False,
            "log_contents_included": False,
            "user_identity_included": False,
            "credential_contents_included": False,
            "repository_contents_included": False,
            "process_details_included": False,
        },
    }


def write_agent_readiness_record(record: JSONDict, *, home: str) -> str:
    """Atomically replace the current user's private readiness record."""
    user_home = os.path.abspath(os.path.expanduser(home))
    validate_filesystem_path(user_home, must_exist=True)
    try:
        owner_uid = os.stat(user_home).st_uid
    except OSError as exc:
        raise RuntimeError(f"Could not inspect agent home: {user_home}") from exc
    if owner_uid != os.geteuid():
        raise RuntimeError("Agent readiness must be recorded by the home owner")

    path = _state_path(user_home)
    parent = os.path.dirname(path)
    if not _within(parent, user_home):
        raise RuntimeError("Agent readiness state must remain below the user's home")
    os.makedirs(parent, mode=0o700, exist_ok=True)
    if os.path.islink(parent) or not _within(parent, user_home):
        raise RuntimeError("Agent readiness state directory is unsafe")
    os.chmod(parent, 0o700)
    write_json_atomic(path, record, mode=0o600, sort_keys=True)
    return path


def record_agent_readiness(
    tools: list[JSONDict],
    capabilities: list[JSONDict],
    *,
    trigger: str,
    home: Optional[str] = None,
    now: Optional[datetime] = None,
    boot_id_path: str = _BOOT_ID_PATH,
) -> JSONDict:
    """Build and persist a readiness record."""
    user_home = os.path.abspath(home or os.path.expanduser("~"))
    record = build_agent_readiness_record(
        tools,
        capabilities,
        trigger=trigger,
        now=now,
        boot_id_path=boot_id_path,
    )
    write_agent_readiness_record(record, home=user_home)
    return record


def load_agent_readiness_record(
    *,
    home: Optional[str] = None,
    boot_id_path: str = _BOOT_ID_PATH,
) -> Optional[JSONDict]:
    """Load the private record and report whether it is from this boot."""
    user_home = os.path.abspath(home or os.path.expanduser("~"))
    path = _state_path(user_home)
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
        )
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise RuntimeError("Could not safely read the agent readiness record") from exc
    try:
        metadata = os.fstat(descriptor)
        resolved_descriptor = os.path.realpath(f"/proc/self/fd/{descriptor}")
        home_owner = os.stat(user_home).st_uid
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != home_owner
            or stat.S_IMODE(metadata.st_mode) & 0o077
            or metadata.st_size > _MAX_RECORD_BYTES
            or not _within(resolved_descriptor, user_home)
        ):
            raise RuntimeError("Agent readiness record permissions or location are unsafe")
        with os.fdopen(descriptor, encoding="utf-8") as file_obj:
            descriptor = -1
            payload = file_obj.read(_MAX_RECORD_BYTES + 1)
        if len(payload.encode("utf-8")) > _MAX_RECORD_BYTES:
            raise RuntimeError("Agent readiness record exceeds its size limit")
        value = json.loads(payload)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Agent readiness record is invalid") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != 1
        or not isinstance(value.get("tools"), list)
        or not isinstance(value.get("capabilities"), list)
    ):
        raise RuntimeError("Agent readiness record has an unsupported schema")
    record = dict(value)
    current_boot_id = _boot_id(boot_id_path)
    recorded_boot_id = record.get("boot_id")
    record["current_boot"] = (
        current_boot_id == recorded_boot_id
        if current_boot_id is not None and isinstance(recorded_boot_id, str)
        else None
    )
    return record


def format_agent_readiness_record(record: JSONDict) -> str:
    """Render a concise path-free readiness history summary."""
    current_boot = record.get("current_boot")
    boot_status = (
        "current"
        if current_boot is True
        else "previous"
        if current_boot is False
        else "unknown"
    )
    lines = [
        "Last agent readiness record",
        f"  result: {'healthy' if record.get('healthy') is True else 'UNHEALTHY'}",
        f"  recorded: {record.get('recorded_at') or 'unknown'}",
        f"  trigger: {record.get('trigger') or 'unknown'}",
        f"  boot: {boot_status}",
        f"  tools: {len(record.get('tools', []))}",
        f"  capabilities: {len(record.get('capabilities', []))}",
    ]
    return "\n".join(lines)
