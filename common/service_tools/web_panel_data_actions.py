"""Single-use scrub recovery reviews and independently approved requests."""

from __future__ import annotations

import html
import json
import secrets
import threading
import time

from common.service_tools.web_panel_admin import _review_url, _timestamp
from common.service_tools.web_panel_findings import task_key
from common.service_tools.web_panel_templates import render_heading
from lib.data_actions import BATCH_LIMIT, DATA_ACTIONS, HEX_ID, validate_data_action
from lib.privilege_client import exchange

FAILURE_LABELS = {
    "storage_busy": "Another storage operation held the lock. No recovery was started.",
    "setup_recovery_required": "An unfinished setup operation needs administrator recovery before file recovery can continue.",
    "finding_changed": "The finding changed after review. Refresh the findings before reviewing it again.",
    "file_changed": "The file changed after its recorded check. Verify it again before recovery.",
    "identity_unavailable": "This older finding has no file identity. Verify it again before recovery.",
    "overlapping_scrubs": "Another scrub task covers this file. Quarantine and deletion require one protecting task; verification remains available. Coordinate recovery across the reports through the administrator command line.",
    "reserved_path": "The file is inside a configured parity database and cannot be quarantined or deleted as source data.",
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
            return {"error": "Recovery approvals are disabled. Re-enable the panel approval service with --privilege-broker, or use the controller commands below."}
        try:
            result = exchange({"action": "data-status"})
            if (any(type(result.get(key)) is not bool for key in ("available", "blocked")) or not isinstance(result.get("requests"), list)
                    or not isinstance(result.get("latest"), dict)
                    or result.get("unit_state") not in {"active", "activating", "deactivating", "inactive", "failed", "unavailable"}):
                raise ValueError("Unsupported recovery status")
            return result
        except (OSError, ValueError, RuntimeError):
            return {"error": "Recovery status is unavailable. Check or update the approval service before submitting work."}

    def prepare(self, fields: dict) -> tuple[str, dict]:
        if "items" in fields:
            if isinstance(fields["items"], list) and fields["items"][:1] == [""]:
                fields = {**fields, "items": fields["items"][1:]}
            if not isinstance(fields["items"], list) or not 1 <= len(fields["items"]) <= BATCH_LIMIT:
                raise ValueError(f"Select between 1 and {BATCH_LIMIT} files")
            try:
                fields = {**fields, "items": [json.loads(item) for item in fields["items"]]}
            except (TypeError, ValueError, RecursionError) as exc:
                raise ValueError("Invalid selected finding; refresh and select again") from exc
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
        required = confirmation_phrase(parameters)
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
    options = ''.join(f'<option value="{key}"{" selected" if key == selected else ""}>{html.escape(label)}</option>' for key, (label, _effect) in DATA_ACTIONS.items() if key not in {"quarantine", "delete"})
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
    if "items" in fields:
        raise ValueError("Refresh and select the remaining files again")
    value = validate_data_action({**fields, "action": "verify", "backup": "", "job_id": secrets.token_hex(16)})
    return ('<section class="event data-review" aria-labelledby="recovery-inputs">'
            + render_heading("Correct recovery inputs", "security", heading_id="recovery-inputs")
            + '<p>File: <strong>' + html.escape(value["file"]) + '</strong></p>'
            + recovery_form({"directory": value["directory"], "database": value["database"]},
                            {"path": value["file"], "revision": value["finding"]}, csrf, fields) + '</section>')


def confirmation_phrase(parameters: dict) -> str:
    if "items" in parameters and parameters["action"] == "delete":
        return f'DELETE {len(parameters["items"])} FILES'
    return "ACCEPT CURRENT CONTENT" if parameters["action"] == "accept" else "CHANGE FILE" if parameters["action"] != "verify" else ""


def batch_form(job: dict, csrf: str) -> str:
    hidden = ''.join(f'<input type="hidden" name="{key}" value="{html.escape(job[key], quote=True)}">'
                     for key in ("directory", "database"))
    options = ''.join(f'<option value="{key}">{label}</option>' for key, label in
                      (("verify", "Verify selected files"), ("quarantine", "Quarantine as corrupt (recommended)"),
                       ("delete", "Permanently delete selected files")))
    return f'''<form id="scrub-batch" method="post" action="/actions/data/review" class="event data-recovery-form">
{csrf}{hidden}<input type="hidden" name="items" value="">
<h3>Triage selected findings</h3><p>Select up to {BATCH_LIMIT} findings on this page. Selection clears when you change filters or pages. Review every path before requesting approval.</p>
<label for="batch-action">Action for all selected files</label><select id="batch-action" name="action">{options}</select>
<p>Quarantine retains corrupt bytes privately. Permanent deletion removes source bytes. Both retain parity, history and mirror protection. Repair, restore and acceptance require individual review.</p>
<button>Review selected findings</button></form>'''


def batch_checkbox(finding: dict) -> str:
    value = html.escape(json.dumps({"file": finding["path"], "finding": finding["revision"]}), quote=True)
    return (f'<label class="share-user"><input type="checkbox" form="scrub-batch" name="items" value="{value}">'
            f'Select <span>{html.escape(finding["path"])}</span></label>')


def _targets(parameters: dict) -> str:
    if "items" not in parameters:
        return '<p>' + html.escape(parameters.get("file", "")) + '</p>'
    return f'<p>{len(parameters["items"])} reviewed files</p><ol>' + ''.join(
        '<li>' + html.escape(item["file"]) + '</li>' for item in parameters["items"]) + '</ol>'


def _findings_url(parameters: dict) -> str:
    if parameters.get("directory") and parameters.get("database"):
        return '/shares-data?view=scrubs&scope=' + task_key(parameters["directory"], parameters["database"]) + '#findings-heading'
    return '/shares-data?view=scrubs'


def _task_context(parameters: dict) -> str:
    facts = ''.join('<div><dt>' + label + '</dt><dd>' + html.escape(parameters[key]) + '</dd></div>'
                    for key, label in (("directory", "Scrub root"), ("database", "Parity directory"))
                    if parameters.get(key))
    if not facts:
        return ''
    return ('<dl class="job-facts">' + facts + '</dl><p><a href="'
            + html.escape(_findings_url(parameters), quote=True) + '">Browse this task’s findings</a></p>')


def render_saved_review(ticket: str, parameters: dict, csrf: str) -> str:
    action = parameters["action"]
    label, effect = DATA_ACTIONS[action]
    if "items" in parameters and action == "verify":
        label = "Verify selected files"
    required = confirmation_phrase(parameters)
    confirmation = (f'<label for="recovery-confirm">Type <code>{required}</code> to confirm</label><input id="recovery-confirm" name="confirmation" required autocomplete="off">'
                    if required else '<input type="hidden" name="confirmation" value="">')
    facts = ''.join(f'<div><dt>{label}</dt><dd>{html.escape(parameters[key])}</dd></div>'
                    for label, key in (("File", "file"), ("Scrub root", "directory"), ("Parity directory", "database"), ("Independent copy", "backup"))
                    if parameters.get(key))
    targets = _targets(parameters) if "items" in parameters else ""
    return f'''<section class="event data-review" aria-labelledby="recovery-review">{render_heading("Review: " + label, "security", heading_id="recovery-review")}
<p>{html.escape(effect)}</p>{targets}<dl class="job-facts">{facts}</dl>
<p>This creates a single-use request. Open its approval review to approve or deny the exact action. The worker rejects a changed finding or file and shares the storage operation lock. Jobs have a six-hour limit; dispatch is separate from completion.</p>
<p>Batches report each file separately. Interrupted or uncertain work is never replayed automatically. Inspect outcomes and individually review remaining files before retrying.</p>
<form method="post" action="/actions/data/submit" class="data-recovery-form">{csrf}<input type="hidden" name="ticket" value="{html.escape(ticket, quote=True)}">{confirmation}<button>Create recovery approval request</button></form>
<a href="{html.escape(_findings_url(parameters), quote=True)}">Cancel review</a></section>'''


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
        body += f'<article class="event"><h3>{escape(label)}</h3><p><span class="badge">{escape(result)}</span></p>' + _task_context(parameters)
        if "items" in parameters:
            outcomes = {item["index"]: item for item in latest.get("items", []) if type(item.get("index")) is int}
            states = ("pending", "running", "completed", "skipped", "failed", "unresolved")
            counts = dict.fromkeys(states, 0)
            for index in range(len(parameters["items"])):
                state = outcomes.get(index, {}).get("status", "unresolved")
                counts[state if state in counts else "unresolved"] += 1
            summary = ' · '.join(f'{counts[state]} {state}' for state in states if counts[state])
            body += f'<p><strong>{len(parameters["items"])} reviewed files</strong>: {summary}.</p>'
            body += '<p>Completed means the action finished; verification can still report damage. Unresolved means the result needs inspection.</p>'
            body += '<ol aria-label="Per-file outcomes">'
            for index, item in enumerate(parameters["items"]):
                outcome = outcomes.get(index, {})
                state = outcome.get("status", "unresolved")
                if state not in counts:
                    state = "unresolved"
                body += f'<li><strong>{escape(item["file"])}</strong>: {escape(state)}'
                if category := outcome.get("category"):
                    body += ' · ' + escape(category.replace("_", " "))
                if failure := outcome.get("failure"):
                    body += '<p>' + escape(FAILURE_LABELS.get(failure, "Inspect before retrying.")) + '</p>'
                body += '</li>'
            body += '</ol><p>Partial or interrupted batches are never replayed. Inspect unresolved items; individually review remaining files before retrying.</p>'
        else:
            body += _targets(parameters)
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
        body += f'<article class="event"><h3>{escape(label)}</h3>' + _task_context(parameters) + _targets(parameters) + f'<p>Approval request: <strong>{escape(state)}</strong></p>'
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
        if url := _review_url(row.get("review_url")):
            body += f'<a class="refresh-link" href="{escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">Open recovery approval ↗</a>'
        if state in {"pending", "approved"}:
            body += f'<form method="post" action="/actions/data/cancel">{csrf}<input type="hidden" name="id" value="{identifier}"><button>Cancel recovery request</button></form>'
        body += '</article>'
    return body + '</section>'
