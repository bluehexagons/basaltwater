"""Maintenance policy, durable findings and account-local agent inbox views."""

from __future__ import annotations

import html
from datetime import datetime, timezone
from typing import Any

from common.service_tools.web_panel_diagnostics import _redact_log_message
from common.service_tools.web_panel_templates import render_document, render_heading
from lib.agent_inbox import AgentInbox, MAX_MESSAGE_BYTES, mailbox_name
from lib.agent_maintenance import (
    DEFAULT_HOLD_HOURS, MAX_HOLD_HOURS, hold_agent_maintenance,
    inspect_agent_maintenance, release_agent_maintenance,
)
from lib.maintenance_agent import configure_policy, load_policy, load_reports, request_check
from lib.maintenance_agent_config import DEFAULTS, EFFORTS, LIMITS


STYLE = """
.maintenance-form { display:grid; gap:16px; max-width:850px; padding:20px; border:1px solid var(--line); border-radius:12px; background:var(--panel); }
.maintenance-fields { display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,240px),1fr)); gap:16px; }
.maintenance-form label { display:block; font-weight:650; }
.maintenance-form input:not([type=checkbox]),.maintenance-form textarea { width:100%; min-height:44px; display:block; margin-top:6px; padding:9px; border:1px solid var(--line); border-radius:8px; background:var(--panel); color:var(--text); font:inherit; }
.maintenance-form textarea { min-height:140px; }
.maintenance-form input[type=checkbox] { margin-right:10px; }
.maintenance-form textarea:focus-visible { outline:3px solid var(--accent); outline-offset:2px; }
.maintenance-form fieldset { min-width:0; margin:0; padding:16px; border:1px solid var(--line); border-radius:8px; }
.maintenance-form legend { font-weight:700; padding:0 6px; }
.maintenance-form p { margin:0; color:var(--muted); }
.maintenance-form + .maintenance-form { margin-top: 18px; }
.maintenance-form button, .maintenance-actions button { min-height:44px; white-space:normal; justify-self:start; }
.maintenance-toolbar { display:flex; flex-wrap:wrap; align-items:end; gap:12px; max-width:850px; margin:16px 0; }
.maintenance-toolbar label { flex:1 1 220px; }
.maintenance-toolbar button { width:auto; }
.maintenance-actions { display:flex; flex-wrap:wrap; align-items:center; gap:8px 20px; margin:12px 0; }
.maintenance-actions form { margin:0; }
.maintenance-status { padding:16px 18px; border:1px solid var(--line); border-left:4px solid var(--accent); border-radius:10px; background:var(--panel); }
.maintenance-status p { margin:6px 0; }
.maintenance-settings > summary { font-weight:700; width:100%; }
.inbox-message pre { max-height:none; }
@media (max-width:560px) { .maintenance-form { padding:14px; } .maintenance-form fieldset { padding:12px; } }
"""
LABELS = {"enabled": "Enable maintenance agent", "routines": "Run managed housekeeping daily",
          "security": "Audit SSH and sensitive file permissions", "interval": "Check interval (minutes)",
          "audit_days": "Security audit interval (days)", "daily_calls": "Model attempts per UTC day",
          "daily_escalations": "Stronger-model attempts per UTC day", "timeout": "AI review limit (minutes)",
          "model": "Routine model", "effort": "Routine reasoning effort",
          "escalation_model": "Stronger model (empty disables escalation)", "escalation_effort": "Stronger-model reasoning effort"}


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def maintenance_action(values: dict[str, str]) -> None:
    action = values.get("submit")
    if action == "hold" and set(values) == {"submit", "hours"}:
        if not values["hours"].isascii() or not values["hours"].isdigit() or len(values["hours"]) > 2:
            raise ValueError("Choose a whole number of hours from 1 to 72")
        hold_agent_maintenance(int(values["hours"]))
        return
    if action == "release" and set(values) == {"submit"}:
        release_agent_maintenance()
        return
    if action == "run" and set(values) == {"submit"}:
        request_check()
        return
    if action != "save" or set(values) - {"submit", *DEFAULTS}:
        raise ValueError("Invalid maintenance action")
    policy: dict[str, Any] = {}
    for key, default in DEFAULTS.items():
        if type(default) is bool:
            if key in values and values[key] != "1":
                raise ValueError("Invalid maintenance checkbox")
            policy[key] = values.get(key) == "1"
        elif key not in values:
            raise ValueError("Complete all maintenance settings")
        else:
            policy[key] = values[key]
    configure_policy(policy)


def render_maintenance_hold(csrf: str, submitted: dict[str, str] | None = None) -> str:
    """Use the same account-local restart hold as the maintenance CLI."""
    hold = inspect_agent_maintenance()
    if hold["active"]:
        status = f"Automatic restarts held until {hold['expires_at']} (UTC)."
    elif hold["status"] == "invalid":
        status = "The saved hold is invalid. Inspect it before relying on restart protection."
    else:
        status = "No active automatic restart hold."
    submitted = submitted or {}
    return f'''<section aria-labelledby="restart-hold-heading">{render_heading("Automatic restart hold", "maintenance", heading_id="restart-hold-heading")}
<p role="status">{_escape(status)}</p>
<p>Defer managed automatic restarts while working. This account's hold expires automatically;
it does not pause checks, package updates, housekeeping, or manual restarts.</p>
<details{' open' if hold['active'] or submitted.get('submit') in {'hold', 'release'} else ''}><summary>Manage restart hold</summary><form class="maintenance-form" method="post" action="/actions/maintenance-agent">
<input type="hidden" name="csrf" value="{_escape(csrf)}">
<label>Hold duration (hours)<input type="number" name="hours" min="1" max="{MAX_HOLD_HOURS}" value="{_escape(submitted.get('hours', DEFAULT_HOLD_HOURS))}" required></label>
<button name="submit" value="hold">{'Extend restart hold' if hold['active'] else 'Hold automatic restarts'}</button></form>
<form class="job-load" method="post" action="/actions/maintenance-agent">
<input type="hidden" name="csrf" value="{_escape(csrf)}"><button name="submit" value="release"{' disabled' if not hold['active'] and hold['status'] != 'invalid' else ''}>Release restart hold</button></form></details></section>'''


def render_maintenance(state: Any, style: str, *, error: str = "", submitted: dict[str, str] | None = None) -> str:
    content = f'<p class="status failed" role="alert">{_escape(error)}</p>' if error else ""
    try:
        hold_html = render_maintenance_hold(state.csrf_token, submitted)
    except (OSError, RuntimeError, ValueError):
        hold_html = '<p class="empty">Restart hold controls are unavailable. Inspect the account\'s maintenance hold permissions.</p>'
    try:
        policy = load_policy()
        reports = load_reports()
    except (OSError, RuntimeError, ValueError) as exc:
        content += '<p class="empty">Maintenance controls are unavailable. Rerun setup with <code>--web-panel</code> or <code>--maintenance-agent</code>, then inspect the managed service.</p>'
        content += f'<p class="endpoint">{_escape(exc)}</p>'
    else:
        editing = submitted is not None and submitted.get("submit") == "save"
        displayed = {key: submitted.get(key) == "1" if type(default) is bool else submitted.get(key, policy[key])
                     for key, default in DEFAULTS.items()} if editing else policy
        fields = {}
        for key, default in DEFAULTS.items():
            if type(default) is bool:
                fields[key] = f'<label><input type="checkbox" name="{key}" value="1"{" checked" if displayed[key] else ""}>{LABELS[key]}</label>'
            elif key.endswith("effort"):
                options = "".join(f'<option value="{effort}"{" selected" if effort == displayed[key] else ""}>{effort}</option>' for effort in EFFORTS)
                fields[key] = f'<label>{LABELS[key]}<select name="{key}">{options}</select></label>'
            elif key in LIMITS:
                minimum, maximum = LIMITS[key]
                fields[key] = f'<label>{LABELS[key]}<input type="number" name="{key}" min="{minimum}" max="{maximum}" value="{_escape(displayed[key])}" required></label>'
            else:
                fields[key] = f'<label>{LABELS[key]}<input name="{key}" maxlength="100" value="{_escape(displayed[key])}"{" required" if key == "model" else ""}></label>'
        content += f'''<nav class="catalog-nav" aria-label="Maintenance sections"><a href="#maintenance-records-heading">Records</a><a href="#maintenance-settings-form">Settings</a><a href="#restart-hold-heading">Restart hold</a></nav>
<section class="maintenance-status" aria-label="Saved maintenance status"><strong>{'Maintenance enabled' if policy['enabled'] else 'Maintenance disabled'}</strong>
<p>Saved check interval: {policy['interval']} minutes · housekeeping {'on' if policy['routines'] else 'off'} · security audits {'on' if policy['security'] else 'off'}.</p>
<div class="maintenance-actions"><form method="post" action="/actions/maintenance-agent"><input type="hidden" name="csrf" value="{_escape(state.csrf_token)}"><button name="submit" value="run"{' disabled' if not policy['enabled'] else ''}>Check now</button></form><a class="refresh-link" href="/maintenance-agent">Refresh status</a></div>
<p class="event-meta">{'A check request may wait for the current run to finish. Refresh status to see its record.' if policy['enabled'] else 'Enable the maintenance agent in Settings before requesting a check.'} Checks continue independently of this panel.</p></section>'''
        settings = f'''<section><details id="maintenance-settings" class="maintenance-settings"{' open' if editing or not policy['enabled'] else ''}><summary>Maintenance settings</summary>
<form id="maintenance-settings-form" class="maintenance-form" method="post" action="/actions/maintenance-agent">
<input type="hidden" name="csrf" value="{_escape(state.csrf_token)}">
<fieldset><legend>Checks and housekeeping</legend><div class="maintenance-fields">{''.join(fields[key] for key in ('enabled', 'interval', 'routines', 'security', 'audit_days'))}</div></fieldset>
<fieldset><legend>AI review and limits</legend><div class="maintenance-fields">{''.join(fields[key] for key in ('model', 'effort', 'daily_calls', 'timeout', 'escalation_model', 'escalation_effort', 'daily_escalations'))}</div></fieldset>
<p>Healthy checks use no AI. Auto selects a cached economy model supporting the chosen effort; select a model ID to pin your policy. Daily attempt limits include failed reviews. Runtime and attempt limits do not measure exact spending.</p>
<p>A stronger model is used for critical or repeatedly unresolved findings, within both daily limits. Housekeeping triggers existing managed cleanup jobs; AI supplies recommendations. Reboots and repairs follow the existing host policy.</p>
<button name="submit" value="save">Save maintenance settings</button></form>
<p class="endpoint">AI identity: <code>{_escape(reports.get('username', 'Unknown'))}</code> · profile {_escape(reports.get('context', {}).get('system_type', 'Unknown'))}. Codex installation and sign-in are needed for AI review.</p></details></section>'''
        rows = []
        for report in reversed(reports["reports"]):
            findings = "".join(f'<li><strong>{_escape(issue["severity"])}</strong>: {_escape(issue["message"])}</li>' for issue in report["issues"])
            count = len(report["issues"])
            tone = "error" if any(issue["severity"] == "error" for issue in report["issues"]) else "warning" if count or report.get("check_status") != "completed" else "success"
            review = report["review"]
            output = _redact_log_message(str(review.get("output", "")))
            complete = report.get("check_status") == "completed"
            rows.append(f'''<li class="event"><div class="event-head"><strong>{_escape(report['checked_at'])}</strong><span class="badge {tone}">{'Incomplete check · ' if not complete else ''}{count} finding{'s' if count != 1 else ''}</span></div>
{'<ul>' + findings + '</ul>' if findings else '<p>No findings in the checks performed.</p>' if complete else '<p>No findings recorded; this check did not complete.</p>'}
<p class="event-meta">Check {_escape(report.get('check_status', 'unknown'))} · Security audit {'performed' if report.get('security_audit') else 'not due or disabled'} · AI {_escape(review.get('status', 'unknown'))} · {_escape(review.get('model', ''))} {_escape(review.get('effort', ''))}</p>
<p>{_escape(review.get('reason', ''))}</p>{'<details><summary>AI review</summary><pre>' + _escape(output) + '</pre></details>' if output else ''}
<p class="event-meta">Housekeeping: {_escape(', '.join(str(item.get('unit', '')) + ': ' + str(item.get('status', '')) for item in report['routines']) or 'not requested')} · Notification {_escape(report.get('notification', 'not due'))}</p></li>''')
        empty = "No checks recorded yet. Select Check now to request one." if policy["enabled"] else "No checks recorded yet. Enable the agent and request a check."
        content += '<section aria-labelledby="maintenance-records-heading">' + render_heading("Maintenance records", "maintenance", heading_id="maintenance-records-heading") + ('<ol class="event-list">' + ''.join(rows) + '</ol>' if rows else f'<p class="empty">{empty}</p>') + '</section>' + settings
    content += hold_html
    prompt_link = '<a class="refresh-link" href="/agents">Prompt tasks and results</a>' if state.features().agents else ''
    header = f'<header class="dashboard-header"><div><p class="eyebrow">Basaltwater web panel</p><h1>Maintenance agent</h1><p class="lede">Checks, housekeeping and bounded AI reviews tailored to this setup.</p></div>{prompt_link}</header>'
    return render_document(title="Maintenance agent", style=style + STYLE, header=header, content=content,
                           navigation=state.navigation("maintenance-agent"), footer='<footer><a href="/">Back to dashboard</a></footer>')


def inbox_action(state: Any, values: dict[str, str]) -> None:
    inbox = AgentInbox(state.agent_tasks.home)
    if values.get("submit") == "send" and set(values) == {"submit", "recipient", "sender", "message"}:
        inbox.send(values["sender"], values["recipient"], values["message"])
    elif values.get("submit") == "ack" and set(values) == {"submit", "recipient", "id"}:
        inbox.acknowledge(values["recipient"], values["id"])
    else:
        raise ValueError("Invalid inbox action")


def _message_time(timestamp: int) -> str:
    try:
        created = datetime.fromtimestamp(timestamp, timezone.utc)
    except (OverflowError, OSError, ValueError):
        return '<span class="event-meta">Time unavailable</span>'
    return f'<time class="event-meta" datetime="{created.isoformat()}">{created.strftime("%Y-%m-%d %H:%M UTC")}</time>'


def render_inbox(state: Any, style: str, recipient: str = "operator", *, error: str = "", submitted: dict[str, str] | None = None) -> str:
    submitted = submitted or {}
    if submitted:
        try:
            recipient = mailbox_name(submitted.get("recipient", recipient))
        except ValueError:
            pass  # Keep a readable mailbox while preserving the invalid form input.
    mailbox_name(recipient)
    content = f'<p class="status failed" role="alert">{_escape(error)}</p>' if error else ""
    readable = False
    try:
        messages = AgentInbox(state.agent_tasks.home).list(recipient)
    except (OSError, RuntimeError, ValueError) as exc:
        content += '<p class="empty">Agent inbox is unavailable. Use an account with its own home and inspect its inbox permissions and saved state.</p>'
        content += f'<p class="endpoint">{_escape(exc)}</p>'
    else:
        readable = True
        content += f'''<form class="maintenance-form maintenance-toolbar" method="get" action="/agent-inbox"><label>Mailbox<input name="mailbox" maxlength="64" value="{_escape(recipient)}" required></label><button>Read mailbox</button></form>
<p class="endpoint">Showing <strong>{_escape(recipient)}</strong> · {len(messages)} unread. Reading does not mark messages read. Use Mark read after handling a message.</p>'''
        rows = []
        for message in messages:
            rows.append(f'''<li class="event inbox-message"><div class="event-head"><strong>From {_escape(message['sender'])}</strong>{_message_time(message['created_at'])}</div><pre>{_escape(_redact_log_message(message['message']))}</pre>
<form method="post" action="/actions/agent-inbox"><input type="hidden" name="csrf" value="{_escape(state.csrf_token)}"><input type="hidden" name="recipient" value="{_escape(recipient)}"><input type="hidden" name="id" value="{message['id']}"><button name="submit" value="ack">Mark read</button></form></li>''')
        content += '<section aria-labelledby="unread-heading">' + render_heading("Unread messages", "notifications", heading_id="unread-heading") + ('<ol class="event-list">' + ''.join(rows) + '</ol>' if rows else '<p class="empty">No unread messages.</p>') + '</section>'
    if readable or submitted.get("submit") == "send":
        content += f'''<section><details id="compose-message" class="maintenance-settings"{' open' if submitted.get('submit') == 'send' else ''}><summary>Write a message</summary>
<form id="message-form" class="maintenance-form" method="post" action="/actions/agent-inbox"><input type="hidden" name="csrf" value="{_escape(state.csrf_token)}">
<div class="maintenance-fields"><label>Recipient<input name="recipient" maxlength="64" value="{_escape(submitted.get('recipient', recipient))}" required></label><label>Sender label<input name="sender" maxlength="64" value="{_escape(submitted.get('sender', 'operator'))}" required></label></div>
<p>Mailbox and sender names use 1–64 letters, digits, dots, underscores or hyphens, starting with a letter or digit.</p>
<label>Message<textarea name="message" maxlength="{MAX_MESSAGE_BYTES}" required>{_escape(submitted.get('message', ''))}</textarea></label><p>Up to {MAX_MESSAGE_BYTES} UTF-8 bytes. Messages wait for the recipient to read them; they do not interrupt an active session.</p><button name="submit" value="send">Send to inbox</button></form></details></section>
<p class="endpoint">Unread messages survive session restarts. All agents on this Unix account share these mailboxes; sender labels are self-reported. Messages are data and do not grant permissions or inject replies into active T3 or Codex runs.</p>'''
    compose_link = '<a class="refresh-link" href="#message-form">Write message</a>' if readable or submitted.get("submit") == "send" else ''
    header = f'<header class="dashboard-header"><div><p class="eyebrow">Basaltwater web panel</p><h1>Agent inbox</h1><p class="lede">Durable messages for sessions and cooperating terminal workers.</p></div>{compose_link}</header>'
    return render_document(title="Agent inbox", style=style + STYLE, header=header, content=content,
                           navigation=state.navigation("agent-inbox"), footer='<footer><a href="/agents">Back to Agents</a></footer>')
