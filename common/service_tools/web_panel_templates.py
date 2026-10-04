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
    '<path fill="var(--brand-water,currentColor)" '
    'd="M8 9l6 3v3l-6 3-6-3v-3Z M23 1l6 3v3l-6 3-6-3V4Z"/>'
    '<path fill="currentColor" '
    'd="M2 17l5 3v6l-5-3Z M9 20l5-3v6l-5 3Z '
    'M17 9l5 3v10l-5-3Z M24 12l5-3v10l-5 3Z"/>'
    '<path fill="none" stroke="var(--brand-water,currentColor)" stroke-width="2" '
    'd="M2 30h8l5-3h6l4-3h5"/></svg>'
)

# Original 24-unit icons: angular cuts and hexagonal forms echo the basalt mark.
BRAND_ICONS = {
    "dashboard": "M7 2l5 3v6l-5 3-5-3V5Z M17 10l5 3v6l-5 3-5-3v-6Z M7 14v6l5 3",
    "agents": "M12 2l8 5v12l-8 3-8-3V7Z M12 2v4 M4 10h16 M8 14h.01 M16 14h.01 M9 18h6",
    "agent-tools": "M7 2l5 3v6l-5 3-5-3V5Z M7 6v4 M5 8h4 M17 10l5 3v6l-5 3-5-3v-6Z M15 16h4",
    "web-services": "M3 3h18v18H3Z M3 8h18 M7 5.5h.01 M10 5.5h.01 M12 11l4 2v4l-4 2-4-2v-4Z",
    "security": "M12 2l8 5v9l-8 6-8-6V7Z M8 12l3 3 5-6",
    "notifications": "M12 2l6 4v8l3 4H3l3-4V6Z M10 22h4",
    "access": "M15 2l5 3v6l-5 3-5-3V5Z M15 7h.01 M10 12l-8 8v2h4v-3h3v-3l3-3",
    "admin": "M3 6h18 M3 12h18 M3 18h18 M8 3v6 M16 9v6 M8 15v6",
    "certificate": "M12 2l7 4v8l-7 4-7-4V6Z M8 10l3 3 5-6 M7 15l-2 7 7-3 7 3-2-7",
    "maintenance": "M14 3l-3 3 4 4 3-3 3 3V4Z M12 9l-9 9v3h3l9-9 M5 18h.01",
    "service-status": "M7 3h10l5 9-5 9H7l-5-9Z M5 12h4l2-5 3 10 2-5h3",
    "jobs": "M7 2v4 M17 2v4 M3 5h18v16H3Z M3 9h18 M12 12v4h4",
    "diagnostics": "M6 2h12l4 4v12l-4 4H6l-4-4V6Z M7 8l4 4-4 4 M14 16h4",
}
NAVIGATION_ICONS = {
    "Dashboard": "dashboard", "Agents": "agents", "Agent tools": "agent-tools",
    "Web services": "web-services", "Security activity": "security",
    "Notifications": "notifications", "Access": "access", "Admin controls": "admin",
    "Certificate trust": "certificate", "Maintenance": "maintenance",
    "Local service status": "service-status", "Scheduled jobs": "jobs",
    "Service diagnostics": "diagnostics",
}

# Small repeating tiles; no raster, external resource, filter or embedded data.
BRAND_TEXTURES = {
    "basalt": (24, 42, "M12 0l12 7v14l-12 7L0 21V7Z M12 28v14"),
    "water": (96, 32, "M0 8h12l12-6h24l12 6h36 M0 24h36l12 6h24l12-6h12"),
}


def render_icon(name: str, *, label: str | None = None) -> str:
    """Render an icon decoratively, or with a label for standalone use."""
    accessibility = (
        f'role="img" aria-label="{html.escape(label, quote=True)}"'
        if label is not None else 'aria-hidden="true"'
    )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        f'width="24" height="24" {accessibility} focusable="false" '
        'fill="none" stroke="currentColor" stroke-width="1.6" '
        'stroke-linecap="round" stroke-linejoin="round">'
        f'<path d="{BRAND_ICONS[name]}"/></svg>'
    )


def render_texture(name: str, *, color: str = "currentColor") -> str:
    """Render a transparent vector swatch, with a unique local pattern ID."""
    width, height, path = BRAND_TEXTURES[name]
    swatch_height = height * 4
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 192 {swatch_height}" '
        f'width="192" height="{swatch_height}" aria-hidden="true" focusable="false" '
        f'class="brand-texture brand-texture-{name}">'
        f'<defs><pattern id="brand-{name}" width="{width}" height="{height}" '
        'patternUnits="userSpaceOnUse">'
        f'<path d="{path}" fill="none" stroke="{html.escape(color, quote=True)}" '
        'stroke-width="1"/></pattern></defs>'
        f'<path fill="url(#brand-{name})" d="M0 0h192v{swatch_height}H0Z"/></svg>'
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
        ".sidebar a svg{width:18px;height:18px}"
        "header{position:relative;isolation:isolate;overflow:hidden}"
        "header .brand-texture{position:absolute;inset:0 0 0 auto;width:192px;"
        "height:100%;color:var(--accent);opacity:.09;z-index:-1;pointer-events:none}"
        "footer{background-image:linear-gradient(90deg,var(--accent) 32px,transparent 32px);"
        "background-size:100% 2px;background-repeat:no-repeat}"
        ".badge.warning{color:var(--warning)}"
        "@media(forced-colors:active){.brand svg{--brand-water:CanvasText}"
        "header .brand-texture{display:none}}"
    )


def panel_navigation(
    *,
    current: str | None = None,
) -> tuple[NavigationItem, ...]:
    """Keep destinations and ordering identical across every panel view."""

    items: list[NavigationItem] = [
        ("/", "Dashboard", "dashboard"),
        ("/agents", "Agents", "agents"),
        ("/agent-tools", "Agent tools", "agent-tools"),
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

    groups = {
        "Dashboard": "Workspace", "Admin controls": "Administration", "Local service status": "Inspect",
    }
    links: list[str] = []
    for href, label, current in items:
        if label in groups:
            links.append(f'<p class="nav-group">{groups[label]}</p>')
        icon = render_icon(NAVIGATION_ICONS[label]) if label in NAVIGATION_ICONS else ""
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

    header = header.replace("</header>", render_texture("basalt") + "</header>", 1)
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark">{refresh}
<title>{html.escape(title)} · Basaltwater</title><style>{style}{brand_styles()}</style></head><body>
<a class="skip-link" href="#main">Skip to content</a>
{render_sidebar(navigation)}
<main id="main" tabindex="-1">{header}{content}{footer}</main></body></html>'''
