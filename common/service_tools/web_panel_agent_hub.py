"""Shared navigation and compact layout for the account's agent workspace."""

from __future__ import annotations

from html import escape
from typing import Any

from common.service_tools.web_panel_templates import render_document


STYLE = """
.agent-header { padding:18px 22px; margin-bottom:0; }
.agent-header h1 { font-size:1.65rem; margin:4px 0; }
.agent-header .lede { margin:0; font-size:.9rem; }
.agent-tabs { display:flex; flex-wrap:wrap; gap:2px 6px; margin:10px 0 18px; border-bottom:1px solid var(--line); }
.agent-tabs a { display:inline-flex; align-items:center; min-height:44px; padding:8px 12px; color:var(--muted); text-decoration:none; border-bottom:3px solid transparent; }
.agent-tabs a[aria-current=page] { border-color:var(--workspace); color:var(--workspace); font-weight:700; }
.agent-tabs a:hover { background:var(--workspace-soft); color:var(--workspace); }
.agent-tabs a:focus-visible { outline:3px solid var(--accent); outline-offset:-3px; }
.agent-hub section { margin:18px 0; }
.agent-hub h2 { font-size:1.05rem; margin:0 0 10px; }
.agent-hub .section-heading { margin-bottom:10px; gap:10px; }
.agent-hub .overview-grid { gap:10px; margin:12px 0; }
.agent-hub .metric { padding:12px 14px; }
.agent-hub .metric-value { font-size:1.4rem; }
.agent-hub .metric-description { margin-top:4px; }
.agent-hub .agent-columns { max-width:none; margin:12px 0; }
.agent-hub .agent-task { padding:12px 16px; }
.agent-hub .agent-task-facts { margin:10px 0; gap:8px; }
.agent-hub .agent-panel { padding:12px 16px; }
.agent-hub .agent-run > summary { padding:4px 0; }
.agent-hub .agent-result-preview { margin-top:6px; line-height:1.4; }
.agent-hub .agent-follow-up { margin:10px 0; }
.agent-hub .event { padding:12px 14px; }
.agent-hub .action-row { padding:12px 16px; }
.agent-hub .tool-form,.agent-hub .maintenance-form { max-width:none; }
.agent-hub .maintenance-form { padding:14px; gap:12px; }
.agent-hub .maintenance-fields { gap:12px; grid-template-columns:repeat(auto-fit,minmax(min(100%,210px),1fr)); }
.agent-hub .maintenance-form select { width:100%; }
.agent-dashboard-grid { display:grid; grid-template-columns:minmax(0,1.5fr) minmax(260px,1fr); gap:18px; align-items:start; }
.agent-dashboard-grid > div { min-width:0; }
.agent-dashboard-box { background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:14px 16px; }
.agent-dashboard-box p { margin:6px 0; }
.agent-dashboard-list { list-style:none; padding:0; margin:0; }
.agent-dashboard-list li { padding:10px 0; border-top:1px solid var(--accent-soft); }
.agent-dashboard-list li:first-child { border-top:0; }
.agent-dashboard-list strong,.agent-dashboard-list small { display:block; }
.agent-dashboard-list small { color:var(--muted); margin-top:3px; }
.agent-dashboard-list p { margin:4px 0; overflow-wrap:anywhere; font-size:.85rem; }
.agent-inline-status { display:flex; flex-wrap:wrap; gap:6px 16px; color:var(--muted); font-size:.85rem; }
@media(min-width:1100px) { .agent-hub .agent-fields { grid-template-columns:repeat(2,minmax(0,1fr)); } }
@media(max-width:800px) { .agent-dashboard-grid { grid-template-columns:minmax(0,1fr); gap:0; } }
@media(max-width:560px) { .agent-header { padding:14px; } .agent-tabs { gap:0; } .agent-tabs a { padding:7px 9px; font-size:.82rem; } }
"""


def agent_tabs(state: Any, current: str) -> str:
    available = state.features().pages
    choices = (
        ("dashboard", "/agents", "Dashboard", "agents"),
        ("tasks", "/agents?view=tasks", "Tasks", "agent-tools"),
        ("history", "/agents?view=history", "Run history", "agent-tools"),
        ("tools", "/agent-tools", "Agent tools", "agent-tools"),
        ("inbox", "/agent-inbox", "Inbox", "agent-inbox"),
        ("maintenance", "/maintenance-agent", "Maintenance", "maintenance-agent"),
        ("diagnostics", "/agents?view=diagnostics", "Readiness", "agents"),
    )
    return '<nav class="agent-tabs" aria-label="Agents">' + ''.join(
        f'<a href="{escape(href, quote=True)}"' + (' aria-current="page"' if key == current else '')
        + f'>{label}</a>' for key, href, label, feature in choices if feature in available
    ) + '</nav>'


def agent_document(state: Any, style: str, *, title: str, current: str, content: str,
                   description: str, actions: str = "") -> str:
    header = f'''<header class="dashboard-header agent-header"><div><p class="eyebrow">Agents · {escape(state.manifest.get('host', 'This host'))}</p>
<h1>{escape(title)}</h1><p class="lede">{escape(description)}</p></div>{actions}</header>'''
    return render_document(title=title, style=style + STYLE, header=header,
        content='<div class="agent-hub">' + agent_tabs(state, current) + content + '</div>',
        navigation=state.navigation("agents"),
        footer='<footer><a href="/">Host dashboard</a><span>Refresh to see current status · all times UTC</span></footer>')
