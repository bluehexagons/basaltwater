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
        "workspace": "#694399", "workspace-soft": "#eee7f7",
        "stone": "#80501f", "stone-soft": "#f8eddf",
        "sea": "#206452", "sea-soft": "#e1f3ed",
        "bad-soft": "#fbeae7",
    },
    "dark": {
        "bg": "#101a21", "panel": "#17232c", "text": "#e8f5f8",
        "muted": "#aec6cf", "line": "#718c99", "accent": "#4dc5dd",
        "accent-soft": "#223e4b", "ok": "#75d2ae", "bad": "#ffa69d",
        "warning": "#e9b979", "brand-water": "#4dc5dd",
        "workspace": "#c3a4ef", "workspace-soft": "#30283e",
        "stone": "#e9b979", "stone-soft": "#392e23",
        "sea": "#86d5b9", "sea-soft": "#203a34",
        "bad-soft": "#362329",
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
    "storage": "M6 3h12l3 4v13H3V7Z M3 12h18 M3 16h18 M17 9h.01 M17 14h.01 M17 18h.01",
    "data": "M6 2h12l4 4v14H2V6Z M2 8h20 M2 14h20 M8 8v12 M15 8v12",
    "import": "M12 2v12 M8 10l4 4 4-4 M3 14v7h18v-7 M6 17h12",
    "export": "M12 14V2 M8 6l4-4 4 4 M3 14v7h18v-7 M6 17h12",
    "power": "M12 2v10 M7 5l-4 4v9l9 4 9-4V9l-4-4",
}
NAVIGATION_ICONS = {
    "Dashboard": "dashboard", "Agents": "agents", "Agent tools": "agent-tools",
    "Desktop": "web-services", "Console": "diagnostics",
    "Web services": "web-services", "Security activity": "security",
    "Notifications": "notifications", "Access": "access", "Admin controls": "admin",
    "Git & credentials": "access",
    "Shares & Data": "storage",
    "Publishing": "export",
    "Maintenance agent": "maintenance", "Agent inbox": "notifications",
    "Certificate trust": "certificate", "Maintenance": "maintenance",
    "GitHub runner": "jobs",
    "Local service status": "service-status", "Scheduled jobs": "jobs",
    "Service diagnostics": "diagnostics", "Storage": "storage",
}
ICON_TONES = {
    "agents": "workspace", "agent-tools": "workspace", "data": "workspace",
    "import": "workspace", "export": "workspace",
    "admin": "stone", "maintenance": "stone", "jobs": "stone", "storage": "stone", "power": "stone",
    "access": "sea", "security": "sea", "certificate": "sea",
}
PAGE_ICONS = {
    "dashboard": "dashboard", "agents": "agents", "agent-tools": "agent-tools",
    "admin": "admin", "services": "service-status", "jobs": "jobs", "logs": "diagnostics",
    "credentials": "access", "storage": "storage", "shares-data": "storage",
    "publishing": "export",
    "github-runner": "jobs",
    "desktop": "web-services", "console": "diagnostics",
    "maintenance-agent": "maintenance", "agent-inbox": "notifications",
}

# Small repeating tiles; no raster, external resource, filter or embedded data.
BRAND_TEXTURES = {
    "basalt": (24, 42, "M12 0l12 7v14l-12 7L0 21V7Z M12 28v14"),
    "water": (96, 32, "M0 8h12l12-6h24l12 6h36 M0 24h36l12 6h24l12-6h12"),
    "contours": (96, 64, "M0 16C16 0 32 0 48 16S80 32 96 16 M0 48C16 32 32 32 48 48S80 64 96 48"),
    "facets": (64, 64, "M0 16l32 16 32-16 M0 48l32-16 32 16 M32 0v64"),
    "mineral": (64, 64, "M8 12l4-4 M40 44l4-4 M48 16h3 M16 48h3"),
}
PAGE_TEXTURES = {
    "github-runner": "facets", "storage": "facets", "shares-data": "facets",
    "agents": "contours", "agent-tools": "contours", "publishing": "contours",
    "admin": "facets", "maintenance-agent": "facets", "jobs": "facets",
    "credentials": "mineral", "agent-inbox": "mineral", "logs": "mineral",
    "services": "water", "desktop": "water", "console": "mineral",
}
BRAND_NETWORK = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 64" '
    'width="128" height="64" class="brand-network" aria-hidden="true" focusable="false">'
    '<g fill="none" stroke="var(--workspace,currentColor)" stroke-width="2">'
    '<path d="M16 5l8 5v8l-8 5-8-5v-8Z M108 5l8 5v8l-8 5-8-5v-8Z"/>'
    '<path d="M24 14h14l10 10 M100 14H86l-6 6"/></g>'
    '<g transform="translate(42 12) scale(1.25)">'
    + BRAND_SYMBOL.partition(">")[2].removesuffix("</svg>") + '</g>'
    '<path fill="none" stroke="var(--brand-water,currentColor)" stroke-width="2" '
    'd="M8 58h24l8-5h48l8-5h24"/></svg>'
)


def svg_theme_styles() -> str:
    """Let standalone SVG artwork follow the viewing browser's theme."""
    def tokens(theme: str) -> str:
        palette = BRAND_PALETTES[theme]
        return (f'color:{palette["text"]};--brand-water:{palette["brand-water"]};'
                f'--workspace:{palette["workspace"]}')

    return '<style>svg{' + tokens("light") + '}@media(prefers-color-scheme:dark){svg{' + tokens("dark") + '}}</style>'


def render_favicon() -> str:
    """Return the theme-aware vector mark served at /favicon.svg."""
    symbol = BRAND_SYMBOL.replace('aria-hidden="true"', 'role="img" aria-label="Basaltwater"')
    opening, _, artwork = symbol.partition(">")
    return opening + ">" + svg_theme_styles() + artwork


def render_heading(label: str, icon: str, *, heading_id: str | None = None) -> str:
    """Pair a visible section title with a decorative category icon."""
    identifier = f' id="{html.escape(heading_id, quote=True)}"' if heading_id else ""
    tone = ICON_TONES.get(icon, "water")
    return (f'<h2{identifier} class="section-title"><span class="section-icon tone-{tone}">'
            f'{render_icon(icon)}</span>{html.escape(label)}</h2>')


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


def render_texture(name: str, *, color: str = "currentColor", pattern_id: str | None = None) -> str:
    """Render a vector swatch; give repeated inline instances distinct IDs."""
    width, height, path = BRAND_TEXTURES[name]
    swatch_height = height * 4
    identifier = html.escape(pattern_id or f"brand-{name}", quote=True)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 192 {swatch_height}" '
        f'width="192" height="{swatch_height}" aria-hidden="true" focusable="false" '
        'preserveAspectRatio="xMidYMid slice" '
        f'class="brand-texture brand-texture-{name}">'
        f'<defs><pattern id="{identifier}" width="{width}" height="{height}" '
        'patternUnits="userSpaceOnUse">'
        f'<path d="{path}" fill="none" stroke="{html.escape(color, quote=True)}" '
        'stroke-width="1"/></pattern></defs>'
        f'<path fill="url(#{identifier})" d="M0 0h192v{swatch_height}H0Z"/></svg>'
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
        ".sidebar a.brand{display:flex;align-items:center;gap:10px;margin:0 8px 24px;padding:0;"
        "font-size:17px;font-weight:750;letter-spacing:-.04em;color:var(--text)}"
        ".sidebar a svg{width:18px;height:18px}"
        ".sidebar a.brand svg{flex:none;width:28px;height:28px;color:var(--text)}"
        ".sidebar a.brand:hover{background:transparent;color:var(--accent)}"
        ":root{--section-ink:var(--accent);--section-soft:var(--accent-soft)}"
        ".tone-water{--section-ink:var(--accent);--section-soft:var(--accent-soft)}"
        ".tone-workspace{--section-ink:var(--workspace);--section-soft:var(--workspace-soft)}"
        ".tone-stone{--section-ink:var(--stone);--section-soft:var(--stone-soft)}"
        ".tone-sea{--section-ink:var(--sea);--section-soft:var(--sea-soft)}"
        ".sidebar a svg{color:var(--section-ink)}"
        ".sidebar .nav-links a:hover{background:var(--section-soft);color:var(--section-ink)}"
        ".sidebar a[aria-current=page]{background:var(--section-soft);color:var(--section-ink);"
        "box-shadow:inset 3px 0 var(--section-ink)}"
        ".brand-texture{pointer-events:none;user-select:none}"
        ".sidebar{isolation:isolate}"
        ".sidebar>.brand-texture{position:absolute;bottom:16px;left:12px;width:min(220px,calc(100% - 24px));"
        "height:160px;color:var(--accent);opacity:.07;z-index:-1}"
        "header{position:relative;isolation:isolate;overflow:hidden}"
        "header{border-top-color:var(--section-ink);"
        "background:radial-gradient(ellipse at top right,var(--section-soft),transparent 70%),var(--panel)}"
        "header .eyebrow{display:flex;align-items:center;gap:8px;color:var(--section-ink)}"
        "header .eyebrow svg{width:20px;height:20px;flex:none}"
        "header .brand-texture{position:absolute;inset:0 0 0 auto;width:38%;min-width:192px;max-width:360px;"
        "height:100%;color:var(--section-ink);opacity:.1;z-index:-1;"
        "mask-image:linear-gradient(90deg,transparent,#000 45%)}"
        "footer{position:relative;isolation:isolate}"
        "footer>.brand-texture{position:absolute;right:0;bottom:0;width:220px;"
        "height:48px;color:var(--accent);opacity:.08;z-index:-1;"
        "mask-image:linear-gradient(90deg,transparent,#000)}"
        "footer{background-image:linear-gradient(90deg,var(--accent) 32px,transparent 32px);"
        "background-size:100% 2px;background-repeat:no-repeat}"
        ".badge.warning{color:var(--warning)}"
        ".section-title{display:flex;align-items:center;gap:10px}"
        ".section-icon,.action-icon{display:inline-flex;align-items:center;justify-content:center;"
        "width:34px;height:34px;border-radius:9px;background:var(--section-soft);"
        "color:var(--section-ink);flex:none}"
        ".section-icon svg,.action-icon svg{width:20px;height:20px}"
        ".service-kind{color:var(--section-ink)}"
        ".service-card{border-top:3px solid var(--section-ink);"
        "background:linear-gradient(140deg,var(--section-soft),var(--panel) 55%)}"
        ".action-row{border-left:3px solid var(--section-ink)}"
        ".action-row:focus-within{background:var(--section-soft)}"
        ".agent-panel,.admin-card{border-top:3px solid var(--section-ink)}"
        '.agent-summary .metric,section[aria-labelledby="agent-activity-heading"] .metric'
        "{border-top:2px solid var(--workspace);background:linear-gradient(140deg,var(--workspace-soft),var(--panel) 55%)}"
        ".host-overview .metric{border-top:2px solid var(--accent)}"
        ".host-overview .metric:nth-child(n+3):nth-child(-n+6){border-top-color:var(--workspace)}"
        ".host-overview .metric:nth-child(n+7){border-top-color:var(--stone)}"
        ".empty-art{grid-column:1/-1;display:flex;align-items:center;gap:24px;"
        "padding:20px;background:var(--panel);border:1px solid var(--accent-soft);border-radius:12px}"
        ".empty-art .brand-network{flex:none;width:128px;height:64px}"
        ".empty-art p{margin:0;color:var(--muted)}"
        ".access-list strong{color:var(--sea)}"
        ".badge.success{background:var(--sea-soft)}"
        ".badge.warning{background:var(--stone-soft)}"
        ".badge.error{background:var(--bad-soft)}"
        ".service-card:hover,.service-card:focus-visible{border-color:var(--section-ink);"
        "background:var(--section-soft)}"
        ".tool-card:focus-within,.admin-card:focus-within{border-color:var(--section-ink)}"
        "button:not(:disabled):hover{background:var(--text);color:var(--panel)}"
        ".agent-secondary:not(:disabled):hover{background:var(--accent);color:var(--panel)}"
        "input:focus-visible,textarea:focus-visible,select:focus-visible{outline:3px solid var(--accent);outline-offset:2px}"
        ".metric-value,.job-facts dd,.admin-result dd,time{font-variant-numeric:tabular-nums}"
        ".job-card{border-left:3px solid var(--stone)}"
        ".job-card .job-facts dd{font-size:.9rem;font-weight:650}"
        ".job-card>details{border-top:1px solid var(--accent-soft);margin-top:16px}"
        ".journal-entry{border-left:3px solid var(--line)}"
        ".journal-error{border-left-color:var(--bad)}"
        ".journal-warning{border-left-color:var(--warning)}"
        ".admin-tools{margin-top:12px}"
        ".nav-mobile{display:none}"
        "@media(max-width:900px){.sidebar a.brand{margin:0 8px 6px;min-height:36px}"
        ".sidebar>.brand-texture,.sidebar .nav-desktop{display:none}"
        ".nav-mobile{display:block;margin:0}"
        ".nav-mobile>summary{width:100%;padding:10px 12px;border:1px solid var(--line);"
        "border-radius:8px;color:var(--text);background:var(--bg)}"
        ".nav-mobile>summary strong{font-size:.83rem}"
        ".nav-location{display:block;margin-left:18px;color:var(--section-ink);font-size:.83rem}"
        ".sidebar .nav-mobile .nav-links{display:block;overflow:visible;padding:0}"
        ".nav-mobile .nav-group{display:block;margin-top:16px}"
        ".sidebar .nav-mobile a{white-space:normal}}"
        "@media(max-width:560px){.empty-art{gap:14px;padding:16px}"
        ".empty-art .brand-network{width:80px;height:40px}}"
        "@media(forced-colors:active){.brand svg{--brand-water:CanvasText}"
        ".brand-texture,.empty-art .brand-network{display:none}}"
    )


def panel_navigation(
    *,
    current: str | None = None,
    available: frozenset[str] = frozenset({"dashboard", "services", "storage", "jobs", "logs"}),
) -> tuple[NavigationItem, ...]:
    """Keep destinations and ordering identical across every panel view."""

    items: list[NavigationItem] = [
        ("/", "Dashboard", "dashboard"),
        ("/desktop/", "Desktop", "desktop"),
        ("/console/", "Console", "console"),
        ("/agents", "Agents", "agents"),
        ("/credentials", "Git & credentials", "credentials"),
        ("/publishing", "Publishing", "publishing"),
        ("/admin", "Admin controls", "admin"),
        ("/shares-data", "Shares & Data", "shares-data"),
    ]
    items.extend(
        (
            ("/services", "Local service status", "services"),
            ("/storage", "Storage", "storage"),
            ("/jobs", "Scheduled jobs", "jobs"),
            ("/github-runner", "GitHub runner", "github-runner"),
            ("/logs", "Service diagnostics", "logs"),
        )
    )
    if current in {"agent-tools", "agent-inbox", "maintenance-agent"}:
        current = "agents"
    return tuple(
        (href, label, key if key == current else None)
        for href, label, key in items
        if key in available
    )


def render_sidebar(items: Iterable[NavigationItem]) -> str:
    """Render a navigation sidebar with one consistent accessible structure."""

    items = tuple(items)
    home = items[0][0] if items else "/"
    home_label = "Basaltwater dashboard" if home == "/" else "Basaltwater home"
    location = next((label for _, label, current in items if current), "Choose a section")
    groups = {
        "Dashboard": "Workspace", "Admin controls": "Administration", "Local service status": "Inspect",
    }
    links: list[str] = []
    for href, label, current in items:
        if label in groups:
            links.append(f'<p class="nav-group">{groups[label]}</p>')
        icon = render_icon(NAVIGATION_ICONS[label]) if label in NAVIGATION_ICONS else ""
        tone = ICON_TONES.get(NAVIGATION_ICONS.get(label, ""), "water")
        links.append('<a href="{}"{} class="tone-{}">{}<span>{}</span></a>'.format(
            html.escape(href, quote=True),
            ' aria-current="page"' if current else "",
            tone,
            icon,
            html.escape(label),
        ))
    return (
        '<nav class="sidebar" aria-label="Panel sections">'
        f'<a class="brand" href="{html.escape(home, quote=True)}" aria-label="{home_label}">{BRAND_SYMBOL}<span>Basaltwater</span></a>'
        '<div class="nav-links nav-desktop">'
        f'{"".join(links)}</div>'
        '<details class="nav-mobile"><summary><strong>Panel menu</strong>'
        f'<span class="nav-location">{html.escape(location)}</span></summary>'
        f'<div class="nav-links">{"".join(links)}</div></details>'
        f'{render_texture("mineral", pattern_id="brand-sidebar")}</nav>'
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
    favicon_href: str = "/favicon.svg",
) -> str:
    """Render the shared no-JavaScript document frame used by every panel view."""

    navigation = tuple(navigation)
    current = next((key for _, _, key in navigation if key), "dashboard")
    icon = PAGE_ICONS.get(current, "dashboard")
    tone = ICON_TONES.get(icon, "water")
    header = header.replace('<p class="eyebrow">', '<p class="eyebrow">' + render_icon(icon), 1)
    header = header.replace("</header>", render_texture(PAGE_TEXTURES.get(current, "basalt"), pattern_id="brand-header") + "</header>", 1)
    footer = footer.replace("</footer>", render_texture("water", pattern_id="brand-footer") + "</footer>", 1)
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark">{refresh}
<link rel="icon" href="{html.escape(favicon_href, quote=True)}" type="image/svg+xml" sizes="any">
<title>{html.escape(title)} · Basaltwater</title><style>{style}{brand_styles()}</style></head><body class="tone-{tone}">
<a class="skip-link" href="#main">Skip to content</a>
{render_sidebar(navigation)}
<main id="main" tabindex="-1">{header}{content}{footer}</main></body></html>'''
