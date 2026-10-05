#!/usr/bin/env python3
"""Regenerate Basaltwater vector assets and static review specimens."""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from common.service_tools.web_panel_templates import (
    BRAND_ICONS, BRAND_NETWORK, BRAND_PALETTES, BRAND_SYMBOL, BRAND_TEXTURES,
    ICON_TONES, NAVIGATION_ICONS, brand_styles, render_document, render_favicon,
    render_icon, render_texture, svg_theme_styles,
)
from common.service_tools import web_panel_service as panel
from common.service_tools import web_panel_agents as agents
from common.service_tools import web_panel_diagnostics as diagnostics
from common.service_tools import web_panel_jobs as jobs
from common.service_tools import web_panel_storage as storage
from common.service_tools import web_panel_host as host

GUIDE_STYLE = """
.identity-intro { display: grid; grid-template-columns: minmax(0, 1fr) 160px;
  align-items: center; gap: 32px; padding: 28px 0 12px; }
.identity-intro h2 { font-size: clamp(1.6rem, 3vw, 2.2rem); line-height: 1.15; }
.identity-intro p { max-width: 540px; color: var(--muted); }
.identity-mark { display: grid; place-items: center; padding: 20px; background: var(--panel);
  border: 1px solid var(--accent-soft); border-radius: 16px; }
.identity-mark svg { width: 112px; height: 112px; }
.asset-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px; margin-top: 16px; }
.asset-card { border: 1px solid var(--accent-soft); border-radius: 12px; overflow: hidden;
  background: var(--panel); }
.asset-card p { margin: 0; padding: 14px 20px; font-size: .85rem; color: var(--muted); }
.asset-card a { color: var(--accent); text-underline-offset: 3px; }
.logo-swatch { min-height: 128px; display: flex; align-items: center; justify-content: center;
  gap: 24px; padding: 24px; }
.logo-swatch img { max-width: 100%; height: auto; }
.logo-swatch.light { background: #f3f8fa; }
.logo-swatch.dark { background: #101a21; }
.icon-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 132px), 1fr));
  gap: 10px; margin-top: 16px; }
.icon-swatch { display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 12px; min-height: 112px; padding: 16px 10px; border: 1px solid var(--accent-soft);
  border-radius: 10px; background: var(--panel); color: var(--section-ink); text-decoration: none; }
.icon-swatch:hover { background: var(--accent-soft); border-color: var(--accent); }
.icon-swatch svg { width: 28px; height: 28px; }
.icon-swatch span { color: var(--muted); font-size: .72rem; text-align: center; }
.texture-swatch { min-height: 144px; background: var(--bg); }
.texture-swatch.basalt { background-image: url(texture-basalt-light.svg); }
.texture-swatch.water { background-image: url(texture-water-light.svg); }
.color-roles { display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,180px),1fr)); gap:12px; margin-top:16px; }
.color-role { padding:16px; border-left:4px solid var(--section-ink); border-radius:8px; background:var(--section-soft); }
.color-role strong { display:block; color:var(--section-ink); }
.color-role span { font-size:.85rem; color:var(--muted); }
.logo-swatch.adaptive { background:var(--panel); }
@media (prefers-color-scheme: dark) {
  .texture-swatch.basalt { background-image: url(texture-basalt-dark.svg); }
  .texture-swatch.water { background-image: url(texture-water-dark.svg); }
}
@media (max-width: 560px) {
  .asset-grid { grid-template-columns: minmax(0, 1fr); }
  .identity-intro { grid-template-columns: minmax(0, 1fr) 80px; gap: 16px; padding-top: 12px; }
  .identity-mark { padding: 12px; }
  .identity-mark svg { width: 56px; height: 56px; }
  .logo-swatch { padding: 20px 16px; }
}
"""


def write_svg(destination: Path, source: str) -> None:
    """Write readable SVG XML without editor metadata or binary payloads."""
    ET.register_namespace("", "http://www.w3.org/2000/svg")
    root = ET.fromstring(source)
    ET.indent(root, space="  ")
    destination.write_text(ET.tostring(root, encoding="unicode") + "\n", encoding="utf-8")


def export_assets(assets: Path) -> None:
    """Render assets into the supplied development output directory."""
    assets.mkdir(exist_ok=True)
    write_svg(assets / "favicon.svg", render_favicon())
    opening, _, artwork = BRAND_NETWORK.partition(">")
    network = opening.replace('aria-hidden="true"', 'role="img" aria-label="Connected basalt columns"')
    write_svg(assets / "artwork-network.svg", network + ">" + svg_theme_styles() + artwork)
    for theme in ("light", "dark", "mono"):
        palette = BRAND_PALETTES["dark" if theme == "dark" else "light"]
        ink = palette["text"] if theme != "mono" else "#000000"
        water = palette["brand-water"] if theme != "mono" else ink
        symbol = BRAND_SYMBOL.replace('aria-hidden="true" focusable="false"', 'role="img" aria-label="Basaltwater symbol"')
        symbol = symbol.replace("currentColor", ink).replace(f"var(--brand-water,{ink})", water)
        write_svg(assets / f"symbol-{theme}.svg", symbol)
        mark = symbol.replace('viewBox="0 0 32 32"', 'viewBox="0 0 264 48"').replace('width="32" height="32"', 'width="264" height="48"')
        mark = mark.replace('aria-label="Basaltwater symbol"', 'aria-label="Basaltwater"')
        opening, _, artwork = mark.partition(">")
        mark = (opening + '><g transform="translate(0 8)">' + artwork.removesuffix("</svg>")
                + '</g><text x="44" y="33" font-family="DejaVu Sans, sans-serif" '
                'font-size="30" letter-spacing="-1.2" fill="' + ink + '">Basaltwater</text></svg>')
        write_svg(assets / f"wordmark-{theme}.svg", mark)
    for name in BRAND_ICONS:
        write_svg(assets / f"icon-{name}.svg", render_icon(name, label=name.replace("-", " ").title()))
    for theme, palette in BRAND_PALETTES.items():
        for name in BRAND_TEXTURES:
            texture = render_texture(name, color=palette["accent"])
            texture = texture.replace('aria-hidden="true"', f'role="img" aria-label="{name.title()} texture"')
            texture = texture.replace('stroke-width="1"', 'stroke-width="1" opacity=".18"')
            write_svg(assets / f"texture-{name}-{theme}.svg", texture)
    (assets / "palette.json").write_text(json.dumps(BRAND_PALETTES, indent=2) + "\n", encoding="utf-8")
    (assets / "theme.css").write_text(brand_styles() + "\n", encoding="utf-8")
    navigation = (("index.html", "Identity", "identity"), ("readme.html", "README specimen", None),
                  ("panel.html", "Web panel specimen", None), ("tools.html", "Agent tools specimen", None),
                  ("services.html", "Service artwork specimen", None), ("admin.html", "Administration specimen", None),
                  ("jobs.html", "Scheduled jobs specimen", None), ("logs.html", "Diagnostics specimen", None),
                  ("agents.html", "Agents specimen", None))
    icon_labels = {name: label for label, name in NAVIGATION_ICONS.items()}
    icon_gallery = "".join(
        f'<a class="icon-swatch tone-{ICON_TONES.get(name, "water")}" href="icon-{name}.svg">{render_icon(name)}'
        f'<span>{html.escape(icon_labels.get(name, name.title()))}</span></a>'
        for name in BRAND_ICONS
    )
    specimens = {
        "index.html": (
            "Identity guide", "Basaltwater identity",
            '<div class="identity-intro"><div><p class="eyebrow">Stone / structure / current</p>'
            '<h2>Built on basalt.<br>Connected by water.</h2>'
            '<p>Hexagonal columns, split faces, and a stepped current form a compact identity '
            'for the machines and services in your network.</p></div>'
            f'<div class="identity-mark">{BRAND_SYMBOL}</div></div>'
            '<section><h2>The mark</h2><div class="asset-grid">'
            '<div class="asset-card"><div class="logo-swatch light">'
            '<img src="wordmark-light.svg" width="264" height="48" alt="Basaltwater on light"></div>'
            '<p>Light surfaces · <a href="wordmark-light.svg">Wordmark SVG</a> · '
            '<a href="symbol-light.svg">Symbol SVG</a></p></div>'
            '<div class="asset-card"><div class="logo-swatch dark">'
            '<img src="wordmark-dark.svg" width="264" height="48" alt="Basaltwater on dark"></div>'
            '<p>Dark surfaces · <a href="wordmark-dark.svg">Wordmark SVG</a> · '
            '<a href="symbol-dark.svg">Symbol SVG</a></p></div>'
            '<div class="asset-card"><div class="logo-swatch light">'
            '<img src="symbol-mono.svg" width="64" height="64" alt="Monochrome basalt mark"></div>'
            '<p>One ink · <a href="symbol-mono.svg">Symbol SVG</a> · '
            '<a href="wordmark-mono.svg">Wordmark SVG</a></p></div>'
            '<div class="asset-card"><div class="logo-swatch light">'
            '<img src="symbol-light.svg" width="32" height="32" alt="Symbol at 32 pixels">'
            '<img src="symbol-mono.svg" width="16" height="16" alt="Symbol at 16 pixels"></div>'
            '<p>32 / 16 pixels · Keep eight units of clear space around the 32-unit mark.</p></div>'
            '<div class="asset-card"><div class="logo-swatch adaptive">'
            '<img src="favicon.svg" width="32" height="32" alt="Adaptive favicon at 32 pixels">'
            '<img src="favicon.svg" width="16" height="16" alt="Adaptive favicon at 16 pixels"></div>'
            '<p>Browser tabs · <a href="favicon.svg">Adaptive favicon SVG</a></p></div>'
            '<div class="asset-card"><div class="logo-swatch adaptive">'
            '<img src="artwork-network.svg" width="192" height="96" alt="Connected basalt columns"></div>'
            '<p>Supporting artwork · <a href="artwork-network.svg">Network SVG</a></p></div>'
            '</div></section>'
            '<section><h2>Color with a purpose</h2><div class="color-roles">'
            '<div class="color-role tone-water"><strong>Water blue</strong><span>Services and navigation</span></div>'
            '<div class="color-role tone-workspace"><strong>Violet</strong><span>Agents and workspace data</span></div>'
            '<div class="color-role tone-stone"><strong>Copper</strong><span>Administration and maintenance</span></div>'
            '<div class="color-role tone-sea"><strong>Sea green</strong><span>Access and certificate trust</span></div>'
            '</div><p>Category color supports orientation. Service health still has an explicit text label.</p></section>'
            '<section><h2>Icons from the same stone</h2><p class="lede">A 24-unit grid, '
            'consistent strokes, and angular cuts. Each tile opens its SVG.</p>'
            f'<div class="icon-grid">{icon_gallery}</div></section>'
            '<section><h2>Quiet textures</h2><p class="lede">Hexagonal joints and water strata. '
            'Transparent, repeating vectors for headers and supporting surfaces.</p>'
            '<div class="asset-grid"><div class="asset-card"><div class="texture-swatch basalt"></div>'
            '<p>Basalt joints · <a href="texture-basalt-light.svg">Light SVG</a> · '
            '<a href="texture-basalt-dark.svg">Dark SVG</a></p></div>'
            '<div class="asset-card"><div class="texture-swatch water"></div>'
            '<p>Water strata · <a href="texture-water-light.svg">Light SVG</a> · '
            '<a href="texture-water-dark.svg">Dark SVG</a></p></div></div></section>'
            '<section><h2>States always have labels</h2><p><span class="badge success">Healthy</span> '
            '<span class="badge warning">Needs attention</span> <span class="badge error">Unavailable</span></p>'
            '<p><a class="refresh-link" href="panel.html">Inspect the panel specimen</a></p></section>'
            '<section><h2>Identity in use</h2><p><a class="refresh-link" href="tools.html">Browse the agent tools</a>'
            ' · <a class="refresh-link" href="services.html">Inspect the service artwork</a></p>'
            '<p><a class="refresh-link" href="admin.html">Review administration controls</a> · '
            '<a class="refresh-link" href="jobs.html">Browse scheduled jobs</a> · '
            '<a class="refresh-link" href="logs.html">Inspect diagnostics</a> · '
            '<a class="refresh-link" href="agents.html">Review the prompt workbench</a></p>'
            '<p>These static previews use synthetic records. Controls do not manage a host.</p></section>'
            '<section><h2>Typography and motion</h2><p>DejaVu Sans for interfaces; DejaVu Sans Mono for commands. '
            'System fallbacks remain available. No font download, JavaScript, or animation is required.</p>'
            '<pre><code>basaltw --version\nbasaltw setup server_lite example.test --dry-run</code></pre></section>',
        ),
        "readme.html": (
            "README specimen", "Basaltwater",
            '<picture><source media="(prefers-color-scheme:dark)" srcset="wordmark-dark.svg">'
            '<img src="wordmark-light.svg" width="264" height="48" alt="Basaltwater wordmark"></picture>'
            '<p class="lede">Linux host setup and service management.</p>'
            '<p>The primary command is <code>basaltw</code>.</p>'
            '<section><h2>Commands</h2><p>Configure Linux hosts, services and agent workspaces '
            'with repeatable setup and explicit recovery.</p><pre><code>basaltw --help\nbasaltw agent doctor --json</code></pre></section>',
        ),
    }
    for filename, (title, heading, content) in specimens.items():
        specimen_style = panel._PAGE_STYLE + (GUIDE_STYLE if filename == "index.html" else "")
        document = render_document(title=title, style=specimen_style,
            header=f'<header><p class="eyebrow">Basaltwater identity</p><h1>{heading}</h1></header>',
            content=content, navigation=tuple((url, label, url if url == filename else None) for url, label, _ in navigation),
            footer='<footer>Review specimen · Apache-2.0 assets · bluehexagons</footer>', favicon_href="favicon.svg")
        (assets / filename).write_text(document + "\n", encoding="utf-8")
    state = panel.WebPanelState({
        "title": "Workshop", "host": "workshop.example.test", "username": "operator",
        "system_type": "server_dev", "features": {},
        "services": [
            {"label": "Project library", "url": "https://projects.example.test", "description": "Repositories and shared work"},
            {"label": "HTTPS service: t3code", "url": "https://workshop.example.test:8444/", "description": "live"},
            {"label": "Published site: team-handbook", "url": "https://workshop.example.test:8445/", "description": "live"},
            {"label": "HTTPS service: review", "url": "https://workshop.example.test:8446/", "description": "not responding"},
        ],
        "access": [{"label": "SSH", "value": "ssh operator@workshop.example.test", "description": "Verified host identity"}],
    })
    state.csrf_token = "brand-preview-not-a-runtime-token"
    job_rows = []
    for service, label, status, tone, timer, result in (
        ("auto-update-apt.service", "Package updates", "Last run failed", "error", "active", "exit-code"),
        ("cleanup-maintenance.service", "System cleanup", "Last run succeeded", "warning", "inactive", "success"),
        ("security-monitor.service", "Security monitoring", "Last run succeeded", "info", "active", "success"),
    ):
        job_rows.append({
            "service": service, "label": label, "status": status, "tone": tone,
            "timer": timer, "enabled": "enabled", "result": result,
            "next": "In about 45 minutes" if timer == "active" else "Timer is not active",
            "triggered": "2026-01-01 09:00:00 UTC", "started": "2026-01-01 09:00:01 UTC",
            "finished": "2026-01-01 09:02:14 UTC", "exit": "1" if tone == "error" else "0",
            "persistent": "Yes",
        })
    with (
        patch.object(panel, "discover_basaltwater_web_services", return_value=[]),
        patch.object(panel, "discover_certificate_trust", return_value=None),
        patch.object(state, "system_overview", return_value=[
            {"label": "Uptime", "value": "2d 6h", "description": "Since the last boot"},
            {"label": "Load average (1m)", "value": "0.32", "description": "5m 0.41 · 15m 0.36 · 4 logical CPUs"},
            {"label": "Memory", "value": "24% used", "description": "6.1 GiB available of 8.0 GiB"},
            {"label": "Swap", "value": "8% used", "description": "164 MiB of 2.0 GiB"},
            {"label": "Root disk", "value": "52% used", "description": "30.2 GiB free of 62.8 GiB"},
            {"label": "Root inodes", "value": "12% used", "description": "3,604,480 free of 4,096,000"},
            {"label": "Kernel", "value": "6.12.48+deb13", "description": "x86_64"},
            {"label": "Maintenance", "value": "No reboot pending", "description": "Automatic package updates are scheduled"},
            {"kind": "pressure", "label": "CPU pressure", "value": "0.24% stalled", "description": "10s average · 60s 0.26% · 5m 0.21%"},
            {"kind": "pressure", "label": "Memory pressure", "value": "0.00% stalled", "description": "10s average · 60s 0.00% · 5m 0.00%"},
            {"kind": "pressure", "label": "I/O pressure", "value": "3.28% stalled", "description": "10s average · 60s 2.10% · 5m 1.08%"},
            *(host._filesystem({
                "target": target, "source": source, "fstype": fstype, "fsroot": "/",
                "size": total * 1024**3, "used": (total - free) * 1024**3, "avail": free * 1024**3,
                "ino.total": 1000000 if fstype == "ext4" else 0,
                "ino.used": inodes, "ino.avail": 1000000 - inodes,
                "options": "ro" if read_only else "rw",
            }) for target, source, fstype, total, free, inodes, read_only in (
                ("/", "/dev/mapper/system-root", "ext4", 64, 30, 120000, False),
                ("/home", "/dev/mapper/system-home", "ext4", 256, 36, 430000, False),
                ("/var", "/dev/mapper/system-var", "ext4", 32, 20, 960000, False),
                ("/boot/efi", "/dev/nvme0n1p1", "vfat", 1, 1, 0, True),
                ("/mnt/project archive", "/dev/mapper/archive", "ext4", 1024, 380, 340000, False),
            )),
        ]),
        patch.object(state, "audit_snapshot", return_value={"events": [], "status": "ok"}),
        patch.object(state.agent_tasks, "snapshot", return_value={"tasks": [], "runs": []}),
        patch.object(state.agent_tasks, "available", return_value=False),
        patch.object(agents, "_tool_path", return_value=None),
        patch.object(agents, "codex_models", return_value=[]),
        patch.object(agents, "_available_templates", return_value={
            key: template for key, template in agents.PROMPT_TEMPLATES.items() if "requires" not in template
        }),
        patch.object(state.admin, "snapshot", return_value={"blocked": {}, "requests": [], "unit_state": "inactive"}),
        patch.object(jobs, "collect_jobs", return_value=jobs.JobSnapshot(job_rows, [], absent=9)),
        patch.object(storage, "load_storage_snapshot", return_value={"available": False, "jobs": []}),
        patch.object(diagnostics, "collect_diagnostics", return_value={
            "issues": [], "properties": {"LoadState": "loaded", "ActiveState": "active",
                "SubState": "running", "MemoryCurrent": "41943040", "NRestarts": "0", "Result": "success"},
            "events": [
                {"timestamp": "2026-01-01T09:04:15+00:00", "priority": "3", "message": "Upstream connection refused while serving /review. Check the application service before reloading the gateway."},
                {"timestamp": "2026-01-01T09:03:12+00:00", "priority": "4", "message": "Upstream response exceeded the configured timeout."},
                {"timestamp": "2026-01-01T09:00:00+00:00", "priority": "6", "message": "Configuration test succeeded. Gateway ready for incoming connections."},
            ],
        }),
    ):
        pages = {
            "panel.html": panel.render_page(state),
            "tools.html": panel.render_tools(state, panel._PAGE_STYLE, {}),
            "services.html": panel.render_service_status(state, False),
            "admin.html": panel.render_admin(state, panel._PAGE_STYLE, {}),
            "agents.html": agents.render_agents(state, panel._PAGE_STYLE, {}),
            "jobs.html": jobs.render_jobs(True, panel._PAGE_STYLE, state.manifest["host"]),
            "logs.html": diagnostics.render_diagnostics(diagnostics.DiagnosticQuery(load=True, priority="7"), panel._PAGE_STYLE, state.manifest["host"]),
        }
        for name, document in pages.items():
            document = document.replace('href="/favicon.svg"', 'href="favicon.svg"')
            for route, filename in (("/", "panel.html"), ("/agents", "agents.html"),
                                    ("/agent-tools", "tools.html"), ("/admin", "admin.html"),
                                    ("/services", "services.html"), ("/jobs", "jobs.html"), ("/logs", "logs.html")):
                document = document.replace(f'href="{route}"', f'href="{filename}"')
                document = document.replace(f'href="{route}#', f'href="{filename}#')
            (assets / name).write_text(document + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Compare generated assets without modifying the checkout")
    args = parser.parse_args()
    destination = ROOT / "docs" / "brand"
    if not args.check:
        export_assets(destination)
        return 0
    with tempfile.TemporaryDirectory(prefix="basaltwater-brand-") as directory:
        generated = Path(directory)
        export_assets(generated)
        stale = []
        for expected in sorted(generated.iterdir()):
            actual = destination / expected.name
            if not actual.is_file() or actual.read_bytes() != expected.read_bytes():
                stale.append(expected.name)
        if stale:
            print("Stale or missing brand assets: " + ", ".join(stale))
            print("Run python3 scripts/export_brand.py and commit the regenerated assets.")
            return 1
    print("Brand assets are current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
