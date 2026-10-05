"""Account-scoped Git and credential settings for the authenticated panel."""

from __future__ import annotations

import html
import shlex
import urllib.parse

from common.service_tools.web_panel_diagnostics import _redact_log_message
from common.service_tools.web_panel_templates import panel_navigation, render_document, render_heading


_STYLE = """
.account-section { padding:16px 18px; border:1px solid var(--line); border-left:3px solid var(--sea);
  border-radius:10px; background:var(--panel); }
.account-section h2 { margin-bottom:8px; }
.account-section p { margin:8px 0; color:var(--muted); font-size:.85rem; }
.account-form { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; margin-top:14px; }
.account-form label { font-size:.85rem; font-weight:650; }
.account-form input { display:block; width:100%; min-height:44px; padding:8px 10px; margin-top:5px;
  border:1px solid var(--line); border-radius:7px; background:var(--bg); color:var(--text); font:inherit; }
.account-form .account-wide { grid-column:1/-1; }
.account-actions { display:flex; flex-wrap:wrap; align-items:center; gap:12px; margin-top:12px; }
.account-actions p { flex:1; min-width:min(100%,240px); }
.account-secondary { background:var(--sea-soft); color:var(--sea); }
.account-section button { min-height:44px; }
.account-note { margin:14px 0; }
@media(max-width:650px) { .account-form { grid-template-columns:minmax(0,1fr); }
  .account-section { padding:14px; } .account-actions { align-items:stretch; } }
"""
_MESSAGES = {
    "identity": "Git commit name and email saved for this VM account.",
    "github": "GitHub token saved and Git HTTPS authentication configured. Provider access has not been tested.",
    "removed": "The stored github.com entry was removed. Revoke the token at GitHub if it should no longer be usable.",
}


def parse_credentials_query(raw: str) -> dict[str, str]:
    if len(raw) > 64:
        raise ValueError("Invalid account settings query")
    query = urllib.parse.parse_qs(raw, keep_blank_values=True, max_num_fields=1)
    if not query:
        return {}
    if set(query) != {"saved"} or len(query["saved"]) != 1 or query["saved"][0] not in _MESSAGES:
        raise ValueError("Invalid account settings query")
    return {"saved": query["saved"][0]}


def render_credentials(state, page_style: str, query: dict[str, str], *, error: str = "") -> str:
    snapshot = state.git_settings.snapshot()
    escape = html.escape
    csrf = f'<input type="hidden" name="csrf" value="{escape(state.csrf_token, quote=True)}">'
    username = escape(state.manifest["username"])
    host = escape(state.manifest["host"])
    status = ""
    if error:
        status = f'<aside class="status failed" role="alert"><strong>Settings could not be saved</strong><p>{escape(error)}</p></aside>'
    elif query.get("saved") in _MESSAGES:
        status = f'<aside class="status complete" role="status"><p>{_MESSAGES[query["saved"]]}</p></aside>'
    header = f'''<header><p class="eyebrow">Basaltwater web panel</p><h1>Git &amp; credentials</h1>
<p class="lede">Commit identity and GitHub access for <strong>{username}</strong> on <code>{host}</code>.</p></header>'''
    content = status
    if not snapshot["available"]:
        content += '<p class="empty">Account editing requires a panel running as the non-root setup user with an owned home. Root-profile panels use a dedicated service account; configure Git and credentials through the controller for the intended user.</p>'
    else:
        name = escape(_redact_log_message(str(snapshot["name"])), quote=True)
        email = escape(_redact_log_message(str(snapshot["email"])), quote=True)
        git_error = f'<p role="status">{escape(str(snapshot["git_error"]))}</p>' if snapshot["git_error"] else ""
        github_error = f'<p role="status">{escape(str(snapshot["github_error"]))}</p>' if snapshot["github_error"] else ""
        github_status = "Stored token present" if snapshot["github_present"] else "No stored token found"
        identity_status = "Ready to commit" if snapshot["name"] and snapshot["email"] and not snapshot["git_error"] else "Commit identity needs attention"
        disabled = ' disabled aria-describedby="github-transport-help"' if not state.credential_transport_available() else ""
        transport_help = (
            '<p id="github-transport-help">Token changes require this panel over HTTPS. Add <code>--ssl</code> through setup, or use <code>basaltw agent auth set HOST USER --tool gh --file PATH</code> over SSH.</p>'
            if disabled else '<p id="github-transport-help">Tokens are written privately on this VM and are never displayed after submission.</p>'
        )
        content += f'''<nav class="catalog-nav" aria-label="Account settings"><a href="#git-identity">Commit identity</a><a href="#github-token">GitHub token</a><a href="#provider-sign-in">Agent sign-in</a></nav>
<section class="account-section" id="git-identity" aria-labelledby="git-identity-heading">
{render_heading("Commit identity", "agents", heading_id="git-identity-heading")}
<p><strong>{identity_status}</strong> · public author fields in your global Git configuration.</p>{git_error}
<form class="account-form" method="post" action="/actions/credentials/identity">{csrf}
<label>Author name<input name="name" value="{name}" maxlength="256" autocomplete="name" required></label>
<label>Author email<input name="email" type="email" value="{email}" maxlength="320" autocomplete="email" required></label>
<div class="account-wide account-actions"><button type="submit">Save commit identity</button><p>Applies to new commits across this account. Repository settings can override these values.</p></div></form>
<details><summary>Controller defaults and per-VM overrides</summary>
<p>Setup fills missing name and email from the controller even without a GitHub credential transfer. Existing VM values are retained.</p>
<pre><code>basaltw patch HOST USER --git-name 'Author Name' --git-email author@example.net</code></pre>
<p>Explicit controller overrides are saved per VM and reapplied on setup reruns. Panel edits affect this VM immediately; update an explicit controller override too if you want the edit retained on future reruns.</p></details></section>
<section class="account-section" id="github-token" aria-labelledby="github-token-heading">
{render_heading("GitHub access", "access", heading_id="github-token-heading")}
<p><strong>{github_status}</strong> · github.com · provider acceptance and repository permissions have not been checked.</p>{github_error}
<p>Use a fine-grained token limited to the repositories this VM needs. Contents read permits cloning; Contents write permits pushing. Other GitHub operations may need more permissions.</p>
<form class="account-form" method="post" action="/actions/credentials/github" autocomplete="off">{csrf}
<label class="account-wide">New GitHub access token<input name="token" type="password" maxlength="8192" autocomplete="new-password" spellcheck="false" required{disabled}></label>
<div class="account-wide account-actions"><button type="submit"{disabled}>Save or replace token</button><p>Replaces the stored github.com entry for this account and configures Git to use GitHub CLI. Other hosts are retained.</p></div></form>
{transport_help}
<details><summary>Remove stored GitHub access</summary><p>This removes the github.com entry from this VM's credential file. Credentials in a keyring or environment remain separate. It does not revoke the token at GitHub.</p>
<form method="post" action="/actions/credentials/github-remove">{csrf}
<label><input name="confirmation" type="checkbox" value="remove" required> Remove the stored github.com entry</label>
<div class="account-actions"><button class="account-secondary" type="submit"{disabled}>Remove stored entry</button></div></form></details>
<p>Inspect authenticated access from the VM with <code>gh auth status --hostname github.com</code>, then test the intended repository. <a href="/agents">Agent readiness</a> provides additional diagnostics.</p></section>'''
    login = shlex.join(["basaltw", "agent", "auth", "login", state.manifest["host"], state.manifest["username"]])
    content += f'''<section class="account-section" id="provider-sign-in" aria-labelledby="provider-sign-in-heading">
{render_heading("Agent sign-in", "agent-tools", heading_id="provider-sign-in-heading")}
<p>Codex subscription sessions belong to each VM. From the controller, start a device login and authorize its code in your browser:</p>
<pre><code>{escape(login)}</code></pre>
<p>Claude Code and OpenCode use their own sign-in flows or explicit credential file imports through <code>basaltw agent auth set</code>.</p>
<a class="refresh-link" href="/agents">View tool credentials and readiness</a></section>'''
    footer = '<footer><a href="/">Back to dashboard</a><span>Settings apply to this VM account</span></footer>'
    return render_document(title="Git & credentials", style=page_style + _STYLE, header=header, content=content,
                           navigation=panel_navigation(current="credentials"), footer=footer)
