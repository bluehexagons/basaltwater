#!/usr/bin/env python3
"""Regenerate Basaltwater vector assets and static review specimens."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from common.service_tools.web_panel_templates import BRAND_PALETTES, BRAND_SYMBOL, render_document
from common.service_tools import web_panel_service as panel


def export_assets(assets: Path) -> None:
    """Render assets into the supplied development output directory."""
    assets.mkdir(exist_ok=True)
    for theme in ("light", "dark", "mono"):
        palette = BRAND_PALETTES["dark" if theme == "dark" else "light"]
        ink = palette["text"] if theme != "mono" else "#000000"
        water = palette["brand-water"] if theme != "mono" else ink
        symbol = BRAND_SYMBOL.replace('aria-hidden="true" focusable="false"', 'role="img" aria-label="Basaltwater symbol"')
        symbol = symbol.replace("currentColor", ink).replace(f"var(--brand-water,{ink})", water)
        (assets / f"symbol-{theme}.svg").write_text(symbol + "\n", encoding="utf-8")
        mark = symbol.replace('viewBox="0 0 32 32"', 'viewBox="0 0 264 48"').replace('width="32" height="32"', 'width="264" height="48"')
        mark = mark.replace('aria-label="Basaltwater symbol"', 'aria-label="Basaltwater"')
        mark = mark.replace('<path ', '<g transform="translate(0 8)"><path ', 1)
        mark = mark.replace('</svg>', '</g><text x="44" y="33" font-family="DejaVu Sans, sans-serif" font-size="30" letter-spacing="-1.2" fill="' + ink + '">Basaltwater</text></svg>')
        (assets / f"wordmark-{theme}.svg").write_text(mark + "\n", encoding="utf-8")
    (assets / "palette.json").write_text(json.dumps(BRAND_PALETTES, indent=2) + "\n", encoding="utf-8")
    from common.service_tools.web_panel_templates import brand_styles
    (assets / "theme.css").write_text(brand_styles() + "\n", encoding="utf-8")
    navigation = (("index.html", "Identity", "identity"), ("readme.html", "README specimen", None), ("panel.html", "Web panel specimen", None))
    specimens = {
        "index.html": (
            "Identity guide", "One machine. Your whole network.",
            '<p class="lede">Basaltwater combines solid foundations with a clear current. '
            'Angular columns and a waterline form the compact mark.</p>'
            '<section><h2>Small, simple, recognizable</h2><p>'
            '<img src="symbol-light.svg" width="32" height="32" alt="Color symbol on light" style="background:white"> '
            '<img src="symbol-mono.svg" width="16" height="16" alt="Monochrome symbol at favicon size" style="background:white">'
            '</p><p>Leave one quarter of the symbol width clear on every side. '
            'Use at least 16 pixels for the symbol and 176 pixels for the wordmark.</p></section>'
            '<section><h2>States always have labels</h2><p><span class="badge success">Healthy</span> '
            '<span class="badge warning">Needs attention</span> <span class="badge error">Unavailable</span></p>'
            '<p><a class="refresh-link" href="panel.html">Inspect the panel specimen</a></p></section>'
            '<section><h2>Typography and motion</h2><p>DejaVu Sans for interfaces; DejaVu Sans Mono for commands. '
            'System fallbacks remain available. No font download, JavaScript, or animation is required.</p>'
            '<pre><code>basaltw --version\nbasaltw setup server_lite example.test --dry-run</code></pre></section>',
        ),
        "readme.html": (
            "README specimen", "Basaltwater",
            '<picture><source media="(prefers-color-scheme:dark)" srcset="wordmark-dark.svg">'
            '<img src="wordmark-light.svg" width="264" height="48" alt="Basaltwater wordmark"></picture>'
            '<p class="lede">Infrastructure management, from one machine to your whole network.</p>'
            '<p>The primary command is <code>basaltw</code>.</p>'
            '<section><h2>Install. Describe. Manage.</h2><p>Configure Linux hosts, services and agent workspaces '
            'with repeatable setup and explicit recovery.</p><pre><code>basaltw --help\nbasaltw agent doctor --json</code></pre></section>',
        ),
    }
    for filename, (title, heading, content) in specimens.items():
        document = render_document(title=title, style=panel._PAGE_STYLE,
            header=f'<header><p class="eyebrow">Basaltwater identity</p><h1>{heading}</h1></header>',
            content=content, navigation=tuple((url, label, url if url == filename else None) for url, label, _ in navigation),
            footer='<footer>Review specimen · Apache-2.0 assets · bluehexagons</footer>')
        (assets / filename).write_text(document, encoding="utf-8")
    state = panel.WebPanelState({
        "title": "Workshop", "host": "workshop.example.test", "username": "operator",
        "system_type": "server_dev", "features": {},
        "services": [{"label": "Project library", "url": "https://projects.example.test", "description": "Repositories and shared work"}],
        "access": [{"label": "SSH", "value": "ssh operator@workshop.example.test", "description": "Verified host identity"}],
    })
    with (
        patch.object(panel, "discover_basaltwater_web_services", return_value=[]),
        patch.object(panel, "discover_certificate_trust", return_value=None),
        patch.object(state, "system_overview", return_value=[{"label": "Host", "value": "Ready", "description": "Example data", "status": "active"}]),
        patch.object(state, "audit_snapshot", return_value={"events": [], "status": "ok"}),
    ):
        (assets / "panel.html").write_text(panel.render_page(state), encoding="utf-8")


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
