"""Render structured evidence from security monitor notifications."""

from __future__ import annotations

import html
from typing import Any

from common.service_tools.web_panel_refresh import render_log_time


_SOURCES = {"ssh_journal": "SSH system journal", "auditd": "Audit log", "fail2ban": "Fail2ban log"}
_EVENT_FIELDS = {
    "source_retention_gap": (
        ("Unobserved from", "since"), ("Until (exclusive)", "until"),
        ("Oldest retained entry", "oldest_retained"),
    ),
    "source_health": (("Collection issue", "details"),),
    "auditd": (
        ("Audit key", "key"), ("Audit events", "event_count"),
        ("First seen", "first_seen"), ("Last seen", "last_seen"),
        ("Modules", "module_names"), ("Paths", "paths"), ("Operations", "operations"),
        ("Results", "outcomes"), ("Actors", "actors"), ("Executables", "executables"),
        ("Audit login identity", "login_context"),
    ),
    "fail2ban": (("Logged at", "timestamp"), ("Jail", "jail"), ("Source address", "source_ip")),
    "ssh_authentication": (
        ("Failed attempts", "failure_count"), ("Reporting threshold", "reporting_threshold"),
        ("Additional source groups", "suppressed_sources"),
        ("Failures without source breakdown", "unattributed_failures"),
    ),
    "account_lockout": (
        ("Account", "username"), ("Source address", "source_ip"), ("Occurrences", "count"),
        ("First seen", "first_seen"), ("Last seen", "last_seen"),
    ),
    "xrdp_certificate": (("Certificate health", "details"),),
    "monitor_recovery": (("Collection status", "message"),),
}
_TIME_FIELDS = {
    "since", "until", "coverage_since", "coverage_until", "oldest_retained",
    "first_seen", "last_seen", "timestamp",
}
_MAX_FINDINGS = 20
_INDIVIDUAL_EVENTS = {"fail2ban", "account_lockout"}


def _text(value: object) -> str:
    if isinstance(value, str):
        return value
    if type(value) is int:
        return str(value)
    if isinstance(value, list):
        return ", ".join(item for item in value if isinstance(item, str) and item)
    return ""


def _facts(data: dict[str, Any], fields: tuple[tuple[str, str], ...]) -> str:
    rows = []
    for label, key in fields:
        if value := _text(data.get(key)):
            rendered = render_log_time(value) if key in _TIME_FIELDS else html.escape(value)
            rows.append(f"<dt>{label}</dt><dd>{rendered}</dd>")
    return f'<dl class="event-facts">{"".join(rows)}</dl>' if rows else ""


def _finding_title(kind: str, event: dict[str, Any]) -> str:
    if kind in {"source_retention_gap", "source_health"}:
        source = _SOURCES.get(_text(event.get("source")), "Security event source")
        return f"{source} coverage gap" if kind == "source_retention_gap" else f"{source} unavailable"
    if kind == "auditd":
        meaning = _text(event.get("meaning")) or "Audit activity"
        return meaning[0].upper() + meaning[1:]
    if kind == "fail2ban":
        return "Fail2ban unban" if event.get("action") == "unban" else "Fail2ban ban"
    if kind == "xrdp_certificate":
        return _text(event.get("change")) or "XRDP TLS certificate"
    return {
        "ssh_authentication": "SSH authentication failures",
        "account_lockout": "Account lockout",
        "monitor_recovery": "Event sources recovered",
    }[kind]


def render_security_findings(notification: dict[str, Any]) -> tuple[str, str]:
    """Return a legacy summary and a readable view of available evidence."""
    data = notification.get("data")
    if (notification["operator"]["job"] != "security_monitor"
            or not isinstance(data, dict) or data.get("schema_version") != 2
            or not isinstance(data.get("events"), list)):
        return "", ""
    findings = []
    individual_events = []
    titles = []
    for event in data["events"]:
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        if not isinstance(kind, str) or kind not in _EVENT_FIELDS:
            continue
        title = _finding_title(kind, event)
        if event.get("severity") in ("warning", "error") and title not in titles:
            titles.append(title)
        fields = _EVENT_FIELDS[kind]
        if kind == "source_retention_gap" and "coverage_since" in event and "coverage_until" in event:
            fields = (
                ("Unobserved from", "coverage_since"), ("Until (exclusive)", "coverage_until"),
                ("Oldest retained entry", "oldest_retained"),
            )
        is_module_event = kind == "auditd" and event.get("key") == "modules"
        if is_module_event and "module_names" not in event:
            # Earlier senders stored KERN_MODULE names in paths.
            fields = tuple(("Reported modules or paths", key) if key == "paths" else (label, key)
                           for label, key in fields)
        evidence = _facts(event, fields)
        if is_module_event:
            context = event.get("login_context")
            actors = event.get("actors")
            if (isinstance(context, list) and "No audit login identity" in context
                    or isinstance(actors, list) and "unset" in actors):
                evidence += (
                    '<p class="event-meta">Automatic module loading can produce audit records '
                    'without a login identity. Compare the event times with boot and service logs.</p>'
                )
        if kind == "ssh_authentication" and isinstance(sources := event.get("sources"), list):
            source_fields = (("Source address", "source_ip"), ("Account", "username"),
                             ("Method", "method"), ("Attempts", "count"))
            evidence += "".join(_facts(row, source_fields) for row in sources if isinstance(row, dict))
        destination = individual_events if kind in _INDIVIDUAL_EVENTS else findings
        destination.append(f"<li><strong>{html.escape(title)}</strong>{evidence}</li>")
    findings.extend(individual_events)
    if not findings:
        return "", ""
    window = data.get("window")
    window_facts = _facts(window, (("Scan start", "since"), ("Scan end", "until"))) if isinstance(window, dict) else ""
    omitted = len(findings) - _MAX_FINDINGS
    note = f'<p class="event-meta">Showing {_MAX_FINDINGS} of {len(findings)} structured findings.</p>' if omitted > 0 else ""
    summary = " · ".join(titles[:3]) or "Security monitor findings"
    return summary, (
        '<details><summary>Security findings</summary>'
        '<p class="event-meta">Times without a timezone are shown as reported by the sender.</p>'
        f'{window_facts}<ul class="event-findings">{"".join(findings[:_MAX_FINDINGS])}</ul>{note}</details>'
    )
