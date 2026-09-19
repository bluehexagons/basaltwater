"""Shared Basaltwater identity, HTML shell and navigation helpers."""

from __future__ import annotations

import html
from collections.abc import Iterable


NavigationItem = tuple[str, str, str | None]

# Canonical semantic palette; scripts/export_brand.py derives the design assets.
BRAND_PALETTES = {
    "light": {
        "bg": "#f3f8fa", "panel": "#ffffff", "text": "#17232c",
        "muted": "#49616e", "line": "#738995", "accent": "#17657d",
        "accent-soft": "#d8f0f4", "ok": "#21694f", "bad": "#a2342b",
        "warning": "#825119", "brand-water": "#17657d",
    },
    "dark": {
        "bg": "#101a21", "panel": "#17232c", "text": "#e8f5f8",
        "muted": "#aec6cf", "line": "#718c99", "accent": "#4dc5dd",
        "accent-soft": "#223e4b", "ok": "#75d2ae", "bad": "#ffa69d",
        "warning": "#e9b979", "brand-water": "#4dc5dd",
    },
}
BRAND_SYMBOL = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" '
    'width="32" height="32" aria-hidden="true" focusable="false">'
    '<path fill="currentColor" d="M3 12 9 9l6 3v12H3Z M17 6l6-3 6 3v18H17Z"/>'
    '<path fill="none" stroke="var(--brand-water,currentColor)" stroke-width="3" '
    'd="M2 28h8l6-4h14"/></svg>'
)


def brand_styles() -> str:
    """Return theme tokens shared by the panel and generated visual specimens."""
    def tokens(theme: str) -> str:
        return ";".join(f"--{name}:{value}" for name, value in BRAND_PALETTES[theme].items())

    return (
        ":root{" + tokens("light") + ";--shadow:0 12px 36px rgb(23 35 44 / 7%)}"
        "@media(prefers-color-scheme:dark){:root{" + tokens("dark") + ";--shadow:none}}"
        'body{font-family:"DejaVu Sans",system-ui,sans-serif}'
        'code,pre{font-family:"DejaVu Sans Mono",monospace}'
        ".sidebar strong.brand{display:flex;align-items:center;gap:8px;"
        "font-size:17px;letter-spacing:-.04em;color:var(--text)}"
        ".brand svg{flex:none;width:28px;height:28px}"
        ".badge.warning{color:var(--warning)}"
        "@media(forced-colors:active){.brand svg{--brand-water:CanvasText}}"
    )


def panel_navigation(
    *,
    current: str | None = None,
    include_notifications: bool = False,
    include_trust: bool = False,
    include_maintenance: bool = False,
) -> tuple[NavigationItem, ...]:
    """Return the common panel navigation, including available dashboard areas."""

    section_prefix = "" if current == "dashboard" else "/"
    items: list[NavigationItem] = [
        ("/", "Dashboard", "dashboard"),
        (f"{section_prefix}#services-heading", "Web services", None),
        (f"{section_prefix}#audit-heading", "Security activity", None),
    ]
    if include_notifications:
        items.append((f"{section_prefix}#notifications-heading", "Notifications", None))
    items.append((f"{section_prefix}#access-heading", "Access", None))
    if include_trust:
        items.append((f"{section_prefix}#trust", "Certificate trust", None))
    if include_maintenance:
        items.append((f"{section_prefix}#maintenance-heading", "Maintenance", None))
    items.extend(
        (
            ("/services", "Local service status", "services"),
            ("/jobs", "Scheduled jobs", "jobs"),
            ("/logs", "Service diagnostics", "logs"),
        )
    )
    return tuple(
        (href, label, key if key == current else None)
        for href, label, key in items
    )


def render_sidebar(items: Iterable[NavigationItem]) -> str:
    """Render a navigation sidebar with one consistent accessible structure."""

    links = "".join(
        '<a href="{}"{}>{}</a>'.format(
            html.escape(href, quote=True),
            ' aria-current="page"' if current else "",
            html.escape(label),
        )
        for href, label, current in items
    )
    return (
        '<nav class="sidebar" aria-label="Panel sections">'
        f'<strong class="brand">{BRAND_SYMBOL}<span>Basaltwater</span></strong>'
        '<div class="nav-links">'
        f"{links}</div></nav>"
    )


def render_document(
    *,
    title: str,
    style: str,
    header: str,
    content: str,
    navigation: Iterable[NavigationItem],
    footer: str,
    refresh: str = "",
) -> str:
    """Render the shared no-JavaScript document frame used by every panel view."""

    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark">{refresh}
<title>{html.escape(title)} · Basaltwater</title><style>{style}{brand_styles()}</style></head><body>
<a class="skip-link" href="#main">Skip to content</a>
{render_sidebar(navigation)}
<main id="main" tabindex="-1">{header}{content}{footer}</main></body></html>'''
