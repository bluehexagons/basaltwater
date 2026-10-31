"""Single-use scrub recovery reviews and independently approved requests."""

from __future__ import annotations

import html
import secrets
import threading
import time

from common.service_tools.web_panel_admin import _https_url, _timestamp
from common.service_tools.web_panel_templates import render_heading
from lib.data_actions import DATA_ACTIONS, HEX_ID, validate_data_action
from lib.privilege_client import exchange

FAILURE_LABELS = {
    "storage_busy": "Another storage operation held the lock. No recovery was started.",
    "setup_recovery_required": "An unfinished setup operation needs administrator recovery before file recovery can continue.",
    "finding_changed": "The finding changed after review. Refresh the findings before reviewing it again.",
    "file_changed": "The file changed after its recorded check. Verify it again before recovery.",
    "identity_unavailable": "This older finding has no file identity. Verify it again before recovery.",
    "execution_rejected": "Recovery could not complete. Inspect setup activity, configured mounts and the scrub report through the controller before retrying.",
    "job_interrupted": "The worker stopped without a confirmed result. Inspect the file and report before retrying.",
}
DISPATCH_FAILURES = {
    "action_unavailable": "The recovery worker was busy or unavailable at dispatch.",
    "executor_unavailable": "The worker could not be staged or launched. Inspect the approval service and protected payload before retrying.",
    "wait_timeout": "Dispatch exceeded its wait deadline; the worker may still start. Inspect its state before retrying.",
    "executor_signal": "Dispatch was interrupted; the worker may still start. Inspect its state before retrying.",
    "nonzero_exit": "The service manager refused dispatch. Inspect its worker unit before retrying.",
}


class PanelData:
    def __init__(self, admin) -> None:
        self.admin = admin
        self.lock = threading.Lock()
        self.tickets: dict[str, tuple[dict, float]] = {}

    def snapshot(self) -> dict:
        if not self.admin.approval_url:
            return {"error": "Recovery controls require the optional privilege approval service. Configure it for a supported non-root VM operator, or use the controller commands below."}
        try:
            result = exchange({"action": "data-status"})
            if (any(type(result.get(key)) is not bool for key in ("available", "blocked")) or not isinstance(result.get("requests"), list)
                    or not isinstance(result.get("latest"), dict)
                    or result.get("unit_state") not in {"active", "activating", "deactivating", "inactive", "failed", "unavailable"}):
                raise ValueError("Unsupported recovery status")
            return result
        except (OSError, ValueError, RuntimeError):
            return {"error": "Recovery status is unavailable. Check or update the approval service before submitting work."}

    def prepare(self, fields: dict[str, str]) -> tuple[str, dict]:
        parameters = validate_data_action({**fields, "job_id": secrets.token_hex(16)})
        with self.lock:
            self.tickets = {key: value for key, value in self.tickets.items() if value[1] > time.monotonic()}
            if len(self.tickets) >= 32:
                self.tickets.pop(next(iter(self.tickets)))
            ticket = secrets.token_urlsafe(32)
            self.tickets[ticket] = (parameters, time.monotonic() + 300)
        return ticket, parameters

    def reviewed(self, ticket: str) -> dict:
        with self.lock:
            saved = self.tickets.get(ticket)
            if saved is None or saved[1] <= time.monotonic():
                self.tickets.pop(ticket, None)
                raise ValueError("This review expired or was already submitted. Check recent requests before reviewing again.")
            return saved[0]

    def submit(self, ticket: str, confirmation: str) -> None:
        parameters = self.reviewed(ticket)
        required = "ACCEPT CURRENT CONTENT" if parameters["action"] == "accept" else "CHANGE FILE" if parameters["action"] != "verify" else ""
        if confirmation != required:
            raise ValueError("Type the exact confirmation phrase shown in the review")
        status = self.snapshot()
        reason = unavailable_reason(status)
        if reason:
            raise ValueError(reason)
        # Consume the ticket before crossing the socket: a lost response must
        # never cause automatic replay of an accepted recovery request.
        with self.lock:
            saved = self.tickets.pop(ticket, None)
        if saved is None or saved[1] <= time.monotonic():
            raise ValueError("This review expired or was already submitted. Check recent requests before reviewing again.")
        exchange({"action": "request", "operation": "data.run", "parameters": parameters,
                  "reason": "Shares & Data: " + DATA_ACTIONS[parameters["action"]][0]})


def unavailable_reason(status: dict) -> str:
    if status.get("error"):
        return status["error"]
    if not status.get("available") or status.get("unit_state") == "unavailable":
        return "The recovery worker is unavailable. Check its service and installed source."
    if status.get("blocked"):
        return "Setup recovery must finish before file recovery. Inspect the setup operation marker through the administrator command line."
    if status.get("unit_state") not in {"inactive", "failed"}:
        return "A recovery job is running. Refresh status after it finishes."
    if status.get("other_active"):
        return "A storage routine job is running. Refresh its status in Backups & sync after it finishes."
    if status.get("outstanding") or any(row.get("state") in {"pending", "approved", "executing"} for row in status.get("requests", [])):
        return "Review or cancel the outstanding storage request before submitting another."
    return ""


def recovery_form(job: dict, finding: dict, csrf: str, submitted: dict | None = None) -> str:
    if not job.get("database") or not finding.get("revision"):
        return '<p>Refresh the installed snapshot exporter before using browser recovery for this finding.</p>'
    identifier = secrets.token_hex(8)
    selected = (submitted or {}).get("action", "verify")
    backup = html.escape((submitted or {}).get("backup", ""), quote=True)
    options = ''.join(f'<option value="{key}"{" selected" if key == selected else ""}>{html.escape(label)}</option>' for key, (label, _effect) in DATA_ACTIONS.items())
    fields = {"directory": job["directory"], "database": job["database"], "file": finding["path"], "finding": finding["revision"]}
    hidden = ''.join(f'<input type="hidden" name="{key}" value="{html.escape(value, quote=True)}">' for key, value in fields.items())
    return f'''<form method="post" action="/actions/data/review" class="data-recovery-form">{csrf}{hidden}
<label for="recovery-{identifier}">Recovery action</label><select id="recovery-{identifier}" name="action">{options}</select>
<label for="backup-{identifier}">Independent copy path (restore only)</label><input id="backup-{identifier}" name="backup" value="{backup}" maxlength="1024" placeholder="/path/to/known-good-copy">
<p>Review creates no host operation. Recovery requires a separate approval. Verify first if the file has changed since this finding was recorded.</p>
<button>Review recovery</button></form>'''


def render_review(manager: PanelData, fields: dict[str, str], csrf: str) -> str:
    ticket, parameters = manager.prepare(fields)
    return render_saved_review(ticket, parameters, csrf)


def render_failed_review(fields: dict, csrf: str) -> str:
    """Preserve editable recovery inputs only for a valid file/finding identity."""
    value = validate_data_action({**fields, "action": "verify", "backup": "", "job_id": secrets.token_hex(16)})
    return ('<section class="event data-review" aria-labelledby="recovery-inputs">'
            + render_heading("Correct recovery inputs", "security", heading_id="recovery-inputs")
            + '<p>File: <strong>' + html.escape(value["file"]) + '</strong></p>'
            + recovery_form({"directory": value["directory"], "database": value["database"]},
                            {"path": value["file"], "revision": value["finding"]}, csrf, fields) + '</section>')


def render_saved_review(ticket: str, parameters: dict, csrf: str) -> str:
    action = parameters["action"]
    label, effect = DATA_ACTIONS[action]
    required = "ACCEPT CURRENT CONTENT" if action == "accept" else "CHANGE FILE" if action != "verify" else ""
    confirmation = (f'<label for="recovery-confirm">Type <code>{required}</code> to confirm</label><input id="recovery-confirm" name="confirmation" required autocomplete="off">'
                    if required else '<input type="hidden" name="confirmation" value="">')
    facts = ''.join(f'<div><dt>{label}</dt><dd>{html.escape(parameters[key])}</dd></div>'
                    for label, key in (("File", "file"), ("Scrub root", "directory"), ("Parity directory", "database"), ("Independent copy", "backup"))
                    if parameters[key])
    return f'''<section class="event data-review" aria-labelledby="recovery-review">{render_heading("Review: " + label, "security", heading_id="recovery-review")}
<p>{html.escape(effect)}</p><dl class="job-facts">{facts}</dl>
<p>This creates a single-use request for the separate approval page. The worker rejects a changed finding or file and shares the storage operation lock. Jobs have a six-hour limit; dispatch is separate from completion.</p>
<form method="post" action="/actions/data/submit" class="data-recovery-form">{csrf}<input type="hidden" name="ticket" value="{html.escape(ticket, quote=True)}">{confirmation}<button>Create recovery approval request</button></form>
<a href="/shares-data?view=scrubs">Cancel review</a></section>'''


def render_status(status: dict, csrf: str) -> str:
    escape = html.escape
    body = '<section aria-labelledby="recovery-status">' + render_heading("Recovery activity", "maintenance", heading_id="recovery-status")
    if reason := unavailable_reason(status):
        body += f'<p class="data-notice" role="status">{escape(reason)}</p>'
    latest = status.get("latest", {})
    if not latest and not status.get("requests") and not unavailable_reason(status):
        body += '<p class="data-notice">No recovery requests recorded. Open a finding below to review verification or recovery.</p>'
    if latest:
        parameters = latest.get("parameters", {})
        label = DATA_ACTIONS.get(parameters.get("action"), ("Recovery job", ""))[0]
        result = latest.get("status", "unavailable")
        body += f'<article class="event"><h3>{escape(label)}</h3><p><span class="badge">{escape(result)}</span></p><p>{escape(parameters.get("file", ""))}</p>'
        body += f'<p class="event-meta">Started: {_timestamp(latest.get("started"))} · Finished: {_timestamp(latest.get("finished")) if latest.get("finished") is not None else "Not recorded"}</p>'
        if category := latest.get("category"):
            body += f'<p>Recorded result: {escape(category.replace("_", " "))}</p>'
        if failure := latest.get("failure"):
            body += f'<p>{escape(FAILURE_LABELS.get(failure, "Inspect the worker and scrub report before retrying."))}</p>'
        body += '<p>Findings below follow the five-minute snapshot export. A completed recovery may appear there until the next export.</p></article>'
    for row in status.get("requests", [])[:10]:
        identifier = row.get("id", "")
        if not isinstance(identifier, str) or not HEX_ID.fullmatch(identifier):
            continue
        parameters = row.get("parameters", {})
        label = DATA_ACTIONS.get(parameters.get("action"), ("Recovery request", ""))[0]
        state = row.get("state", "unavailable")
        body += f'<article class="event"><h3>{escape(label)}</h3><p>{escape(parameters.get("file", ""))}</p><p>Approval request: <strong>{escape(state)}</strong></p>'
        body += f'<p class="event-meta">Requested: {_timestamp(row.get("created"))}</p>'
        if state in {"pending", "approved"}:
            body += f'<p class="event-meta">Approval expires: {_timestamp(row.get("expires"))}</p>'
        if state in {"failed", "uncertain", "invalidated"}:
            body += '<p>Dispatch did not establish completion. Inspect the request on the approval page and the current worker result before retrying.</p>'
            failure = (row.get("execution") or {}).get("failure")
            if failure in DISPATCH_FAILURES:
                body += f'<p>{escape(DISPATCH_FAILURES[failure])}</p>'
        if parameters.get("job_id") and latest.get("parameters", {}).get("job_id") == parameters["job_id"]:
            body += f'<p>Worker result for this request: <strong>{escape(latest.get("status", "unavailable"))}</strong></p>'
        elif state == "dispatched":
            body += '<p>This request was dispatched. Its completion is not established by the latest worker record above; inspect the scrub report before retrying.</p>'
        if url := _https_url(row.get("review_url")):
            body += f'<a class="refresh-link" href="{escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">Open recovery approval ↗</a>'
        if state in {"pending", "approved"}:
            body += f'<form method="post" action="/actions/data/cancel">{csrf}<input type="hidden" name="id" value="{identifier}"><button>Cancel recovery request</button></form>'
        body += '</article>'
    return body + '</section>'
