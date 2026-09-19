#!/usr/bin/env python3
"""Restart the system when updates require it and policy allows it."""

from __future__ import annotations

import json
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
from lib.kernel_restart import newer_installed_kernel
from lib.machine_state import can_restart_system, load_setup_config
from lib.notifications import load_notification_configs_from_state, send_notification_safe
from lib.plugin_registry import get_system_type_definition
from lib.validation import validate_filesystem_path

logger = get_service_logger('auto_restart_if_needed', 'common', use_syslog=True)

STATE_FILE = "/var/lib/basaltwater/auto_restart_state.json"
MIN_UPTIME_SECONDS = 30 * 60
_AGENT_PROCESS_NAMES = frozenset(("claude", "codex", "opencode"))
_BUILD_PROCESS_NAMES = frozenset(
    (
        "bun",
        "cargo",
        "cc",
        "cc1",
        "cc1plus",
        "clang",
        "clang++",
        "cmake",
        "deno",
        "g++",
        "gcc",
        "git",
        "go",
        "ld",
        "make",
        "meson",
        "ninja",
        "npm",
        "npx",
        "pnpm",
        "rustc",
        "yarn",
    )
)


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
    """Return active loginctl sessions, or None when they cannot be queried."""
    try:
        result = subprocess.run(
            ["loginctl", "list-sessions", "--no-legend"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return [line for line in result.stdout.splitlines() if line.strip()]


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


def _within(path: str, parent: str) -> bool:
    try:
        resolved_parent = os.path.realpath(parent)
        return os.path.commonpath(
            (os.path.realpath(path), resolved_parent)
        ) == resolved_parent
    except ValueError:
        return False


def _process_workload_category(command_name: str) -> str | None:
    normalized = command_name.strip().lower()
    first_word = normalized.split(maxsplit=1)[0] if normalized else ""
    if first_word in _AGENT_PROCESS_NAMES:
        return "coding agent process"
    if first_word in _BUILD_PROCESS_NAMES:
        return "build or Git process"
    if first_word.startswith("tmux") or first_word in {"screen", "screen-4.9.0"}:
        return "terminal multiplexer process"
    return None


def get_active_agent_workloads(
    *,
    proc_root: str = "/proc",
) -> list[str] | None:
    """Return category-only blockers for the configured agent account.

    Process command lines and repository contents are deliberately not read or
    logged. A disappearing process is an expected race and is skipped.
    """
    account = _configured_agent_account()
    if account is None:
        return []

    categories: set[str] = set()
    hold = inspect_agent_maintenance(account.pw_dir)
    if hold["status"] == "active":
        categories.add("agent maintenance hold")
    elif hold["status"] == "invalid":
        categories.add("invalid agent maintenance hold")

    managed_worktrees = os.path.join(
        account.pw_dir,
        ".local",
        "share",
        "basaltwater",
        "worktrees",
    )
    try:
        processes = list(os.scandir(proc_root))
    except OSError:
        return None

    for process in processes:
        if not process.name.isdigit():
            continue
        try:
            if (
                not process.is_dir(follow_symlinks=False)
                or process.stat(follow_symlinks=False).st_uid != account.pw_uid
            ):
                continue
            with open(
                os.path.join(process.path, "comm"),
                encoding="utf-8",
            ) as file_obj:
                command_name = file_obj.read(128)
        except (OSError, UnicodeError):
            continue

        category = _process_workload_category(command_name)
        if category:
            categories.add(category)
        try:
            working_directory = os.readlink(os.path.join(process.path, "cwd"))
        except OSError:
            continue
        if working_directory != managed_worktrees and _within(
            working_directory,
            managed_worktrees,
        ):
            categories.add("managed agent worktree process")

    return sorted(categories)


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
    config = load_setup_config() or {}
    system_type = config.get("system_type")
    defaults = None
    if isinstance(system_type, str):
        defaults = get_system_type_definition(system_type)

    auto_restart = bool(config.get("auto_restart", defaults.default_auto_restart if defaults else True))
    if "auto_restart" not in config and "no_restart" in config:
        auto_restart = not bool(config.get("no_restart"))

    return {
        "auto_restart": auto_restart,
        "force_days": _nonnegative_int(
            config.get(
                "auto_restart_force_days",
                defaults.default_auto_restart_force_days if defaults else 7,
            ),
            defaults.default_auto_restart_force_days if defaults else 7,
        ),
        "grace": _nonnegative_int(config.get("auto_restart_grace", 5), 5),
    }


def load_restart_state() -> dict[str, Any]:
    """Load persistent auto-restart deferral state."""
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as handle:
            state = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    return state if isinstance(state, dict) else {}


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


def main() -> int:
    """Check and perform restart if needed."""
    log_event(logger, "Starting restart check")
    notification_configs = load_notification_configs_from_state(logger)

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

    return perform_restart(notification_configs, int(policy["grace"]), forced=forced)


if __name__ == "__main__":
    sys.exit(main())
