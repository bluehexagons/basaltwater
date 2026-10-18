#!/usr/bin/env python3
"""Restart the system when updates require it and policy allows it."""

from __future__ import annotations

import math
import os
import pwd
import shutil
import subprocess
import sys
import time
from logging import ERROR
from typing import Any

# Add lib directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '../..'))

from lib.logging_utils import get_service_logger, log_event
from lib.atomic_io import write_json_atomic
from lib.agent_maintenance import inspect_agent_maintenance
from lib.agent_activity import active_agent_workloads
from lib.config import validate_current_setup_fields
from lib.kernel_restart import newer_installed_kernel
from lib.machine_state import can_restart_system, load_setup_config
from lib.maintenance_lock import maintenance_lock
from lib.notifications import load_notification_configs_from_state, send_notification_safe
from lib.plugin_registry import get_system_type_definition
from lib.proxmox_preflight import check_proxmox_update_safety, is_proxmox_host
from lib.state_read import StateReadError, read_state_object
from lib.validation import validate_filesystem_path

logger = get_service_logger('auto_restart_if_needed', 'common', use_syslog=True)

STATE_FILE = "/var/lib/basaltwater/auto_restart_state.json"
MIN_UPTIME_SECONDS = 30 * 60


def check_restart_required() -> bool:
    """Check if system restart is required."""
    return os.path.exists("/var/run/reboot-required")


def get_uptime_seconds() -> float | None:
    """Return system uptime in seconds, or None when it cannot be read."""
    try:
        with open("/proc/uptime", "r", encoding="utf-8") as handle:
            return float(handle.read().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def get_active_sessions() -> list[str] | None:
    """Return non-idle human logins on hosts without managed coding agents.

    A lingering user manager is registered with logind even before any login.
    Agent hosts use live work rather than login state, so an open SSH/desktop
    session cannot keep an idle agent VM up indefinitely.
    """
    config = load_setup_config() or {}
    tools = config.get("agent_tools") or []
    has_agents = any(tool in tools or config.get(f"install_{tool}") is True
                     for tool in ("codex", "claude", "opencode", "t3code"))
    if has_agents:
        return []
    try:
        result = subprocess.run(
            ["loginctl", "list-sessions", "--no-legend"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        if result.returncode != 0:
            return None
        sessions = []
        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            session_id = line.split()[0]
            if not session_id.isascii() or not session_id.isalnum():
                return None
            details = subprocess.run(
                ["loginctl", "show-session", session_id,
                 "--property=Class", "--property=State", "--property=IdleHint"],
                capture_output=True, text=True, check=False, timeout=10,
            )
            if details.returncode != 0:
                return None
            properties = dict(row.split("=", 1) for row in details.stdout.splitlines() if "=" in row)
            if not all(properties.get(key) for key in ("Class", "State", "IdleHint")):
                return None
            if properties["Class"] not in {"user", "user-early", "user-light", "user-early-light"}:
                continue
            if properties["State"] in {"active", "online"} and properties["IdleHint"] == "no":
                sessions.append(line)
        return sessions
    except (OSError, subprocess.TimeoutExpired):
        return None


def _configured_agent_account() -> pwd.struct_passwd | None:
    """Return the setup account used for user-scoped agent work."""
    config = load_setup_config() or {}
    username = config.get("username")
    if not isinstance(username, str) or not username:
        return None
    try:
        account = pwd.getpwnam(username)
    except KeyError:
        return None
    return account if os.path.isabs(account.pw_dir) else None


def get_active_agent_workloads(
    *,
    proc_root: str = "/proc",
) -> list[str] | None:
    """Return explicit holds and verified live work for the coding account."""
    account = _configured_agent_account()
    if account is None:
        return []

    categories: set[str] = set()
    hold = inspect_agent_maintenance(account.pw_dir)
    if hold["status"] == "active":
        categories.add("agent maintenance hold")
    elif hold["status"] == "invalid":
        categories.add("invalid agent maintenance hold")

    workloads = active_agent_workloads(account.pw_uid, proc_root=proc_root)
    if workloads is None:
        return None
    return sorted(categories.union(workloads))


def _nonnegative_int(value: Any, default: int) -> int:
    """Return a policy integer, falling back for invalid persisted values."""
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return max(0, parsed)


def _timestamp(value: Any, default: float) -> float:
    """Return a finite timestamp from persisted state."""
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return parsed if math.isfinite(parsed) else default


def load_restart_policy() -> dict[str, Any]:
    """Load auto-restart policy from persisted setup config."""
    config = load_setup_config()
    if not config:
        raise ValueError("Saved setup policy is required before automatic restarts")
    validate_current_setup_fields(config)
    system_type = config.get("system_type")
    if not isinstance(system_type, str) or not system_type:
        raise ValueError("Saved setup policy requires a system_type")
    defaults = get_system_type_definition(system_type)

    auto_restart = config.get("auto_restart", defaults.default_auto_restart)
    force_days = config.get("auto_restart_force_days", defaults.default_auto_restart_force_days)
    grace = config.get("auto_restart_grace", 5)
    if type(auto_restart) is not bool:
        raise ValueError("Saved auto_restart policy must be boolean")
    for name, value in (("auto_restart_force_days", force_days), ("auto_restart_grace", grace)):
        if type(value) is not int or value < 0:
            raise ValueError(f"Saved {name} policy must be a non-negative integer")
    return {"auto_restart": auto_restart, "force_days": force_days, "grace": grace}


def load_restart_state() -> dict[str, Any]:
    """Retain invalid deferral history instead of resetting the force deadline."""
    state = read_state_object(STATE_FILE, versioned=False)
    if state is None:
        return {}
    for name in ("first_required", "last_notified"):
        if name not in state:
            continue
        value = state[name]
        try:
            valid = type(value) in {int, float} and math.isfinite(value) and value >= 0
        except OverflowError:
            valid = False
        if not valid:
            raise StateReadError(STATE_FILE, f"invalid {name} timestamp")
    if 'last_reason' in state and not isinstance(state['last_reason'], str):
        raise StateReadError(STATE_FILE, "invalid last deferral reason")
    return state


def save_restart_state(state: dict[str, Any]) -> None:
    """Save persistent auto-restart deferral state atomically."""
    validate_filesystem_path(STATE_FILE, must_exist=False)
    write_json_atomic(STATE_FILE, state, mode=0o600, sort_keys=True)


def clear_restart_state() -> None:
    """Remove deferral state when a restart is no longer required."""
    try:
        os.unlink(STATE_FILE)
    except FileNotFoundError:
        pass
    except OSError as exc:
        log_event(logger, "Failed to clear restart state", level=ERROR, error=str(exc))


def should_notify(state: dict[str, Any], now: float) -> bool:
    """Return true when a deferral notification should be sent."""
    last_notified = _timestamp(state.get("last_notified"), 0)
    if last_notified <= 0 or last_notified > now:
        return True
    try:
        return time.localtime(last_notified)[:3] != time.localtime(now)[:3]
    except (OSError, OverflowError, ValueError):
        return True


def record_deferral(
    reason: str, notification_configs, details: str | None = None,
    *, advisory: bool = False,
) -> None:
    """Record a deferred restart and notify at most once per day."""
    now = time.time()
    state = load_restart_state()
    if advisory:
        state.pop("first_required", None)
    else:
        first_required = _timestamp(state.get("first_required"), now)
        state["first_required"] = now if first_required > now else first_required
    log_event(logger, "Restart deferred", reason=reason, advisory=advisory)
    eligible = [
        config for config in notification_configs
        if getattr(config, "level", "normal") not in ("error", "off")
    ]
    if not eligible:
        log_event(logger, "No eligible restart notification targets")
    elif should_notify(state, now):
        delivered = send_notification_safe(
            eligible,
            subject="Newer kernel installed: review restart" if advisory else "Restart required: deferred",
            job="auto_restart_if_needed",
            status="warning",
            message=reason if advisory else f"A restart is required, but it was deferred: {reason}.",
            details=details,
            logger=logger,
        )
        if delivered is True:
            state["last_notified"] = now
        else:
            log_event(logger, "Restart notification failed; next check will retry", level=ERROR)
    state["last_reason"] = reason
    save_restart_state(state)


def force_deadline_reached(policy: dict[str, Any]) -> bool:
    """Return true when restart deferrals have exceeded the configured max."""
    force_days = _nonnegative_int(policy.get("force_days"), 0)
    if force_days <= 0:
        return False
    state = load_restart_state()
    now = time.time()
    first_required = _timestamp(state.get("first_required"), now)
    if first_required > now:
        first_required = now
    return now - first_required >= force_days * 24 * 60 * 60


def perform_restart(notification_configs, grace_minutes: int, forced: bool = False) -> int:
    """Schedule system restart."""
    log_event(logger, "Restart required, scheduling restart", forced=forced, grace_minutes=grace_minutes)
    send_notification_safe(
        notification_configs,
        subject="Restart required: restarting soon",
        job="auto_restart_if_needed",
        status="warning",
        message=f"A restart is required. Automatic restart is scheduled in {grace_minutes} minute(s).",
        logger=logger,
    )

    try:
        shutdown_cmd = shutil.which("shutdown")
        if not shutdown_cmd:
            raise FileNotFoundError("shutdown command not found")
        subprocess.run(
            [shutdown_cmd, "-r", f"+{max(0, grace_minutes)}", "Automatic restart for system updates"],
            check=True,
        )
        return 0
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        log_event(logger, "Failed to initiate restart", level=ERROR, error=str(exc))
        send_notification_safe(
            notification_configs,
            subject="Error: automatic restart failed",
            job="auto_restart_if_needed",
            status="error",
            message=f"Restart required but automatic restart failed: {exc}",
            logger=logger,
        )
        return 1


def _check_restart(notification_configs) -> int:
    """Apply restart policy only after the retained history can be read safely."""
    load_restart_state()
    if not check_restart_required():
        try:
            pending_kernel = newer_installed_kernel()
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            log_event(logger, "Could not inspect installed kernels", level=ERROR, error=str(exc))
            return 1
        if pending_kernel:
            record_deferral(
                f"Newer kernel {pending_kernel} is installed, but no reboot "
                "marker exists. Review boot selection and kernel pins, then schedule "
                "a restart to activate the intended kernel. Automatic restart is "
                "not scheduled from package comparison alone.",
                notification_configs,
                advisory=True,
            )
            return 0
        clear_restart_state()
        log_event(logger, "No restart required")
        return 0

    if not can_restart_system():
        record_deferral("machine type cannot restart itself", notification_configs)
        return 0

    policy = load_restart_policy()
    uptime = get_uptime_seconds()
    if uptime is None:
        record_deferral("system uptime could not be determined", notification_configs)
        return 0
    if uptime < MIN_UPTIME_SECONDS:
        record_deferral("system booted recently", notification_configs)
        return 0

    sessions = get_active_sessions()
    if sessions is None:
        record_deferral("active sessions could not be determined", notification_configs)
        return 0
    agent_workloads = get_active_agent_workloads()
    if agent_workloads is None:
        record_deferral("agent workloads could not be determined", notification_configs)
        return 0
    forced = force_deadline_reached(policy)
    if not policy["auto_restart"] and not forced:
        record_deferral("automatic restarts are disabled", notification_configs)
        return 0

    if sessions and not forced:
        log_event(logger, "Active sessions detected, skipping restart")
        record_deferral("active sessions detected", notification_configs, "\n".join(sessions))
        return 0

    if agent_workloads and not forced:
        log_event(
            logger,
            "Active agent workloads detected, skipping restart",
            category_count=len(agent_workloads),
        )
        record_deferral(
            "active agent workloads detected",
            notification_configs,
            "\n".join(agent_workloads),
        )
        return 0

    if is_proxmox_host():
        try:
            check_proxmox_update_safety(require_evacuated=True)
        except (OSError, TimeoutError, RuntimeError, ValueError) as exc:
            record_deferral(str(exc), notification_configs)
            return 0
    return perform_restart(notification_configs, int(policy["grace"]), forced=forced)


def main() -> int:
    """Check and perform restart, reporting invalid policy or retained state."""
    log_event(logger, "Starting restart check")
    notification_configs = load_notification_configs_from_state(logger)
    try:
        with maintenance_lock() as acquired:
            if not acquired:
                log_event(logger, "Restart check deferred while setup or maintenance is running")
                return 0
            return _check_restart(notification_configs)
    except (OSError, ValueError) as exc:
        log_event(logger, "Restart check stopped", level=ERROR, error=str(exc))
        send_notification_safe(
            notification_configs, subject="Error: restart check stopped",
            job="auto_restart_if_needed", status="error", message=str(exc), logger=logger,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
