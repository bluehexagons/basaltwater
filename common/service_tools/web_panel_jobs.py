"""Read-only, on-demand visibility into managed systemd maintenance jobs."""

from __future__ import annotations

import html
import math
import re
import threading
import time
import urllib.parse
from dataclasses import dataclass

from common.service_tools.web_panel_templates import panel_navigation, render_document
from common.service_tools.web_panel_diagnostics import JOB_SERVICES, _bounded_command
from common.service_tools.web_panel_storage import render_storage


_PROPERTIES = (
    "Id", "LoadState", "ActiveState", "UnitFileState", "Unit", "Persistent",
    "NextElapseUSecRealtime", "NextElapseUSecMonotonic", "LastTriggerUSec",
    "Result", "ExecMainStartTimestamp", "ExecMainExitTimestamp", "ExecMainStatus",
)
_COLLECTOR = threading.BoundedSemaphore(1)


@dataclass(frozen=True)
class JobSnapshot:
    jobs: list[dict[str, str]]
    issues: list[str]
    absent: int = 0


def parse_job_query(raw: str) -> bool:
    if len(raw) > 64:
        raise ValueError("Invalid scheduled job query")
    query = urllib.parse.parse_qs(raw, keep_blank_values=True, max_num_fields=1)
    if query not in ({}, {"load": ["1"]}):
        raise ValueError("Invalid scheduled job query")
    return bool(query)


def _reported(value: str | None) -> bool:
    return bool(value and value not in {"n/a", "0", "infinity", "[not set]"})


def _next_trigger(timer: dict[str, str]) -> str:
    if timer.get("ActiveState") != "active":
        return "Timer is not active"
    realtime = timer.get("NextElapseUSecRealtime")
    fallback = str(realtime) if _reported(realtime) else "Next trigger not reported"
    # systemctl formats monotonic deadlines as boot-relative time spans.
    raw = timer.get("NextElapseUSecMonotonic", "")
    factors = {"w": 604800, "d": 86400, "h": 3600, "min": 60, "s": 1, "ms": .001, "us": .000001}
    parts = raw.split()
    seconds = 0.0
    for part in parts:
        match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)(min|ms|us|w|d|h|s)", part)
        if not match:
            return fallback
        seconds += float(match[1]) * factors[match[2]]
    if not parts or seconds <= 0 or not math.isfinite(seconds):
        return fallback
    remaining = seconds - time.monotonic()
    if remaining <= 0:
        return "Due; waiting for timer dispatch"
    minutes = max(1, round(remaining / 60))
    interval = f"In about {minutes} minute{'s' if minutes != 1 else ''}"
    if _reported(realtime):
        return f"{realtime} or {interval.lower()}, whichever is earlier"
    return interval


def collect_jobs() -> JobSnapshot:
    """Inspect fixed timer/service pairs in one bounded systemctl request."""

    if not _COLLECTOR.acquire(blocking=False):
        return JobSnapshot([], ["A scheduled job inspection is running. Try loading again shortly."])
    try:
        units = [unit for service in JOB_SERVICES for unit in (service, service.removesuffix(".service") + ".timer")]
        output, issue = _bounded_command([
            "systemctl", "show", *units, "--no-pager", "--property=" + ",".join(_PROPERTIES),
        ])
        records = {}
        for block in output.strip().split("\n\n"):
            fields = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
            if fields.get("Id") in units:
                records[fields["Id"]] = fields
        issues = [issue] if issue else []
        jobs = []
        absent = 0
        for service, label in JOB_SERVICES.items():
            timer_name = service.removesuffix(".service") + ".timer"
            timer = records.get(timer_name, {})
            runtime = records.get(service, {})
            if timer.get("LoadState") == "not-found":
                absent += 1
                continue
            if timer.get("LoadState") != "loaded":
                issues.append(f"{label}: timer information unavailable.")
                continue
            status, tone = "No run recorded", "info"
            started = runtime.get("ExecMainStartTimestamp")
            result = runtime.get("Result", "")
            if runtime.get("LoadState") != "loaded" or timer.get("Unit") != service:
                status, tone = "Service information unavailable", "warning"
            elif runtime.get("ActiveState") in {"active", "activating", "deactivating", "reloading"}:
                status = "Running"
            elif runtime.get("ActiveState") == "failed" or result not in {"", "success"}:
                status, tone = "Last run failed", "error"
            elif _reported(started) and result == "success":
                status = "Last run succeeded"
            elif _reported(started):
                status, tone = "Last result unavailable", "warning"
            elif _reported(timer.get("LastTriggerUSec")):
                status, tone = "Timer triggered; result unavailable", "warning"
            timer_state = timer.get("ActiveState", "unknown")
            if timer_state != "active" and tone == "info":
                tone = "warning"
            jobs.append({
                "label": label, "service": service, "status": status, "tone": tone,
                "timer": timer_state, "enabled": timer.get("UnitFileState") or "Not reported",
                "next": _next_trigger(timer),
                "triggered": timer.get("LastTriggerUSec") if _reported(timer.get("LastTriggerUSec")) else "No trigger recorded",
                "started": str(started) if _reported(started) else "No start recorded",
                "finished": runtime.get("ExecMainExitTimestamp") if _reported(runtime.get("ExecMainExitTimestamp")) else "No finish recorded",
                "result": (result or "Not reported") if _reported(started) or result not in {"", "success"} else "No result recorded",
                "exit": runtime.get("ExecMainStatus", "Not reported") if _reported(started) else "Not recorded",
                "persistent": {"yes": "Yes", "no": "No"}.get(timer.get("Persistent", ""), "Not reported"),
            })
        jobs.sort(key=lambda job: ({"error": 0, "warning": 1, "info": 2}[job["tone"]], job["label"]))
        return JobSnapshot(jobs, issues, absent)
    finally:
        _COLLECTOR.release()


def render_jobs(
    load: bool,
    style: str,
    host: str,
    *,
    notification_ingest: bool = False,
) -> str:
    content = '<p class="empty">Select Load scheduled jobs to inspect maintenance timers and their last runs.</p>'
    if load:
        snapshot = collect_jobs()
        notices = "".join(f"<li>{html.escape(issue)}</li>" for issue in snapshot.issues)
        content = f'<aside class="audit-issues" role="status"><strong>Collection notice</strong><ul>{notices}</ul></aside>' if notices else ""
        content += f'<p class="count">{len(snapshot.jobs)} installed jobs · {snapshot.absent} timers not installed · attention items first</p>'
        for job in snapshot.jobs:
            result_tone = "success" if job["status"] == "Last run succeeded" else job["tone"]
            timer_badge = (
                f'<span class="badge warning">Timer {html.escape(job["timer"])}</span> '
                if job["timer"] != "active" else ""
            )
            facts = "".join(
                f'<div><dt>{label}</dt><dd>{html.escape(job[key])}</dd></div>'
                for key, label in (
                    ("timer", "Timer state"), ("enabled", "Start at boot"),
                    ("next", "Next trigger"), ("triggered", "Last timer trigger"),
                    ("started", "Last process start"), ("finished", "Last process finish"),
                    ("result", "Service result"), ("exit", "Process exit status"),
                    ("persistent", "Catch up missed calendar runs"),
                )
            )
            url = "/logs?" + urllib.parse.urlencode({"service": job["service"], "window": "24h", "priority": "7"})
            content += f'''<section class="event" aria-label="{html.escape(job['label'], quote=True)}">
<div class="event-head"><h2>{html.escape(job['label'])}</h2><div>{timer_badge}<span class="badge {result_tone}">{html.escape(job['status'])}</span></div></div>
<dl class="job-facts">{facts}</dl><a class="refresh-link" href="{html.escape(url, quote=True)}">Inspect job logs</a></section>'''
        if not snapshot.jobs and not snapshot.issues:
            content += '<p class="empty">No supported maintenance timers are installed.</p>'
        content += render_storage()
    header = f'''<header><p class="eyebrow">Basaltwater web panel</p><h1>Scheduled jobs</h1>
<p class="lede">Update, security, and housekeeping jobs on <code>{html.escape(host)}</code>.</p></header>'''
    body = f'''<form class="job-load" method="get" action="/jobs"><button name="load" value="1">Load scheduled jobs</button></form>
<p class="endpoint">A snapshot of managed system timers. Times use the host timezone; interval deadlines are approximate. Inactive job services are normal between runs. Results may reset after a reboot or service-manager reload.</p>
{content}'''
    footer = '<footer><a href="/">Back to dashboard</a><span>Loaded on request · no automatic refresh</span></footer>'
    return render_document(
        title=f"Scheduled jobs · {host}",
        style=style,
        header=header,
        content=body,
        navigation=panel_navigation(
            current="jobs", include_notifications=notification_ingest
        ),
        footer=footer,
    )
