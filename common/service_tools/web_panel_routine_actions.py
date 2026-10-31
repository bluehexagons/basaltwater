"""Routine editors and single-use reviews for independently approved workers."""

from __future__ import annotations

import html
import secrets
import threading
import time
from urllib.parse import urlencode

from common.service_tools import web_panel_storage as storage
from common.service_tools.web_panel_admin import _https_url, _timestamp
from common.service_tools.web_panel_templates import render_heading
from lib.privilege_client import exchange
from lib.samba_accounts import HEX_ID, HEX_REVISION
from lib.task_utils import VALID_FREQUENCIES
from lib.storage_routines import (
    SPEC_FIELDS, configured_routines, routine_effect, routine_identity, routine_label,
    validate_routine_action, validate_routine_snapshot,
)

KINDS = {"backup": "Backup mirror", "sync": "Sync mirror", "scrub": "Scrub task"}
FAILURES = {
    "storage_busy": "Another storage or setup operation owns the lock. Inspect its status before preparing a new request.",
    "setup_recovery_required": "An unfinished setup operation needs administrator recovery before storage work can continue.",
    "review_changed": "The saved configuration or pause controls changed. Refresh the export and review the current routine again.",
    "tools_unavailable": "Required tools are missing. Use controller setup to provision rsync and unshare for mirrors, or PAR2 for scrubs, then prepare a new review.",
    "execution_rejected": "Execution was refused by the current configuration, mounts or integrity guards. Inspect storage job logs.",
    "routine_failed": "The operation failed. Inspect storage job logs, mount availability and any setup recovery guard before retrying.",
    "job_interrupted": "The worker stopped without a completed result. Inspect actual files, history and any setup recovery guard before retrying.",
}


def view_for(kind: str) -> str:
    return "scrubs" if kind == "scrub" else "routines"


def editor_url(kind: str, identifier: str = "") -> str:
    return "/shares-data?" + urlencode({"view": view_for(kind), **({"routine": identifier} if identifier else {"form": kind})})


def unavailable_reason(status: dict) -> str:
    if status.get("error"):
        return status["error"]
    if not status.get("available"):
        return "Routine controls are unavailable. Check or update the privilege approval service."
    if status.get("blocked"):
        return "Setup recovery must finish before routine changes or runs. Inspect the setup operation marker through the administrator command line."
    if status.get("unit_state") not in {"inactive", "failed"} or status.get("other_active"):
        return "A data worker is running. Refresh its status after it finishes."
    if status.get("outstanding") or any(row.get("state") in {"pending", "approved", "executing"} for row in status.get("requests", [])):
        return "Review or cancel the outstanding data request before another routine change or run."
    return ""


def parse_fields(values: dict[str, list[str]], action: str) -> dict[str, str]:
    """Reject unknown, repeated and oversized fields before preparing a review."""
    if any(len(entries) != 1 for entries in values.values()):
        raise ValueError("Invalid routine fields")
    fields = {key: entries[0] for key, entries in values.items() if key != "csrf"}
    if action == "review":
        expected = {"op", "kind", "original", "revision"}
        if fields.get("op") == "save":
            expected |= {"first", "second", "interval", "paused"}
            if fields.get("kind") == "scrub":
                expected.add("redundancy")
        if (set(fields) != expected or fields.get("op") not in {"save", "remove", "pause", "resume", "run"}
                or fields.get("kind") not in KINDS):
            raise ValueError("Invalid routine action")
        for key, value in fields.items():
            limit = 512 if key in {"first", "second"} else 64
            if len(value) > limit or any(ord(character) < 32 or ord(character) == 127 for character in value):
                raise ValueError("Invalid routine field length or characters")
    else:
        expected = {"ticket", "confirmation", "view"} if action == "submit" else {"id", "view"}
        if set(fields) != expected or fields["view"] not in {"routines", "scrubs"} or any(len(value) > 128 for value in fields.values()):
            raise ValueError("Invalid routine request")
    return fields


def confirmation_for(value: dict) -> str:
    return {"Run routine once": "RUN ROUTINE", "Remove routine": "REMOVE ROUTINE", "Create routine": "SAVE ROUTINE",
            "Update routine": "SAVE ROUTINE", "Pause routine": "PAUSE ROUTINE", "Resume routine": "RESUME ROUTINE"}[routine_label(value)]


class PanelRoutines:
    def __init__(self, admin) -> None:
        self.admin = admin
        self.lock = threading.Lock()
        self.tickets: dict[str, tuple[dict, float]] = {}

    def snapshot(self) -> dict:
        if not self.admin.approval_url:
            return {"error": "Routine controls require the optional privilege approval service for a supported non-root VM operator."}
        try:
            value = exchange({"action": "routine-status"})
            if (any(type(value.get(key)) is not bool for key in ("available", "blocked", "outstanding", "other_active"))
                    or not isinstance(value.get("requests"), list) or not isinstance(value.get("latest"), dict)
                    or value.get("unit_state") not in {"active", "activating", "deactivating", "inactive", "failed", "unavailable"}):
                raise ValueError("Invalid routine job status")
            return value
        except (OSError, ValueError, RuntimeError):
            return {"error": "Routine request status is unavailable. Check or update the privilege approval service."}

    def prepare(self, fields: dict[str, str]) -> tuple[str, dict]:
        snapshot = storage.load_storage_snapshot()
        control = validate_routine_snapshot(snapshot.get("routine_management"))
        if not snapshot.get("available") or not control.get("available") or fields["revision"] != control["revision"]:
            raise ValueError("Refresh the current complete routine snapshot before editing")
        original, kind, operation = fields["original"], fields["kind"], fields["op"]
        if original and not HEX_REVISION.fullmatch(original):
            raise ValueError("Invalid selected routine")
        rows = control["routines"]
        before = next((row for row in rows if routine_identity(row["kind"], row["spec"]) == original), None)
        if original and before is None or before and before["kind"] != kind or not original and operation != "save":
            raise ValueError("The selected routine is no longer in this snapshot")
        after = None
        if operation == "save":
            if fields["paused"] not in {"0", "1"}:
                raise ValueError("Choose an active or paused schedule")
            after = {"kind": kind, "paused": fields["paused"] == "1", "spec": [fields["first"], fields["second"],
                *([fields["redundancy"]] if kind == "scrub" else []), fields["interval"]]}
        elif operation in {"pause", "resume"}:
            after = {**before, "paused": operation == "pause"}
        value = validate_routine_action({"action": "run" if operation == "run" else "change", "before": before,
            "after": after, "revision": fields["revision"], "job_id": secrets.token_hex(16)})
        if operation != "run":
            proposed = [row for row in rows if row is not before] + ([value["after"]] if value["after"] else [])
            saved = {key: [] for key in SPEC_FIELDS.values()}
            for row in proposed:
                saved[SPEC_FIELDS[row["kind"]]].append(row["spec"])
            configured_routines(saved, set())
        with self.lock:
            self.tickets = {key: row for key, row in self.tickets.items() if row[1] > time.monotonic()}
            if len(self.tickets) >= 32:
                self.tickets.pop(next(iter(self.tickets)))
            ticket = secrets.token_urlsafe(32)
            self.tickets[ticket] = value, time.monotonic() + 300
        return ticket, value

    def reviewed(self, ticket: str) -> dict:
        with self.lock:
            saved = self.tickets.get(ticket)
            if saved is None or saved[1] <= time.monotonic():
                self.tickets.pop(ticket, None)
                raise ValueError("This routine review expired or was already submitted")
            return saved[0]

    def submit(self, ticket: str, confirmation: str) -> None:
        value = self.reviewed(ticket)
        if confirmation != confirmation_for(value):
            raise ValueError("Type the exact confirmation shown in the review")
        if reason := unavailable_reason(self.snapshot()):
            raise ValueError(reason)
        # Retain a review after a typo or busy status; consume before crossing
        # the request socket. A lost response must never offer automatic replay.
        with self.lock:
            saved = self.tickets.pop(ticket, None)
        if saved is None or saved[1] <= time.monotonic():
            raise ValueError("This routine review expired or was already submitted")
        exchange({"action": "request", "operation": "data.routine", "parameters": value,
                  "reason": "Shares & Data: " + routine_label(value)})


def row_facts(row: dict) -> str:
    first, second, *rest = row["spec"]
    pairs = [("Kind", KINDS[row["kind"]]), ("Scrub root" if row["kind"] == "scrub" else "Source", first),
             ("Parity directory" if row["kind"] == "scrub" else "Destination", second),
             ("Full scan interval" if row["kind"] == "scrub" else "Interval", rest[-1]),
             ("Schedule", "Paused" if row["paused"] else "Active")]
    if row["kind"] == "scrub":
        pairs.append(("Redundancy", rest[0]))
    return '<dl class="job-facts">' + ''.join(f'<div><dt>{label}</dt><dd>{html.escape(text)}</dd></div>' for label, text in pairs) + '</dl>'


def render_editor(control: dict, query: dict, csrf: str, submitted: dict | None = None) -> str:
    rows = control["routines"]
    selected = next((row for row in rows if routine_identity(row["kind"], row["spec"]) == query.get("routine")), None)
    if query.get("routine") and (selected is None or view_for(selected["kind"]) != query.get("view")):
        return '<p class="event" role="alert">That routine is no longer in this view. Refresh the routine list.</p>'
    kind = selected["kind"] if selected else query.get("form")
    if kind not in KINDS:
        kinds = ["scrub"] if query.get("view") == "scrubs" else ["backup", "sync"]
        return '<p class="routine-actions">' + ' · '.join(f'<a class="refresh-link" href="{html.escape(editor_url(kind), quote=True)}">Add {KINDS[kind].lower()} →</a>' for kind in kinds) + '</p>'
    fields = ({"first": selected["spec"][0], "second": selected["spec"][1], "interval": selected["spec"][-1],
               "paused": "1" if selected["paused"] else "0", "redundancy": selected["spec"][2] if kind == "scrub" else ""} if selected else
              {"first": "", "second": "", "interval": "weekly" if kind == "scrub" else "daily", "paused": "1", "redundancy": "10%"})
    if submitted:
        fields.update({key: submitted[key] for key in fields if key in submitted})
    escape = html.escape
    original = routine_identity(kind, selected["spec"]) if selected else ""
    first_label, second_label = ("Scrub root", "Parity directory") if kind == "scrub" else ("Source directory", "Destination directory")
    body = f'''<section class="event" aria-labelledby="routine-editor">{render_heading(('Edit ' if selected else 'Add ') + KINDS[kind].lower(), "data", heading_id="routine-editor")}
<form method="post" action="/actions/routines/review" class="data-recovery-form">{csrf}
<input type="hidden" name="op" value="save"><input type="hidden" name="kind" value="{kind}">
<input type="hidden" name="original" value="{original}"><input type="hidden" name="revision" value="{control['revision']}">
<label for="routine-first">{first_label}</label><input id="routine-first" name="first" required maxlength="512" value="{escape(fields['first'], quote=True)}" placeholder="/srv/data" autocomplete="off">
<label for="routine-second">{second_label}</label><input id="routine-second" name="second" required maxlength="512" value="{escape(fields['second'], quote=True)}" placeholder="{'/srv/parity/data' if kind == 'scrub' else '/mnt/backup/data'}" autocomplete="off">
<p>Use absolute directory paths without a trailing slash or <code>..</code>. Mounts must already be configured and available.</p>'''
    if kind == "scrub":
        body += f'<label for="routine-redundancy">Parity redundancy</label><input id="routine-redundancy" name="redundancy" required maxlength="64" value="{escape(fields["redundancy"], quote=True)}" aria-describedby="routine-parity-help"><p id="routine-parity-help">For example, 10%. Parity uses additional disk space. Choose a separate directory; it cannot contain the scrub root or overlap another parity directory.</p>'
    intervals = ''.join(f'<option value="{frequency}"{" selected" if fields["interval"] == frequency else ""}>{frequency.capitalize()}</option>' for frequency in VALID_FREQUENCIES)
    body += f'''<label for="routine-interval">{'Full scan interval' if kind == 'scrub' else 'Interval'}</label><select id="routine-interval" name="interval" aria-describedby="routine-interval-help">{intervals}</select>
<p id="routine-interval-help">Biweekly means every 14 days; bimonthly means every 60 days. The hourly timer checks which routines are due.{' Active scrubs also maintain parity daily.' if kind == 'scrub' else ''}</p>
<label for="routine-paused">Schedule</label><select id="routine-paused" name="paused"><option value="1"{' selected' if fields['paused'] == '1' else ''}>Paused</option><option value="0"{' selected' if fields['paused'] == '0' else ''}>Active</option></select>
<p>Saving does not run the routine now. {'An active scrub verifies files, attempts staged repair while retaining originals, and maintains parity.' if kind == 'scrub' else 'Active mirrors can replace and delete destination files. Neither mirror kind retains versions.'} Required tools must be provisioned through controller setup before activation or a manual run.</p>
<button>Review routine change</button><a href="/shares-data?view={view_for(kind)}">Cancel edit</a></form></section>'''
    return body


def render_review(manager: PanelRoutines, fields: dict, csrf: str) -> str:
    return render_saved_review(*manager.prepare(fields), csrf)


def render_saved_review(ticket: str, value: dict, csrf: str) -> str:
    escape = html.escape
    label = routine_label(value)
    row = value["after"] or value["before"]
    view = view_for(row["kind"])
    body = '<section class="event data-review" aria-labelledby="routine-review">' + render_heading("Review: " + label, "security", heading_id="routine-review")
    if value["action"] == "run":
        body += row_facts(row)
    else:
        body += '<div class="share-comparison">'
        for title, selected in (("Current", value["before"]), ("Proposed", value["after"])):
            body += f'<div><h3>{title}</h3>' + (row_facts(selected) if selected else '<p>Not configured</p>') + '</div>'
        body += '</div>'
    confirmation = confirmation_for(value)
    body += f'''<p>{escape(routine_effect(value))}</p><p>A changed configuration is rejected. The worker has a six-hour limit. Submitting creates a request for the separate approval page; approval and worker completion are separate steps. Reviews expire after five minutes.</p>
<form method="post" action="/actions/routines/submit" class="data-recovery-form">{csrf}<input type="hidden" name="ticket" value="{escape(ticket, quote=True)}"><input type="hidden" name="view" value="{view}">
<label for="routine-confirm">Type <code>{confirmation}</code> to confirm</label><input id="routine-confirm" name="confirmation" required autocomplete="off"><button>Create routine approval request</button></form>
<a href="/shares-data?view={view}">Cancel review</a></section>'''
    return body


def render_controls(row: dict, revision: str, csrf: str) -> str:
    identifier = routine_identity(row["kind"], row["spec"])
    body = f'<div class="routine-actions"><a class="refresh-link" href="{html.escape(editor_url(row["kind"], identifier), quote=True)}">Edit routine →</a>'
    for operation, label in (("resume" if row["paused"] else "pause", "Resume schedule" if row["paused"] else "Pause schedule"), ("run", "Run once")):
        body += _action_form(row, identifier, revision, csrf, operation, label)
    body += '</div><details><summary>Remove routine</summary><p>Retain files, parity, reports and history. Removing a scrub also removes its findings safeguards for mirrors; pause keeps those protections.</p>'
    return body + _action_form(row, identifier, revision, csrf, "remove", "Review routine removal") + '</details>'


def _action_form(row: dict, identifier: str, revision: str, csrf: str, operation: str, label: str) -> str:
    return f'''<form method="post" action="/actions/routines/review" class="routine-action">{csrf}<input type="hidden" name="op" value="{operation}"><input type="hidden" name="kind" value="{row['kind']}"><input type="hidden" name="original" value="{identifier}"><input type="hidden" name="revision" value="{revision}"><button>{label}</button></form>'''


def render_history(control: dict, row: dict) -> str:
    history = control.get("history", {})
    if not history.get("available"):
        return '<p class="data-notice">Routine history unavailable. Configuration and pause state do not establish completion.</p>'
    entry = history["routines"][routine_identity(row["kind"], row["spec"])]
    label = "Scan cadence marker" if row["kind"] == "scrub" else "Last completed mirror"
    body = f'<p class="event-meta">{label}: {_timestamp(entry["cadence_at"]) if entry["cadence_at"] is not None else "Not recorded"}</p>'
    if row["kind"] == "scrub":
        body += '<p class="event-meta">A scan cadence marker can record initial deferral. Use the integrity report below to establish the last full scan.</p>'
        body += f'<p class="event-meta">Last completed parity maintenance: {_timestamp(entry["parity_at"]) if entry["parity_at"] is not None else "Not recorded"}</p>'
    for key, label in (("retry", "Full scan" if row["kind"] == "scrub" else "Mirror"), ("parity_retry", "Parity maintenance")):
        if retry := entry[key]:
            body += f'<p class="data-notice">{label} retry delay: {retry["failures"]} consecutive failure(s); failed {_timestamp(retry["failed_at"])}, retry after {_timestamp(retry["retry_after"])}. A run once request overrides this delay.</p>'
    return body


def render_status(status: dict, csrf: str) -> str:
    reason = unavailable_reason(status)
    rows = status.get("requests", [])[:10]
    if not rows and not reason:
        return ""
    escape = html.escape
    body = '<section aria-labelledby="routine-activity">' + render_heading("Routine activity", "maintenance", heading_id="routine-activity")
    if reason:
        body += f'<p class="data-notice" role="status">{escape(reason)}</p>'
    latest, earlier = status.get("latest", {}), False
    for index, request in enumerate(rows):
        try:
            value = validate_routine_action(request.get("parameters"))
        except (ValueError, TypeError):
            continue
        identifier = request.get("id", "")
        if not isinstance(identifier, str) or not HEX_ID.fullmatch(identifier):
            continue
        if index == 1:
            body += '<details><summary>Earlier routine requests</summary>'
            earlier = True
        row, state = value["after"] or value["before"], request.get("state", "unavailable")
        body += f'<article class="event"><h3>{escape(routine_label(value))}: {escape(row["spec"][0])}</h3><p>Approval request: <strong>{escape(state)}</strong></p><p class="event-meta">Requested: {_timestamp(request.get("created"))}</p>'
        body += row_facts(row)
        if value["job_id"] == latest.get("job_id"):
            result = latest.get("status", "unavailable")
            body += f'<p>Worker result: <strong>{escape(result)}</strong></p>'
            body += f'<p class="event-meta">Started: {_timestamp(latest.get("started"))} · Finished: {_timestamp(latest.get("finished"))}</p>'
            if result == "succeeded":
                body += ('<p>The selected run completed. Inspect exported history and integrity reports after the next five-minute export.</p>' if value["action"] == "run" else
                         '<p>The reviewed routine selection was saved. No copy or scrub was run by this change. The list updates on the next five-minute export.</p>')
            elif result == "warnings":
                body += '<p>The run completed with repairs or mirror precautions. Inspect storage job logs and the updated integrity report; repaired originals are retained.</p>'
            elif result == "findings":
                body += '<p>The full scan completed with unresolved findings. Browse findings below and inspect each file before choosing recovery.</p>'
            elif result in {"failed", "interrupted"}:
                body += f'<p>{escape(FAILURES.get(latest.get("failure"), FAILURES["routine_failed"]))}</p>'
        elif state in {"dispatched", "failed", "uncertain", "invalidated"}:
            body += '<p>Completion is not established by the current worker record. Inspect the approval, logs and actual routine state before preparing another request.</p>'
        if state in {"dispatched", "failed", "uncertain", "invalidated"}:
            body += '<details><summary>Inspect routine execution over SSH</summary><p>On this host, inspect the shared operation log. The scheduled service journal does not include this selected worker’s output. Correlate entries with this request’s times and paths.</p><pre><code>sudo tail -n 100 /var/log/basaltwater/operations/storage-ops.log</code></pre></details>'
        if url := _https_url(request.get("review_url")):
            body += f'<a class="refresh-link" href="{escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">Open routine approval ↗</a>'
        if state in {"pending", "approved"}:
            body += f'<p class="event-meta">Approval expires: {_timestamp(request.get("expires"))}</p><form method="post" action="/actions/routines/cancel" class="data-recovery-form">{csrf}<input type="hidden" name="id" value="{identifier}"><input type="hidden" name="view" value="{view_for(row["kind"])}"><button>Cancel routine request</button></form>'
        body += '</article>'
    return body + ('</details>' if earlier else '') + '</section>'
