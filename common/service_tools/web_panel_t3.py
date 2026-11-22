"""Open T3's own workspace without duplicating its APIs or authentication."""

from __future__ import annotations

from html import escape
from typing import Any
from urllib.parse import urlsplit

from lib.validation import validate_network_ip, validate_no_control_characters
from lib.validators import validate_host


def workspace_url(state: Any) -> str | None:
    """Use only an unambiguous, discovered T3 HTTPS root; never infer a port."""

    if not state.features().t3:
        return None
    urls = set()
    for service in state.web_services().value or []:
        if service.get("application") != "t3code":
            continue
        url = service.get("url")
        if not isinstance(url, str) or len(url) > 2048:
            continue
        try:
            validate_no_control_characters(url, "T3 Code URL")
            parsed = urlsplit(url)
            port = parsed.port
            host = parsed.hostname or ""
            if ":" in host:
                validate_network_ip(host)
            elif not validate_host(host):
                continue
        except ValueError:
            continue
        if (parsed.scheme == "https" and parsed.username is None
                and parsed.password is None and parsed.path in {"", "/"}
                and not parsed.query and not parsed.fragment and "?" not in url and "#" not in url
                and "\\" not in url and not any(character.isspace() for character in url)
                and (port is None or 1 <= port <= 65535)):
            urls.add(url.rstrip("/") + "/")
    return next(iter(urls)) if len(urls) == 1 else None


def render_workspace(state: Any) -> str:
    """Keep coding, schedules and updates in the native T3 interface."""

    if not state.features().t3:
        return ""
    url = workspace_url(state)
    action = (f'<a class="refresh-link agent-primary" href="{escape(url, quote=True)}" '
              'target="_blank" rel="noopener noreferrer">Open T3 Code ↗</a>' if url else
              '<p class="empty">The T3 HTTPS address is unavailable. Check the gateway routes on the host '
              'or open your existing T3 Connect connection.</p>')
    return ('<section class="agent-panel t3-workspace" aria-labelledby="t3-workspace-heading">'
            '<h2 id="t3-workspace-heading">Coding workspace</h2>'
            '<p>Use T3 Code for coding conversations, provider selection, goals, worktrees, '
            'schedules, pull requests and browser preview.</p>' + action +
            '<p class="agent-help">Manage T3 updates in its environment settings. '
            'Sign in or pair through T3 when requested.</p>'
            '<p class="agent-help">Independent tasks retain Basaltwater’s run limits, '
            'host operations and execution receipts. Existing tasks and history stay here.</p></section>')
