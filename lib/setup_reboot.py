"""Optional post-setup restart and remote health-check workflow."""

from __future__ import annotations

import json
import os
import re
import secrets
import shlex
import shutil
import subprocess
import time
from typing import Optional
from uuid import UUID

from lib.config import SetupConfig
from lib.machine_state import can_restart_system
from lib.maintenance_lock import maintenance_lock
from lib.remote_utils import CommandTimeoutError, run
from lib.ssh_utils import build_ssh_command, get_ssh_control_path, ssh_batch_mode
from lib.validators import validate_host, validate_username


_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
_RESTART_WAIT_SECONDS = 300
_GUEST_SHUTDOWN_WAIT_SECONDS = 1800
_POLL_INTERVAL_SECONDS = 5
_REMOTE_RESTART_STATUS = r"""
import sys
sys.path.insert(0, "/opt/basaltwater")
from lib.setup_reboot import _local_restart_status
print(_local_restart_status())
"""
_REMOTE_PROXMOX_RESTART = r"""
import sys
sys.path.insert(0, "/opt/basaltwater")
from lib.setup_reboot import _restart_local_proxmox
raise SystemExit(_restart_local_proxmox())
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


def _shutdown_proxmox_guests() -> bool:
    """Wait for Proxmox's ordered graceful shutdown, never forcing guest stops."""
    try:
        result = run(
            ["pvesh", "create", "/nodes/localhost/stopall", "--force-stop", "0",
             "--timeout", "180", "--output-format", "json"],
            capture_output=True, text=True, check=False, timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout).strip() or "guest shutdown request failed")
        task = json.loads(result.stdout)
        parts = task.split(":") if isinstance(task, str) else []
        if (
            len(parts) != 9 or parts[0] != "UPID" or not validate_host(parts[1])
            or not all(re.fullmatch(r"[0-9A-Fa-f]+", field) for field in parts[2:5])
            or parts[5:7] != ["stopall", ""] or not parts[7] or parts[8]
            or any(character.isspace() for character in task)
        ):
            raise ValueError("invalid Proxmox shutdown task response")

        deadline = time.monotonic() + _GUEST_SHUTDOWN_WAIT_SECONDS
        command = ["pvenode", "task", "status", task, "--output-format", "json"]
        while (remaining := deadline - time.monotonic()) > 0:
            result = run(command, capture_output=True, text=True, check=False, timeout=min(30, remaining))
            if result.returncode != 0:
                raise RuntimeError((result.stderr or result.stdout).strip() or "could not inspect shutdown task")
            status = json.loads(result.stdout)
            if not isinstance(status, dict) or status.get("status") not in ("running", "stopped"):
                raise ValueError("invalid Proxmox shutdown task status")
            if status["status"] == "stopped":
                if status.get("exitstatus") != "OK":
                    raise RuntimeError(f"shutdown task failed: {status.get('exitstatus', 'unknown result')}")
                return True
            time.sleep(min(_POLL_INTERVAL_SECONDS, max(0, deadline - time.monotonic())))
        raise RuntimeError(f"guest shutdown did not finish within {_GUEST_SHUTDOWN_WAIT_SECONDS} seconds")
    except (CommandTimeoutError, OSError, RuntimeError, subprocess.SubprocessError, ValueError) as exc:
        print(f"Error shutting down Proxmox guests: {exc}")
        print("  Host restart not requested; some guests may already be stopped")
        return False


def _prepare_proxmox_restart(config: SetupConfig) -> bool:
    """Check health before scheduling the explicit target-side restart job."""

    from lib.proxmox_hosts import ProxmoxHost
    from lib.proxmox_maintenance import collect_local_maintenance_report, collect_maintenance_report

    try:
        report = (
            collect_local_maintenance_report() if config.host in _LOCAL_HOSTS
            else collect_maintenance_report(ProxmoxHost(
                name=config.host, address=config.host, user="root", ssh_key=config.ssh_key,
            ))
        )
        if report.healthy and not report.locked_guests and report.running_guests:
            guests = ", ".join(str(guest.vmid) for guest in report.running_guests)
            print(f"  Explicit restart will gracefully shut down Proxmox guests: {guests}")
            return True
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as exc:
        print(f"Error checking Proxmox reboot safety on {config.host}: {exc}")
        return False
    if report.reboot_safe:
        return True

    blockers = "; ".join(report.reboot_blockers()) or "maintenance checks failed"
    print(f"Error: refusing to restart Proxmox host {config.host}: {blockers}")
    return False


def _restart_local_proxmox() -> int:
    """Complete an explicitly requested restart independently of the controller."""
    from lib.proxmox_maintenance import collect_local_maintenance_report

    try:
        with maintenance_lock() as acquired:
            if not acquired:
                raise RuntimeError("setup or another maintenance job is running")
            status = _local_restart_status()
            if status == "clear":
                print("No restart required; skipping Proxmox guest shutdown")
                return 0
            if status != "needed-proxmox":
                raise RuntimeError("target is not a reboot-capable Proxmox host")
            report = collect_local_maintenance_report()
            if not report.healthy or report.locked_guests:
                raise RuntimeError("; ".join(report.reboot_blockers()) or "maintenance checks failed")
            if report.running_guests:
                guests = ", ".join(str(guest.vmid) for guest in report.running_guests)
                print(f"Gracefully shutting down Proxmox guests: {guests}", flush=True)
                if not _shutdown_proxmox_guests():
                    return 1
                # stopall can finish successfully despite a failed guest
                # shutdown, and skips HA-managed guests. Verify evacuation.
                report = collect_local_maintenance_report()
            if not report.reboot_safe:
                raise RuntimeError("; ".join(report.reboot_blockers()) or "maintenance checks failed")
            print("Proxmox maintenance checks passed; requesting host reboot", flush=True)
            result = run(
                ["/usr/bin/systemctl", "reboot", "--no-wall"],
                capture_output=True, text=True, check=False, timeout=30,
            )
            if result.returncode != 0:
                raise RuntimeError((result.stderr or result.stdout).strip() or "reboot request failed")
            return 0
    except (CommandTimeoutError, OSError, RuntimeError, subprocess.SubprocessError, ValueError) as exc:
        print(f"Error: refusing explicit Proxmox restart: {exc}")
        return 1


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


def _request_restart(config: SetupConfig, *, proxmox: bool = False) -> bool:
    command = [
        "/usr/bin/systemd-run",
        "--quiet",
        "--unit",
        f"basaltwater-setup-reboot-{secrets.token_hex(6)}",
        "--on-active=2s",
    ]
    if proxmox:
        command += [
            "--property=RuntimeMaxSec=2400",
            "/usr/bin/python3", "-c", _REMOTE_PROXMOX_RESTART,
        ]
    else:
        command += ["/usr/bin/systemctl", "reboot", "--no-wall"]
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


def _wait_for_remote_restart(
    config: SetupConfig, old_boot_id: str, *, timeout_seconds: int | None = None,
) -> bool:
    wait_seconds = _RESTART_WAIT_SECONDS if timeout_seconds is None else timeout_seconds
    deadline = time.monotonic() + wait_seconds
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
        f"within {wait_seconds} seconds"
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

    old_boot_id: Optional[str] = None
    if wait_for_restart:
        old_boot_id = _boot_id(config)
        if old_boot_id is None:
            return 1

    proxmox = status == "needed-proxmox" or config.system_type == "server_proxmox"
    if proxmox:
        if not _prepare_proxmox_restart(config):
            return 1

    print(f"  Restart required on {config.host}; requesting restart")
    requested = _request_restart(config, proxmox=True) if proxmox else _request_restart(config)
    if not requested:
        return 1
    if proxmox:
        print("  Proxmox shutdown and restart job queued; failures appear in the node's systemd journal")

    if not wait_for_restart:
        print("  Restart requested; the setup command will not wait for reconnection")
        return 0

    assert old_boot_id is not None
    print(f"  Waiting for {config.host} to boot again over SSH")
    completed = (
        _wait_for_remote_restart(
            config, old_boot_id,
            timeout_seconds=_GUEST_SHUTDOWN_WAIT_SECONDS + _RESTART_WAIT_SECONDS,
        ) if proxmox else _wait_for_remote_restart(config, old_boot_id)
    )
    if not completed:
        return 1

    print("  ✓ Restart completed; running host health checks")
    from lib.sysadmin_health import run_health

    return run_health(config.host, config.username, config.ssh_key)
