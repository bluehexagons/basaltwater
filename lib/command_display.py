"""Redaction for setup commands shown in terminals and logs."""

from __future__ import annotations

import shlex
from urllib.parse import urlsplit


def _git_url_needs_redaction(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        return bool(
            parsed.query
            or parsed.fragment
            or (parsed.scheme != "ssh" and "@" in parsed.netloc)
            or (parsed.scheme == "ssh" and parsed.password is not None)
        )
    except ValueError:
        return "://" in value


def redacted_setup_parts(parts: list[str]) -> list[str]:
    """Hide token-bearing notification and Git URLs in displayed commands."""
    visible: list[str] = []
    for part in parts:
        try:
            tokens = shlex.split(part)
        except ValueError:
            tokens = []
        if part.startswith("--notify "):
            if len(tokens) == 3 and tokens[1] == "mailbox":
                visible.append(part)
            elif len(tokens) == 3 and tokens[1] == "webhook":
                visible.append("--notify webhook https://REPLACE_WITH_WEBHOOK_URL")
            else:
                visible.append("--notify REDACTED REDACTED")
            continue
        git_url_position = {
            "--deploy": (3, 2),
            "--deploy-latest": (3, 2),
            "--repo": (2, 1),
            "--git-credential": (3, 1),
            "--git-ca-certificate": (3, 1),
        }.get(tokens[0] if tokens else "")
        if git_url_position and len(tokens) == git_url_position[0]:
            index = git_url_position[1]
            if _git_url_needs_redaction(tokens[index]):
                tokens[index] = "https://REPLACE_WITH_GIT_URL"
                visible.append(shlex.join(tokens))
                continue
        visible.append(part)
    return visible
