#!/usr/bin/env python3
"""Serve the authenticated Basaltwater web panel behind Nginx."""

from __future__ import annotations

import argparse
import html
import http.client
import ipaddress
import json
import os
import secrets
import shlex
import shutil
import socketserver
import subprocess
import sys
import threading
import time
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any


SOURCE_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
)
if SOURCE_ROOT not in sys.path:
    sys.path.insert(0, SOURCE_ROOT)

from common.t3code_steps import _temporary_t3_loginctl_shim
from common.service_tools.web_panel_diagnostics import (
    SYSTEM_UNITS,
    parse_query as parse_diagnostic_query,
    render_diagnostics,
)
from common.service_tools.web_panel_jobs import parse_job_query, render_jobs
from common.service_tools.web_panel_agents import AgentDiagnostics, parse_agent_query, render_agent_activity, render_agents
from common.service_tools.web_panel_templates import (
    BRAND_NETWORK, panel_navigation, render_document, render_favicon, render_heading, render_icon,
)
from common.service_tools.web_panel_admin import PanelAdmin, parse_admin_query, render_admin
from common.service_tools.web_panel_agent_tools import FORM_FIELDS, parse_tools_query, prepare_tool, render_tools, tool_link
from lib.agent_tasks import AgentTasks
from common.web_panel_events import (
    WEB_PANEL_AUDIT_SNAPSHOT,
    WEB_PANEL_INGEST_TOKEN,
    WEB_PANEL_NOTIFICATION_ENDPOINT,
    WEB_PANEL_NOTIFICATION_LOG,
    append_notification_event,
    load_audit_snapshot,
    load_ingest_token,
    load_notification_events,
    unresolved_notification_alerts,
    validate_notification_payload,
)


_MAX_CONFIG_BYTES = 256 * 1024
_MAX_REQUEST_BYTES = 16 * 1024
_MAX_INGEST_REQUEST_BYTES = 64 * 1024
_MAX_OUTPUT_BYTES = 24 * 1024
_MAX_SERVICE_PROBE_BYTES = 8 * 1024
_SERVICE_PROBE_TIMEOUT_SECONDS = 1
_SYSTEM_OVERVIEW_CACHE_SECONDS = 30
_INTERNAL_WEB_URL_FILE = "/etc/basaltwater/internal-web/base-url"
_T3_UPDATE_TIMEOUT_SECONDS = 30 * 60
_T3_CORE_READINESS_CHECKS = (
    "service_active",
    "service_enabled",
    "runtime",
    "native_runtime",
    "wrapper",
    "pairing_helper",
    "endpoint",
    "t3_agent_skill",
)
_T3_GITHUB_READINESS_CHECKS = (
    "git_identity",
    "gh_authenticated",
    "git_credential_helper",
)
_T3_READINESS_LABELS = {
    "service_active": "service active",
    "service_enabled": "service enabled at boot",
    "runtime": "T3 runtime installed",
    "native_runtime": "native runtime ready",
    "wrapper": "pairing provider installed",
    "pairing_helper": "pairing helper installed",
    "endpoint": "web endpoint responding",
    "t3_agent_skill": "managed T3 skills installed",
    "git_identity": "Git author identity configured",
    "gh_authenticated": "GitHub CLI authenticated",
    "git_credential_helper": "Git credential helper configured",
}
_T3_UPDATE_SCRIPT = r'''
set -eu
export NVM_DIR="$HOME/.nvm"
if [ -s "$NVM_DIR/nvm.sh" ]; then . "$NVM_DIR/nvm.sh"; fi
export PATH="$BASALTWATER_T3_LOGINCTL_SHIM:$HOME/.local/share/basaltwater/t3-npm/bin:$PATH"
unset BASALTWATER_T3_LOGINCTL_SHIM
unset npm_config_dangerously_allow_all_scripts
unset NPM_CONFIG_DANGEROUSLY_ALLOW_ALL_SCRIPTS
unset npm_config_allow_scripts
unset NPM_CONFIG_ALLOW_SCRIPTS
export CC=gcc
export CXX=g++
export npm_config_strict_allow_scripts=false
export npm_config_foreground_scripts=true
npx --yes --package=t3@latest -c \
  'env -u npm_config_allow_scripts \
    -u NPM_CONFIG_ALLOW_SCRIPTS \
    -u npm_config_dangerously_allow_all_scripts \
    -u NPM_CONFIG_DANGEROUSLY_ALLOW_ALL_SCRIPTS \
    t3 service install'
'''.strip()


def _subprocess_text(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Invalid JSON number: {value}")


def _load_manifest(path: str) -> dict[str, Any]:
    if os.path.islink(path) or not os.path.isfile(path):
        raise RuntimeError(f"Web panel manifest must be a regular file: {path}")
    if os.path.getsize(path) > _MAX_CONFIG_BYTES:
        raise RuntimeError("Web panel manifest exceeds the size limit")
    with open(path, encoding="utf-8") as file_obj:
        value = json.load(file_obj)
    if not isinstance(value, dict) or value.get("version") != 1:
        raise RuntimeError("Invalid web panel manifest")
    for name in ("host", "system_type", "username"):
        if not isinstance(value.get(name), str) or not value[name]:
            raise RuntimeError(f"Web panel manifest has no valid {name}")
    for name in ("services", "access"):
        if not isinstance(value.get(name), list):
            raise RuntimeError(f"Web panel manifest has no valid {name}")
    if not isinstance(value.get("features"), dict):
        raise RuntimeError("Web panel manifest has no valid features")
    for name in (
        "t3_github_readiness",
        "t3_git_identity_readiness",
        "notification_ingest",
    ):
        if not isinstance(value["features"].get(name, False), bool):
            raise RuntimeError("Web panel manifest has invalid feature settings")
    panel_url = value.get("panel_url")
    if panel_url is not None:
        safe_panel_url = _safe_url(panel_url)
        if not safe_panel_url:
            raise RuntimeError("Web panel manifest has an invalid panel_url")
        parsed_panel_url = urllib.parse.urlsplit(safe_panel_url)
        if (
            parsed_panel_url.path != "/"
            or parsed_panel_url.query
            or parsed_panel_url.fragment
        ):
            raise RuntimeError("Web panel manifest has an invalid panel_url")
    return value


def _safe_url(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 2048:
        return None
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or (port is not None and not 1 <= port <= 65535)
    ):
        return None
    return value


def _run_json(command: list[str]) -> dict[str, Any]:
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if result.returncode != 0 or len(result.stdout.encode("utf-8")) > _MAX_CONFIG_BYTES:
        return {}
    try:
        value = json.loads(result.stdout)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _internal_web_landing_service(
    utility: str,
    *,
    url_file: str = _INTERNAL_WEB_URL_FILE,
) -> dict[str, str] | None:
    """Return the shared web-hosting landing page when it is installed."""

    if not utility or os.path.islink(url_file) or not os.path.isfile(url_file):
        return None
    try:
        if os.path.getsize(url_file) > 2048:
            return None
        with open(url_file, encoding="utf-8") as file_obj:
            value = file_obj.read().strip().rstrip("/") + "/"
    except OSError:
        return None
    url = _safe_url(value)
    if not url:
        return None
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.path != "/"
        or parsed.query
        or parsed.fragment
    ):
        return None
    return {
        "label": "Web hosting",
        "url": url,
        "description": "Published sites, previews, and certificate trust",
    }


def discover_basaltwater_web_services() -> list[dict[str, str]]:
    """Return live routes owned by the web panel service user."""

    utility = shutil.which("basaltwater-web")
    if not utility:
        return []
    services: list[dict[str, str]] = []
    landing = _internal_web_landing_service(utility)
    if landing:
        services.append(landing)
    for command, collection, name_field, label_prefix in (
        ([utility, "forward", "list", "--json"], "forwards", "name", "HTTPS service"),
        ([utility, "site", "list", "--json"], "sites", "name", "Published site"),
    ):
        payload = _run_json(command)
        records = payload.get(collection)
        if not isinstance(records, list):
            continue
        for record in records:
            if not isinstance(record, dict):
                continue
            url = _safe_url(record.get("url"))
            name = record.get(name_field)
            if url and isinstance(name, str) and name:
                readiness = record.get("ready")
                if readiness is True:
                    description = "live"
                elif readiness is False:
                    description = "not responding"
                else:
                    description = "Readiness not reported"
                services.append(
                    {
                        "label": f"{label_prefix}: {name}",
                        "url": url,
                        "description": description,
                    }
                )
    return services


def discover_certificate_trust() -> dict[str, str | bool] | None:
    """Return safe trust metadata for the shared internal-web certificate."""

    utility = shutil.which("basaltwater-web")
    if not utility:
        return None
    payload = _run_json([utility, "ca", "--json"])
    if payload.get("publicly_trusted") is True:
        return {"publicly_trusted": True}
    if payload.get("status") == "unknown":
        return {"status": "unknown"}
    url = _safe_url(payload.get("url"))
    fingerprint = payload.get("sha256")
    parsed = urllib.parse.urlsplit(url) if url else None
    if (
        not url
        or parsed is None
        or parsed.scheme != "https"
        or parsed.path != "/basaltwater-ca.crt"
        or parsed.query
        or parsed.fragment
        or not isinstance(fingerprint, str)
        or len(fingerprint) != 64
        or any(character not in "0123456789abcdefABCDEF" for character in fingerprint)
    ):
        return {"status": "unknown"}
    return {
        "publicly_trusted": False,
        "url": url,
        "sha256": fingerprint.lower(),
    }


def _read_proc_values(path: str) -> dict[str, int]:
    values: dict[str, int] = {}
    try:
        with open(path, encoding="utf-8") as file_obj:
            for line in file_obj:
                name, separator, raw_value = line.partition(":")
                if not separator:
                    continue
                fields = raw_value.split()
                if fields and fields[0].isdigit():
                    values[name] = int(fields[0]) * 1024
    except OSError:
        return {}
    return values


def _format_bytes(value: int) -> str:
    size = float(max(0, value))
    for suffix in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or suffix == "TiB":
            precision = 0 if suffix in {"B", "KiB", "MiB"} else 1
            return f"{size:.{precision}f} {suffix}"
        size /= 1024
    return "0 B"


def _format_uptime(seconds: float) -> str:
    total_minutes = max(0, int(seconds // 60))
    days, remaining_minutes = divmod(total_minutes, 24 * 60)
    hours, minutes = divmod(remaining_minutes, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _timer_properties(unit: str) -> dict[str, str]:
    try:
        result = subprocess.run(
            [
                "systemctl",
                "show",
                unit,
                "--property=LoadState",
                "--property=ActiveState",
                "--property=NextElapseUSecRealtime",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if result.returncode != 0:
        return {}
    properties: dict[str, str] = {}
    for line in (result.stdout or "").splitlines():
        name, separator, value = line.partition("=")
        if separator:
            properties[name] = value
    return properties


def collect_system_overview() -> list[dict[str, str]]:
    """Collect a small, non-sensitive live host report for the dashboard."""

    try:
        with open("/proc/uptime", encoding="utf-8") as file_obj:
            uptime = _format_uptime(float(file_obj.read(128).split()[0]))
    except (OSError, ValueError, IndexError):
        uptime = "Unavailable"

    memory = _read_proc_values("/proc/meminfo")
    memory_total = memory.get("MemTotal", 0)
    memory_available = memory.get("MemAvailable", 0)
    if memory_total and "MemAvailable" in memory:
        memory_used = max(0, memory_total - memory_available)
        memory_percent = round(memory_used * 100 / memory_total)
        memory_value = f"{memory_percent}% used"
        memory_description = (
            f"{_format_bytes(memory_available)} available of "
            f"{_format_bytes(memory_total)}"
        )
    else:
        memory_value = "Unavailable"
        memory_description = "Memory information could not be read"

    swap_total = memory.get("SwapTotal")
    swap_free = memory.get("SwapFree")
    if swap_total is None or swap_free is None:
        swap_value = "Unavailable"
        swap_description = "Swap information could not be read"
    elif swap_total == 0:
        swap_value = "Not configured"
        swap_description = "No swap space on this host"
    else:
        swap_used = max(0, swap_total - swap_free)
        swap_value = f"{round(swap_used * 100 / swap_total)}% used"
        swap_description = f"{_format_bytes(swap_used)} of {_format_bytes(swap_total)}"

    cpu_count = os.cpu_count()
    try:
        load = os.getloadavg()
        load_value = f"{load[0]:.2f}"
        load_description = f"5m {load[1]:.2f} · 15m {load[2]:.2f}"
        load_status = "warning" if cpu_count and load[0] > cpu_count else ""
    except OSError:
        load_value = "Unavailable"
        load_description = "Load information could not be read"
        load_status = "unavailable"
    load_description += f" · {cpu_count} logical CPUs" if cpu_count else " · CPU count unavailable"

    try:
        disk = shutil.disk_usage("/")
        disk_percent = round(disk.used * 100 / disk.total) if disk.total else 0
        disk_value = f"{disk_percent}% used"
        disk_description = (
            f"{_format_bytes(disk.free)} free of {_format_bytes(disk.total)}"
        )
    except OSError:
        disk_value = "Unavailable"
        disk_description = "Root filesystem usage could not be read"

    try:
        filesystem = os.statvfs("/")
        if filesystem.f_files:
            inode_used = max(0, filesystem.f_files - filesystem.f_ffree)
            inode_value = f"{round(inode_used * 100 / filesystem.f_files)}% used"
            inode_description = f"{filesystem.f_ffree:,} free of {filesystem.f_files:,}"
        else:
            inode_value = "Not reported"
            inode_description = "This filesystem does not report a fixed inode count"
    except OSError:
        inode_value = "Unavailable"
        inode_description = "Root filesystem inode count could not be read"

    try:
        kernel = os.uname()
        kernel_value = kernel.release
        kernel_description = kernel.machine
    except OSError:
        kernel_value = "Unavailable"
        kernel_description = "Kernel information could not be read"

    timer = _timer_properties("auto-update-apt.timer")
    if timer.get("LoadState") == "loaded" and timer.get("ActiveState") == "active":
        next_run = timer.get("NextElapseUSecRealtime")
        update_description = (
            f"Automatic package updates; next run {next_run}"
            if next_run and next_run != "n/a"
            else "Automatic package updates are scheduled"
        )
    elif timer.get("LoadState") == "loaded":
        update_description = "Automatic package update timer needs attention"
    else:
        update_description = "Automatic package updates are not configured"
    reboot_required = os.path.exists("/var/run/reboot-required")

    return [
        {"label": "Uptime", "value": uptime, "description": "Since the last boot"},
        {
            "label": "Load average (1m)", "value": load_value,
            "description": load_description, "status": load_status,
        },
        {
            "label": "Memory",
            "value": memory_value,
            "description": memory_description,
        },
        {
            "label": "Swap", "value": swap_value, "description": swap_description,
        },
        {
            "label": "Root disk",
            "value": disk_value,
            "description": disk_description,
        },
        {"label": "Root inodes", "value": inode_value, "description": inode_description},
        {"label": "Kernel", "value": kernel_value, "description": kernel_description},
        {
            "label": "Maintenance",
            "value": "Reboot required" if reboot_required else "No reboot pending",
            "description": update_description,
        },
    ]


def collect_service_health() -> list[dict[str, str]]:
    """Read only fixed service properties; never expose commands or journals."""

    records: list[dict[str, str]] = []
    scopes = (([], SYSTEM_UNITS), (["--user"], {"t3code.service": "T3 Code"}))
    for scope, units in scopes:
        unavailable = {
            "label": "User services" if scope else "System services",
            "value": "Unavailable",
            "description": "Service manager could not be read",
        }
        try:
            result = subprocess.run(
                [
                    "systemctl", *scope, "show", *units,
                    "--property=Id,LoadState,ActiveState,SubState", "--no-pager",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=2,
            )
        except (OSError, subprocess.TimeoutExpired):
            result = None
        if (
            result is None or not result.stdout.strip()
            or len(result.stdout) > _MAX_CONFIG_BYTES
        ):
            records.append(unavailable)
            continue
        recognized = False
        for block in result.stdout.strip().split("\n\n"):
            properties = dict(
                line.split("=", 1) for line in block.splitlines() if "=" in line
            )
            unit = properties.get("Id", "")
            if unit not in units:
                continue
            recognized = True
            load_state = properties.get("LoadState")
            if load_state == "not-found":
                continue
            active = properties.get("ActiveState", "unknown") if load_state == "loaded" else "Unavailable"
            substate = properties.get("SubState", "unknown")
            records.append({
                "label": units[unit], "value": active,
                "description": f"{unit} · {substate}",
                "unit": unit,
            })
        if not recognized:
            records.append(unavailable)
    return records


def _parse_on_demand_query(raw: str) -> bool:
    """Accept the one explicit action shared by on-demand panel views."""

    if len(raw) > 64:
        raise ValueError("Invalid on-demand query")
    query = urllib.parse.parse_qs(raw, keep_blank_values=True, max_num_fields=1)
    if query not in ({}, {"load": ["1"]}):
        raise ValueError("Invalid on-demand query")
    return bool(query)


def _homebox_probe_port(record: dict[str, Any]) -> int | None:
    """Return a validated local HomeBox probe port from a service record."""

    probe = record.get("probe")
    if not isinstance(probe, dict) or probe.get("kind") != "homebox":
        return None
    port = probe.get("port")
    if isinstance(port, bool) or not isinstance(port, int):
        return None
    return port if 1024 <= port <= 65535 else None


def _probe_homebox(port: int) -> tuple[str, str]:
    """Read the bounded public HomeBox status endpoint over loopback."""

    connection = http.client.HTTPConnection(
        "127.0.0.1", port, timeout=_SERVICE_PROBE_TIMEOUT_SECONDS
    )
    try:
        connection.request("GET", "/api/v1/status")
        response = connection.getresponse()
        body = response.read(_MAX_SERVICE_PROBE_BYTES + 1)
        if response.status != HTTPStatus.OK or len(body) > _MAX_SERVICE_PROBE_BYTES:
            return "unavailable", "HomeBox is not responding"
        status = json.loads(body)
    except (
        OSError,
        UnicodeDecodeError,
        http.client.HTTPException,
        json.JSONDecodeError,
    ):
        return "unavailable", "HomeBox is not responding"
    finally:
        connection.close()

    if not isinstance(status, dict) or status.get("health") is not True:
        return "unavailable", "HomeBox is not responding"
    if status.get("allowRegistration") is True:
        return "attention", "Registration is open"
    if status.get("allowRegistration") is False:
        return "ready", "HomeBox is ready"
    return "unavailable", "HomeBox is not responding"


def _deduplicate_services(
    configured: list[object], dynamic: list[dict[str, str]]
) -> list[dict[str, Any]]:
    services: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in [*configured, *dynamic]:
        if not isinstance(record, dict):
            continue
        url = _safe_url(record.get("url"))
        label = record.get("label")
        if not url or not isinstance(label, str) or not label or url in seen:
            continue
        description = record.get("description")
        service: dict[str, Any] = {
            "label": label,
            "url": url,
            "description": description if isinstance(description, str) else "",
        }
        if (probe_port := _homebox_probe_port(record)) is not None:
            service["probe"] = {"kind": "homebox", "port": probe_port}
        services.append(service)
        seen.add(url)
    return services


def _evaluate_t3_readiness(
    manifest: dict[str, Any], output: str
) -> tuple[bool, str] | None:
    """Evaluate doctor JSON against the capabilities selected during setup."""

    try:
        records = json.loads(output)
    except (TypeError, ValueError):
        return None
    if not isinstance(records, list):
        return None
    result = next(
        (
            record
            for record in records
            if isinstance(record, dict) and record.get("capability") == "t3code"
        ),
        None,
    )
    if not isinstance(result, dict) or not isinstance(result.get("checks"), dict):
        return None

    checks = result["checks"]
    required = list(_T3_CORE_READINESS_CHECKS)
    if manifest["features"].get("t3_git_identity_readiness") is True:
        required.append("git_identity")
    if manifest["features"].get("t3_github_readiness") is True:
        required.extend(("gh_authenticated", "git_credential_helper"))
    failed = [name for name in required if checks.get(name) is not True]
    optional_failed = [
        name
        for name in _T3_GITHUB_READINESS_CHECKS
        if name not in required and checks.get(name) is False
    ]

    lines = ["Readiness checks:"]
    for name in required:
        marker = "✓" if checks.get(name) is True else "✗"
        lines.append(f"  {marker} {_T3_READINESS_LABELS[name]}")
    if optional_failed:
        labels = ", ".join(_T3_READINESS_LABELS[name] for name in optional_failed)
        lines.append(f"  • Not required by this setup: {labels}")
    fixes = result.get("fixes")
    if isinstance(fixes, list):
        for fix in fixes:
            if isinstance(fix, str) and fix:
                lines.append(f"  ✓ Repair applied: {fix}")
    return not failed, "\n".join(lines) + "\n"


class WebPanelState:
    """In-memory state for bounded maintenance actions."""

    def __init__(
        self,
        manifest: dict[str, Any],
        *,
        audit_snapshot_path: str = WEB_PANEL_AUDIT_SNAPSHOT,
        notification_log_path: str = WEB_PANEL_NOTIFICATION_LOG,
        ingest_token_path: str = WEB_PANEL_INGEST_TOKEN,
        agent_home: str | None = None,
    ) -> None:
        self.manifest = manifest
        self.csrf_token = secrets.token_urlsafe(32)
        self._lock = threading.Lock()
        self._audit_snapshot_path = audit_snapshot_path
        self._notification_log_path = notification_log_path
        self._ingest_token = (
            load_ingest_token(ingest_token_path)
            if self.notification_ingest_enabled()
            else None
        )
        self.action_status = "idle"
        self.action_message = ""
        self.action_output = ""
        self.action_started_at: float | None = None
        self._overview: list[dict[str, str]] = []
        self._overview_at = 0.0
        self._service_health: list[dict[str, str]] = []
        self._service_health_at = float("-inf")
        self._service_health_lock = threading.Lock()
        self.agent_tasks = AgentTasks(agent_home)
        self.admin = PanelAdmin(manifest)
        self.agent_diagnostics = AgentDiagnostics(
            self.agent_tasks.home, manifest["features"].get("t3_update") is True,
        )

    def notification_ingest_enabled(self) -> bool:
        return self.manifest["features"].get("notification_ingest") is True

    def notification_ingest_url(self) -> str | None:
        """Return the complete administrator-only sender URL, when available."""

        if not self.notification_ingest_enabled() or not self._ingest_token:
            return None
        panel_url = _safe_url(self.manifest.get("panel_url"))
        if not panel_url:
            return None
        parsed = urllib.parse.urlsplit(panel_url)
        if (
            parsed.scheme != "https"
            or parsed.path != "/"
            or parsed.query
            or parsed.fragment
        ):
            return None
        return f"{panel_url.rstrip('/')}{WEB_PANEL_NOTIFICATION_ENDPOINT}#{self._ingest_token}"

    def audit_snapshot(self) -> dict[str, Any]:
        return load_audit_snapshot(self._audit_snapshot_path)

    def notification_events(self) -> list[dict[str, Any]]:
        return list(reversed(load_notification_events(self._notification_log_path)))

    def authorized_for_ingest(self, authorization: str) -> bool:
        prefix = "Bearer "
        if self._ingest_token is None or not authorization.startswith(prefix):
            return False
        supplied = authorization[len(prefix):]
        return bool(supplied) and secrets.compare_digest(supplied, self._ingest_token)

    def accept_notification(self, value: object, source_ip: str) -> bool:
        notification = validate_notification_payload(value)
        with self._lock:
            return append_notification_event(
                notification,
                source_ip,
                path=self._notification_log_path,
            )

    def system_overview(self) -> list[dict[str, str]]:
        """Return a briefly cached host report to keep refreshes inexpensive."""

        now = time.monotonic()
        with self._lock:
            if self._overview and now - self._overview_at < _SYSTEM_OVERVIEW_CACHE_SECONDS:
                return list(self._overview)
        overview = collect_system_overview()
        with self._lock:
            self._overview = overview
            self._overview_at = now
            return list(self._overview)

    def service_health(self) -> list[dict[str, str]]:
        """Cache local service state for thirty seconds, including failures."""

        with self._service_health_lock:
            now = time.monotonic()
            if now - self._service_health_at >= _SYSTEM_OVERVIEW_CACHE_SECONDS:
                self._service_health = collect_service_health()
                self._service_health_at = now
            return list(self._service_health)

    def t3_update_available(self) -> bool:
        """Return whether T3 Code is both configured and present for this user."""

        return bool(
            self.manifest["features"].get("t3_update")
            and os.path.isdir(os.path.expanduser("~/.t3/runtime"))
        )

    def trigger_t3_update(self) -> bool:
        if not self.t3_update_available():
            return False
        with self._lock:
            if self.action_status == "running":
                return False
            self.action_status = "running"
            self.action_message = "Updating T3 Code…"
            self.action_output = ""
            self.action_started_at = time.monotonic()
        threading.Thread(target=self._run_t3_update_guarded, daemon=True).start()
        return True

    def _run_t3_update_guarded(self) -> None:
        try:
            self._run_t3_update()
        except Exception as exc:
            self._finish_action(
                "failed",
                "T3 Code update stopped unexpectedly.",
                f"{type(exc).__name__}: {exc}",
            )

    def _run_t3_update(self) -> None:
        home = os.path.expanduser("~")
        environment = os.environ.copy()
        environment.pop("BASALTWATER_T3_LOGINCTL_SHIM", None)
        environment["HOME"] = home
        environment["PATH"] = os.pathsep.join(
            (
                os.path.join(home, ".local", "bin"),
                os.path.join(home, ".local", "share", "basaltwater", "t3-npm", "bin"),
                environment.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            )
        )
        environment.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        environment.setdefault(
            "DBUS_SESSION_BUS_ADDRESS",
            f"unix:path=/run/user/{os.getuid()}/bus",
        )
        try:
            with _temporary_t3_loginctl_shim(
                home, self.manifest["username"]
            ) as shim_path:
                update_environment = environment.copy()
                update_environment["BASALTWATER_T3_LOGINCTL_SHIM"] = shim_path
                update_environment["PATH"] = os.pathsep.join(
                    (shim_path, environment["PATH"])
                )
                result = subprocess.run(
                    ["/bin/bash", "-lc", _T3_UPDATE_SCRIPT],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=_T3_UPDATE_TIMEOUT_SECONDS,
                    cwd=home,
                    env=update_environment,
                )
        except subprocess.TimeoutExpired as exc:
            output = _subprocess_text(exc.stdout) + _subprocess_text(exc.stderr)
            self._finish_action("failed", "T3 Code update timed out.", output)
            return
        except OSError as exc:
            self._finish_action("failed", "T3 Code updater could not start.", str(exc))
            return

        output = (result.stdout or "") + (result.stderr or "")
        if result.returncode != 0:
            self._finish_action(
                "failed",
                f"T3 Code update failed with exit code {result.returncode}.",
                output,
            )
            return

        doctor = shutil.which("basaltw", path=environment["PATH"])
        if not doctor:
            self._finish_action(
                "failed",
                "T3 Code updated, but the managed readiness command is unavailable.",
                output,
            )
            return
        try:
            check = subprocess.run(
                [
                    doctor,
                    "agent",
                    "doctor",
                    "--capability",
                    "t3code",
                    "--fix",
                    "--json",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=300,
                cwd=home,
                env=environment,
            )
        except subprocess.TimeoutExpired as exc:
            output += _subprocess_text(exc.stdout) + _subprocess_text(exc.stderr)
            self._finish_action(
                "failed",
                "T3 Code updated, but its readiness check timed out.",
                output,
            )
            return
        except OSError as exc:
            self._finish_action(
                "failed",
                "T3 Code updated, but its readiness check could not start.",
                output + str(exc),
            )
            return
        readiness = _evaluate_t3_readiness(self.manifest, check.stdout or "")
        if readiness is None:
            output += (check.stdout or "") + (check.stderr or "")
            self._finish_action(
                "failed",
                "T3 Code updated, but its readiness result was invalid.",
                output,
            )
            return
        ready, readiness_output = readiness
        output += (check.stderr or "") + readiness_output
        if not ready:
            self._finish_action(
                "failed",
                "T3 Code updated, but its readiness check failed.",
                output,
            )
            return
        self._finish_action("complete", "T3 Code update completed.", output)

    def _finish_action(self, status: str, message: str, output: str) -> None:
        encoded = output.encode("utf-8", errors="replace")
        if len(encoded) > _MAX_OUTPUT_BYTES:
            encoded = encoded[-_MAX_OUTPUT_BYTES:]
            output = "… earlier output omitted …\n" + encoded.decode(
                "utf-8", errors="replace"
            )
        with self._lock:
            self.action_status = status
            self.action_message = message
            self.action_output = output.strip()
            self.action_started_at = None


_PAGE_STYLE = """
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font: 15px/1.5 system-ui, sans-serif;
}
main { max-width: 1560px; margin: 0 auto 0 248px; padding: 24px 28px 48px; }
body { overflow-wrap: anywhere; }
.sidebar {
  position: fixed; inset: 0 auto 0 0; width: 248px; padding: 28px 16px;
  background: var(--panel); border-right: 1px solid var(--accent-soft); overflow-y: auto;
}
.sidebar strong { display: block; margin: 0 8px 28px; color: var(--accent); }
.sidebar a { display: flex; align-items: center; gap: 11px; min-height: 44px; padding: 10px 12px;
  margin: 3px 0; color: var(--muted); text-decoration: none; border-radius: 8px; font-size: .83rem; }
.sidebar a svg { flex: none; }
.nav-group { margin: 24px 12px 8px; color: var(--muted); font-size: .66rem; font-weight: 750;
  letter-spacing: .12em; text-transform: uppercase; }
.sidebar a:hover { background: var(--accent-soft); color: var(--accent); }
a:focus-visible { outline: 3px solid var(--accent); outline-offset: 2px; }
.skip-link { position: fixed; top: -100px; left: 16px; padding: 12px; background: var(--panel); z-index: 3; }
.skip-link:focus { top: 12px; }
h2, #trust { scroll-margin-top: 24px; }
.refresh-link { color: var(--accent); display: inline-block; padding: 10px 0; text-underline-offset: 4px; }
header .refresh-link { padding: 9px 14px; margin-top: 18px; min-height: 44px; border: 1px solid var(--accent);
  border-radius: 8px; text-decoration: none; font-size: .8rem; font-weight: 650; }
header .refresh-link:hover { background: var(--accent-soft); }
.metric-value.active { color: var(--ok); }
.metric-value.failed, .metric-value.unavailable { color: var(--bad); }
.history > summary { margin: 10px 0; }
.diagnostic-filters {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 180px), 1fr));
  gap: 14px; align-items: end; padding: 18px; margin-bottom: 18px;
  background: var(--panel); border: 1px solid var(--line); border-radius: 12px;
}
.diagnostic-filters label { min-width: 0; font-weight: 650; }
.diagnostic-filters > div { min-width: 0; }
select, .diagnostic-filters input { display: block; width: 100%; min-height: 44px; margin-top: 6px; padding: 8px;
  background: var(--panel); color: var(--text); border: 1px solid var(--line); border-radius: 8px; font: inherit; }
select:focus-visible, input:focus-visible { outline: 3px solid var(--accent); outline-offset: 2px; }
.sidebar a[aria-current="page"] { background: var(--accent-soft); color: var(--accent);
  box-shadow: inset 3px 0 var(--accent); font-weight: 700; }
.sidebar a[href^="/#"]:active { background: var(--accent-soft); }
body:has(main :is(#services-heading, #audit-heading, #notifications-heading, #access-heading, #trust, #maintenance-heading):target) .sidebar a[aria-current="page"] { background: transparent; box-shadow: none; color: var(--muted); }
body:has(#services-heading:target) .sidebar a[href="/#services-heading"],
body:has(#audit-heading:target) .sidebar a[href="/#audit-heading"],
body:has(#notifications-heading:target) .sidebar a[href="/#notifications-heading"],
body:has(#access-heading:target) .sidebar a[href="/#access-heading"],
body:has(#trust:target) .sidebar a[href="/#trust"],
body:has(#maintenance-heading:target) .sidebar a[href="/#maintenance-heading"] {
  background: var(--accent-soft); color: var(--accent); box-shadow: inset 3px 0 var(--accent); font-weight: 700;
}
.metric a { display: inline-block; min-height: 44px; padding-top: 10px; color: var(--accent); font-size: .85rem; }
.job-facts { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 220px), 1fr)); gap: 16px; }
.job-facts dt { color: var(--muted); font-size: .85rem; }
.job-facts dd { margin: 4px 0 0; }
.job-load { margin-bottom: 14px; }
.badge.success { color: var(--ok); }
header { margin-bottom: 24px; padding: 20px 24px; border: 1px solid var(--accent-soft);
  border-top: 3px solid var(--accent); border-radius: 12px;
  background: radial-gradient(ellipse at top right, var(--accent-soft), transparent 65%), var(--panel); }
.dashboard-header { display: flex; align-items: center; justify-content: space-between; gap: 20px; }
.dashboard-header > div { min-width: 0; }
.dashboard-header h1 { font-size: clamp(1.6rem, 3vw, 2rem); }
.dashboard-header .meta { margin-top: 12px; }
.dashboard-header .meta div { padding: 0; border-radius: 0; background: none; }
.dashboard-header .meta div + div { border-left: 1px solid var(--line); padding-left: 12px; }
.dashboard-header .refresh-link { margin: 0; flex: none; }
.eyebrow {
  margin: 0 0 6px;
  color: var(--accent);
  font-size: .75rem;
  font-weight: 750;
  letter-spacing: .11em;
  text-transform: uppercase;
}
h1 {
  margin: 0;
  font-size: clamp(2rem, 4vw, 2.75rem);
  letter-spacing: -.045em;
  line-height: 1.06;
}
.lede { max-width: 620px; margin: 12px 0 0; color: var(--muted); }
.meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 22px 0 0;
}
.meta div {
  display: flex;
  gap: 7px;
  padding: 7px 10px;
  border-radius: 6px;
  background: var(--bg);
  font-size: .8rem;
}
.meta dt { color: var(--muted); }
.meta dd { margin: 0; font-weight: 650; }
section { margin-top: 24px; }
.section-heading {
  display: flex;
  align-items: end;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 10px;
}
h2 { margin: 0; font-size: 1.2rem; letter-spacing: -.015em; }
.count { color: var(--muted); font-size: .75rem; }
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 230px), 1fr));
  gap: 10px;
}
.card {
  display: flex;
  min-height: 116px;
  flex-direction: column;
  gap: 4px;
  padding: 12px 14px;
  border: 1px solid var(--accent-soft);
  border-radius: 12px;
  background: var(--panel);
  box-shadow: var(--shadow);
  color: var(--text);
  text-decoration: none;
}
.card:hover { border-color: var(--accent); background: linear-gradient(var(--accent-soft), var(--panel)); }
.card strong { font-size: .9rem; letter-spacing: -.02em; }
.card-top { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.service-kind { color: var(--muted); font-size: .65rem; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; }
.service-arrow { color: var(--accent); font-size: 1.2rem; }
.card:focus-visible, button:focus-visible, summary:focus-visible {
  outline: 3px solid var(--accent);
  outline-offset: 3px;
}
.card-description { color: var(--muted); font-size: .8rem; }
.card-status { display: flex; align-items: center; gap: 6px; font-size: .75rem; font-weight: 700; }
.card-status::before { content: ""; width: 6px; height: 6px; border-radius: 50%; background: currentColor; flex: none; }
.card-status.ready { color: var(--ok); }
.card-status.attention { color: var(--warning); }
.card-status.unavailable { color: var(--bad); }
.card-url {
  margin-top: auto;
  padding-top: 6px;
  border-top: 1px solid var(--accent-soft);
  color: var(--muted);
  font-size: .72rem;
  overflow-wrap: anywhere;
}
.overview-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 170px), 1fr));
  gap: 10px;
  margin: 0;
}
.metric {
  min-height: 110px;
  padding: 12px 14px;
  border: 1px solid var(--accent-soft);
  border-radius: 12px;
  background: var(--panel);
  box-shadow: var(--shadow);
}
.metric dt { color: var(--muted); font-size: .82rem; }
.metric dd { margin: 5px 0 0; }
.metric-value { display: block; font-size: 1.15rem; font-weight: 750; letter-spacing: -.035em; }
.metric-description { display: block; margin-top: 4px; color: var(--muted); font-size: .75rem; }
.host-overview { grid-template-columns: repeat(4, minmax(0, 1fr)); }
meter { display: block; width: 100%; height: 6px; margin: 8px 0; border: 0; border-radius: 999px;
  background: var(--accent-soft); accent-color: var(--accent); }
meter::-webkit-meter-bar { height: 6px; border: 0; border-radius: 999px; background: var(--accent-soft); }
meter::-webkit-meter-optimum-value { background: var(--accent); }
meter::-webkit-meter-suboptimum-value { background: var(--warning); }
meter::-webkit-meter-even-less-good-value { background: var(--bad); }
meter::-moz-meter-bar { background: var(--accent); }
.metric-value.warning { color: var(--warning); }
.agent-activity-notes { display: flex; flex-wrap: wrap; gap: 4px 20px; align-items: center;
  margin-top: 8px; color: var(--muted); font-size: .8rem; }
.agent-activity-notes p { margin: 0; }
.trust-panel {
  padding: 17px 18px 18px;
}
.trust-disclosure {
  border: 1px solid var(--line);
  border-radius: 12px;
  background: var(--panel);
  box-shadow: var(--shadow);
  overflow: hidden;
}
.trust-disclosure > summary {
  display: flex;
  width: auto;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  padding: 16px 18px;
  color: var(--text);
  list-style: none;
}
.trust-disclosure > summary::-webkit-details-marker { display: none; }
.trust-disclosure > summary::after {
  content: "+";
  color: var(--accent);
  font-size: 1.25rem;
  line-height: 1;
}
.trust-disclosure[open] > summary { border-bottom: 1px solid var(--line); }
.trust-disclosure[open] > summary::after { content: "−"; }
.trust-summary-label { display: block; font-weight: 750; }
.trust-summary-note { display: block; color: var(--muted); font-size: .85rem; }
.trust-panel > strong { display: block; }
.trust-panel > p { margin: 5px 0 0; color: var(--muted); }
.trust-panel details { margin-top: 14px; }
.trust-panel details > summary { width: auto; font-weight: 700; }
.trust-panel details[open] > summary { margin-bottom: 10px; }
.trust-panel .trust-actions + .fingerprint { margin-top: 13px; }
.trust-gui { margin: 0; padding-left: 22px; }
.trust-gui li { margin-top: 8px; }
.trust-gui li:first-child { margin-top: 0; }
.trust-panel-public {
  padding: 18px;
  border: 1px solid var(--line);
  border-radius: 12px;
  background: var(--panel);
  box-shadow: var(--shadow);
}
.trust-panel-public > p { margin: 5px 0 0; color: var(--muted); }
.trust-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; margin-top: 15px; }
.trust-download {
  display: inline-block;
  padding: 9px 13px;
  border-radius: 9px;
  background: var(--accent);
  color: var(--panel);
  font-weight: 750;
  text-decoration: none;
}
.fingerprint { display: block; margin-top: 13px; overflow-wrap: anywhere; }
.trust-intro { margin: 10px 0; }
.platform-script { margin-top: 15px; }
.platform-script h3 { margin: 0 0 5px; font-size: .95rem; }
.platform-script pre { margin: 0; max-height: none; user-select: all; }
.access-list {
  list-style: none;
  padding: 0;
  margin: 0;
  overflow: hidden;
  border: 1px solid var(--line);
  border-radius: 12px;
  background: var(--panel);
  box-shadow: var(--shadow);
}
.access-list li {
  display: grid;
  grid-template-columns: 130px minmax(0, 1fr);
  gap: 4px 18px;
  padding: 14px 17px;
  border-bottom: 1px solid var(--line);
}
.access-list li:last-child { border: 0; }
.access-list span { grid-column: 2; color: var(--muted); font-size: .88rem; }
.event-list {
  display: grid;
  gap: 9px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.event {
  padding: 15px 17px;
  border: 1px solid var(--line);
  border-radius: 12px;
  background: var(--panel);
  box-shadow: var(--shadow);
}
.event-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
}
.event-meta { margin: 3px 0 0; color: var(--muted); font-size: .82rem; }
.event-detail { margin: 8px 0 0; }
.event-values { margin: 8px 0 0; color: var(--muted); font-size: .85rem; }
.badge {
  display: inline-block;
  padding: 2px 7px;
  border-radius: 999px;
  background: var(--accent-soft);
  color: var(--accent);
  font-size: .72rem;
  font-weight: 750;
  text-transform: uppercase;
}
.badge.warning { color: var(--warning); }
.badge.error { color: var(--bad); }
.endpoint { margin: 0 0 12px; color: var(--muted); }
.audit-issues {
  margin: 0 0 12px;
  padding: 13px 16px;
  border: 1px solid var(--line);
  border-left: 4px solid var(--bad);
  border-radius: 8px;
  background: var(--panel);
}
.audit-issues strong { display: block; margin-bottom: 4px; }
.audit-issues ul { margin: 0; padding-left: 20px; color: var(--muted); }
code { overflow-wrap: anywhere; }
.empty {
  margin: 0;
  padding: 12px 14px;
  border: 1px dashed var(--line);
  border-radius: 12px;
  color: var(--muted);
}
.action {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 22px;
  padding: 18px;
  border: 1px solid var(--line);
  border-radius: 12px;
  background: var(--panel);
  box-shadow: var(--shadow);
}
.action p { max-width: 590px; margin: 4px 0 0; color: var(--muted); }
button {
  border: 0;
  border-radius: 9px;
  background: var(--accent);
  color: var(--panel);
  font: inherit;
  font-weight: 750;
  padding: 10px 14px;
  cursor: pointer;
  white-space: nowrap;
}
button:disabled { opacity: .58; cursor: wait; }
.status {
  margin: 0 0 30px;
  padding: 15px 17px;
  border: 1px solid var(--line);
  border-left: 4px solid var(--accent);
  border-radius: 8px;
  background: var(--panel);
}
.status.complete { border-left-color: var(--ok); }
.status.failed { border-left-color: var(--bad); }
.status p { margin: 2px 0 0; }
details { margin-top: 10px; }
summary { width: fit-content; max-width: 100%; min-height: 44px; padding: 10px 0; cursor: pointer; color: var(--accent); }
pre {
  max-height: 22rem;
  overflow: auto;
  padding: 12px;
  border-radius: 8px;
  background: var(--bg);
  white-space: pre-wrap;
  font-size: .8rem;
}
footer {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  margin-top: 48px;
  padding-top: 18px;
  border-top: 1px solid var(--line);
  color: var(--muted);
  font-size: .85rem;
}
@media (max-width: 900px) {
  main { margin: 0; padding: 24px 20px 48px; }
  .sidebar { position: static; width: auto; padding: 12px 16px; border-right: 0; border-bottom: 1px solid var(--line); }
  .sidebar strong { margin: 0 8px 6px; }
  .sidebar .nav-links { display: flex; overflow-x: auto; gap: 4px; padding-bottom: 6px;
    scrollbar-width: thin; scrollbar-color: var(--line) var(--panel); }
  .sidebar a { padding: 10px 12px; flex: none; white-space: nowrap; }
  .nav-group { display: none; }
}
@media (min-width: 901px) and (max-width: 1150px) {
  .host-overview { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 700px) {
  .host-overview { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 560px) {
  main { padding: 20px 16px 40px; }
  header { padding: 16px; }
  .dashboard-header { align-items: start; flex-direction: column; gap: 12px; }
  .dashboard-header .meta { gap: 4px 12px; }
  .dashboard-header .meta div + div { border: 0; padding: 0; }
  .overview-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .metric { padding: 12px; }
  .metric-value { font-size: 1.15rem; }
  .section-heading, .event-head { align-items: start; flex-direction: column; gap: 6px; }
  .meta div { flex-wrap: wrap; border-radius: 12px; }
  .access-list li { grid-template-columns: 1fr; }
  .access-list span { grid-column: 1; }
  .action { align-items: stretch; flex-direction: column; }
  button { width: 100%; }
  footer { flex-direction: column; }
}
""".strip()


def _system_type_label(value: str) -> str:
    words = value.replace("_", " ").replace("-", " ").split()
    return " ".join("VM" if word.lower() == "vm" else word.capitalize() for word in words)


def _linux_trust_script(
    fingerprint: str,
    download_url: str,
    install_commands: str,
) -> str:
    return f"""(
  set -eu
  download_url={shlex.quote(download_url)}
  expected_sha256='{fingerprint}'
  certificate='./basaltwater-ca.crt'
  temporary=$(mktemp)
  trap 'rm -f "$temporary"' EXIT
  # Requires an already trusted HTTPS connection. For first enrollment, use SSH.
  if command -v curl >/dev/null 2>&1; then
    curl --fail --location --proto '=https' --proto-redir '=https' --show-error --output "$temporary" "$download_url"
  elif command -v wget >/dev/null 2>&1; then
    wget --https-only --output-document="$temporary" "$download_url"
  else
    echo 'Install curl or wget, then run this script again.'
    exit 1
  fi
  actual_sha256=$(sha256sum "$temporary")
  actual_sha256=${{actual_sha256%% *}}
  if [ "$actual_sha256" != "$expected_sha256" ]; then
    echo 'SHA-256 mismatch; certificate was not installed.'
    exit 1
  fi
  mv "$temporary" "$certificate"
  trap - EXIT
{install_commands}
  echo 'Certificate downloaded, verified, and installed. Fully restart your browser.'
)"""


def _macos_trust_script(fingerprint: str, download_url: str) -> str:
    return f"""(
  set -eu
  download_url={shlex.quote(download_url)}
  expected_sha256='{fingerprint}'
  certificate='./basaltwater-ca.crt'
  temporary=$(mktemp)
  trap 'rm -f "$temporary"' EXIT
  # Requires an already trusted HTTPS connection. For first enrollment, use SSH.
  curl --fail --location --proto '=https' --proto-redir '=https' --show-error --output "$temporary" "$download_url"
  actual_sha256=$(shasum -a 256 "$temporary")
  actual_sha256=${{actual_sha256%% *}}
  if [ "$actual_sha256" != "$expected_sha256" ]; then
    echo 'SHA-256 mismatch; certificate was not installed.'
    exit 1
  fi
  mv "$temporary" "$certificate"
  trap - EXIT
  sudo security add-trusted-cert -d -r trustRoot \\
    -k /Library/Keychains/System.keychain "$certificate"
  echo 'Certificate downloaded, verified, and installed. Fully restart your browser.'
)"""


def _platform_script(label: str, script: str) -> str:
    return (
        '<div class="platform-script"><h3>'
        + html.escape(label)
        + "</h3><pre><code>"
        + html.escape(script)
        + "</code></pre></div>"
    )


def _render_certificate_trust(
    trust: dict[str, str | bool] | None,
) -> str:
    if not trust:
        return f'''<section aria-labelledby="trust-heading"><div class="section-heading"><div>
{render_heading("Certificate trust", "certificate", heading_id="trust-heading")}</div></div>
<p class="empty">No managed gateway certificate information is available on this machine.</p></section>'''
    if trust.get("publicly_trusted") is True:
        return f'''<section aria-labelledby="trust-heading">
<div class="section-heading"><div>
{render_heading("Certificate trust", "certificate", heading_id="trust-heading")}</div></div>
<div class="trust-panel-public"><strong>No certificate installation required</strong>
<p>The shared web-hosting certificate is issued by a publicly trusted authority.</p></div></section>'''
    if trust.get("status") == "unknown":
        return f'''<section aria-labelledby="trust-heading">
<div class="section-heading"><div>
{render_heading("Certificate trust", "certificate", heading_id="trust-heading")}</div></div>
<div class="trust-panel-public"><strong>Certificate trust could not be verified</strong>
<p>Check the gateway certificate and CA on the host with <code>basaltwater-web ca</code> before installing a certificate on this device.</p></div></section>'''

    download_url = str(trust["url"])
    trust_url = html.escape(download_url, quote=True)
    fingerprint = str(trust["sha256"])
    powershell_url = download_url.replace("'", "''")
    scripts = "".join(
        (
            _platform_script(
                "Debian / Ubuntu",
                _linux_trust_script(
                    fingerprint,
                    download_url,
                    "  sudo install -m 0644 \"$certificate\" "
                    "/usr/local/share/ca-certificates/basaltwater-ca.crt\n"
                    "  sudo update-ca-certificates",
                ),
            ),
            _platform_script(
                "Arch Linux",
                _linux_trust_script(
                    fingerprint,
                    download_url,
                    "  if ! sudo trust anchor \"$certificate\"; then\n"
                    "    sudo install -Dm0644 \"$certificate\" "
                    "/etc/ca-certificates/trust-source/anchors/basaltwater-ca.crt\n"
                    "  fi\n"
                    "  sudo update-ca-trust",
                ),
            ),
            _platform_script(
                "Fedora / RHEL",
                _linux_trust_script(
                    fingerprint,
                    download_url,
                    "  sudo install -Dm0644 \"$certificate\" "
                    "/etc/pki/ca-trust/source/anchors/basaltwater-ca.crt\n"
                    "  sudo update-ca-trust extract",
                ),
            ),
            _platform_script(
                "macOS",
                _macos_trust_script(fingerprint, download_url),
            ),
            _platform_script(
                "Windows PowerShell",
                f'''$DownloadUrl = '{powershell_url}'
$ExpectedSha256 = "{fingerprint}"
$Certificate = Join-Path (Get-Location) "basaltwater-ca.crt"
$Temporary = [System.IO.Path]::GetTempFileName()
try {{
  # Requires an already trusted HTTPS connection. For first enrollment, use SSH.
  Invoke-WebRequest -Uri $DownloadUrl -OutFile $Temporary -UseBasicParsing -MaximumRedirection 0 -ErrorAction Stop
  $ActualSha256 = (Get-FileHash -LiteralPath $Temporary -Algorithm SHA256 -ErrorAction Stop).Hash.ToLowerInvariant()
  if ($ActualSha256 -ne $ExpectedSha256) {{
    throw "SHA-256 mismatch; certificate was not installed."
  }}
  Move-Item -LiteralPath $Temporary -Destination $Certificate -Force -ErrorAction Stop
}} catch {{
  Remove-Item -LiteralPath $Temporary -Force -ErrorAction SilentlyContinue
  throw
}}
& certutil.exe -user -addstore -f Root $Certificate
if ($LASTEXITCODE -ne 0) {{ throw "certutil failed with exit code $LASTEXITCODE" }}
Write-Host "Certificate downloaded, verified, and installed. Fully restart your browser."''',
            ),
        )
    )
    escaped_fingerprint = html.escape(fingerprint)
    return f'''<section aria-label="Certificate trust">
<details class="trust-disclosure"><summary><span><span class="trust-summary-label">Certificate trust</span>
<span class="trust-summary-note">One-time setup for browsers and other devices</span></span></summary>
<div class="trust-panel"><strong>Trust this machine on another device</strong>
<p>Install this machine's public CA once to trust the web panel, hosted sites, and managed HTTPS services. The help stays collapsed when you do not need it.</p>
<p>For first enrollment, copy <code>/srv/basaltwater/web/basaltwater-ca.crt</code> through an existing trusted SSH connection. Obtain its SHA-256 with <code>basaltwater-web ca</code> over that connection or the VM console. Verify that independent value before installation; a fingerprint on a page opened past a certificate warning is not proof of authenticity.</p>
<div class="trust-actions"><a class="trust-download" href="{trust_url}">Download VM CA certificate</a></div>
<code class="fingerprint">SHA-256 {escaped_fingerprint}</code>
<details><summary>Download, verify, and install with a script</summary>
<p class="trust-intro">These download scripts require HTTPS that your client already trusts. They stop if TLS or the checksum fails. For first enrollment, use the trusted transfer and manual installation steps below. Never bypass a certificate warning to obtain an installation script.</p>
{scripts}
</details>
<details><summary>Manual / GUI installation</summary><p class="trust-intro">Transfer the certificate over SSH, or download it through an already trusted HTTPS connection, and compare its SHA-256 with the independently obtained fingerprint before following the platform steps.</p><ul class="trust-gui">
<li><strong>Windows:</strong> download the certificate, open it, choose Install Certificate → Current User, then place it in Trusted Root Certification Authorities.</li>
<li><strong>macOS:</strong> download it, import it into the System keychain with Keychain Access, open the certificate, and set Trust to Always Trust.</li>
<li><strong>Linux:</strong> after independent verification, Debian/Ubuntu users can run <code>sudo install -m 0644 basaltwater-ca.crt /usr/local/share/ca-certificates/basaltwater-ca.crt &amp;&amp; sudo update-ca-certificates</code>. On Arch/Fedora, use <code>sudo trust anchor basaltwater-ca.crt &amp;&amp; sudo update-ca-trust</code>.</li>
<li><strong>Firefox on Linux:</strong> Settings → Privacy &amp; Security → Certificates → View Certificates → Authorities → Import. Chromium-based browsers use the operating-system store.</li>
<li><strong>ChromeOS:</strong> Certificate Manager → Authorities → Import, then enable website trust.</li>
<li><strong>Android:</strong> Security &amp; privacy → Install a certificate → CA certificate.</li>
<li><strong>iPhone / iPad:</strong> install the downloaded profile, then enable it under Settings → General → About → Certificate Trust Settings.</li>
</ul></details>
<p class="trust-intro">Transfer only the public certificate. Keep the CA private key on the VM.</p>
</div></details></section>'''


def _render_event_history(rows: list[str]) -> str:
    content = f'<ol class="event-list">{"".join(rows[:5])}</ol>'
    if len(rows) > 5:
        content += (
            f'<details class="history"><summary>Show {len(rows) - 5} more events</summary>'
            f'<ol class="event-list" start="6">{"".join(rows[5:])}</ol></details>'
        )
    return content


def render_service_status(state: WebPanelState, load: bool) -> str:
    """Render service state separately so the dashboard remains fast to open."""

    host = html.escape(state.manifest["host"])
    content = (
        f'<div class="empty-art">{BRAND_NETWORK}<p>Select Load local service status to inspect fixed '
        'system and current-user services.</p></div>'
    )
    if load:
        health = state.service_health()
        cards = "".join(
            '<div class="metric"><dt>{}</dt><dd><span class="metric-value {}">{}</span>'
            '<span class="metric-description">{}</span>{}</dd></div>'.format(
                html.escape(record["label"]),
                {"active": "active", "failed": "failed", "Unavailable": "unavailable"}.get(record["value"], ""),
                html.escape(record["value"]),
                html.escape(record["description"]),
                '<a href="/logs?service={}">Inspect service</a>'.format(
                    urllib.parse.quote(record["unit"], safe=""),
                ) + tool_link("logs", "Prepare agent review", service=record["unit"])
                if record.get("unit") else "",
            )
            for record in health
        )
        attention = sum(record["value"] != "active" for record in health)
        summary = f"{len(health)} records"
        if attention:
            summary += f" · {attention} inactive or unavailable"
        content = f'<p class="count">{summary} · cached up to 30 seconds</p>'
        content += (
            f'<dl class="overview-grid">{cards}</dl>' if cards else
            '<p class="empty">No supported local services were found.</p>'
        )
    header = f'''<header><p class="eyebrow">Basaltwater web panel</p><h1>Local service status</h1>
<p class="lede">Process state for supported services on <code>{host}</code>.</p></header>'''
    body = f'''<form class="job-load" method="get" action="/services"><button name="load" value="1">Load local service status</button></form>
<p class="endpoint">This checks fixed system services and the panel user's T3 Code service. It does not prove public DNS, TLS, or application readiness.</p>
{tool_link("checkup", "Prepare a system checkup")}{content}'''
    footer = '<footer><a href="/">Back to dashboard</a><span>Loaded on request · no automatic refresh</span></footer>'
    return render_document(
        title=f"Local service status · {state.manifest['host']}",
        style=_PAGE_STYLE,
        header=header,
        content=body,
        navigation=panel_navigation(current="services"),
        footer=footer,
    )


def _render_audit_section(state: WebPanelState) -> str:
    snapshot = state.audit_snapshot()
    events = snapshot.get("events", [])
    status = str(snapshot.get("status", "unavailable"))
    generated_at = str(snapshot.get("generated_at", ""))
    issues = snapshot.get("issues", [])
    suppressed_setup_events = snapshot.get("suppressed_setup_events", 0)
    issue_html = ""
    if isinstance(issues, list) and issues:
        issue_rows = "".join(
            f"<li>{html.escape(str(issue))}</li>" for issue in issues
        )
        issue_html = (
            '<aside class="audit-issues" role="status">'
            "<strong>Audit coverage needs attention</strong>"
            f"<ul>{issue_rows}</ul></aside>"
        )
    suppression_html = ""
    if (
        isinstance(suppressed_setup_events, int)
        and not isinstance(suppressed_setup_events, bool)
        and suppressed_setup_events > 0
    ):
        event_label = "event" if suppressed_setup_events == 1 else "events"
        suppression_html = (
            '<p class="endpoint">'
            f"Omitted {suppressed_setup_events:,} routine audit {event_label} "
            "recorded during a managed Basaltwater setup.</p>"
        )
    if events:
        rows = []
        for event in events:
            values: list[str] = []
            for label, name in (
                ("Path", "paths"),
                ("Operation", "operations"),
                ("Actor", "actors"),
                ("Executable", "executables"),
            ):
                entries = event.get(name)
                if isinstance(entries, list) and entries:
                    values.append(f"{label}: {', '.join(str(item) for item in entries)}")
            value_html = (
                f'<p class="event-values">{html.escape(" · ".join(values))}</p>'
                if values
                else ""
            )
            severity = str(event.get("severity", "info"))
            rows.append(
                '<li class="event"><div class="event-head"><strong>{}</strong>'
                '<span class="badge {}">{}</span></div>'
                '<p class="event-meta">{} · audit key <code>{}</code></p>{}</li>'.format(
                    html.escape(str(event.get("meaning", "Audit event"))),
                    html.escape(severity, quote=True),
                    html.escape(severity),
                    html.escape(str(event.get("timestamp", "Unknown time"))),
                    html.escape(str(event.get("key", "unknown"))),
                    value_html,
                )
            )
        content = _render_event_history(rows)
    elif status == "unavailable":
        content = (
            '<p class="empty">Audit data is unavailable. This is not a clean '
            'result; review the coverage warning and audit service status.</p>'
        )
    elif status == "degraded":
        content = (
            '<p class="empty">No matching audit events were returned, but '
            'collection is incomplete. This is not a clean result.</p>'
        )
    else:
        content = '<p class="empty">No matching audit events were recorded in the last 24 hours.</p>'
    if status == "unavailable":
        status_label = "Collection unavailable"
    elif status == "degraded":
        status_label = "Collection incomplete"
    else:
        status_label = f"Snapshot {generated_at}" if generated_at else "Latest 24 hours"
    count = len(events) if isinstance(events, list) else 0
    warning_count = sum(
        event.get("severity") in {"warning", "error"} for event in events
    ) if isinstance(events, list) else 0
    warning_label = f" · {warning_count} warning/error event" + ("s" if warning_count != 1 else "")
    return f'''<section aria-labelledby="audit-heading"><div class="section-heading"><div>
{render_heading("System audit log", "security", heading_id="audit-heading")}</div>
<span class="count">{count} event{"" if count == 1 else "s"}{warning_label} · {html.escape(status_label)}</span>
</div>{issue_html}{suppression_html}{content}</section>'''


def _render_notification_section(state: WebPanelState) -> str:
    if not state.notification_ingest_enabled():
        return f'''<section aria-labelledby="notifications-heading"><div class="section-heading"><div>
{render_heading("Notifications", "notifications", heading_id="notifications-heading")}</div>
<span class="count">Not configured</span></div>
<p class="empty">Remote notifications are not enabled. Configure the HTTPS receiver with <code>--web-panel-notification-ingest</code> during setup to receive events from your managed machines.</p></section>'''
    events = state.notification_events()
    alerts = unresolved_notification_alerts(events)
    alert_rows = []
    for alert in alerts:
        record = alert["record"]
        notification = record["notification"]
        operator = notification["operator"]
        severity = notification["event"]["status"]
        alert_rows.append(
            '<li class="event"><div class="event-head"><strong>{}</strong>'
            '<span class="badge {}">{}</span></div>'
            '<p class="event-meta">Reported system {} · from {} · {} {} · '
            'first received {} · latest received {}</p><p class="event-detail">{}</p></li>'.format(
                html.escape(operator["subject"]), html.escape(severity, quote=True),
                html.escape(severity), html.escape(operator["system"]),
                html.escape(record["source_ip"]), alert["count"],
                "report" if alert["count"] == 1 else "reports",
                html.escape(alert["first_received_at"]), html.escape(record["received_at"]),
                html.escape(operator["what_happened"]),
            )
        )
    alert_content = (
        _render_event_history(alert_rows) if alert_rows else
        '<p class="empty">No unresolved warning or error reports in retained history.</p>'
    )
    if not events:
        content = '<p class="empty">No remote notifications have been received.</p>'
    else:
        rows = []
        for record in events:
            notification = record["notification"]
            event = notification["event"]
            operator = notification["operator"]
            details = str(operator.get("details", ""))
            detail_html = (
                '<details><summary>Details</summary><pre>{}</pre></details>'.format(
                    html.escape(details)
                )
                if details
                else ""
            )
            actions = operator.get("suggested_actions", [])
            action_html = ""
            if isinstance(actions, list) and actions:
                action_html = '<p class="event-values">Suggested: {}</p>'.format(
                    html.escape(" · ".join(str(action) for action in actions))
                )
            status = str(event.get("status", "info"))
            state_label = str(event.get("state", "unknown"))
            occurred_at = event.get("occurred_at")
            timing = (
                f"reported occurrence {occurred_at} · received "
                if isinstance(occurred_at, str)
                else "received "
            )
            rows.append(
                '<li class="event"><div class="event-head"><strong>{}</strong>'
                '<span class="badge {}">{}</span></div>'
                '<p class="event-meta">{} · {} · reported system {} · {}{} '
                'from {}</p>'
                '<p class="event-detail">{}</p>{}{}</li>'.format(
                    html.escape(str(operator.get("subject", "Notification"))),
                    html.escape(status, quote=True),
                    html.escape(status),
                    html.escape(str(operator.get("job", "unknown job"))),
                    html.escape(state_label),
                    html.escape(str(operator.get("system", "Unknown system"))),
                    html.escape(timing),
                    html.escape(str(record.get("received_at", "Unknown time"))),
                    html.escape(str(record.get("source_ip", "unknown"))),
                    html.escape(str(operator.get("what_happened", ""))),
                    action_html,
                    detail_html,
                )
            )
        content = '<details class="history"><summary>Notification history ({} received)</summary>{}</details>'.format(
            len(events), _render_event_history(rows),
        )
    count = len(events)
    full_link = state.notification_ingest_url()
    link_help = ""
    if full_link:
        link_help = f'''<details class="notification-link"><summary>Reveal full sender link</summary>
<p>This link includes the bearer token. Reveal it only on this administrator panel and treat it as a credential.</p>
<pre><code>{html.escape(full_link)}</code></pre>
<p>Paste this complete URL as the target for <code>--notify webhook</code> on a managed sender that can reach this panel.</p></details>'''
    return f'''<section aria-labelledby="notifications-heading"><div class="section-heading"><div>
{render_heading("Notifications", "notifications", heading_id="notifications-heading")}</div>
<span class="count">{len(alerts)} unresolved · {count} received</span></div>
<p class="endpoint">Latest warning and error reports, grouped by sender and alert key. Recovery reports clear matching alerts. This summary covers only the latest 100 receipts; it is not a live health check.</p>
{alert_content}
<p class="endpoint">Ingest endpoint: <code>{WEB_PANEL_NOTIFICATION_ENDPOINT}</code>. Sender names are self-reported; use the receipt address when investigating.</p>
{link_help}
<details class="notification-help"><summary>Configure a Basaltwater sender</summary>
<p>From any managed system that can reach this panel over the local network or another available network, add the panel URL as a webhook target. During an initial sender setup, replace the placeholders with its profile, host, account, and a reachable panel host:</p>
<p>Read the token on this panel host with <code>sudo cat /etc/basaltwater/web-panel/notification-ingest.token</code>.</p>
<pre><code>basaltw setup agent_vm SENDER_HOST SENDER_USER \\
  --notify webhook 'https://PANEL_HOST{WEB_PANEL_NOTIFICATION_ENDPOINT}#TOKEN_FROM_PANEL_HOST'</code></pre>
<p>For an existing sender, use <code>basaltw patch SENDER_HOST SENDER_USER</code> with the same <code>--notify webhook</code> flag.</p>
<p>The token fragment becomes a bearer header and is not sent in the request path. Keep the full fragment-bearing URL private.</p></details>
{content}</section>'''


def render_page(state: WebPanelState) -> str:
    """Render a small no-JavaScript dashboard from current capability state."""

    manifest = state.manifest
    services = _deduplicate_services(
        manifest["services"], discover_basaltwater_web_services()
    )
    service_cards = ""
    service_states = {"ready": 0, "attention": 0, "unavailable": 0, "unchecked": 0}
    for record in services:
        status_html = ""
        status_class = "unchecked"
        description = record["description"] or "Open this service"
        label = record["label"]
        kind = "Web service"
        for prefix, service_kind in (
            ("HTTPS service: ", "HTTPS service"),
            ("Published site: ", "Published site"),
        ):
            if label.startswith(prefix):
                label = label.removeprefix(prefix)
                kind = service_kind
                break
        if description in {"live", "not responding"}:
            tone, text = (
                ("ready", "Responding") if description == "live"
                else ("unavailable", "Not responding")
            )
            status_html = f'<span class="card-status {tone}">{text}</span>'
            status_class = tone
            description = ""
        if (probe_port := _homebox_probe_port(record)) is not None:
            status_class, status_text = _probe_homebox(probe_port)
            status_html = '<span class="card-status {}">{}</span>'.format(
                status_class, html.escape(status_text)
            )
        service_states[status_class] += 1
        icon, category = {
            "Published site": ("web-services", "sea"),
            "HTTPS service": ("service-status", "workspace"),
        }.get(kind, ("web-services", "water"))
        service_cards += (
            '<a class="card service-card tone-{}" href="{}"><span class="card-top"><span class="service-kind">{}{}</span>'
            '<span class="service-arrow" aria-hidden="true">&#8599;</span></span><strong>{}</strong>'
            '{}{}<span class="card-url">{}</span></a>'
        ).format(
            category,
            html.escape(record["url"], quote=True),
            render_icon(icon),
            kind,
            html.escape(label),
            f'<span class="card-description">{html.escape(description)}</span>' if description else "",
            status_html,
            html.escape(record["url"]),
        )
    service_cards = service_cards or (
        f'<div class="empty-art">{BRAND_NETWORK}<p>No hosted web services are available on this machine.</p></div>'
    )

    access_rows = "".join(
        "<li><strong>{}</strong><code>{}</code><span>{}</span></li>".format(
            html.escape(str(record.get("label", "Access"))),
            html.escape(str(record.get("value", ""))),
            html.escape(str(record.get("description", ""))),
        )
        for record in manifest["access"]
        if isinstance(record, dict) and record.get("value")
    )
    access_content = (
        f'<ul class="access-list">{access_rows}</ul>'
        if access_rows
        else '<p class="empty">No additional access methods are configured.</p>'
    )

    overview = state.system_overview()
    overview_cards = ""
    for record in overview:
        value = record["value"]
        tone = record.get("status", "") if record.get("status") in {"warning", "unavailable"} else ""
        meter = ""
        percent = value.removesuffix("% used")
        if (
            record["label"] in {"Memory", "Root disk", "Swap", "Root inodes"} and value.endswith("% used")
            and 1 <= len(percent) <= 3 and percent.isascii() and percent.isdigit()
            and 0 <= int(percent) <= 100
        ):
            tone = "warning" if int(percent) >= 80 else ""
            meter = (
                f'<meter min="0" max="100" low="80" high="95" optimum="0" value="{int(percent)}" '
                f'aria-label="{html.escape(record["label"], quote=True)} used">{int(percent)}%</meter>'
            )
        elif value == "Reboot required":
            tone = "warning"
        elif value == "Unavailable":
            tone = "unavailable"
        overview_cards += (
            '<div class="metric"><dt>{}</dt><dd><span class="metric-value {}">{}</span>{}'
            '<span class="metric-description">{}</span></dd></div>'
        ).format(
            html.escape(record["label"]), tone, html.escape(value), meter,
            html.escape(record["description"]),
        )
    trust_section = _render_certificate_trust(discover_certificate_trust())
    audit_section = _render_audit_section(state)
    notification_section = _render_notification_section(state)

    action = f'''<section aria-labelledby="maintenance-heading"><div class="section-heading"><div>
{render_heading("Maintenance", "maintenance", heading_id="maintenance-heading")}</div></div>
<div class="action"><div><strong>Scheduled maintenance</strong><p>Review update timers, housekeeping jobs, and their last results.</p></div>
<a class="refresh-link" href="/jobs">View scheduled jobs <span aria-hidden="true">→</span></a></div></section>'''
    if state.t3_update_available():
        running = state.action_status == "running"
        disabled = " disabled" if running else ""
        button_label = "Update in progress…" if running else "Update to latest"
        action = f'''<section aria-labelledby="maintenance-heading">
<div class="section-heading"><div>
{render_heading("Maintenance", "maintenance", heading_id="maintenance-heading")}</div></div>
<div class="action"><div><strong>T3 Code</strong><p>Install the latest upstream release, then verify that the managed service is ready. This runs in the background.</p></div>
<form method="post" action="/actions/t3-update">
<input type="hidden" name="csrf" value="{html.escape(state.csrf_token, quote=True)}">
<button type="submit"{disabled}>{button_label}</button></form></div></section>'''

    status = ""
    if state.action_message:
        output = (
            f"<details><summary>View command output</summary><pre>{html.escape(state.action_output)}</pre></details>"
            if state.action_output
            else ""
        )
        role = "alert" if state.action_status == "failed" else "status"
        message = state.action_message
        if state.action_status == "running":
            timeout_minutes = max(1, int(_T3_UPDATE_TIMEOUT_SECONDS // 60))
            started_at = state.action_started_at
            if isinstance(started_at, (int, float)):
                elapsed = max(0, int(time.monotonic() - started_at))
                minutes, seconds = divmod(elapsed, 60)
                message = (
                    f"{message} Still running · {minutes}m {seconds:02d}s elapsed. "
                    "This panel refreshes every 3 seconds; the updater times out "
                    f"after {timeout_minutes} minutes."
                )
            else:
                message = (
                    f"{message} Still running. This panel refreshes every 3 seconds; "
                    f"the updater times out after {timeout_minutes} minutes."
                )
        status = (
            f'<aside class="status {html.escape(state.action_status)}" role="{role}" '
            f'aria-live="polite"><strong>Maintenance status</strong>'
            f"<p>{html.escape(message)}</p>{output}</aside>"
        )

    title = str(manifest.get("title") or "Managed machine")
    host = html.escape(manifest["host"])
    username = html.escape(manifest["username"])
    system_type = html.escape(_system_type_label(manifest["system_type"]))
    refresh = (
        '<meta http-equiv="refresh" content="3">'
        if state.action_status == "running"
        else ""
    )
    service_count = f"{len(services)} service" + ("" if len(services) == 1 else "s")
    if services:
        service_count += f" · {service_states['ready']} responding"
        attention_count = service_states["attention"] + service_states["unavailable"]
        if attention_count:
            service_count += f" · {attention_count} {'needs' if attention_count == 1 else 'need'} attention"
        if service_states["unchecked"]:
            service_count += f" · {service_states['unchecked']} not checked"
    access_count = sum(
        1
        for record in manifest["access"]
        if isinstance(record, dict) and record.get("value")
    )
    access_label = f"{access_count} method" + ("" if access_count == 1 else "s")
    header = f'''<header class="dashboard-header"><div><p class="eyebrow">Basaltwater web panel</p><h1>{html.escape(title)}</h1>
<dl class="meta"><div><dt>Host</dt><dd><code>{host}</code></dd></div>
<div><dt>System</dt><dd>{system_type}</dd></div>
<div><dt>User</dt><dd>{username}</dd></div></dl></div>
<a class="refresh-link" href="/">Refresh dashboard</a></header>'''
    body = f'''{status}<section aria-labelledby="overview-heading"><div class="section-heading"><div>
{render_heading("System overview", "dashboard", heading_id="overview-heading")}</div>
<span class="count">Snapshot on page load · cached up to 30 seconds</span></div>
<dl class="overview-grid host-overview">{overview_cards}</dl></section>
{render_agent_activity(state)}
<section aria-labelledby="services-heading"><div class="section-heading"><div>
{render_heading("Web services", "web-services", heading_id="services-heading")}</div>
<span class="count">{service_count}</span></div><div class="grid">{service_cards}</div></section>
{audit_section}{notification_section}
<section aria-labelledby="access-heading"><div class="section-heading"><div>
{render_heading("Access", "access", heading_id="access-heading")}</div>
<span class="count">{access_label}</span></div>{access_content}</section><div id="trust">{trust_section}</div>{action}
'''
    footer = f'<footer><span>Managed by Basaltwater</span><span>Authenticated as {username}</span></footer>'
    return render_document(
        title=f"Web panel · {title}",
        style=_PAGE_STYLE,
        header=header,
        content=body,
        navigation=panel_navigation(current="dashboard"),
        footer=footer,
        refresh=refresh,
    )


class WebPanelHandler(BaseHTTPRequestHandler):
    server_version = "basaltwater-web-panel/1"
    sys_version = ""
    state: WebPanelState

    def _send(
        self,
        status: HTTPStatus,
        body: str,
        content_type: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(payload)

    def _send_json(self, status: HTTPStatus, value: dict[str, object]) -> None:
        self._send(
            status,
            json.dumps(value, separators=(",", ":")) + "\n",
            "application/json",
        )

    def do_GET(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        if path == "/favicon.svg":
            self._send(HTTPStatus.OK, render_favicon(), "image/svg+xml")
            return
        if path == "/healthz":
            self._send(HTTPStatus.OK, "ok\n", "text/plain")
            return
        if path == "/agents":
            try:
                query = parse_agent_query(parsed.query)
            except ValueError:
                self._send(HTTPStatus.BAD_REQUEST, "Invalid agent view\n", "text/plain")
                return
            self._send(HTTPStatus.OK, render_agents(self.state, _PAGE_STYLE, query), "text/html")
            return
        if path == "/agent-tools":
            try:
                query = parse_tools_query(parsed.query)
            except ValueError:
                self._send(HTTPStatus.BAD_REQUEST, "Invalid agent tool view\n", "text/plain")
                return
            self._send(HTTPStatus.OK, render_tools(self.state, _PAGE_STYLE, query), "text/html")
            return
        if path == "/admin":
            try:
                query = parse_admin_query(parsed.query)
            except ValueError:
                self._send(HTTPStatus.BAD_REQUEST, "Invalid administration view\n", "text/plain")
                return
            self._send(HTTPStatus.OK, render_admin(self.state, _PAGE_STYLE, query), "text/html")
            return
        if path == "/logs":
            try:
                query = parse_diagnostic_query(parsed.query)
            except ValueError:
                self._send(HTTPStatus.BAD_REQUEST, "Invalid diagnostic filters\n", "text/plain")
                return
            self._send(
                HTTPStatus.OK,
                render_diagnostics(
                    query,
                    _PAGE_STYLE,
                    self.state.manifest["host"],
                ),
                "text/html",
            )
            return
        if path == "/jobs":
            try:
                load = parse_job_query(parsed.query)
            except ValueError:
                self._send(HTTPStatus.BAD_REQUEST, "Invalid scheduled job query\n", "text/plain")
                return
            self._send(
                HTTPStatus.OK,
                render_jobs(
                    load,
                    _PAGE_STYLE,
                    self.state.manifest["host"],
                ),
                "text/html",
            )
            return
        if path == "/services":
            try:
                load = _parse_on_demand_query(parsed.query)
            except ValueError:
                self._send(HTTPStatus.BAD_REQUEST, "Invalid local service query\n", "text/plain")
                return
            self._send(HTTPStatus.OK, render_service_status(self.state, load), "text/html")
            return
        if path != "/":
            self._send(HTTPStatus.NOT_FOUND, "Not found\n", "text/plain")
            return
        self._send(HTTPStatus.OK, render_page(self.state), "text/html")

    def do_POST(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        if path == WEB_PANEL_NOTIFICATION_ENDPOINT:
            self._handle_notification_ingest()
            return
        agent_paths = {"/actions/agent-task/save", "/actions/agent-task", "/actions/agent-diagnostics", "/actions/agent-tool/prepare"}
        admin_paths = {"/actions/admin", "/actions/admin/cancel"}
        if path != "/actions/t3-update" and path not in agent_paths | admin_paths:
            self._send(HTTPStatus.NOT_FOUND, "Not found\n", "text/plain")
            return
        if parsed.query:
            self._send(HTTPStatus.BAD_REQUEST, "Invalid action query\n", "text/plain")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if not 0 <= length <= _MAX_REQUEST_BYTES:
            self._send(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Invalid request\n", "text/plain")
            return
        try:
            values = urllib.parse.parse_qs(
                self.rfile.read(length).decode("utf-8", errors="strict"),
                keep_blank_values=True,
                max_num_fields=18,
            )
        except (UnicodeDecodeError, ValueError):
            self._send(HTTPStatus.BAD_REQUEST, "Invalid request\n", "text/plain")
            return
        if len(values.get("csrf", [])) != 1 or not secrets.compare_digest(
            values["csrf"][0].encode("utf-8"), self.state.csrf_token.encode("utf-8"),
        ):
            self._send(HTTPStatus.FORBIDDEN, "Invalid request\n", "text/plain")
            return
        if path in agent_paths:
            self._handle_agent_action(path, values)
            return
        if path in admin_paths:
            self._handle_admin_action(path, values)
            return
        if set(values) - {"csrf", "return"} or any(len(entries) != 1 for entries in values.values()) or values.get("return", ["dashboard"])[0] not in {"agents", "admin", "dashboard"}:
            self._send(HTTPStatus.BAD_REQUEST, "Invalid update action\n", "text/plain")
            return
        if not self.state.trigger_t3_update():
            self._send(HTTPStatus.CONFLICT, "Action is unavailable\n", "text/plain")
            return
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", {"agents": "/agents", "admin": "/admin"}.get(values.get("return", [""])[0], "/"))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _handle_admin_action(self, path: str, values: dict[str, list[str]]) -> None:
        expected = {"csrf", "id"} if path.endswith("/cancel") else {"csrf", "action", "ticket", "confirmation"}
        if set(values) != expected or any(len(entries) != 1 for entries in values.values()):
            self._send(HTTPStatus.BAD_REQUEST, "Invalid administration action\n", "text/plain")
            return
        try:
            if path.endswith("/cancel"):
                self.state.admin.cancel(values["id"][0])
            else:
                self.state.admin.submit(values["action"][0], values["ticket"][0], values["confirmation"][0])
        except (OSError, ValueError, RuntimeError):
            self._send(HTTPStatus.CONFLICT, render_admin(self.state, _PAGE_STYLE, {}, error=
                "The action could not be submitted. It may be unavailable, expired, or already submitted; power controls also require the exact host name. Check Recent requests and refresh status before trying again."), "text/html")
            return
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", "/admin")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _handle_agent_action(self, path: str, values: dict[str, list[str]]) -> None:
        allowed = {
            "/actions/agent-tool/prepare": {"csrf", *FORM_FIELDS},
            "/actions/agent-diagnostics": {"csrf"},
            "/actions/agent-task": {"csrf", "id", "action"},
            "/actions/agent-task/save": {
                "csrf", "id", "title", "prompt", "directory", "mode", "interval", "model",
                "custom_model", "network", "submit", "effort", "timeout_minutes",
                "web_search", "session_history", "temporary_files",
                "failure_limit", "repeat_minutes",
            },
        }[path]
        if set(values) - allowed or any(len(entries) != 1 for entries in values.values()):
            self._send(HTTPStatus.BAD_REQUEST, "Invalid agent action\n", "text/plain")
            return
        if path == "/actions/agent-tool/prepare":
            inputs = {key: entries[0] for key, entries in values.items() if key != "csrf"}
            try:
                submitted = prepare_tool(inputs, self.state.agent_tasks.home)
            except (OSError, ValueError, RuntimeError) as exc:
                self._send(HTTPStatus.UNPROCESSABLE_ENTITY, render_tools(
                    self.state, _PAGE_STYLE, {}, error=str(exc), submitted=inputs,
                ), "text/html")
                return
            self._send(HTTPStatus.OK, render_agents(self.state, _PAGE_STYLE, {}, submitted=submitted, prepared=True), "text/html")
            return
        if path == "/actions/agent-diagnostics":
            if not self.state.agent_diagnostics.trigger():
                self._send(HTTPStatus.CONFLICT, "Agent diagnostics are already running\n", "text/plain")
                return
        else:
            manager = self.state.agent_tasks
            if manager.error or not manager.available():
                self._send(HTTPStatus.SERVICE_UNAVAILABLE, "Prompt task execution is unavailable\n", "text/plain")
                return
            submitted = {key: entries[0] for key, entries in values.items() if key != "csrf"}
            submitted["network"] = values.get("network") == ["1"]
            submitted["session_history"] = values.get("session_history") == ["1"]
            submitted["temporary_files"] = values.get("temporary_files") == ["1"]
            try:
                if path == "/actions/agent-task":
                    manager.action(values.get("id", [""])[0], values.get("action", [""])[0])
                else:
                    for option in ("network", "session_history", "temporary_files"):
                        if option in values and values[option] != ["1"]:
                            raise ValueError("Invalid checkbox option")
                    if submitted.get("custom_model"):
                        submitted["model"] = submitted["custom_model"]
                    identifier = values.get("id", [""])[0]
                    choice = values.get("submit", [""])[0]
                    if identifier:
                        if choice != "save":
                            raise ValueError("Select Save changes for an existing task")
                        manager.update(identifier, submitted)
                    else:
                        if choice not in {"run", "schedule", "draft"}:
                            raise ValueError("Select Run now, Create schedule, or Save draft")
                        manager.create(submitted, run_now=choice == "run", draft=choice == "draft")
            except ValueError as exc:
                self._send(HTTPStatus.UNPROCESSABLE_ENTITY, render_agents(
                    self.state, _PAGE_STYLE, {}, error=str(exc),
                    submitted=submitted if path.endswith("/save") else None,
                ), "text/html")
                return
            except (OSError, RuntimeError):
                self._send(HTTPStatus.SERVICE_UNAVAILABLE, "Prompt task storage is unavailable\n", "text/plain")
                return
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", "/agents")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _handle_notification_ingest(self) -> None:
        if not self.state.notification_ingest_enabled():
            self._send(HTTPStatus.NOT_FOUND, "Not found\n", "text/plain")
            return
        if self.headers.get("X-Forwarded-Proto", "").lower() != "https":
            self._send(HTTPStatus.FORBIDDEN, "Secure transport required\n", "text/plain")
            return
        if not self.state.authorized_for_ingest(
            self.headers.get("Authorization", "")
        ):
            self._send(
                HTTPStatus.UNAUTHORIZED,
                "Unauthorized\n",
                "text/plain",
                headers={"WWW-Authenticate": 'Bearer realm="basaltwater"'},
            )
            return
        content_type = self.headers.get("Content-Type", "").partition(";")[0].strip()
        if content_type.lower() != "application/json":
            self._send(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                "Expected application/json\n",
                "text/plain",
            )
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if not 0 < length <= _MAX_INGEST_REQUEST_BYTES:
            self._send(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                "Invalid request\n",
                "text/plain",
            )
            return
        try:
            payload = json.loads(
                self.rfile.read(length).decode("utf-8", errors="strict"),
                parse_constant=_reject_json_constant,
            )
            source_ip = self.headers.get("X-Real-IP", "")
            try:
                source_ip = str(ipaddress.ip_address(source_ip))
            except ValueError:
                source_ip = "unknown"
            stored = self.state.accept_notification(payload, source_ip)
        except (RecursionError, UnicodeDecodeError, ValueError):
            self._send_json(HTTPStatus.BAD_REQUEST, {"accepted": False})
            return
        except (OSError, RuntimeError):
            self._send_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"accepted": False},
            )
            return
        self._send_json(
            HTTPStatus.ACCEPTED if stored else HTTPStatus.OK,
            {"accepted": True, "duplicate": not stored},
        )

    def log_message(self, format_string: str, *args: object) -> None:
        return


class _ThreadingUnixHTTPServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


class _ThreadingTCPHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Serve the Basaltwater web panel")
    parser.add_argument("--config", required=True)
    listener = parser.add_mutually_exclusive_group(required=True)
    listener.add_argument("--socket")
    listener.add_argument("--listen", choices=("127.0.0.1", "::1"))
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--socket-group", type=int)
    return parser


def main() -> int:
    args = _parser().parse_args()
    state = WebPanelState(_load_manifest(args.config))
    WebPanelHandler.state = state
    if args.socket:
        if args.socket_group is not None and args.socket_group < 0:
            raise ValueError("--socket-group must be a non-negative group ID")
        if os.path.lexists(args.socket):
            if os.path.islink(args.socket) or not os.path.exists(args.socket):
                raise RuntimeError(f"Refusing unsafe web panel socket: {args.socket}")
            os.unlink(args.socket)
        previous_umask = os.umask(0o177)
        try:
            server: socketserver.BaseServer = _ThreadingUnixHTTPServer(
                args.socket, WebPanelHandler
            )
        finally:
            os.umask(previous_umask)
        try:
            if args.socket_group is not None:
                os.chown(args.socket, -1, args.socket_group)
            os.chmod(args.socket, 0o660)
        except OSError:
            server.server_close()
            try:
                os.unlink(args.socket)
            except FileNotFoundError:
                pass
            raise
    else:
        if args.socket_group is not None:
            raise ValueError("--socket-group requires --socket")
        if not 1 <= args.port <= 65535:
            raise ValueError("--port must be between 1 and 65535")
        server = _ThreadingTCPHTTPServer((args.listen, args.port), WebPanelHandler)
    try:
        state.agent_tasks.start()
        server.serve_forever(poll_interval=0.5)
    finally:
        state.agent_tasks.close()
        server.server_close()
        if args.socket:
            try:
                os.unlink(args.socket)
            except FileNotFoundError:
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
