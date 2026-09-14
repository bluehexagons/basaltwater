"""On-demand, unprivileged service diagnostics for the authenticated panel."""

from __future__ import annotations

import html
import json
import os
import re
import selectors
import shlex
import subprocess
import threading
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone

from common.service_tools.web_panel_templates import panel_navigation, render_document

SYSTEM_UNITS = {
    "nginx.service": "Web gateway",
    "ssh.service": "SSH",
    "gogs.service": "Gogs",
    "homebox.service": "HomeBox",
    "docker.service": "Docker",
    "smbd.service": "File sharing",
    "xrdp.service": "Remote desktop",
    "fail2ban.service": "Login protection",
    "auditd.service": "System audit",
    "infra-tools-web-panel.service": "Web panel",
}
JOB_SERVICES = {
    "infra-tools-web-panel-audit.service": "Audit snapshot exporter",
    "auto-update-apt.service": "Package updates",
    "auto-update-uv.service": "uv updates",
    "auto-update-node.service": "Node.js updates",
    "auto-update-gogs.service": "Gogs updates",
    "auto-update-homebox.service": "HomeBox updates",
    "auto-update-godot.service": "Godot updates",
    "security-monitor.service": "Security monitoring",
    "auto-restart-if-needed.service": "Automatic restart check",
    "cleanup-maintenance.service": "System cleanup",
    "user-cache-maintenance.service": "User cache cleanup",
    "storage-ops.service": "Storage operations",
}
SOURCES = {
    **SYSTEM_UNITS,
    **JOB_SERVICES,
    "t3code.service": "T3 Code (current user)",
}
WINDOWS = {"1h": "Last hour", "24h": "Last 24 hours", "boot": "Current boot"}
PRIORITIES = {"4": "Warnings and errors", "3": "Errors only", "7": "All priorities"}
PROPERTIES = {
    "LoadState": "Unit availability",
    "ActiveState": "Process state",
    "SubState": "Process detail",
    "UnitFileState": "Start at boot",
    "ActiveEnterTimestamp": "Last activated",
    "NRestarts": "Automatic restarts",
    "MemoryCurrent": "Current memory",
    "TasksCurrent": "Current tasks",
    "Result": "Last service result",
    "ExecMainStatus": "Main process exit status",
}
_MAX_BYTES = 64 * 1024
_TIMEOUT = 5
_COLLECTORS = threading.BoundedSemaphore(2)
_PRIVATE_KEY_PATTERN = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.DOTALL,
)
_CREDENTIAL_VALUE_PATTERN = re.compile(
    r"(?ix)\b(authorization|cookie|set-cookie|password|passwd|secret|api[_-]?key|"
    r"access[_-]?token|refresh[_-]?token|client[_-]?secret)\s*([:=])\s*"
    r"(?:bearer\s+|basic\s+)?(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)
_SENSITIVE_HEADER_PATTERN = re.compile(
    r"(?im)^\s*(authorization|cookie|set-cookie)\s*:\s*[^\r\n]*"
)
_CREDENTIAL_URL_PATTERN = re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s<>()]+", re.IGNORECASE)
_KNOWN_SECRET_PARAMETERS = {
    "access_token",
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "client_secret",
    "key",
    "password",
    "secret",
    "sig",
    "signature",
    "token",
}


@dataclass(frozen=True)
class DiagnosticQuery:
    service: str = "nginx.service"
    window: str = "1h"
    priority: str = "4"
    load: bool = False
    search: str = ""


def parse_query(raw: str) -> DiagnosticQuery:
    """Accept only fixed diagnostic choices, with no arbitrary unit or path."""

    if len(raw) > 2048:
        raise ValueError("Diagnostic query is too long")
    values = urllib.parse.parse_qs(raw, keep_blank_values=True, max_num_fields=5)
    choices = {"service": SOURCES, "window": WINDOWS, "priority": PRIORITIES, "load": {"1": "Load"}}
    for key, entries in values.items():
        if key == "search":
            if len(entries) != 1 or len(entries[0]) > 120 or any(ord(c) < 32 for c in entries[0]):
                raise ValueError("Invalid message search")
            continue
        if key not in choices or len(entries) != 1 or entries[0] not in choices[key]:
            raise ValueError("Invalid diagnostic filter")
    return DiagnosticQuery(
        service=values.get("service", ["nginx.service"])[0],
        window=values.get("window", ["1h"])[0],
        priority=values.get("priority", ["4"])[0],
        load="load" in values,
        search=values.get("search", [""])[0],
    )


def _bounded_command(command: list[str]) -> tuple[str, str]:
    """Drain at most 64 KiB from a fixed command and reap it on every exit."""

    output = bytearray()
    deadline = time.monotonic() + _TIMEOUT
    try:
        with subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, env={**os.environ, "LC_ALL": "C", "SYSTEMD_COLORS": "0"},
        ) as process:
            try:
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    while True:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0 or not selector.select(remaining):
                            return output.decode("utf-8", errors="replace"), "Query timed out; results may be incomplete."
                        chunk = os.read(process.stdout.fileno(), min(4096, _MAX_BYTES + 1 - len(output)))
                        if not chunk:
                            code = process.wait(timeout=max(0.01, deadline - time.monotonic()))
                            # journalctl returns 1 when --grep finds no matches.
                            no_matches = (
                                code == 1 and not output and command[0] == "journalctl"
                                and any(arg.startswith("--grep=") for arg in command)
                            )
                            return output.decode("utf-8", errors="replace"), (
                                "Query unavailable; the service or journal may not be accessible."
                                if code and not no_matches else ""
                            )
                        output.extend(chunk)
                        if len(output) > _MAX_BYTES:
                            return output[:_MAX_BYTES].decode("utf-8", errors="replace"), "Output limit reached; narrow the time window or priority."
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait()
    except (OSError, subprocess.TimeoutExpired):
        return "", "Query unavailable; the local diagnostic command could not complete."


def _journal_command(query: DiagnosticQuery) -> list[str]:
    command = [
        "journalctl", "--user" if query.service == "t3code.service" else "--system",
        "--unit=" + query.service, "--no-pager", "--lines=100", "--reverse",
        "--priority=" + query.priority,
    ]
    command += ["--boot=0"] if query.window == "boot" else ["--since=-" + query.window]
    if query.search:
        command += ["--grep=" + re.escape(query.search), "--case-sensitive=no"]
    return command


def _redact_log_message(message: str) -> str:
    """Hide common credential encodings while retaining operational context."""

    message = _PRIVATE_KEY_PATTERN.sub("[private key redacted]", message)
    message = _SENSITIVE_HEADER_PATTERN.sub(
        lambda match: f"{match[1]}: [redacted]", message
    )
    message = _CREDENTIAL_VALUE_PATTERN.sub(
        lambda match: f"{match[1]}{match[2]}[redacted]", message
    )

    def redact_url(match: re.Match[str]) -> str:
        try:
            parsed = urllib.parse.urlsplit(match[0])
            if not parsed.hostname:
                return match[0]
            query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
            safe_query = urllib.parse.urlencode(
                [
                    (name, "[redacted]" if name.lower() in _KNOWN_SECRET_PARAMETERS else value)
                    for name, value in query
                ],
                doseq=True,
                safe="[]",
            )
            host = parsed.hostname
            if parsed.port is not None:
                host += f":{parsed.port}"
            return urllib.parse.urlunsplit(
                (parsed.scheme, host, parsed.path, safe_query, "[redacted]" if parsed.fragment else "")
            )
        except ValueError:
            return match[0]

    return _CREDENTIAL_URL_PATTERN.sub(redact_url, message)


def collect_diagnostics(query: DiagnosticQuery) -> dict[str, object]:
    """Load bounded runtime properties and the newest 100 matching messages."""

    if (
        query.service not in SOURCES or query.window not in WINDOWS or query.priority not in PRIORITIES
        or len(query.search) > 120 or any(ord(c) < 32 for c in query.search)
    ):
        raise ValueError("Invalid diagnostic filter")
    if not _COLLECTORS.acquire(blocking=False):
        return {"issues": ["Diagnostics are busy. Try loading again shortly."], "properties": {}, "events": []}
    try:
        scope = ["--user"] if query.service == "t3code.service" else []
        properties, property_issue = _bounded_command([
            "systemctl", *scope, "show", query.service, "--no-pager",
            "--property=" + ",".join(PROPERTIES),
        ])
        fields = dict(line.split("=", 1) for line in properties.splitlines() if "=" in line)
        fields = {key: value for key, value in fields.items() if key in PROPERTIES}
        command = [
            *_journal_command(query), "--output=json",
            "--output-fields=MESSAGE,PRIORITY,__REALTIME_TIMESTAMP",
        ]
        journal, journal_issue = _bounded_command(command)
        issues = [issue for issue in (property_issue, journal_issue) if issue]
        if not fields and not property_issue:
            issues.append("Service properties were unavailable.")
        events = []
        for line in journal.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                if line.strip():
                    issues.append("Journal access or output is incomplete; some entries may be hidden.")
                continue
            if not isinstance(event, dict):
                issues.append("An unreadable journal record was omitted.")
                continue
            message = event.get("MESSAGE")
            if not isinstance(message, str):
                message = "[Binary or oversized message omitted by the journal]"
            else:
                message = _redact_log_message(message)
            timestamp = "Unknown time"
            try:
                timestamp = datetime.fromtimestamp(
                    int(event.get("__REALTIME_TIMESTAMP", "")) / 1_000_000, timezone.utc,
                ).isoformat(timespec="seconds")
            except (ValueError, TypeError, OverflowError, OSError):
                pass
            priority = str(event.get("PRIORITY", ""))
            events.append({"message": message, "timestamp": timestamp, "priority": priority})
            if len(events) == 100:
                break
        return {"properties": fields, "events": events, "issues": list(dict.fromkeys(issues))}
    finally:
        _COLLECTORS.release()


def _options(choices: dict[str, str], selected: str) -> str:
    return "".join(
        f'<option value="{html.escape(key, quote=True)}"{" selected" if key == selected else ""}>{html.escape(label)}</option>'
        for key, label in choices.items()
    )


def _property_value(key: str, value: str) -> str:
    if not value or value in {"[not set]", "infinity", "18446744073709551615"}:
        return "Not reported"
    if key == "MemoryCurrent" and value.isdigit():
        return f"{int(value) / (1024 ** 2):.1f} MiB"
    return value


def render_diagnostics(
    query: DiagnosticQuery,
    style: str,
    host: str,
    *,
    notification_ingest: bool = False,
) -> str:
    """Render a separate screen; opening the form never invokes a collector."""

    content = '<p class="empty">Choose a service and select Load diagnostics. No log query has run yet.</p>'
    if query.load:
        result = collect_diagnostics(query)
        issues = "".join(f'<li>{html.escape(issue)}</li>' for issue in result["issues"])
        warning = f'<aside class="audit-issues" role="status"><strong>Collection notice</strong><ul>{issues}</ul></aside>' if issues else ""
        metrics = "".join(
            '<div class="metric"><dt>{}</dt><dd class="metric-value">{}</dd></div>'.format(
                html.escape(PROPERTIES[key]), html.escape(_property_value(key, value)),
            ) for key, value in result["properties"].items()
        )
        rows = []
        severity_names = {"0": "Emergency", "1": "Alert", "2": "Critical", "3": "Error", "4": "Warning", "5": "Notice", "6": "Info", "7": "Debug"}
        for event in result["events"]:
            severity = severity_names.get(event["priority"], "Unknown priority")
            badge = "error" if event["priority"] in {"0", "1", "2", "3"} else "warning" if event["priority"] == "4" else "info"
            message = event["message"]
            safe_message = _redact_log_message(message) if isinstance(message, str) else "[Invalid message omitted]"
            rows.append(
                '<li class="event"><div class="event-head"><time>{}</time><span class="badge {}">{}</span></div><pre>{}</pre></li>'.format(
                    html.escape(event["timestamp"]), badge, severity, html.escape(safe_message),
                )
            )
        logs = f'<ol class="event-list">{"".join(rows)}</ol>' if rows else '<p class="empty">No matching entries are visible to the panel account. The journal may be restricted, rotated, or empty for these filters.</p>'
        content = f'''{warning}<section aria-labelledby="runtime-heading"><h2 id="runtime-heading">Runtime details</h2>
<p class="endpoint">Current values; restart counts and resource accounting depend on the service manager.</p>
<dl class="overview-grid">{metrics}</dl></section>
<section aria-labelledby="journal-heading"><div class="section-heading"><h2 id="journal-heading">Recent journal entries</h2><span class="count">{len(rows)} entries · newest first · UTC · maximum 100</span></div>{logs}</section>'''
    ssh_command = shlex.join([
        *([] if query.service == "t3code.service" else ["sudo"]),
        *_journal_command(query), "--output=short-iso", "--utc",
    ])
    header = f'''<header><p class="eyebrow">infra-tools web panel</p><h1>Service diagnostics</h1>
<p class="lede">Inspect runtime details and recent logs on <code>{html.escape(host)}</code>.</p></header>'''
    body = f'''<form class="diagnostic-filters" method="get" action="/logs">
<div><label for="service-filter">Service</label><select id="service-filter" name="service">{_options(SOURCES, query.service)}</select></div>
<div><label for="window-filter">Time window</label><select id="window-filter" name="window">{_options(WINDOWS, query.window)}</select></div>
<div><label for="priority-filter">Severity</label><select id="priority-filter" name="priority">{_options(PRIORITIES, query.priority)}</select></div>
<div><label for="message-filter">Message contains</label><input id="message-filter" name="search" type="search" maxlength="120" value="{html.escape(query.search, quote=True)}" placeholder="Optional text"></div>
<button name="load" value="1" type="submit">Load diagnostics</button></form>
<p class="endpoint">Only logs readable by the panel account are included. System and user journals have separate permissions. Messages are supplied by services; review them before sharing.</p>
{content}<details><summary>Continue inspection over SSH</summary>
<p>Run on this host for the same filters. System logs may require administrator access; user logs belong to the signed-in Linux user.</p>
<pre><code>{html.escape(ssh_command)}</code></pre></details>'''
    footer = '<footer><a href="/">Back to dashboard</a><span>Loaded on request · no automatic refresh</span></footer>'
    return render_document(
        title=f"Service diagnostics · {host}",
        style=style,
        header=header,
        content=body,
        navigation=panel_navigation(
            current="logs", include_notifications=notification_ingest
        ),
        footer=footer,
    )
