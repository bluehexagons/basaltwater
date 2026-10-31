"""Share editors, single-use reviews and independently approved operations."""

from __future__ import annotations

import html
import secrets
import threading
import time
from urllib.parse import urlencode

from common.service_tools.web_panel_admin import _https_url, _timestamp
from common.service_tools import web_panel_storage as storage
from common.service_tools.web_panel_templates import render_heading
from lib.privilege_client import exchange
from lib.samba_accounts import HEX_ID
from lib.samba_shares import share_label, share_name, validate_share_action


def unavailable_reason(status: dict) -> str:
    if status.get("error"):
        return status["error"]
    if not status.get("available"):
        return "Share editing is unavailable. Check or update the privilege approval service."
    if status.get("blocked"):
        return "Setup or share recovery must finish before another share change. Inspect the setup operation marker through the administrator command line."
    if status.get("unit_state") not in {"inactive", "failed"}:
        return "A share change is running. Refresh its status after it finishes."
    if status.get("outstanding") or any(row.get("state") in {"pending", "approved", "executing"} for row in status.get("requests", [])):
        return "Review or cancel the outstanding Samba request before another change."
    return ""


class PanelShares:
    def __init__(self, admin) -> None:
        self.admin = admin
        self.lock = threading.Lock()
        self.tickets: dict[str, tuple[dict, float]] = {}

    def snapshot(self) -> dict:
        if not self.admin.approval_url:
            return {"error": "Share editing requires the optional privilege approval service for a supported non-root VM operator."}
        try:
            value = exchange({"action": "share-status"})
            if (type(value.get("available")) is not bool or type(value.get("blocked")) is not bool
                    or not isinstance(value.get("requests"), list) or not isinstance(value.get("latest"), dict)
                    or value.get("unit_state") not in {"active", "activating", "deactivating", "inactive", "failed", "unavailable"}):
                raise ValueError("Invalid share job status")
            return value
        except (OSError, ValueError, RuntimeError):
            return {"error": "Share request status is unavailable. Check or update the privilege approval service."}

    def prepare(self, fields: dict) -> tuple[str, dict]:
        snapshot = storage.load_storage_snapshot()
        inventory = snapshot.get("inventory", {})
        control = snapshot.get("share_management", {})
        accounts = snapshot.get("samba", {})
        if (not snapshot.get("available") or not inventory.get("available") or not control.get("available")
                or not inventory.get("samba_enabled") or inventory.get("omitted_shares")
                or any(row["omitted_users"] for row in inventory.get("shares", []))
                or not accounts.get("available") or fields["revision"] != control["revision"]):
            raise ValueError("Refresh the current complete share and account snapshot before editing")
        original = fields.get("original", fields["name"])
        before = next(({key: row[key] for key in ("name", "access", "path", "users")}
                       for row in inventory["shares"] if row["name"] == original), None)
        if original and before is None:
            raise ValueError("The selected share is no longer in this snapshot")
        if not original and any(row["name"].casefold() == fields["name"].casefold() for row in inventory["shares"]):
            raise ValueError("A share with this name already exists; edit it instead")
        after = ({key: fields[key] for key in ("name", "access", "path", "users")} if fields["kind"] == "save" else None)
        eligible = {row["username"] for row in accounts["accounts"] if row["manageable"]}
        if after is not None and not set(after["users"]) <= eligible:
            raise ValueError("Choose existing regular local Samba accounts")
        value = validate_share_action({"before": before, "after": after, "revision": fields["revision"], "job_id": secrets.token_hex(16)})
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
                raise ValueError("This share review expired or was already submitted")
            return saved[0]

    def submit(self, ticket: str, confirmation: str) -> None:
        value = self.reviewed(ticket)
        if confirmation != share_name(value):
            raise ValueError("Type the exact share name to confirm")
        if reason := unavailable_reason(self.snapshot()):
            raise ValueError(reason)
        with self.lock:
            saved = self.tickets.pop(ticket, None)
        if saved is None or saved[1] <= time.monotonic():
            raise ValueError("This share review expired or was already submitted")
        exchange({"action": "request", "operation": "samba.shares", "parameters": value,
                  "reason": "Shares & Data: " + share_label(value)})


def editor_url(name: str = "") -> str:
    return "/shares-data?" + urlencode({"view": "shares", **({"edit": name} if name else {"form": "add"})})


def render_draft(fields: dict) -> str:
    values = (("Share name", fields["name"]), ("Directory", fields["path"]),
              ("Access", fields["access"]), ("Users", ", ".join(fields["users"])))
    return ('<section class="event" aria-labelledby="share-draft">'
            + render_heading("Unsaved share draft", "access", heading_id="share-draft")
            + '<p>Editing is unavailable or the snapshot changed. Copy this draft, refresh and review the current share before editing again.</p>'
            + '<dl class="job-facts">' + ''.join(f'<div><dt>{label}</dt><dd>{html.escape(value)}</dd></div>' for label, value in values)
            + '</dl></section>')


def render_editor(snapshot: dict, query: dict, csrf: str, submitted: dict | None = None) -> str:
    inventory, control, accounts = (snapshot.get(key, {}) for key in ("inventory", "share_management", "samba"))
    if (not control.get("available") or not inventory.get("available") or not accounts.get("available")):
        if submitted is not None:
            return render_draft(submitted)
        return '<p class="data-notice">Share editing needs a complete supported snapshot. Check the exporter, local Samba accounts and setup recovery state.</p>'
    rows = inventory["shares"]
    selected = next((row for row in rows if row["name"] == query.get("edit")), None)
    if query.get("edit") and selected is None:
        if submitted is not None:
            return render_draft(submitted)
        return '<p class="event" role="alert">That share is no longer in the snapshot. Refresh the share list.</p>'
    if not query.get("edit") and query.get("form") != "add":
        return f'<p><a class="refresh-link" href="{html.escape(editor_url(), quote=True)}">Add a share →</a></p>'
    eligible = [row for row in accounts["accounts"] if row["manageable"]]
    if not eligible:
        if submitted is not None:
            return render_draft(submitted)
        return '<p class="empty">Create a regular local Samba account in Samba accounts below, then refresh after the next export before adding share access.</p>'
    if selected and not set(selected["users"]) <= {row["username"] for row in eligible}:
        if submitted is not None:
            return render_draft(submitted)
        return '<p class="data-notice">This share includes accounts that require administrator command-line management.</p>'
    escape = html.escape
    name = selected["name"] if selected else ""
    values = submitted if submitted is not None else selected or {"name": "", "path": "", "access": "read", "users": []}
    if submitted is not None and submitted["revision"] != control["revision"]:
        return render_draft(submitted)
    path, access = values["path"], values["access"]
    title = "Edit share: " + name if selected else "Add a share"
    name_field = (f'<input type="hidden" name="name" value="{escape(name, quote=True)}"><p>Share name: <strong>{escape(name)}</strong></p>' if selected else
                  f'<label for="share-name">Share name</label><input id="share-name" name="name" value="{escape(values["name"], quote=True)}" required maxlength="22" pattern="(?:[A-Za-z0-9._]|-)+" autocomplete="off">')
    choices = ''.join(f'<label class="share-user"><input type="checkbox" name="users" value="{escape(row["username"], quote=True)}"'
                      + (' checked' if row["username"] in values["users"] else '')
                      + f'>{escape(row["username"])}{" (login disabled)" if row["disabled"] else ""}</label>' for row in eligible)
    body = f'''<section class="event" aria-labelledby="share-editor">{render_heading(title, "access", heading_id="share-editor")}
<form method="post" action="/actions/shares/review" class="data-recovery-form">{csrf}
<input type="hidden" name="kind" value="save"><input type="hidden" name="original" value="{escape(name, quote=True)}">
<input type="hidden" name="revision" value="{control['revision']}">{name_field}
<label for="share-path">Directory</label><input id="share-path" name="path" required maxlength="512" value="{escape(path, quote=True)}" placeholder="/srv/shared/documents" autocomplete="off">
<label for="share-access">Access</label><select id="share-access" name="access"><option value="read"{' selected' if access == 'read' else ''}>Read only</option><option value="write"{' selected' if access == 'write' else ''}>Read &amp; write</option></select>
<fieldset class="share-users"><legend>Users with access (choose up to 64)</legend>{choices}</fieldset>
<p>Changes recursively apply the saved share permissions. Passwords and login flags are preserved. The SMB name ends in <code>_read</code> or <code>_write</code>; changing access changes that connection name.</p>
<button>Review share change</button><a href="/shares-data?view=shares">Cancel edit</a></form>'''
    if selected:
        body += f'''<details><summary>Remove this share</summary><p>Stop publishing this managed share and remove its access group. Files and user accounts are retained; existing connections may need to reconnect.</p>
<form method="post" action="/actions/shares/review">{csrf}<input type="hidden" name="kind" value="remove">
<input type="hidden" name="name" value="{escape(name, quote=True)}"><input type="hidden" name="revision" value="{control['revision']}"><button>Review share removal</button></form></details>'''
    return body + '</section>'


def render_review(manager: PanelShares, fields: dict, csrf: str) -> str:
    ticket, value = manager.prepare(fields)
    return render_saved_review(ticket, value, csrf)


def render_saved_review(ticket: str, value: dict, csrf: str) -> str:
    label, name = share_label(value), share_name(value)
    escape = html.escape
    body = '<section class="event data-review" aria-labelledby="share-review">' + render_heading("Review: " + label, "security", heading_id="share-review")
    body += f'<p>Share: <strong>{escape(name)}</strong></p><div class="share-comparison">'
    for title, share in (("Current", value["before"]), ("Proposed", value["after"])):
        body += f'<div><h3>{title}</h3>'
        if share is None:
            body += '<p>Not published</p>'
        else:
            body += '<dl>' + ''.join(f'<dt>{key}</dt><dd>{escape(text)}</dd>' for key, text in (
                ("SMB name", share["name"] + "_" + share["access"]), ("Directory", share["path"]),
                ("Access", "Read only" if share["access"] == "read" else "Read & write"), ("Users", ", ".join(share["users"])))) + '</dl>'
        body += '</div>'
    body += f'''</div><p>Reconciliation reapplies group membership and recursive permissions for all saved managed shares, validates and reloads Samba, then saves the share selection. It preserves passwords, login flags, files and accounts. Other services and manually configured shares are retained.</p>
<p>A changed configuration is rejected. The supervised job has a one-hour limit. A failure can leave partial permissions and a setup recovery guard requiring administrator inspection. Submitting creates a request for the separate approval page.</p>
<form method="post" action="/actions/shares/submit" class="data-recovery-form">{csrf}<input type="hidden" name="ticket" value="{escape(ticket, quote=True)}">
<label for="share-confirm">Type <code>{escape(name)}</code> to confirm</label><input id="share-confirm" name="confirmation" required autocomplete="off"><button>Create share approval request</button></form>
<a href="/shares-data?view=shares">Cancel review</a></section>'''
    return body


def render_status(status: dict, csrf: str) -> str:
    reason = unavailable_reason(status)
    rows = status.get("requests", [])[:10]
    if not rows and not reason:
        return ""
    escape = html.escape
    body = '<section aria-labelledby="share-activity">' + render_heading("Share activity", "maintenance", heading_id="share-activity")
    if reason:
        body += f'<p class="data-notice" role="status">{escape(reason)}</p>'
    latest = status.get("latest", {})
    earlier = False
    for index, row in enumerate(rows):
        identifier = row.get("id", "")
        if not isinstance(identifier, str) or not HEX_ID.fullmatch(identifier):
            continue
        if index == 1:
            body += f'<details><summary>Earlier share requests ({len(rows) - 1})</summary>'
            earlier = True
        state = row.get("state", "unavailable")
        body += f'<article class="event"><h3>{escape(row.get("label", "Share change"))}: {escape(row.get("name", ""))}</h3><p>Approval request: <strong>{escape(state)}</strong></p><p class="event-meta">Requested: {_timestamp(row.get("created"))}</p>'
        if row.get("job_id") == latest.get("job_id"):
            body += f'<p>Worker result: <strong>{escape(latest.get("status", "unavailable"))}</strong></p>'
            if latest.get("status") == "succeeded":
                body += '<p>The share selection was applied and saved. The list below updates on the next five-minute export.</p>'
            elif latest.get("status") in {"failed", "interrupted"}:
                body += '<p>Inspect live Samba configuration, permissions, saved setup and any recovery guard before retrying.</p>'
        elif state in {"dispatched", "failed", "uncertain", "invalidated"}:
            body += '<p>Completion is not established by the current worker record. Inspect the approval and actual share state before trying again.</p>'
        if url := _https_url(row.get("review_url")):
            body += f'<a class="refresh-link" href="{escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">Open share approval ↗</a>'
        if state in {"pending", "approved"}:
            body += f'<p class="event-meta">Approval expires: {_timestamp(row.get("expires"))}</p><form method="post" action="/actions/shares/cancel">{csrf}<input type="hidden" name="id" value="{identifier}"><button>Cancel share request</button></form>'
        body += '</article>'
    if earlier:
        body += '</details>'
    return body + '</section>'
