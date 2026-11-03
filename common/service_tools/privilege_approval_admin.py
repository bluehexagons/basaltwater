"""Focused, one-use reviews for administrator-initiated fixed host actions."""

from __future__ import annotations

import html
import secrets
import time
from urllib.parse import urlsplit

from lib.admin_actions import ADMIN_ACTIONS, action_spec
from lib.privilege_policy import canonical

ADMIN_PREFIX = "/approvals/admin/"


def admin_action(path: str) -> str:
    action = path.removeprefix(ADMIN_PREFIX) if path.startswith(ADMIN_PREFIX) else ""
    spec = action_spec(action)
    if spec.get("variant_of"):
        raise ValueError("Open the main action to choose its options")
    return action


def render_catalog() -> str:
    rows = ''.join(
        f'<li><a class="approval-task" href="{ADMIN_PREFIX}{key}"><strong>{html.escape(spec["title"])}</strong>'
        f'<span>{html.escape(spec["effect"])}</span></a></li>'
        for key, spec in ADMIN_ACTIONS.items() if not spec.get("variant_of"))
    return '<h2>Host actions</h2><p>Choose an action to review and approve.</p><ul class="approval-tasks">' + rows + '</ul>'


def render_review(server, action: str) -> str:
    """GET issues a bounded local review ticket, never a broker request."""
    spec = action_spec(action)
    variants = [action, "reboot-warning"] if action == "reboot" else [action]
    previews = {key: server.exchange({"action": "admin-preview", "admin_action": key}) for key in variants}
    now = time.monotonic()
    server.admin_tickets = {key: value for key, value in server.admin_tickets.items() if value["expires"] > now}
    if len(server.admin_tickets) >= 32:
        server.admin_tickets.pop(next(iter(server.admin_tickets)))
    ticket = secrets.token_urlsafe(32)
    server.admin_tickets[ticket] = {"action": action, "previews": previews, "expires": now + 300,
                                    "username": server.auth["username"]}
    escape = html.escape
    back = "/admin" if server.integrated else "/approvals/admin"
    content = f'<p><a href="{back}">← Host actions</a></p>'
    content += '<p>On <strong>' + escape(urlsplit(server.origin).hostname or "this host") + '</strong></p>'
    effect = ("Restarting interrupts all sessions and running work. The panel disconnects until the host returns."
              if action == "reboot" else spec["effect"])
    content += '<p class="notice">' + escape(effect) + '</p>'
    content += f'<form method="post" action="{ADMIN_PREFIX}{action}"><input type="hidden" name="ticket" value="{ticket}">'
    if action == "reboot":
        content += ('<fieldset><legend>Restart timing</legend>'
                    '<label class="approval-option"><input type="radio" name="variant" value="reboot" checked> '
                    '<span><strong>In 5 seconds</strong> · Restart shortly after approval.</span></label>'
                    '<label class="approval-option"><input type="radio" name="variant" value="reboot-warning"> '
                    '<span><strong>Warn users, then restart in 2 minutes</strong> · Send a warning to logged-in users; allow time to cancel.</span></label></fieldset>')
    else:
        content += f'<input type="hidden" name="variant" value="{action}">'
    content += '<p class="muted">This review expires in 5 minutes. Opening it does not schedule any work.</p>'
    content += f'<div class="approval-buttons"><button name="decision" value="approve">Approve {escape(spec["title"].lower())}</button><a href="{back}">Cancel</a></div></form>'
    content += '<details class="approval-details"><summary>Technical details</summary>'
    for key, preview in previews.items():
        content += '<h3>' + escape(ADMIN_ACTIONS[key]["title"]) + '</h3><pre>' + escape(canonical(preview["plan"])) + '</pre>'
    return content + '</details>'


def approve_review(server, action: str, form: dict) -> dict:
    if set(form) != {"ticket", "variant", "decision"} or any(len(value) != 1 for value in form.values()):
        raise ValueError("Invalid review fields")
    saved = server.admin_tickets.pop(form["ticket"][0], None)
    if (saved is None or saved["action"] != action or saved["expires"] <= time.monotonic()
            or saved["username"] != server.auth["username"] or form["decision"] != ["approve"]
            or form["variant"][0] not in saved["previews"]):
        raise ValueError("Review expired or already submitted")
    variant = form["variant"][0]
    # Consume before exchange: a lost response must never repeat the action.
    return server.exchange({"action": "admin-approve", "admin_action": variant,
                            "digest": saved["previews"][variant]["digest"], "actor": server.auth["username"]})
