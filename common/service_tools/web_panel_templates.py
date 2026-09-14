"""Shared HTML shell and navigation helpers for the infra-tools web panel."""

from __future__ import annotations

import html
from collections.abc import Iterable


NavigationItem = tuple[str, str, str | None]


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
        '<strong>infra-tools</strong><div class="nav-links">'
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
<title>{html.escape(title)}</title><style>{style}</style></head><body>
<a class="skip-link" href="#main">Skip to content</a>
{render_sidebar(navigation)}
<main id="main" tabindex="-1">{header}{content}{footer}</main></body></html>'''
