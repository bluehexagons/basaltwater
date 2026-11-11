"""Panel approval reviews rendered by the isolated approval identity."""

from __future__ import annotations

import html
from common.service_tools.web_panel_features import AGENT_TOOLS, MANIFEST, PanelFeatures, unit_installed
from common.service_tools.web_panel_templates import panel_navigation, render_document
from lib.atomic_io import read_json_file
from lib.guacamole_config import SERVICE, SSH_SERVICE
from lib.github_runner_config import SERVICE as RUNNER_SERVICE


STYLE = """
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 system-ui;overflow-wrap:anywhere}
main{max-width:1440px;margin-left:248px;padding:24px 28px 48px;min-width:0}
.sidebar{position:fixed;inset:0 auto 0 0;width:248px;padding:28px 16px;background:var(--panel);border-right:1px solid var(--line);overflow:auto}
.sidebar a{display:flex;align-items:center;gap:11px;min-height:44px;padding:10px 12px;margin:3px 0;color:var(--muted);border-radius:8px;text-decoration:none}
.nav-group,.eyebrow{font-size:.75rem;font-weight:750;letter-spacing:.1em;text-transform:uppercase;color:var(--muted)}
header{padding:20px 24px;border:1px solid var(--line);border-top:3px solid var(--accent);border-radius:12px;margin-bottom:24px}
h1{margin:.4rem 0;font-size:clamp(1.5rem,3vw,2rem);line-height:1.25}h2{font-size:1.2rem}h3{font-size:1rem}a{color:var(--accent);text-underline-offset:4px}a:focus-visible,button:focus-visible{outline:3px solid var(--accent);outline-offset:3px}
dl,ul{padding:20px;background:var(--panel);border:1px solid var(--line);border-radius:12px}
ul{padding-left:40px}li{padding:10px 0}dt{font-size:.85rem;color:var(--muted);margin-top:16px}dt:first-child{margin-top:0}
dd{margin:4px 0 0;white-space:pre-wrap;overflow-wrap:anywhere}code,pre{white-space:pre-wrap;overflow-wrap:anywhere}
.notice{padding:16px 20px;border-left:3px solid var(--warning);background:var(--stone-soft);border-radius:0 8px 8px 0}
.approval-state{display:flex;align-items:center;gap:10px}.approval-state strong{padding:5px 12px;border:1px solid var(--line);border-radius:20px;background:var(--panel);font-size:.85rem}
.approval-facts{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px 24px;margin:20px 0}
.approval-facts dt{margin:0}.approval-targets{padding:16px 20px;background:var(--panel);border:1px solid var(--line);border-radius:12px}
.approval-targets ol{margin:0;padding-left:24px}.approval-targets li{padding:6px 0;margin:0;font-size:.9rem}
.approval-details{border-top:1px solid var(--line);padding-top:8px}.approval-details dl{font-size:.85rem}
.approval-details pre{margin:0}pre{padding:16px;border:1px solid var(--line);border-radius:8px;background:var(--panel);font-size:.85rem;max-height:28rem;overflow:auto}
.approval-task:hover{background:var(--accent-soft)}.approval-task{color:var(--text)}.muted{color:var(--muted)}
.approval-option{color:var(--text)}fieldset{border-color:var(--line)}
button{font:inherit;min-height:44px;padding:10px 18px;border:1px solid var(--accent);border-radius:8px;color:var(--accent);background:var(--panel);cursor:pointer}
button[value=approve]{color:var(--panel);background:var(--accent)}footer{margin-top:32px;padding:16px 0;color:var(--muted);font-size:.85rem}
.skip-link{position:fixed;top:-100px;left:16px;padding:12px;background:var(--panel);z-index:3}.skip-link:focus{top:12px}
@media(max-width:900px){.sidebar{position:relative;width:auto;padding:12px;border-right:0;border-bottom:1px solid var(--line)}main{margin-left:0;padding:16px}}
@media(max-width:600px){.approval-facts{grid-template-columns:1fr}header{padding:16px}}
"""


def configured_panel_features() -> PanelFeatures:
    """Read setup-selected destinations without accessing the operator's home.

    The isolated approval account cannot discover binaries inside that home.
    Navigation is informational; each destination still enforces availability.
    """
    try:
        manifest = read_json_file(MANIFEST, max_bytes=256 * 1024)
        configured = manifest["features"]
        tools = set()
        for key, allowed in (("agent_tools", AGENT_TOOLS), ("publishing_tools", {"butler", "steamcmd"})):
            selected = configured.get(key, [])
            if isinstance(selected, list) and all(isinstance(item, str) for item in selected):
                tools.update(allowed.intersection(selected))
        if configured.get("git") is True:
            tools |= {"git"}
        features = PanelFeatures(
            tools=frozenset(tools), t3=configured.get("t3_update") is True,
            desktop=unit_installed(SERVICE + ".service", ""),
            console=unit_installed(SSH_SERVICE + ".socket", ""),
            github_runner=unit_installed(RUNNER_SERVICE, ""),
        )
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        features = PanelFeatures()
    return features


def approval_navigation() -> tuple:
    items = list(panel_navigation(available=configured_panel_features().pages))
    index = next((index + 1 for index, item in enumerate(items) if item[0] == "/admin"), len(items))
    items.insert(index, ("/approvals", "Privilege approvals", "admin"))
    return tuple(items)


def render_approval_page(content: str, *, title: str = "Privilege approvals") -> str:
    from common.service_tools.privilege_approval import REVIEW_STYLE

    return render_document(
        title=title, style=REVIEW_STYLE + STYLE,
        header='<header><p class="eyebrow">Host administration</p><h1>' + html.escape(title) + '</h1></header>',
        content=content,
        navigation=approval_navigation(),
        footer='<footer><a href="/admin">Back to Admin controls</a></footer>',
    )
