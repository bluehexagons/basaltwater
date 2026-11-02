"""Panel approval reviews rendered by the isolated approval identity."""

from __future__ import annotations

from common.service_tools.web_panel_templates import render_document


STYLE = """
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 system-ui;overflow-wrap:anywhere}
main{max-width:1200px;margin-left:248px;padding:24px 28px 48px}
.sidebar{position:fixed;inset:0 auto 0 0;width:248px;padding:28px 16px;background:var(--panel);border-right:1px solid var(--line);overflow:auto}
.sidebar a{display:flex;align-items:center;gap:11px;min-height:44px;padding:10px 12px;margin:3px 0;color:var(--muted);border-radius:8px;text-decoration:none}
.nav-group,.eyebrow{font-size:.75rem;font-weight:750;letter-spacing:.1em;text-transform:uppercase;color:var(--muted)}
header{padding:20px;border:1px solid var(--line);border-top:3px solid var(--accent);border-radius:12px}
h1{margin:.4rem 0}a{color:var(--accent);text-underline-offset:4px}a:focus-visible,button:focus-visible{outline:3px solid var(--accent);outline-offset:3px}
dl,ul{padding:20px;background:var(--panel);border:1px solid var(--line);border-radius:12px}
ul{padding-left:40px}li{padding:10px 0}dt{font-size:.85rem;color:var(--muted);margin-top:16px}dt:first-child{margin-top:0}
dd{margin:4px 0 0;white-space:pre-wrap;overflow-wrap:anywhere}code,pre{white-space:pre-wrap;overflow-wrap:anywhere}
.notice{padding:16px;border-left:4px solid var(--warning);background:var(--stone-soft)}
form{display:flex;flex-wrap:wrap;gap:12px}button{font:inherit;min-height:44px;padding:10px 18px;border:1px solid var(--accent);border-radius:8px;color:var(--accent);background:var(--panel);cursor:pointer}
button[value=approve]{color:var(--panel);background:var(--accent)}footer{margin-top:32px;padding:16px 0;color:var(--muted);font-size:.85rem}
.skip-link{position:fixed;top:-100px;left:16px;padding:12px;background:var(--panel);z-index:3}.skip-link:focus{top:12px}
@media(max-width:900px){.sidebar{position:relative;width:auto;padding:12px;border-right:0;border-bottom:1px solid var(--line)}main{margin-left:0;padding:16px}}
"""


def render_approval_page(content: str) -> str:
    return render_document(
        title="Privilege approvals", style=STYLE,
        header='<header><p class="eyebrow">Host administration</p><h1>Privilege approvals</h1>'
               '<p>Review requests and approve or deny them once.</p></header>',
        content=content,
        navigation=(("/", "Dashboard", None), ("/admin", "Admin controls", None),
                    ("/approvals", "Privilege approvals", "admin")),
        footer='<footer><a href="/admin">Back to Admin controls</a><span> · Every decision is recorded.</span></footer>',
    )
