"""systemctl and journalctl convenience wrappers."""

from __future__ import annotations

import os
import shlex
import sys
from typing import Optional

from lib.sysadmin_process import run_command
from lib.cache import load_setup_command
from lib.ssh_utils import build_ssh_command

VALID_ACTIONS = ("status", "restart", "start", "stop", "enable", "disable", "reload")


def _resolve_credentials(
    host: str,
    username: Optional[str],
    ssh_key: Optional[str],
) -> tuple[str, Optional[str]]:
    config = load_setup_command(host)
    if config:
        if not username:
            username = config.username
        if not ssh_key:
            ssh_key = config.ssh_key
    return username or "root", ssh_key


def run_svc(
    host: str,
    unit: str,
    action: str = "status",
    username: Optional[str] = None,
    ssh_key: Optional[str] = None,
) -> int:
    if action not in VALID_ACTIONS:
        print(f"Error: unknown action {action!r}. Choose from: {', '.join(VALID_ACTIONS)}", file=sys.stderr)
        return 1

    username, ssh_key = _resolve_credentials(host, username, ssh_key)

    # status is non-privileged; others need sudo
    if action == "status":
        remote = f"systemctl status {shlex.quote(unit)} --no-pager"
    else:
        remote = f"sudo systemctl {action} {shlex.quote(unit)}"

    # After non-status actions, append a status check so the user sees current state
    if action != "status":
        remote += f" && systemctl status {shlex.quote(unit)} --no-pager"

    cmd = build_ssh_command(host, username, ssh_key, batch_mode=False, remote_command=remote)
    result = run_command(cmd)
    # systemctl status returns 3 for inactive units — treat as success for display
    if action == "status" and result.returncode == 3:
        return 0
    return result.returncode


def run_logs(
    host: str,
    unit: str,
    lines: int = 50,
    follow: bool = False,
    username: Optional[str] = None,
    ssh_key: Optional[str] = None,
) -> int:
    username, ssh_key = _resolve_credentials(host, username, ssh_key)

    parts = ["journalctl", "-u", shlex.quote(unit), "--no-pager", f"-n", str(lines)]
    if follow:
        parts.append("-f")

    remote = " ".join(parts)
    cmd = build_ssh_command(host, username, ssh_key, batch_mode=False, remote_command=remote)
    if follow:
        os.execvp(cmd[0], cmd)
        return 0  # pragma: no cover
    return run_command(cmd, timeout=120).returncode
