"""No-JavaScript administration controls backed by isolated approval reviews."""

from __future__ import annotations

from datetime import datetime, timezone
import html
import secrets
import threading
import time
from urllib.parse import parse_qs, urlsplit
from typing import Any

from common.service_tools.web_panel_templates import render_document, render_heading, render_icon
from lib.admin_actions import ADMIN_ACTIONS, action_spec
from lib.admin_status import FAILURE_LABELS, STAGE_LABELS, job_diagnostics
from lib.privilege_client import exchange
from lib.privilege_policy import ID_PATTERN
from lib.validation import validate_network_ip
from lib.validators import validate_host


def parse_admin_query(raw: str) -> dict[str, str]:
    if len(raw) > 100:
        raise ValueError("Invalid administration view")
    values = parse_qs(raw, keep_blank_values=True, max_num_fields=1)
    if not values:
        return {}
    if set(values) == {"view"} and values["view"] in (["actions"], ["requests"], ["activity"]):
        return {"view": values["view"][0]}
    if set(values) != {"action"} or len(values["action"]) != 1:
        raise ValueError("Invalid administration view")
    action_spec(values["action"][0])
    return {"action": values["action"][0]}


def _review_url(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 2048 or any(ord(c) < 33 for c in value):
        return None
    try:
        parsed = urlsplit(value)
        if value == "/approvals" or value.startswith("/requests/") and ID_PATTERN.fullmatch(value[len("/requests/"):]):
            return value
        if parsed.hostname and ":" in parsed.hostname:
            validate_network_ip(parsed.hostname, "approval address")
            valid_host = True
        else:
            valid_host = bool(parsed.hostname and validate_host(parsed.hostname))
        if parsed.scheme in {"http", "https"} and valid_host and not parsed.username and not parsed.password:
            parsed.port
            return value
    except ValueError:
        pass
    return None


class PanelAdmin:
    def __init__(self, manifest: dict) -> None:
        self.host = manifest.get("host", "")
        self.integrated = manifest.get("features", {}).get("approvals") is True
        self.approval_url = next((url for record in manifest.get("services", [])
                                  if isinstance(record, dict) and record.get("label") == "Privilege approvals"
                                  and (url := _review_url(record.get("url")))), None)
        if self.integrated:
            self.approval_url = "/approvals"
        self._lock = threading.Lock()
        self._tickets: dict[str, tuple[str, float]] = {}

    def snapshot(self) -> dict:
        if not self.approval_url:
            return {"error": "Host controls require the approval service. It is included with the web panel by default. If it was disabled, rerun setup with --privilege-broker to re-enable it."}
        try:
            result = exchange({"action": "admin-status"})
            if not isinstance(result, dict) or not isinstance(result.get("blocked"), dict) or not isinstance(result.get("requests"), list):
                raise ValueError("Invalid administration status")
            result["requests"] = result["requests"][:20]
            return result
        except (OSError, ValueError, RuntimeError):
            return {"error": "The approval service could not report administration status. Check its service and installed version before submitting work."}

    def ticket(self, action: str) -> str:
        action_spec(action)
        with self._lock:
            self._tickets = {key: value for key, value in self._tickets.items() if value[1] > time.monotonic()}
            if len(self._tickets) >= 32:
                self._tickets.pop(next(iter(self._tickets)))
            ticket = secrets.token_urlsafe(32)
            self._tickets[ticket] = (action, time.monotonic() + 300)
            return ticket

    def submit(self, action: str, ticket: str, confirmation: str) -> str:
        action_spec(action)
        if action in {"reboot", "reboot-warning", "shutdown"} and confirmation != self.host:
            raise ValueError("Type the host exactly to confirm the power action.")
        with self._lock:
            saved = self._tickets.pop(ticket, None)
        if saved is None or saved[0] != action or saved[1] <= time.monotonic():
            raise ValueError("This review expired or was already submitted. Refresh status before reviewing another action.")
        snapshot = self.snapshot()
        reason = unavailable_reason(snapshot, action)
        if reason:
            raise ValueError(reason)
        # A lost response may follow a saved request. Never retry automatically.
        request = exchange({"action": "request", "operation": "admin.run", "parameters": {"action": action},
                            "reason": "Administration screen: " + ADMIN_ACTIONS[action]["title"]})
        identifier = request.get("id")
        if not isinstance(identifier, str) or not ID_PATTERN.fullmatch(identifier):
            raise ValueError("Request acceptance was not confirmed; check Requests before retrying")
        if self.integrated:
            return "/requests/" + identifier
        origin = urlsplit(self.approval_url or "")
        return f"{origin.scheme}://{origin.netloc}/requests/{identifier}"

    def cancel(self, identifier: str) -> None:
        if not isinstance(identifier, str) or not ID_PATTERN.fullmatch(identifier):
            raise ValueError("Invalid approval request")
        if not self.approval_url:
            raise ValueError("Privilege approvals are not configured")
        exchange({"action": "cancel", "id": identifier})

    def action_url(self, action: str) -> str:
        action_spec(action)
        if self.integrated:
            return "/approvals/admin/" + action
        origin = urlsplit(self.approval_url or "")
        return f"{origin.scheme}://{origin.netloc}/approvals/admin/{action}"


def unavailable_reason(snapshot: dict, action: str) -> str:
    if snapshot.get("error"):
        return snapshot["error"]
    if snapshot["blocked"].get(action):
        return str(snapshot["blocked"][action])
    if any(row.get("state") in {"pending", "approved", "executing"} for row in snapshot["requests"]):
        return "Review or cancel the outstanding administration request first."
    if action != "cancel-shutdown":
        active = snapshot.get("unit_state")
        if active in {"active", "activating", "deactivating"}:
            return "A maintenance job is still running. Refresh status after it finishes."
        if active not in {"inactive", "failed"}:
            return "The maintenance service state is unavailable. Check it before starting another job."
    return ""


def _timestamp(value: object) -> str:
    try:
        if type(value) in {int, float}:
            return datetime.fromtimestamp(value, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    except (ValueError, OSError, OverflowError):
        pass
    return "Unavailable"


_GROUP_ICONS = {"Updates": "maintenance", "Services": "service-status", "Power": "power"}

_STYLE = """
main a { color:var(--accent); }
.admin-card { display:flex; flex-direction:column; padding:16px; border:1px solid var(--line); border-radius:12px; background:var(--panel); }
.admin-card p { color:var(--muted); font-size:.85rem; margin:8px 0; }
.admin-card .refresh-link { min-height:44px; margin-top:auto; }
.admin-review { border-left:4px solid var(--warning); margin-bottom:24px; }
.admin-review input { width:100%; max-width:420px; min-height:44px; display:block; margin:8px 0 16px;
  padding:10px; background:var(--bg); color:var(--text); border:1px solid var(--line); border-radius:8px; font:inherit; }
.admin-review button { white-space:normal; }
.admin-history { display:grid; gap:10px; list-style:none; padding:0; }
.admin-history li { padding:12px 16px; background:var(--panel); border:1px solid var(--line); border-radius:10px; }
.admin-history .admin-request-actions { display:flex; flex-wrap:wrap; align-items:center; gap:16px; }
.admin-result { display:flex; flex-wrap:wrap; gap:10px 24px; margin:12px 0; }
.admin-result dd { margin:0; font-weight:650; } .admin-result dt { color:var(--muted); font-size:.8rem; }
.admin-tools { display:flex; flex-wrap:wrap; gap:8px 24px; }
.admin-views { display:flex; flex-wrap:wrap; gap:8px; margin:20px 0; }
.admin-views a { display:flex; align-items:center; min-height:44px; padding:8px 16px; border:1px solid var(--line); border-radius:8px; text-decoration:none; }
.admin-views a[aria-current=page] { background:var(--accent); color:var(--panel); }
.admin-action-link { color:inherit!important; text-decoration:none; border-radius:12px; }
.admin-action-link:hover { border-color:var(--accent); background:var(--accent-soft); }
.admin-action-link:focus-visible { outline:3px solid var(--accent); outline-offset:3px; }
.admin-action-link .row-action { color:var(--accent); }
.action-list>li+li { border-top:1px solid var(--line); }
"""


def _request_history(snapshot: dict, csrf: str, *, integrated: bool = False) -> str:
    rows = []
    escape = html.escape
    for row in snapshot.get("requests", []):
        title = ADMIN_ACTIONS.get(row.get("action"), {}).get("title", "Administration request")
        status = row.get("state", "unavailable")
        tone = {"succeeded": "success", "failed": "error", "denied": "warning", "expired": "warning", "invalidated": "warning", "uncertain": "warning"}.get(status, "")
        url = _review_url(row.get("review_url"))
        identifier = row.get("id", "")
        if integrated and isinstance(identifier, str) and ID_PATTERN.fullmatch(identifier):
            url = "/requests/" + identifier
        actions = f'<a class="refresh-link" href="{escape(url, quote=True)}">Open approval review ↗</a>' if url else ""
        if status in {"pending", "approved"} and isinstance(identifier, str) and ID_PATTERN.fullmatch(identifier):
            actions += f'<form method="post" action="/actions/admin/cancel">{csrf}<input type="hidden" name="id" value="{identifier}"><button>Cancel request</button></form>'
        rows.append(f'<li><div class="event-head"><strong>{escape(title)}</strong><span class="badge {tone}">{escape(str(status))}</span></div><p class="event-meta">{_timestamp(row.get("created"))}</p><div class="admin-request-actions">{actions}</div></li>')
    content = '<section aria-labelledby="requests-heading"><div class="section-heading">' + render_heading("Recent requests", "security", heading_id="requests-heading") + '<span class="count">Latest 20 administration requests</span></div>'
    if not rows:
        return content + '<p class="empty">No administration requests available.</p></section>'
    content += f'<ol class="admin-history">{"".join(rows[:3])}</ol>'
    if len(rows) > 3:
        content += f'<details><summary>Show {len(rows) - 3} more requests</summary><ol class="admin-history" start="4">{"".join(rows[3:])}</ol></details>'
    return content + '</section>'


def _privilege_requests(snapshot: dict, csrf: str, approval_url: str | None) -> str:
    """Browse owned broker metadata and link to the isolated review surface."""
    escape = html.escape
    content = '<section aria-labelledby="privilege-requests-heading"><div class="section-heading">' + render_heading(
        'Privilege approvals', 'security', heading_id='privilege-requests-heading') + '</div>'
    if approval_url:
        if approval_url == "/approvals":
            content += '<p class="endpoint"><a class="inline-link" href="/approvals">Review all requests</a> · Uses your web panel sign-in.</p>'
        else:
            content += f'<p class="endpoint"><a class="inline-link" href="{escape(approval_url, quote=True)}" target="_blank" rel="noopener noreferrer">Open approval portal ↗</a> · Sign in there with your separate approval password.</p>'
    records = snapshot.get('privilege_requests')
    if isinstance(snapshot.get('restartable_services'), list):
        targets = ', '.join(str(unit) for unit in snapshot['restartable_services']) or 'None registered'
        content += '<p class="endpoint">Registered service restarts: ' + escape(targets) + '. Other exact restart commands require individual command approval; no service rule is added.</p>'
    if not isinstance(records, list):
        return content + '<p class="empty">Broker request listing is unavailable. Use the approval portal; rerun setup to update an older broker.</p></section>'
    rows = []
    titles = {'service.restart': 'Service restart', 'system.reboot': 'Reboot',
              'command.run': 'Privileged command', 'admin.run': 'Administration job'}
    for record in records[:20]:
        operation = record.get('operation', '')
        target = str(record.get('target', ''))
        title = ADMIN_ACTIONS.get(target, {}).get('title', target) if operation == 'admin.run' else target
        title = titles.get(operation, 'Privilege request') + (': ' + title if title else '')
        status = str(record.get('state', 'unavailable'))
        tone = 'success' if status == 'succeeded' else 'error' if status == 'failed' else 'warning'
        url = _review_url(record.get('review_url'))
        actions = f'<a class="refresh-link" href="{escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">Review exact action ↗</a>' if url else ''
        identifier = record.get('id')
        if approval_url == "/approvals" and isinstance(identifier, str) and ID_PATTERN.fullmatch(identifier):
            actions = f'<a class="refresh-link" href="/requests/{identifier}">Review exact action</a>'
        if status in {'pending', 'approved'} and isinstance(identifier, str) and ID_PATTERN.fullmatch(identifier):
            actions += f'<form method="post" action="/actions/admin/cancel">{csrf}<input type="hidden" name="id" value="{identifier}"><button>Cancel request</button></form>'
        detail = ''
        execution = record.get('execution')
        if isinstance(execution, dict):
            detail = '<p class="event-meta">Exit code: ' + escape(str(execution.get('exit_code', 'Unavailable'))) + ' · ' + escape(str(execution.get('failure') or 'Execution finished')) + '</p>'
        rows.append(f'<li><div class="event-head"><strong>{escape(title)}</strong><span class="badge {tone}">{escape(status)}</span></div><p class="event-meta">Requested {_timestamp(record.get("created"))} · Approval expires {_timestamp(record.get("expires"))}</p>{detail}<div class="admin-request-actions">{actions}</div></li>')
    if not rows:
        return content + '<p class="empty">No privilege requests for this panel account.</p></section>'
    content += '<p class="endpoint">Latest 20 requests for this account. Includes agent follow-ups and terminal requests. Refresh status after an action finishes; failed or uncertain actions are not retried here.</p>'
    content += '<ol class="admin-history">' + ''.join(rows[:5]) + '</ol>'
    if len(rows) > 5:
        content += f'<details><summary>Show {len(rows) - 5} more privilege requests</summary><ol class="admin-history" start="6">' + ''.join(rows[5:]) + '</ol></details>'
    return content + '</section>'


def render_admin(state: Any, style: str, query: dict[str, str], *, error: str = "") -> str:
    manager = state.admin
    snapshot = manager.snapshot()
    escape = html.escape
    host = escape(state.manifest["host"])
    action = query.get("action")
    view = query.get("view", "actions")
    current_url = "/admin?action=" + action if action else "/admin?view=" + view
    csrf = f'<input type="hidden" name="csrf" value="{escape(state.csrf_token, quote=True)}">'
    header = f'''<header class="dashboard-header"><div><p class="eyebrow">Host administration</p><h1>Admin controls</h1>
<p class="lede">Updates, service maintenance, and power controls for <code>{host}</code>.</p></div>
<a class="refresh-link" href="{current_url}">Refresh status</a></header>'''
    body = f'<aside class="status failed" role="alert">{escape(error)}</aside>' if error else ""
    body += '<nav class="admin-views" aria-label="Administration views">'
    for key, label in (("actions", "Host actions"), ("requests", "Requests"), ("activity", "Activity & diagnostics")):
        selected = ' aria-current="page"' if key == view and not action else ''
        body += f'<a href="/admin?view={key}"{selected}>{label}</a>'
    body += '</nav>'
    if snapshot.get("error"):
        body += f'<aside class="status" role="status">{escape(snapshot["error"])} See the <a href="https://github.com/bluehexagons/basaltwater/blob/main/docs/agents/privilege-approvals.md">approval setup guide</a>.</aside>'
    else:
        body += ('<p class="endpoint">Choose a host action, review it, then approve once using your web panel sign-in.</p>' if manager.integrated else
                 '<p class="endpoint">Choose a host action, then review and approve it with your separate approval password.</p>') if view == "actions" and not action else ''
        unit_label = {"active": "Running", "activating": "Starting", "deactivating": "Stopping", "inactive": "Ready", "failed": "Stopped after a failure"}.get(snapshot.get("unit_state"), "Unavailable")
        body += f'<p class="endpoint"><strong>Maintenance service: {unit_label}</strong></p>'
    latest = snapshot.get("latest", {})
    if latest and view == "activity" and not action:
        title = ADMIN_ACTIONS.get(latest.get("action"), {}).get("title", "Maintenance job")
        status = latest.get("status", "unavailable")
        tone = "complete" if status == "succeeded" else "failed" if status in {"failed", "interrupted", "unavailable"} else ""
        body += f'<aside class="status {tone}" role="status"><strong>Latest job: {escape(title)}</strong><dl class="admin-result">'
        for label, value in (("Result", status), ("Started", _timestamp(latest.get("started"))), ("Finished", _timestamp(latest.get("finished")) if latest.get("finished") is not None else "—"), ("Exit code", latest.get("exit_code") if latest.get("exit_code") is not None else "—")):
            body += f'<div><dt>{label}</dt><dd>{escape(str(value))}</dd></div>'
        diagnostics = job_diagnostics(latest)
        details = [("Stage", STAGE_LABELS.get(diagnostics["stage"], "Unavailable")),
                   ("Command index", diagnostics["command_index"] or "Unavailable"),
                   ("Failure", FAILURE_LABELS.get(diagnostics["failure"], "Unavailable"))]
        setup = diagnostics["setup_context"]
        if setup:
            details.extend((("Setup phase", setup["phase"]), ("Setup stage", setup["setup_stage"] or "Unavailable"),
                            ("Setup step", setup["step_index"] or "Unavailable"),
                            ("Error class", setup["error_type"] or "Unavailable")))
        for label, value in details:
            body += f'<div><dt>{label}</dt><dd>{escape(str(value))}</dd></div>'
        body += '</dl><details><summary>What these results mean</summary><p>The result covers the latest maintenance job only. Dispatch means the service started; it does not confirm completion. Power actions report scheduling, not the subsequent reboot or shutdown. Failed or interrupted work is never retried automatically.</p></details></aside>'
    if view == "activity" and not latest:
        body += '<p class="empty">No maintenance job has run yet.</p>'
    records = snapshot.get("privilege_requests")
    if not isinstance(records, list):
        records = snapshot.get("requests", [])
    active = sum(row.get("state") in {"pending", "approved", "executing"} for row in records)
    if view == "actions" and active and not action:
        body += f'<aside class="status"><a href="/admin?view=requests">{active} active approval request(s) · Review requests →</a></aside>'
    if view == "requests" and not action:
        if not isinstance(snapshot.get('privilege_requests'), list):
            body += _request_history(snapshot, csrf, integrated=manager.integrated)
        if manager.approval_url:
            body += _privilege_requests(snapshot, csrf, manager.approval_url)
    if action:
        spec = action_spec(action)
        reason = unavailable_reason(snapshot, action)
        if reason:
            body += f'<aside class="status" role="status">{escape(reason)}</aside>'
        else:
            ticket = manager.ticket(action)
            confirmation = (f'<label>Confirm host name <code>{host}</code><input name="confirmation" autocomplete="off" required placeholder="{host}"></label>' if action in {"reboot", "reboot-warning", "shutdown"} else '<input type="hidden" name="confirmation" value="">')
            body += f'''<section class="admin-card admin-review" aria-labelledby="review-heading">{render_heading("Review: " + spec['title'], _GROUP_ICONS[spec['group']], heading_id="review-heading")}
<p>{escape(spec['effect'])}</p><p>Submitting creates an approval request. Open its review link in Recent requests to approve or deny it. The request expires after the configured approval window.</p>
<form method="post" action="/actions/admin">{csrf}<input type="hidden" name="action" value="{action}">
<input type="hidden" name="ticket" value="{ticket}">{confirmation}<button>Create approval request</button></form>
<a class="refresh-link" href="/admin">Cancel review</a></section>'''
    for group in ("Updates", "Services", "Power"):
        if view != "actions" or action:
            break
        icon = _GROUP_ICONS[group]
        tone = "water" if group == "Services" else "stone"
        rows = []
        for key, spec in ADMIN_ACTIONS.items():
            if spec["group"] != group or spec.get("variant_of"):
                continue
            reason = unavailable_reason(snapshot, key)
            if snapshot.get("error"):
                reason = "Approvals disabled." if not manager.approval_url else "Approval service unavailable."
            copy = f'<span class="action-icon">{render_icon(icon)}</span><div class="action-copy"><h3>{escape(spec["title"])}</h3><p>{escape(spec["effect"])}</p></div>'
            if reason:
                rows.append(f'<li class="action-row admin-row tone-{tone}">{copy}<p class="action-unavailable">{escape(reason)}</p></li>')
            else:
                rows.append(f'<li><a class="action-row admin-row admin-action-link tone-{tone}" href="{escape(manager.action_url(key), quote=True)}">{copy}<span class="row-action">Review action <span aria-hidden="true">→</span></span></a></li>')
        identifier = "admin-" + group.lower()
        heading = render_heading(group, icon, heading_id=identifier)
        body += f'<section aria-labelledby="{identifier}"><div class="section-heading">{heading}<span class="count">{len(rows)} actions</span></div><ul class="action-list">{"".join(rows)}</ul></section>'
    if view == "activity":
        body += '<section aria-label="Inspection tools">' + render_heading("Inspection tools", "diagnostics") + '<div class="admin-tools"><a class="refresh-link" href="/shares-data">Shares &amp; Data</a><a class="refresh-link" href="/services">Local services</a><a class="refresh-link" href="/jobs">Scheduled jobs</a><a class="refresh-link" href="/logs">Service diagnostics</a><a class="refresh-link" href="/#audit-heading">Security activity</a></div><p class="endpoint">Long jobs run outside the panel, with a six-hour limit. Refresh status to check results without automatic page reloads. For detailed failures, use the administrator SSH or console path.</p></section>'
    return render_document(title=f"Admin controls · {state.manifest['host']}", style=style + _STYLE,
                           header=header, content=body, navigation=state.navigation("admin"),
                           footer='<footer><a href="/">Back to dashboard</a><span>One-time approval for host actions</span></footer>')
