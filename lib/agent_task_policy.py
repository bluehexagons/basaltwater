"""Narrow preflight of task settings against local Codex administrator policy."""

from __future__ import annotations

import os
import stat
import tomllib
from pathlib import Path
from typing import Any


REQUIREMENTS_PATH = Path("/etc/codex/requirements.toml")
MAX_REQUIREMENTS_BYTES = 64 * 1024
_UNREADABLE = "Codex administrator policy could not be read safely; review policy and task settings."
_INVALID = "Codex administrator policy is invalid or too large; review policy and task settings."


def task_policy_issues(task: dict[str, Any]) -> list[str]:
    """Check known enforced constraints without changing or replacing policy.

    Accept a minimal template containing mode and optional web_search. This is
    a local qualification hint, not a replacement for Codex's policy enforcement;
    other requirements and configuration layers are still enforced at launch.
    """
    try:
        descriptor = os.open(REQUIREMENTS_PATH, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return []
    except OSError:
        return [_UNREADABLE]
    try:
        with os.fdopen(descriptor, "rb") as source:
            metadata = os.fstat(source.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                return [_UNREADABLE]
            if metadata.st_size > MAX_REQUIREMENTS_BYTES:
                return [_INVALID]
            payload = source.read(MAX_REQUIREMENTS_BYTES + 1)
            if len(payload) > MAX_REQUIREMENTS_BYTES:
                return [_INVALID]
        requirements = tomllib.loads(payload.decode("utf-8"))
    except OSError:
        return [_UNREADABLE]
    except (ValueError, UnicodeError, RecursionError):
        return [_INVALID]

    # Reject malformed known constraints rather than silently ignoring them.
    for name in ("allowed_sandbox_modes", "allowed_approval_policies", "allowed_web_search_modes"):
        if name in requirements and (
            not isinstance(requirements[name], list)
            or any(not isinstance(value, str) for value in requirements[name])
        ):
            return [_INVALID]
    profiles = requirements.get("allowed_permission_profiles")
    if "allowed_permission_profiles" in requirements and (
        not isinstance(profiles, dict) or any(type(value) is not bool for value in profiles.values())
    ):
        return [_INVALID]

    issues = []
    sandbox = "read-only" if task["mode"] == "inspect" else "workspace-write"
    if "allowed_sandbox_modes" in requirements and sandbox not in requirements["allowed_sandbox_modes"]:
        issues.append("Codex administrator policy forbids this task's sandbox mode.")
    if "allowed_approval_policies" in requirements and "never" not in requirements["allowed_approval_policies"]:
        issues.append("Codex administrator policy does not permit unattended approval policy never.")
    search = task.get("web_search", "disabled")
    if "allowed_web_search_modes" in requirements:
        allowed_search = requirements["allowed_web_search_modes"] or ["disabled"]
        if search not in allowed_search:
            issues.append("Codex administrator policy forbids this task's web search mode.")
    if profiles is not None:
        if task["mode"] == "workspace":
            issues.append("Codex administrator policy restricts permission profiles; this task's generated workspace profile is not allowlisted.")
        elif profiles.get(":read-only") is not True:
            issues.append("Codex administrator policy does not allow the task's :read-only permission profile.")
    return issues
