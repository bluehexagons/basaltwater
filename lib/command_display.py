"""Redaction for setup commands shown in terminals and logs."""

from __future__ import annotations

from copy import deepcopy
import shlex
from typing import Any
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


def redacted_git_url(value: str) -> str:
    """Replace URLs that could carry inline credentials in displayed output."""
    return "https://REPLACE_WITH_GIT_URL" if _git_url_needs_redaction(value) else value


def redacted_saved_args(args: dict[str, Any]) -> dict[str, Any]:
    """Make a display-only copy of saved setup arguments."""
    visible = deepcopy(args)
    for field in ("deploy_specs", "git_credentials", "git_ca_certificates"):
        records = visible.get(field)
        if not isinstance(records, list):
            continue
        index = 1 if field == "deploy_specs" else 0
        for record in records:
            if isinstance(record, list) and len(record) > index and isinstance(record[index], str):
                record[index] = redacted_git_url(record[index])
    repositories = visible.get("agent_repos")
    if isinstance(repositories, list):
        visible["agent_repos"] = [
            redacted_git_url(value) if isinstance(value, str) else value
            for value in repositories
        ]
    notifications = visible.get("notify_specs")
    if isinstance(notifications, list):
        for spec in notifications:
            if isinstance(spec, list) and len(spec) >= 2 and spec[0] != "mailbox":
                spec[1] = "https://REPLACE_WITH_WEBHOOK_URL"
    return visible


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
            replacement = redacted_git_url(tokens[index])
            if replacement != tokens[index]:
                tokens[index] = replacement
                visible.append(shlex.join(tokens))
                continue
        visible.append(part)
    return visible
