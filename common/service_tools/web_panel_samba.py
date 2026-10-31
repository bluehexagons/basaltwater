"""Account inspection and single-use reviews for Samba login controls."""

from __future__ import annotations

import html
import math
import secrets
import threading
import time

from common.service_tools.web_panel_admin import _https_url, _timestamp
from common.service_tools.web_panel_templates import render_heading
from lib.privilege_client import PrivilegeBrokerUnavailable, PrivilegeRequestRejected, exchange
from lib.samba_accounts import ACCOUNT_ACTIONS, HEX_ID, validate_account_action, validate_reset_password


def unavailable_reason(status: dict) -> str:
    if status.get("error"):
        return status["error"]
    if status.get("available") is not True:
        return "Samba account controls are unavailable. Update or check the privilege approval service."
    if status.get("outstanding") or any(row.get("state") in {"pending", "approved", "executing"} for row in status.get("requests", [])):
        return "Review or cancel the outstanding Samba request before creating another."
    return ""


class PanelSamba:
    def __init__(self, admin) -> None:
        self.admin = admin
        self.lock = threading.Lock()
        self.tickets: dict[str, tuple[dict, float]] = {}

    def snapshot(self) -> dict:
        if not self.admin.approval_url:
            return {"error": "Account changes require the optional privilege approval service. Configure it for a supported non-root VM operator, or use the controller."}
        try:
            value = exchange({"action": "samba-status"})
            if type(value.get("available")) is not bool or not isinstance(value.get("requests"), list):
                raise ValueError("Invalid Samba request status")
            return value
        except (OSError, RuntimeError, ValueError):
            return {"error": "Samba request status is unavailable. Check or update the privilege approval service."}

    def prepare(self, fields: dict) -> tuple[str, dict]:
        value = validate_account_action({**fields, "job_id": secrets.token_hex(16)})
        with self.lock:
            self.tickets = {key: row for key, row in self.tickets.items() if row[1] > time.monotonic()}
            if len(self.tickets) >= 32:
                self.tickets.pop(next(iter(self.tickets)))
            ticket = secrets.token_urlsafe(32)
            self.tickets[ticket] = value, time.monotonic() + 300
        return ticket, value

    def submit(self, ticket: str, confirmation: str, *, password: str | None = None,
               password_repeat: str | None = None) -> None:
        with self.lock:
            saved = self.tickets.pop(ticket, None)
        if saved is None or saved[1] <= time.monotonic():
            raise ValueError("This Samba review expired or was already submitted")
        value = saved[0]
        if confirmation != value["username"]:
            raise ValueError("Type the account username to confirm")
        if reason := unavailable_reason(self.snapshot()):
            raise ValueError(reason)
        reset = value["action"] == "reset-password"
        if reset:
            validate_reset_password(password)
            if password != password_repeat:
                raise ValueError("The new passwords do not match")
            staged = exchange({"action": "samba-stage-password", "parameters": value, "password": password})
            if (staged.get("job_id") != value["job_id"] or type(staged.get("expires")) not in {int, float}
                    or not math.isfinite(staged["expires"]) or not time.time() < staged["expires"] <= time.time() + 3601):
                raise ValueError("Password staging could not be confirmed")
        elif password is not None or password_repeat is not None:
            raise ValueError("Passwords are accepted only for a password reset")
        try:
            exchange({"action": "request", "operation": "samba.account", "parameters": value,
                      "reason": "Shares & Data: " + ACCOUNT_ACTIONS[value["action"]]})
        except (PrivilegeBrokerUnavailable, PrivilegeRequestRejected):
            # Acceptance definitively did not happen. Other failures may have
            # left a pending request, so retain its secret until outcome/expiry.
            if reset:
                try:
                    exchange({"action": "samba-discard-password", "job_id": value["job_id"]})
                except (OSError, ValueError, RuntimeError):
                    pass
            raise


def render_accounts(snapshot: dict, csrf: str, *, controls: bool, password_controls: bool = False) -> str:
    body = '<section aria-labelledby="samba-accounts">' + render_heading("Samba accounts", "access", heading_id="samba-accounts")
    if not snapshot.get("available"):
        return body + '<p class="empty">Account snapshot unavailable. Enable Samba and check the exporter. Account inspection supports standalone Samba with the local tdbsam backend.</p></section>'
    body += '<p class="data-notice">Local Samba accounts at the snapshot time. Login status is separate from share membership and filesystem access. Disabling login may leave existing SMB sessions connected.</p>'
    if not snapshot["accounts"]:
        return body + '<p class="empty">No local Samba accounts found.</p></section>'
    body += '<ul class="data-list">'
    for row in snapshot["accounts"]:
        username = html.escape(row["username"])
        label = "Login disabled" if row["disabled"] else "Login enabled"
        body += f'<li class="event"><div class="event-head"><h3>{username}</h3><span class="badge">{label}</span></div>'
        body += f'<p class="event-meta">Unix UID: {row["uid"] if row["uid"] is not None else "Account missing"}</p>'
        if row["locked"]:
            body += '<p>Account lockout is recorded. Enabling login does not clear a lockout.</p>'
        if not row["manageable"]:
            body += '<p class="data-notice">Manage this account through the administrator command line. Browser controls require a matching, non-system Unix account and a regular password-protected Samba account.</p>'
        elif controls:
            action = "enable" if row["disabled"] else "disable"
            label = ACCOUNT_ACTIONS[action]
            body += f'''<form method="post" action="/actions/samba/review">{csrf}
<input type="hidden" name="action" value="{action}"><input type="hidden" name="username" value="{username}">
<input type="hidden" name="revision" value="{row['revision']}"><button>Review {label[0].lower() + label[1:]}</button></form>'''
            if password_controls:
                body += f'''<form method="post" action="/actions/samba/review">{csrf}
<input type="hidden" name="action" value="reset-password"><input type="hidden" name="username" value="{username}">
<input type="hidden" name="revision" value="{row['revision']}"><button>Reset Samba password</button></form>'''
        body += '</li>'
    return body + '</ul></section>'


def render_review(manager: PanelSamba, fields: dict, csrf: str) -> str:
    ticket, value = manager.prepare(fields)
    label = ACCOUNT_ACTIONS[value["action"]]
    username = html.escape(value["username"])
    reset = value["action"] == "reset-password"
    explanation = ("Change only the Samba password. Unix passwords, login flags and share membership are preserved. "
                   "The supplied password is staged privately for up to one hour; broker restart discards it. "
                   "Any intervening Samba account database change rejects this reset." if reset else
                   "This changes the Samba disabled flag and preserves the password, Unix login and share membership.")
    password_fields = ('''<label for="samba-password">New Samba password</label>
<input id="samba-password" type="password" name="password" required minlength="12" maxlength="128" autocomplete="new-password" aria-describedby="samba-password-help">
<label for="samba-password-repeat">Repeat new password</label><input id="samba-password-repeat" type="password" name="password_repeat" required minlength="12" maxlength="128" autocomplete="new-password">
<p id="samba-password-help">Use 12–128 characters. The password is never included in the approval record or returned by this page. Store it securely before submitting; it cannot be retrieved later.</p>''' if reset else "")
    return f'''<section class="event data-review" aria-labelledby="samba-review">{render_heading("Review: " + label, "security", heading_id="samba-review")}
<p>{label} for <strong>{username}</strong>. {explanation} Existing SMB sessions may continue.</p>
<p>A changed account or Samba configuration is rejected. After submitting, approve or deny this exact operation on the separate approval page.</p>
<form method="post" action="/actions/samba/submit" class="data-recovery-form">{csrf}
<input type="hidden" name="ticket" value="{ticket}">{password_fields}<label for="samba-confirm">Type <code>{username}</code> to confirm</label>
<input id="samba-confirm" name="confirmation" required autocomplete="off"><button>Create Samba approval request</button></form>
<a href="/shares-data?view=shares">Cancel review</a></section>'''


def render_status(status: dict, csrf: str) -> str:
    body = '<section aria-labelledby="samba-activity">' + render_heading("Samba activity", "maintenance", heading_id="samba-activity")
    if reason := unavailable_reason(status):
        body += f'<p class="data-notice" role="status">{html.escape(reason)}</p>'
    rows = status.get("requests", [])[:10]
    if not rows and not unavailable_reason(status):
        body += '<p class="data-notice">No Samba account changes requested from this panel account.</p>'
    earlier = False
    for index, row in enumerate(rows):
        identifier = row.get("id", "")
        if not isinstance(identifier, str) or not HEX_ID.fullmatch(identifier):
            continue
        if index == 1:
            body += f'<details><summary>Earlier Samba requests ({len(rows) - 1})</summary>'
            earlier = True
        parameters = row.get("parameters", {})
        label = ACCOUNT_ACTIONS.get(parameters.get("action"), "Samba account change")
        state = row.get("state", "unavailable")
        body += f'<article class="event"><h3>{html.escape(label)}: {html.escape(parameters.get("username", ""))}</h3><p>Request: <strong>{html.escape(state)}</strong></p>'
        body += f'<p class="event-meta">Requested: {_timestamp(row.get("created"))}</p>'
        if state == "succeeded":
            body += '<p>The account change was verified. The account list updates on the next five-minute export.</p>'
        elif state in {"failed", "uncertain", "invalidated"}:
            body += '<p>The change was not confirmed. Refresh after the next account export and inspect the account before reviewing it again. Stale reviews are rejected.</p>'
        if url := _https_url(row.get("review_url")):
            body += f'<a class="refresh-link" href="{html.escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">Open Samba approval ↗</a>'
        if state in {"pending", "approved"}:
            body += f'<p class="event-meta">Approval expires: {_timestamp(row.get("expires"))}</p><form method="post" action="/actions/samba/cancel">{csrf}<input type="hidden" name="id" value="{identifier}"><button>Cancel Samba request</button></form>'
        body += '</article>'
    if earlier:
        body += '</details>'
    return body + '</section>'
