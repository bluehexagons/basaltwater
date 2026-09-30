"""Optional post-setup restart and remote health-check workflow."""

from __future__ import annotations

import os
import secrets
import shlex
import shutil
import subprocess
import time
from typing import Optional
from uuid import UUID

from lib.config import SetupConfig
from lib.machine_state import can_restart_system
from lib.remote_utils import CommandTimeoutError, run
from lib.ssh_utils import build_ssh_command, get_ssh_control_path, ssh_batch_mode
from lib.validators import validate_host, validate_username


_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
_RESTART_WAIT_SECONDS = 300
_POLL_INTERVAL_SECONDS = 5
_REMOTE_RESTART_STATUS = r"""
import sys
sys.path.insert(0, "/opt/basaltwater")
from lib.setup_reboot import _local_restart_status
print(_local_restart_status())
"""


def _local_restart_status() -> str:
    """Inspect the actual target, independently of the requested setup profile."""
    if not os.path.isfile("/run/reboot-required"):
        return "clear"
    if not can_restart_system():
        return "unsupported"
    if os.path.isdir("/etc/pve") or shutil.which("pveversion"):
        return "needed-proxmox"
    return "needed"


def _ssh_result(
    config: SetupConfig,
    remote_command: str,
    *,
    timeout: float = 30,
) -> subprocess.CompletedProcess[str]:
    return run(
        build_ssh_command(
            config.host,
            "root",
            config.ssh_key,
            remote_command=remote_command,
            batch_mode=ssh_batch_mode(),
            connect_timeout=15,
            server_alive_interval=15,
            control_path=get_ssh_control_path(config.host, "root", config.ssh_key),
        ),
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def _restart_status(config: SetupConfig) -> Optional[str]:
    try:
        if config.host in _LOCAL_HOSTS:
            return _local_restart_status()
        command = "python3 -c " + shlex.quote(_REMOTE_RESTART_STATUS)
        result = _ssh_result(config, command)
    except (CommandTimeoutError, OSError, subprocess.SubprocessError, ValueError) as exc:
        print(f"Error checking restart status on {config.host}: {exc}")
        return None

    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        print(f"Error checking restart status on {config.host}: {detail or 'SSH failed'}")
        return None

    status = result.stdout.strip()
    if status not in {"clear", "needed", "needed-proxmox", "unsupported"}:
        print(f"Error checking restart status on {config.host}: invalid response")
        return None
    return status


def _check_proxmox_reboot_safety(config: SetupConfig) -> bool:
    """Require a clean maintenance report and no active guests before reboot."""

    if config.host in _LOCAL_HOSTS:
        print("Error: use Proxmox maintenance commands to restart a local Proxmox host")
        return False

    from lib.proxmox_hosts import ProxmoxHost
    from lib.proxmox_maintenance import collect_maintenance_report

    try:
        report = collect_maintenance_report(
            ProxmoxHost(
                name=config.host,
                address=config.host,
                user="root",
                ssh_key=config.ssh_key,
            )
        )
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as exc:
        print(f"Error checking Proxmox reboot safety on {config.host}: {exc}")
        return False
    if report.reboot_safe:
        return True

    blockers = "; ".join(report.reboot_blockers()) or "maintenance checks failed"
    print(f"Error: refusing to restart Proxmox host {config.host}: {blockers}")
    return False


def _validated_boot_id(output: str) -> Optional[str]:
    """Require a single canonical kernel UUID, not banners or other SSH output."""
    value = output.strip()
    try:
        parsed = str(UUID(value))
    except ValueError:
        return None
    return parsed if parsed == value.lower() else None


def _boot_id(config: SetupConfig) -> Optional[str]:
    try:
        result = _ssh_result(
            config,
            "cat /proc/sys/kernel/random/boot_id",
        )
    except (CommandTimeoutError, OSError, subprocess.SubprocessError) as exc:
        print(f"Error reading boot ID from {config.host}: {exc}")
        return None
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        print(f"Error reading boot ID from {config.host}: {detail or 'SSH failed'}")
        return None
    value = _validated_boot_id(result.stdout)
    if value is None:
        print(f"Error reading boot ID from {config.host}: invalid response")
    return value


def _request_restart(config: SetupConfig) -> bool:
    command = [
        "/usr/bin/systemd-run",
        "--quiet",
        "--unit",
        f"basaltwater-setup-reboot-{secrets.token_hex(6)}",
        "--on-active=2s",
        "/usr/bin/systemctl",
        "reboot",
        "--no-wall",
    ]
    try:
        if config.host in _LOCAL_HOSTS:
            result = run(
                command,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
        else:
            result = _ssh_result(config, shlex.join(command))
    except (CommandTimeoutError, OSError, subprocess.SubprocessError) as exc:
        print(f"Error requesting restart on {config.host}: {exc}")
        return False

    if result.returncode == 0:
        return True
    detail = (result.stderr or result.stdout).strip()
    print(f"Error requesting restart on {config.host}: {detail or 'command failed'}")
    return False


def _wait_for_remote_restart(config: SetupConfig, old_boot_id: str) -> bool:
    deadline = time.monotonic() + _RESTART_WAIT_SECONDS
    command = "cat /proc/sys/kernel/random/boot_id"
    while (remaining := deadline - time.monotonic()) > 0:
        try:
            result = _ssh_result(
                config,
                command,
                timeout=min(20, remaining),
            )
        except (CommandTimeoutError, OSError, subprocess.SubprocessError):
            result = None

        if result is not None and result.returncode == 0:
            new_boot_id = _validated_boot_id(result.stdout)
            if new_boot_id is not None and new_boot_id != old_boot_id:
                return True

        time.sleep(min(_POLL_INTERVAL_SECONDS, max(0, deadline - time.monotonic())))

    print(
        f"Error: {config.host} did not complete a restart and return over SSH "
        f"within {_RESTART_WAIT_SECONDS} seconds"
    )
    return False


def restart_after_setup(config: SetupConfig, *, wait_for_restart: bool = False) -> int:
    """Restart after successful setup when the target has a reboot marker."""

    if wait_for_restart and config.host in _LOCAL_HOSTS:
        print("Error: --wait-for-restart requires a remote setup target")
        return 1
    if not validate_host(config.host):
        print(f"Error: Invalid restart target: {config.host}")
        return 1
    if not validate_username(config.username):
        print(f"Error: Invalid health-check username: {config.username}")
        return 1
    if config.dry_run:
        print("  [DRY-RUN] Skipping the post-setup restart check")
        return 0

    status = _restart_status(config)
    if status is None:
        return 1
    if status == "clear":
        print(f"  ✓ No restart required on {config.host}")
        return 0
    if status == "unsupported":
        print(f"  ⚠ {config.host} cannot restart itself; leaving the marker pending")
        return 0

    if status == "needed-proxmox" or config.system_type == "server_proxmox":
        if not _check_proxmox_reboot_safety(config):
            return 1

    old_boot_id: Optional[str] = None
    if wait_for_restart:
        old_boot_id = _boot_id(config)
        if old_boot_id is None:
            return 1

    print(f"  Restart required on {config.host}; requesting restart")
    if not _request_restart(config):
        return 1

    if not wait_for_restart:
        print("  Restart requested; the setup command will not wait for reconnection")
        return 0

    assert old_boot_id is not None
    print(f"  Waiting for {config.host} to boot again over SSH")
    if not _wait_for_remote_restart(config, old_boot_id):
        return 1

    print("  ✓ Restart completed; running host health checks")
    from lib.sysadmin_health import run_health

    return run_health(config.host, config.username, config.ssh_key)
