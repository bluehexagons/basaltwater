"""Activate package updates when cloudflared still runs a replaced executable."""

from __future__ import annotations

import os
import re

from lib.remote_utils import run


CLOUDFLARED_BINARY = "/usr/bin/cloudflared"
PROC_ROOT = "/proc"


def _service_command(arguments: list[str], *, timeout: int = 30) -> None:
    """Run a bounded service command without exposing tunnel settings."""
    result = run(
        ["systemctl", *arguments, "cloudflared.service"],
        check=False, capture_output=True, timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Cloudflared service command failed: systemctl {arguments[0]}")


def _running_executable() -> os.stat_result | None:
    """Inspect only the service's main process; inactive services stay inactive."""
    result = run(
        ["systemctl", "show", "cloudflared.service", "--property=LoadState,MainPID"],
        check=False, capture_output=True, timeout=30,
    )
    properties = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    if properties.get("LoadState") == "not-found":
        return None
    if result.returncode != 0:
        raise RuntimeError("Could not inspect the cloudflared service")
    pid = properties.get("MainPID", "")
    if not re.fullmatch(r"[0-9]+", pid):
        raise RuntimeError("Cloudflared service returned an invalid MainPID")
    if int(pid) == 0:
        return None
    try:
        return os.stat(os.path.join(PROC_ROOT, pid, "exe"))
    except FileNotFoundError:
        # The daemon exited between the systemd query and the procfs lookup.
        return None


def refresh_cloudflared_service() -> bool:
    """Restart a stale daemon and verify that it loaded the installed package.

    Comparing device/inode pairs also detects an upgrade applied by an earlier
    run whose service restart failed. No restart is needed for a current or
    inactive daemon, or when cloudflared is not installed.
    """
    try:
        installed = os.stat(CLOUDFLARED_BINARY)
    except FileNotFoundError:
        return False
    running = _running_executable()
    if running is None or os.path.samestat(installed, running):
        return False

    _service_command(["restart"], timeout=240)
    _service_command(["is-active", "--quiet"])
    running = _running_executable()
    if running is None or not os.path.samestat(installed, running):
        raise RuntimeError(
            "Cloudflared service did not load /usr/bin/cloudflared after restart; "
            "check the service's ExecStart path"
        )
    return True
