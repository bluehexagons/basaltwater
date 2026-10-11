"""Validated maintenance policy shared by setup, the CLI and the panel."""

from __future__ import annotations

import argparse
import re
import shlex
from typing import Any


DATA_DIR = "/var/lib/basaltwater/maintenance-agent"
CONFIG_FILE = "/etc/basaltwater/maintenance-agent.json"
CONTROL_DIR = DATA_DIR + "/control"
POLICY_FILE = CONTROL_DIR + "/settings.json"
REPORT_FILE = DATA_DIR + "/reports.json"
SERVICE_NAME = "basaltwater-maintenance-agent"
DEFAULTS = {
    "enabled": False, "interval": 60, "routines": False, "security": True,
    "audit_days": 7, "model": "auto", "effort": "low", "escalation_model": "",
    "escalation_effort": "high", "daily_calls": 2, "daily_escalations": 1, "timeout": 10,
}
LIMITS = {"interval": (15, 10080), "audit_days": (1, 30), "daily_calls": (0, 24),
          "daily_escalations": (0, 4), "timeout": (1, 60)}
EFFORTS = ("minimal", "low", "medium", "high", "xhigh")
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}\Z")


def validate_policy(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - DEFAULTS.keys():
        raise ValueError("Unknown maintenance agent setting")
    policy = {**DEFAULTS, **value}
    for key, default in DEFAULTS.items():
        selected = policy[key]
        if type(default) is bool:
            if type(selected) is not bool:
                raise ValueError(f"{key} must be boolean")
        elif key in LIMITS:
            if isinstance(selected, str) and re.fullmatch(r"[0-9]{1,5}", selected):
                selected = int(selected)
            minimum, maximum = LIMITS[key]
            if type(selected) is not int or not minimum <= selected <= maximum:
                raise ValueError(f"{key} must be {minimum}–{maximum}")
            policy[key] = selected
        elif key.endswith("effort"):
            if not isinstance(selected, str) or selected not in EFFORTS:
                raise ValueError(f"{key} must be one of {', '.join(EFFORTS)}")
        elif not isinstance(selected, str) or not (key == "escalation_model" and selected == "" or _MODEL.fullmatch(selected)):
            raise ValueError(f"Invalid {key}")
    if policy["escalation_model"] == "auto":
        raise ValueError("Select an explicit escalation model or leave it empty")
    return policy


def add_policy_arguments(parser: argparse.ArgumentParser) -> None:
    for key, default in DEFAULTS.items():
        flag = "maintenance-agent" if key == "enabled" else "maintenance-agent-" + key.replace("_", "-")
        dest = "maintenance_agent" if key == "enabled" else "maintenance_agent_" + key
        help_text = {
            "enabled": "Enable tailored host checks and bounded non-root AI reviews",
            "routines": "Allow the fixed service to trigger existing managed cleanup jobs daily",
            "security": "Include periodic SSH and sensitive-file permission audits",
            "model": "Routine model ID, or auto to select a cached economy model",
            "escalation_model": "Explicit stronger model for critical or repeatedly unresolved findings (empty disables)",
            "daily_calls": "Maximum total model attempts per UTC day; 0 disables AI",
            "daily_escalations": "Maximum stronger-model attempts per UTC day",
            "interval": "Minutes between host checks (15–10080; default 60)",
            "audit_days": "Days between security audits (1–30; default 7)",
            "timeout": "Maximum minutes per AI review (1–60; default 10)",
        }.get(key, "Reasoning effort for " + key.replace("_", " "))
        if type(default) is bool:
            parser.add_argument("--" + flag, dest=dest, default=argparse.SUPPRESS,
                                action=argparse.BooleanOptionalAction, help=help_text)
        else:
            parser.add_argument("--" + flag, dest=dest, default=argparse.SUPPRESS,
                                type=int if key in LIMITS else str,
                                choices=EFFORTS if key.endswith("effort") else None,
                                help=help_text)


def setup_policy(config: object) -> dict[str, Any]:
    values = {}
    for key in DEFAULTS:
        field = "maintenance_agent" if key == "enabled" else "maintenance_agent_" + key
        value = getattr(config, field, None)
        if value is not None:
            values[key] = value
    return validate_policy(values)


def setup_policy_args(config: object) -> list[str]:
    result = []
    for key in DEFAULTS:
        field = "maintenance_agent" if key == "enabled" else "maintenance_agent_" + key
        value = getattr(config, field, None)
        if value is None:
            continue
        flag = "maintenance-agent" if key == "enabled" else "maintenance-agent-" + key.replace("_", "-")
        result.append("--" + ("no-" if value is False else "") + flag if type(value) is bool
                      else "--" + flag + " " + shlex.quote(str(value)))
    return result
