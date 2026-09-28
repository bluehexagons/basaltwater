"""Redaction for setup commands shown in terminals and logs."""

from __future__ import annotations

import shlex


def redacted_setup_parts(parts: list[str]) -> list[str]:
    """Hide webhook URLs, which may carry receiver tokens in any URL component."""
    visible: list[str] = []
    for part in parts:
        if not part.startswith("--notify "):
            visible.append(part)
            continue
        try:
            tokens = shlex.split(part)
        except ValueError:
            tokens = []
        if len(tokens) == 3 and tokens[1] == "mailbox":
            visible.append(part)
        elif len(tokens) == 3 and tokens[1] == "webhook":
            visible.append("--notify webhook https://REPLACE_WITH_WEBHOOK_URL")
        else:
            visible.append("--notify REDACTED REDACTED")
    return visible
