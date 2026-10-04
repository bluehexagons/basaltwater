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
) -> tuple[NavigationItem, ...]:
    """Keep destinations and ordering identical across every panel view."""

    items: list[NavigationItem] = [
        ("/", "Dashboard", "dashboard"),
        ("/agents", "Agents", "agents"),
        ("/#services-heading", "Web services", None),
        ("/#audit-heading", "Security activity", None),
        ("/#notifications-heading", "Notifications", None),
        ("/admin", "Admin controls", "admin"),
        ("/#access-heading", "Access", None),
        ("/#trust", "Certificate trust", None),
        ("/#maintenance-heading", "Maintenance", None),
    ]
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

    icons = {
        "Dashboard": "M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z",
        "Agents": "M8 4h8v4H8z M5 11h14v9H5z M12 8v3 M2 14h3 M19 14h3 M9 15h.01 M15 15h.01 M9 18h6",
        "Web services": "M3 5h18v14H3z M3 9h18 M7 7h.01 M10 7h.01",
        "Security activity": "M12 3l8 3v6c0 5-8 9-8 9s-8-4-8-9V6z M8 12l3 3 5-6",
        "Notifications": "M6 8a6 6 0 0 1 12 0v7l2 3H4l2-3z M10 21h4",
        "Access": "M14 10a5 5 0 1 0 0-7 5 5 0 0 0 0 7z M10 10L3 17v4h4v-3h3v-3l2-2",
        "Admin controls": "M4 7h16 M4 17h16 M8 4v6 M16 14v6",
        "Certificate trust": "M6 3h12v13H6z M9 16l-1 5 4-2 4 2-1-5 M9 8l2 2 4-4",
        "Maintenance": "M14 6l4 4 3-3a7 7 0 0 1-9 9l-6 6-4-4 6-6a7 7 0 0 1 9-9z",
        "Local service status": "M2 12h5l3-8 4 16 3-8h5",
        "Scheduled jobs": "M8 2v4 M16 2v4 M3 5h18v16H3z M3 10h18 M8 14h2 M14 14h2",
        "Service diagnostics": "M4 4h16v16H4z M7 8l3 3-3 3 M13 16h4",
    }
    groups = {
        "Dashboard": "Workspace", "Admin controls": "Administration", "Local service status": "Inspect",
    }
    links: list[str] = []
    for href, label, current in items:
        if label in groups:
            links.append(f'<p class="nav-group">{groups[label]}</p>')
        icon = (
            '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" '
            'focusable="false" fill="none" stroke="currentColor" stroke-width="1.6" '
            f'stroke-linecap="round" stroke-linejoin="round"><path d="{icons[label]}"/></svg>'
            if label in icons else ""
        )
        links.append('<a href="{}"{}>{}<span>{}</span></a>'.format(
            html.escape(href, quote=True),
            ' aria-current="page"' if current else "",
            icon,
            html.escape(label),
        ))
    return (
        '<nav class="sidebar" aria-label="Panel sections">'
        f'<strong class="brand">{BRAND_SYMBOL}<span>Basaltwater</span></strong>'
        '<div class="nav-links">'
        f'{"".join(links)}</div></nav>'
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
