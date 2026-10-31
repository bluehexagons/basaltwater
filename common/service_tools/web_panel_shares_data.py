"""Shares & Data configuration and scrub finding browser."""

from __future__ import annotations

import html
import shlex
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlencode

from common.service_tools import web_panel_storage as storage
from common.service_tools.web_panel_findings import MAX_PAGE, PAGE_SIZE, load_findings
from common.service_tools.web_panel_templates import render_document, render_heading
from sync.service_tools.scrub_findings import ADVICE
from common.service_tools.web_panel_data_actions import recovery_form, render_status, unavailable_reason
from common.service_tools import web_panel_samba as samba
from common.service_tools import web_panel_share_actions as shares
from common.service_tools import web_panel_routine_actions as routines
from lib.samba_accounts import HEX_REVISION
from lib.validation import validate_samba_share_name

VIEWS = {"shares": "Samba shares", "routines": "Backups & sync", "scrubs": "Scrubs & findings"}
STATUS_LABELS = {
    "unavailable": "Report unavailable", "never_scanned": "No full scan recorded",
    "incomplete": "Last full scan incomplete", "findings": "Unresolved findings",
    "verified": "Last recorded full scan completed without open findings",
}

STYLE = """
.data-tabs { display:flex; flex-wrap:wrap; gap:8px; margin:16px 0; }
.data-tabs a { display:inline-flex; align-items:center; min-height:44px; padding:8px 14px;
  border:1px solid var(--line); border-radius:8px; color:var(--accent); text-decoration:none; }
.data-tabs a[aria-current=page] { color:var(--panel); background:var(--accent); font-weight:700; }
.data-list { display:grid; gap:10px; list-style:none; padding:0; }
.data-list h3 { margin:0; font-size:1rem; }
.data-list .job-facts { margin-bottom:0; }
.data-notice { margin:12px 0; color:var(--muted); }
.data-finding { border-left:3px solid var(--warning); }
.data-finding summary { width:100%; }
.data-finding p { margin:6px 0; }
.data-filters input { min-width:0; }
.data-pagination { display:flex; flex-wrap:wrap; align-items:center; gap:16px; }
.data-list code { white-space:pre-wrap; }
.data-recovery-form { display:grid; grid-template-columns:minmax(0,1fr); min-width:0; gap:8px; margin:12px 0; max-width:680px; }
.data-recovery-form button { min-width:0; white-space:normal; overflow-wrap:anywhere; }
.data-recovery-form input, .data-recovery-form select { min-width:0; width:100%; min-height:44px;
  padding:10px; font:inherit; color:var(--text); background:var(--panel); border:1px solid var(--line); border-radius:8px; }
.data-review { border-left:3px solid var(--warning); margin:16px 0; }
.share-users { min-width:0; max-height:18rem; overflow:auto; border:1px solid var(--line); border-radius:8px; padding:12px; }
.share-users legend { max-width:100%; }
.share-user { display:flex; gap:10px; align-items:center; min-height:44px; overflow-wrap:anywhere; }
.share-user input { width:20px; min-height:20px; margin:0; flex:none; }
.share-comparison { display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,260px),1fr)); gap:20px; }
.share-comparison dd { margin:0 0 8px; overflow-wrap:anywhere; }
.routine-actions { display:flex; flex-wrap:wrap; align-items:center; gap:12px; margin-top:14px; }
.routine-action { min-width:0; max-width:100%; }
.routine-action button { white-space:normal; overflow-wrap:anywhere; max-width:100%; }
"""


def parse_shares_data_query(raw: str) -> dict[str, str]:
    if len(raw) > 2048:
        raise ValueError("Shares & Data query exceeds its limit")
    values = parse_qs(raw, keep_blank_values=True, max_num_fields=6)
    if set(values) - {"view", "category", "q", "page", "task", "tasks_page", "edit", "form", "routine"} or any(len(items) != 1 for items in values.values()):
        raise ValueError("Invalid Shares & Data filters")
    result = {key: items[0] for key, items in values.items()}
    if result.get("view", "shares") not in VIEWS:
        raise ValueError("Unknown Shares & Data view")
    if result.get("category", "") not in ("", *ADVICE):
        raise ValueError("Unknown finding category")
    for key in ("q", "task"):
        query = result.get(key, "")
        if len(query) > 200 or any(ord(character) < 32 or ord(character) == 127 for character in query):
            raise ValueError("Invalid scrub search")
    for key in ("page", "tasks_page"):
        page = result.get(key, "1")
        if not page.isascii() or not page.isdigit() or len(page) > 7 or not 1 <= int(page) <= MAX_PAGE:
            raise ValueError("Invalid scrub page")
    filters = {"category", "q", "page", "task", "tasks_page"}
    if result.get("view", "shares") != "scrubs" and set(result) & filters:
        raise ValueError("Scrub filters require the scrubs view")
    editors = set(result) & {"edit", "form", "routine"}
    view = result.get("view", "shares")
    if len(editors) > 1 or editors and set(result) & filters:
        raise ValueError("Choose one editor without scrub filters")
    if "edit" in result:
        if view != "shares":
            raise ValueError("Share editing requires the Samba view")
        validate_samba_share_name(result["edit"])
    if "routine" in result and (view == "shares" or not HEX_REVISION.fullmatch(result["routine"])):
        raise ValueError("Invalid routine editor")
    if "form" in result and result["form"] not in {"shares": {"add"}, "routines": {"sync", "backup"}, "scrubs": {"scrub"}}[view]:
        raise ValueError("Unknown form for this view")
    return result


def _facts(items: list[tuple[str, str]]) -> str:
    return '<dl class="job-facts">' + ''.join(
        f'<div><dt>{html.escape(label)}</dt><dd>{html.escape(value)}</dd></div>' for label, value in items
    ) + '</dl>'


def _date(value: str) -> str:
    if not value:
        return "Not recorded"
    try:
        return datetime.fromisoformat(value).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    except (OverflowError, ValueError):
        return value


def _inventory_notice() -> str:
    return ('<p class="empty">Configuration inventory unavailable. The snapshot exporter may need an update. '
            'Rerun setup or refresh the saved setup, then allow the next five-minute export to finish.</p>')


def _shares(inventory: dict, host: str, *, controls: bool = False) -> str:
    body = render_heading("Samba shares", "access", heading_id="samba-heading")
    if not inventory.get("available"):
        return body + _inventory_notice()
    if not inventory["samba_enabled"]:
        body += '<p class="empty">Samba is not enabled in the saved setup. Enable Samba with the controller before publishing shares.</p>'
    elif not inventory["shares"]:
        body += '<p class="empty">Samba is enabled with no managed shares configured.</p>'
    if inventory["shares"]:
        body += '<p class="data-notice">Saved managed shares and access groups. Account login status is listed separately below. Manually configured shares and active connections are outside this inventory.</p><ul class="data-list">'
        for share in inventory["shares"]:
            name = share["name"] + "_" + share["access"]
            access = "Read & write" if share["access"] == "write" else "Read only"
            body += f'<li class="event"><div class="event-head"><h3>{html.escape(name)}</h3><span class="badge">{html.escape(access)}</span></div>'
            body += _facts([("Directory", share["path"]), ("Users", ", ".join(share["users"])),
                            ("Connect", f"\\\\{host}\\{name}")])
            if share["omitted_users"]:
                body += f'<p class="data-notice">{share["omitted_users"]} additional users omitted from this snapshot.</p>'
            if controls:
                body += f'<p><a class="refresh-link" href="{html.escape(shares.editor_url(share["name"]), quote=True)}">Edit access and users →</a></p>'
            body += '</li>'
        body += '</ul>'
    if inventory["omitted_shares"]:
        body += f'<p class="data-notice">{inventory["omitted_shares"]} additional shares omitted from this snapshot.</p>'
    return body


def _routines(inventory: dict) -> str:
    body = render_heading("Backups & sync", "data", heading_id="routines-heading")
    if not inventory.get("available"):
        return body + _inventory_notice()
    body += '<p class="data-notice">These are recurring rsync mirrors. A backup declaration does not create snapshots or retained versions. Changes and deletions can propagate to the destination.</p>'
    if not inventory["routines"]:
        body += '<p class="empty">No backup or sync routines configured in the saved setup.</p>'
    else:
        body += '<ul class="data-list">'
        for routine in inventory["routines"]:
            kind = "Backup mirror" if routine["kind"] == "backup" else "Sync mirror"
            body += f'<li class="event"><div class="event-head"><h3>{html.escape(routine["source"])}</h3><span class="badge">{kind}</span></div>'
            body += _facts([("Destination", routine["destination"]), ("Interval", routine["interval"])]) + '</li>'
        body += '</ul>'
    if inventory["omitted_routines"]:
        body += f'<p class="data-notice">{inventory["omitted_routines"]} additional routines omitted from this snapshot.</p>'
    body += '<p class="data-notice">The hourly storage timer checks which routines are due. Declared mounts and the storage operation lock guard execution. This inventory does not report completion or prove a recoverable backup.</p>'
    return body + '<p><a class="refresh-link" href="/jobs?load=1">Inspect timer and last service result →</a> · <a class="refresh-link" href="/logs?service=storage-ops.service">Open storage job logs →</a></p>'


def _managed_routines(control: dict, query: dict, csrf: str, *, controls: bool, review: bool, submitted: dict | None) -> str:
    scrub = query["view"] == "scrubs"
    body = render_heading("Scrub tasks" if scrub else "Backups & sync", "data", heading_id="routine-list-heading")
    body += ('<p class="data-notice">Active scrubs verify and attempt staged repair on their full scan interval, and maintain parity daily. Pausing skips both while retaining mirror safeguards for open findings.</p>' if scrub else
             '<p class="data-notice">These are recurring rsync mirrors. A backup declaration does not create snapshots or retained versions. Changes and deletions can propagate to the destination.</p>')
    if controls and not review:
        body += routines.render_editor(control, query, csrf, submitted)
    rows = [row for row in control["routines"] if (row["kind"] == "scrub") == scrub]
    if not rows:
        body += '<p class="empty">No scrub tasks configured.</p>' if scrub else '<p class="empty">No backup or sync routines configured.</p>'
    else:
        body += '<ul class="data-list">'
        for row in rows:
            body += f'<li class="event"><div class="event-head"><h3>{html.escape(row["spec"][0])}</h3><span class="badge{" warning" if row["paused"] else ""}">{"Paused" if row["paused"] else "Active"}</span></div>'
            body += routines.row_facts(row) + routines.render_history(control, row)
            if controls:
                body += routines.render_controls(row, control["revision"], csrf)
            body += '</li>'
        body += '</ul>'
    body += '<p class="data-notice">The hourly timer checks due routines. Saved configuration and dispatch do not prove completion or a recoverable backup. History reflects the export time; it does not verify later file changes.</p>'
    return body + '<p><a class="refresh-link" href="/jobs?load=1">Inspect timer and last service result →</a> · <a class="refresh-link" href="/logs?service=storage-ops.service">Open storage job logs →</a></p>'


def _hidden_filters(query: dict, keys: set[str]) -> str:
    return ''.join(f'<input type="hidden" name="{key}" value="{html.escape(value, quote=True)}">'
                   for key, value in query.items() if key in keys)


def _pagination(query: dict, page: int, total: int, field: str, label: str) -> str:
    body = f'<nav class="data-pagination" aria-label="{label.capitalize()} pages">'
    for text, destination in (("Previous " + label, page - 1), ("Next " + label, page + 1)):
        if 1 <= destination <= (total + PAGE_SIZE - 1) // PAGE_SIZE:
            values = {"view": "scrubs", **{key: value for key, value in query.items()
                      if key in {"category", "q", "page", "task", "tasks_page"} - {field}}, field: str(destination)}
            body += f'<a class="refresh-link" href="{html.escape("/shares-data?" + urlencode(values), quote=True)}">{text}</a>'
    return body + '</nav>'


def _scrubs(snapshot: dict, query: dict[str, str], host: str, *, csrf: str = "", controls: bool = False) -> str:
    body = render_heading("Scrubs & findings", "security", heading_id="scrubs-heading")
    jobs = snapshot["jobs"]
    if not jobs:
        return body + '<p class="empty">No scrub integrity reports in this snapshot. Newly saved tasks appear after the next export; active tasks create reports when they run.</p>'
    catalog = load_findings(snapshot, query)
    complete = catalog["available"]
    health = catalog.get("health", {})
    body += '<p class="data-notice">Recorded file integrity is separate from job execution. Daily parity maintenance does not resolve open findings, and a recorded scan does not verify later changes.</p>'
    body += '<section aria-labelledby="scrub-health">' + render_heading("Task health", "security", heading_id="scrub-health")
    if health.get("available"):
        jobs = health["jobs"]
        body += f'''<form class="diagnostic-filters data-filters" method="get" action="/shares-data">
<input type="hidden" name="view" value="scrubs">{_hidden_filters(query, {"category", "q", "page"})}
<div><label for="scrub-task-search">Scrub root contains</label><input id="scrub-task-search" name="task" value="{html.escape(query.get("task", ""), quote=True)}" maxlength="200" type="search"></div>
<button>Filter tasks</button></form>'''
        total, page = health["total"], health["page"]
        offset = (page - 1) * PAGE_SIZE
        body += (f'<p class="count">Showing {offset + 1}–{min(offset + PAGE_SIZE, total)} of {total} matching scrub tasks</p>' if total else
                 '<p class="empty">No scrub tasks match this root filter. Findings below use separate filters.</p>')
    elif complete:
        body += '<p class="event" role="status">Complete task health is unavailable in this export; only the bounded summary below is shown. Update or check the exporter. Missing health does not mean a task is healthy.</p>'
    body += '<ul class="data-list">'
    for job in jobs:
        tone = "warning" if job["status"] in {"findings", "incomplete"} else "error" if job["status"] == "unavailable" else ""
        body += f'<li class="event"><h3>{html.escape(job["directory"])}</h3><p><span class="badge {tone}">{STATUS_LABELS[job["status"]]}</span></p>'
        body += _facts([("Full scan interval", job["interval"]), ("Last full scan", _date(job["last_scan"])),
                        ("Open findings", str(job["open_count"])), ("Parity directory", job.get("database", "Not exported"))])
        if job.get("omitted_findings") and not complete:
            body += f'<p class="data-notice">{job["omitted_findings"]} additional findings for this task are outside this snapshot. Use basaltw scrub status for the complete report.</p>'
        body += '</li>'
    body += '</ul>'
    if health.get("available"):
        body += _pagination(query, health["page"], health["total"], "tasks_page", "tasks")
    elif snapshot["omitted_jobs"]:
        body += f'<p class="data-notice">{snapshot["omitted_jobs"]} additional scrub tasks outside the summary above.</p>'
    body += '</section>'
    if complete:
        noun = "task" if catalog["tasks"] == 1 else "tasks"
        body += f'<p class="data-notice">The finding browser includes all {catalog["tasks"]} configured scrub {noun} in this export.</p>'
        if catalog["unavailable_jobs"]:
            body += f'<p class="event" role="status">{catalog["unavailable_jobs"]} scrub reports could not be fully exported. Results may be incomplete. Check their mounts and reports through the controller.</p>'
    elif catalog.get("legacy"):
        body += '<p class="data-notice">This older export includes up to twenty findings per task and eight tasks. Update the installed exporter to browse the complete report here.</p>'
    category, search = query.get("category", ""), query.get("q", "")
    options = '<option value="">All categories</option>' + ''.join(
        f'<option value="{name}"{" selected" if name == category else ""}>{name.replace("_", " ").capitalize()}</option>' for name in ADVICE
    )
    body += f'''<section aria-labelledby="findings-heading">{render_heading("Browse findings", "diagnostics", heading_id="findings-heading")}
<form class="diagnostic-filters data-filters" method="get" action="/shares-data">
<input type="hidden" name="view" value="scrubs">{_hidden_filters(query, {"task", "tasks_page"})}<div><label for="finding-category">Category</label><select id="finding-category" name="category">{options}</select></div>
<div><label for="finding-search">File path contains</label><input id="finding-search" name="q" value="{html.escape(search, quote=True)}" maxlength="200" type="search"></div>
<button>Filter findings</button><a class="refresh-link" href="/shares-data?view=scrubs">Clear filters</a></form>'''
    if not complete and not catalog.get("legacy"):
        return body + '<p class="empty" role="status">The complete findings catalog is unavailable or changed during export. Refresh after the next export. This does not mean there are no findings.</p></section>'
    findings = catalog["findings"] if complete else [(job, finding) for job in jobs for finding in job["findings"]
                if (not category or finding["category"] == category) and search.casefold() in finding["path"].casefold()]
    total = catalog["total"] if complete else len(findings)
    page = catalog["page"] if complete else min(int(query.get("page", "1")), max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE))
    offset = (page - 1) * PAGE_SIZE
    if not findings:
        body += '<p class="empty">No recorded findings match these filters in this snapshot.</p>'
    else:
        body += f'<p class="count">Showing {offset + 1}–{min(offset + PAGE_SIZE, total)} of {total} matching findings in this snapshot</p><ul class="data-list">'
        visible = findings if complete else findings[offset:offset + PAGE_SIZE]
        for job, finding in visible:
            command = ["basaltw", "scrub", "inspect", host, "--directory", job["directory"]]
            if job.get("database"):
                command += ["--database", job["database"]]
            command += ["--file", finding["path"]]
            body += f'''<li class="event data-finding"><h3>{html.escape(finding["path"])}</h3>
<p class="event-meta">Scrub root: {html.escape(job["directory"])}</p>
<p><span class="badge warning">{html.escape(finding["category"].replace("_", " "))}</span></p>
<p>{html.escape(ADVICE.get(finding["category"], "Inspect the recorded evidence before changing data."))}</p>
<p class="event-meta">First detected: {html.escape(_date(finding["first_seen"]))} · Last checked: {html.escape(_date(finding["last_checked"]))}</p>
<details><summary>Inspect evidence and recovery options</summary>
<p>Run this on the controller to inspect the saved finding, current file identity and parity paths:</p>
<pre><code>{html.escape(shlex.join(command))}</code></pre>
<p>Targeted verify, repair, restore and acceptance are available through basaltw scrub. Repair and restore verify a staged candidate and retain originals. Accept establishes the current content as the new baseline; it requires your confirmation that the content is correct.</p>
<a class="refresh-link" href="https://github.com/bluehexagons/basaltwater/blob/main/docs/storage/scrub-recovery.md">Read the scrub recovery guide ↗</a>
{recovery_form(job, finding, csrf) if controls else ''}</details></li>'''
        body += '</ul>'
    return body + _pagination(query, page, total, "page", "findings") + '</section>'


def render_shares_data(state, style: str, query: dict[str, str], *, review: str = "", error: str = "", routine_fields: dict | None = None) -> str:
    snapshot = storage.load_storage_snapshot()
    view = query.get("view", "shares")
    header = f'''<header class="dashboard-header"><div><p class="eyebrow">Host administration</p><h1>Shares &amp; Data</h1>
<p class="lede">Samba access, recurring copies and file integrity on <code>{html.escape(state.manifest["host"])}</code>.</p></div>
<a class="refresh-link" href="{html.escape('/shares-data?' + urlencode(query), quote=True)}">Refresh snapshot</a></header>'''
    body = '<nav class="data-tabs" aria-label="Shares and data views">' + ''.join(
        '<a href="/shares-data?view={}"{}>{}</a>'.format(
            key, ' aria-current="page"' if key == view else '', html.escape(label),
        ) for key, label in VIEWS.items()
    ) + '</nav>'
    if error:
        body += f'<p class="event" role="alert">{html.escape(error)}</p>'
    body += review
    manager = getattr(state, "data", None)
    status = manager.snapshot() if manager is not None and view == "scrubs" else {}
    csrf = f'<input type="hidden" name="csrf" value="{html.escape(getattr(state, "csrf_token", ""), quote=True)}">'
    if manager is not None and view == "scrubs":
        body += render_status(status, csrf)
    samba_manager = getattr(state, "samba", None)
    samba_status = samba_manager.snapshot() if samba_manager is not None and view == "shares" else {}
    if samba_manager is not None and view == "shares":
        body += samba.render_status(samba_status, csrf)
    share_manager = getattr(state, "share_actions", None)
    share_status = share_manager.snapshot() if share_manager is not None and view == "shares" else {}
    if share_manager is not None and view == "shares":
        body += shares.render_status(share_status, csrf)
    routine_manager = getattr(state, "routines", None)
    routine_status = routine_manager.snapshot() if routine_manager is not None and view != "shares" else {}
    if routine_manager is not None and view != "shares":
        body += routines.render_status(routine_status, csrf)
    if not snapshot.get("available"):
        body += '<p class="empty" role="status">Shares &amp; Data snapshot unavailable or stale. Check the web panel audit exporter and refresh after its next run. This does not mean the host has no shares, routines or findings.</p>'
    else:
        body += f'<p class="data-notice">Snapshot: {html.escape(_date(snapshot["generated_at"]))}. Exported every five minutes. Refreshing this page reads the latest export.</p>'
        inventory = snapshot.get("inventory", {"available": False})
        if view == "shares":
            controls = (share_manager is not None and not shares.unavailable_reason(share_status)
                        and snapshot.get("share_management", {}).get("available") is True)
            if controls and not review:
                body += shares.render_editor(snapshot, query, csrf)
            body += _shares(inventory, state.manifest["host"], controls=controls)
            body += samba.render_accounts(snapshot.get("samba", {"available": False}), csrf,
                                          controls=samba_manager is not None and not samba.unavailable_reason(samba_status),
                                          password_controls=getattr(state, "credential_transport_available", lambda: False)())
        elif view == "routines":
            control = snapshot.get("routine_management", {})
            if control.get("available"):
                body += _managed_routines(control, query, csrf,
                    controls=routine_manager is not None and not routines.unavailable_reason(routine_status), review=bool(review), submitted=routine_fields)
            else:
                body += '<p class="data-notice">Routine editing needs a complete supported export of the configuration and pause controls. Check the exporter and setup recovery state; the older inventory below may be incomplete.</p>'
                body += _routines(inventory)
        else:
            control = snapshot.get("routine_management", {})
            if control.get("available"):
                body += _managed_routines(control, query, csrf,
                    controls=routine_manager is not None and not routines.unavailable_reason(routine_status), review=bool(review), submitted=routine_fields)
            else:
                body += '<p class="data-notice">Scrub editing needs a complete supported export of the configuration and pause controls. Existing integrity reports remain available below.</p>'
            body += _scrubs(snapshot, query, state.manifest["host"], csrf=csrf,
                            controls=manager is not None and not unavailable_reason(status))
    return render_document(title=f'Shares & Data · {state.manifest["host"]}', style=style + STYLE,
                           header=header, content=body, navigation=state.navigation("shares-data"),
                           footer='<footer><a href="/admin">Admin controls</a><span>Configuration and recorded integrity · no automatic refresh</span></footer>')
