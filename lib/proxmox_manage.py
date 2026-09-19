#!/usr/bin/env python3
"""Proxmox guest management against a host over SSH.

Provides helpers for:
- Listing guests (``pct list`` + ``qm list``).
- Querying status, config, and resource usage for a single guest.
- Starting, rebooting, pausing, resuming, and stopping guests.
- Inspecting and configuring guest startup order.
- Destroying guests (caller is expected to handle confirmation).
- Health-checking a guest (status + ping + optional SSH probe).
- Reconfiguring guests (CPU, memory, arbitrary ``pct``/``qm`` options).
- Resizing guest disks.
- Snapshot management (create, list, rollback, delete).
- Configuring native Proxmox notification webhooks.

All commands run via ``ssh root@<node>`` using the existing
:mod:`lib.proxmox_node` SSH helpers so behaviour stays consistent with the
provisioning path.
"""

from __future__ import annotations

import base64
import re
import shlex
import subprocess
from dataclasses import dataclass, field
from typing import Optional
import urllib.parse

from lib.proxmox_hosts import ProxmoxHost
from lib.proxmox_guest import _ssh_opts, _ssh_run
from lib.types import StrList


class ProxmoxManageError(Exception):
    """Raised when a management operation fails on the Proxmox host."""


@dataclass
class ContainerInfo:
    """One row returned from ``pct list``/``qm list`` plus optional enrichment."""

    vmid: int
    status: str
    name: str
    guest_type: str = "lxc"
    lock: Optional[str] = None
    ip: Optional[str] = None


@dataclass(frozen=True)
class GuestStats:
    """Live resource counters returned by ``pct/qm status --verbose``."""

    vmid: int
    guest_type: str
    status: str
    cpu_usage: float = 0.0
    cpu_count: int = 0
    memory_used: int = 0
    memory_total: int = 0
    swap_used: int = 0
    swap_total: int = 0
    disk_used: int = 0
    disk_total: int = 0
    disk_read: int = 0
    disk_written: int = 0
    network_in: int = 0
    network_out: int = 0
    uptime_seconds: int = 0


@dataclass(frozen=True)
class GuestAutostart:
    """Typed Proxmox guest boot and shutdown ordering settings."""

    enabled: bool
    order: Optional[int] = None
    start_delay: Optional[int] = None
    shutdown_timeout: Optional[int] = None


@dataclass
class HealthReport:
    """Result of :func:`health_check` for a single guest."""

    vmid: int
    status: str
    guest_type: Optional[str] = None
    pingable: Optional[bool] = None
    ssh_open: Optional[bool] = None
    ip: Optional[str] = None
    notes: list[str] = field(default_factory=list)

    @property
    def healthy(self) -> bool:
        if self.status != "running":
            return False
        if self.ip is None:
            return False
        # Network probes are best-effort; only fail health if explicitly False.
        if self.pingable is False:
            return False
        if self.ssh_open is False:
            return False
        return True


@dataclass
class SnapshotInfo:
    """One snapshot entry returned by ``pct listsnapshot``/``qm listsnapshot``."""

    name: str
    description: str = ""
    is_current: bool = False


@dataclass
class ProxmoxWebhookNotificationConfig:
    """Configuration for native Proxmox webhook notifications."""

    endpoint_name: str
    matcher_name: str
    url: str
    severities: list[str] = field(default_factory=list)


_GUEST_OPTION_RE = re.compile(r"^[a-z][a-z0-9-]*$")
_SNAPSHOT_NAME_RE = re.compile(r"^[a-zA-Z0-9_]{1,40}$")
_DISK_SIZE_RE = re.compile(r"^\d+[KkMmGgTt]$")
_MISSING_GUEST_PATTERNS = (
    "does not exist",
    "not exist",
    "not found",
    "no such",
    "configuration file",
)


def _validate_guest_option(name: str) -> None:
    if not _GUEST_OPTION_RE.match(name):
        raise ValueError(
            f"Invalid guest option name: {name!r}. "
            "Use lowercase letters, digits, and hyphens."
        )


DEFAULT_NOTIFICATION_ENDPOINT = "basaltwater-webhook"
DEFAULT_NOTIFICATION_MATCHER = "basaltwater-system"
DEFAULT_NOTIFICATION_SEVERITIES = ["info", "notice", "warning", "error", "unknown"]
_NOTIFICATION_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,62}$")


def _run_on_host(
    host: ProxmoxHost,
    cmd: str,
    *,
    dry_run: bool = False,
    log_cmd: Optional[str] = None,
) -> subprocess.CompletedProcess[str]:
    """Execute ``cmd`` on ``host`` over SSH, returning the completed process."""
    opts = _ssh_opts(host.ssh_key)
    return _ssh_run(host.address, host.user, opts, cmd, dry_run=dry_run, log_cmd=log_cmd)


def _looks_like_missing_guest(result: subprocess.CompletedProcess[str]) -> bool:
    text = "\n".join(filter(None, [result.stdout, result.stderr])).lower()
    return any(pattern in text for pattern in _MISSING_GUEST_PATTERNS)


def _parse_status_output(stdout: str) -> str:
    out = (stdout or "").strip()
    if ":" in out:
        return out.split(":", 1)[1].strip() or "unknown"
    return out or "unknown"


def _run_guest_command(
    host: ProxmoxHost,
    vmid: int,
    pct_cmd: str,
    qm_cmd: str,
    *,
    dry_run: bool = False,
    log_cmd: Optional[str] = None,
) -> tuple[subprocess.CompletedProcess[str], str]:
    """Run a pct/qm command pair, retrying with qm when pct reports no guest."""
    if dry_run:
        display = log_cmd if log_cmd is not None else f"{pct_cmd} || {qm_cmd}"
        result = _run_on_host(host, pct_cmd, dry_run=True, log_cmd=display)
        return result, "unknown"

    pct_result = _run_on_host(host, pct_cmd, log_cmd=log_cmd)
    if pct_result.returncode == 0:
        return pct_result, "lxc"
    if not _looks_like_missing_guest(pct_result):
        return pct_result, "lxc"
    qm_result = _run_on_host(host, qm_cmd, log_cmd=log_cmd)
    return qm_result, "vm"


def _get_guest_status(host: ProxmoxHost, vmid: int) -> tuple[str, str]:
    """Return ``(guest_type, status)`` for ``vmid``."""
    result, guest_type = _run_guest_command(
        host,
        vmid,
        f"pct status {int(vmid)}",
        f"qm status {int(vmid)}",
    )
    if result.returncode != 0:
        tool = "qm" if guest_type == "vm" else "pct"
        raise ProxmoxManageError(
            f"{tool} status {vmid} failed on {host.address}: "
            f"{(result.stderr or result.stdout or '').strip() or 'unknown error'}"
        )
    return guest_type, _parse_status_output(result.stdout)


def _parse_pct_list(stdout: str) -> list[ContainerInfo]:
    """Parse ``pct list`` output into :class:`ContainerInfo` rows."""
    rows: list[ContainerInfo] = []
    lines = [ln for ln in (stdout or "").splitlines() if ln.strip()]
    if not lines:
        return rows
    header = lines[0].split()
    if not header or header[0].upper() != "VMID":
        # Some pct versions emit no header; assume all lines are data.
        data_lines = lines
    else:
        data_lines = lines[1:]
    for line in data_lines:
        parts = line.split()
        if not parts:
            continue
        try:
            vmid = int(parts[0])
        except ValueError:
            continue
        status = parts[1] if len(parts) >= 2 else "unknown"
        # pct list columns: VMID Status [Lock] Name
        if len(parts) == 2:
            name = ""
            lock: Optional[str] = None
        elif len(parts) == 3:
            name = parts[2]
            lock = None
        else:
            # Heuristic: if the third token is a recognised lock keyword treat
            # it as the Lock column; otherwise treat everything after status as
            # the name (names can contain spaces in rare cases via display).
            third = parts[2]
            if third in {
                "backup", "create", "destroyed", "disk", "fstrim",
                "migrate", "mounted", "rollback", "snapshot",
                "snapshot-delete",
            }:
                lock = third
                name = " ".join(parts[3:])
            else:
                lock = None
                name = " ".join(parts[2:])
        rows.append(ContainerInfo(vmid=vmid, status=status, name=name, guest_type="lxc", lock=lock))
    return rows


def _parse_qm_list(stdout: str) -> list[ContainerInfo]:
    """Parse ``qm list`` output into :class:`ContainerInfo` rows."""
    rows: list[ContainerInfo] = []
    lines = [ln for ln in (stdout or "").splitlines() if ln.strip()]
    if not lines:
        return rows
    header = lines[0].split()
    if len(header) >= 3 and header[0].upper() == "VMID" and header[1].upper() == "NAME":
        data_lines = lines[1:]
    elif header and header[0].isdigit():
        data_lines = lines
    else:
        return rows
    for line in data_lines:
        parts = line.split()
        if len(parts) < 3:
            continue
        try:
            vmid = int(parts[0])
        except ValueError:
            continue
        name = parts[1]
        status = parts[2]
        rows.append(ContainerInfo(vmid=vmid, status=status, name=name, guest_type="vm"))
    return rows


def list_containers(
    host: ProxmoxHost, *, dry_run: bool = False
) -> list[ContainerInfo]:
    """Return Proxmox guests on ``host`` (sorted by VMID)."""
    if dry_run:
        return []
    pct_result = _run_on_host(host, "pct list")
    if pct_result.returncode != 0:
        raise ProxmoxManageError(
            f"pct list failed on {host.address}: "
            f"{(pct_result.stderr or pct_result.stdout or '').strip() or 'no output'}"
        )
    qm_result = _run_on_host(host, "qm list")
    if qm_result.returncode != 0:
        raise ProxmoxManageError(
            f"qm list failed on {host.address}: "
            f"{(qm_result.stderr or qm_result.stdout or '').strip() or 'no output'}"
        )
    rows_by_vmid: dict[int, ContainerInfo] = {
        row.vmid: row for row in _parse_pct_list(pct_result.stdout)
    }
    for row in _parse_qm_list(qm_result.stdout):
        rows_by_vmid[row.vmid] = row
    rows = list(rows_by_vmid.values())
    rows.sort(key=lambda r: r.vmid)
    return rows


_NET0_IP_RE = re.compile(r"\bip=([^,\s]+)")


def get_container_ip(
    host: ProxmoxHost, vmid: int, *, dry_run: bool = False
) -> Optional[str]:
    """Return the configured IPv4 address from guest config (no CIDR)."""
    if dry_run:
        return None
    result, _guest_type = _run_guest_command(
        host,
        vmid,
        f"pct config {int(vmid)}",
        f"qm config {int(vmid)}",
    )
    if result.returncode != 0:
        return None
    for line in (result.stdout or "").splitlines():
        if not (line.startswith("net0:") or line.startswith("ipconfig0:")):
            continue
        match = _NET0_IP_RE.search(line)
        if not match:
            continue
        return match.group(1).split("/", 1)[0].strip() or None
    return None


def get_container_status(host: ProxmoxHost, vmid: int) -> str:
    """Return the textual status from ``pct status``/``qm status``."""
    _guest_type, status = _get_guest_status(host, vmid)
    return status


def _nonnegative_number(data: dict[str, str], key: str) -> int:
    try:
        return max(0, int(float(data.get(key, "0"))))
    except (TypeError, ValueError):
        return 0


def get_guest_stats(host: ProxmoxHost, vmid: int) -> GuestStats:
    """Return live CPU, memory, disk, network, and uptime counters."""
    result, guest_type = _run_guest_command(
        host,
        vmid,
        f"pct status {int(vmid)} --verbose",
        f"qm status {int(vmid)} --verbose",
    )
    if result.returncode != 0:
        tool = "qm" if guest_type == "vm" else "pct"
        raise ProxmoxManageError(
            f"{tool} status {vmid} --verbose failed on {host.address}: "
            f"{(result.stderr or result.stdout or '').strip() or 'unknown error'}"
        )
    values: dict[str, str] = {}
    for line in (result.stdout or "").splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        values[key.strip().lower()] = value.strip()
    try:
        cpu_usage = max(0.0, float(values.get("cpu", "0")))
    except (TypeError, ValueError):
        cpu_usage = 0.0
    return GuestStats(
        vmid=int(vmid),
        guest_type=guest_type,
        status=values.get("status", "unknown"),
        cpu_usage=cpu_usage,
        cpu_count=_nonnegative_number(values, "cpus"),
        memory_used=_nonnegative_number(values, "mem"),
        memory_total=_nonnegative_number(values, "maxmem"),
        swap_used=_nonnegative_number(values, "swap"),
        swap_total=_nonnegative_number(values, "maxswap"),
        disk_used=_nonnegative_number(values, "disk"),
        disk_total=_nonnegative_number(values, "maxdisk"),
        disk_read=_nonnegative_number(values, "diskread"),
        disk_written=_nonnegative_number(values, "diskwrite"),
        network_in=_nonnegative_number(values, "netin"),
        network_out=_nonnegative_number(values, "netout"),
        uptime_seconds=_nonnegative_number(values, "uptime"),
    )


def start_container(host: ProxmoxHost, vmid: int) -> None:
    """Start a guest on ``host``; idempotent if already running."""
    guest_type, current = _get_guest_status(host, vmid)
    if current == "running":
        return
    cmd = f"qm start {int(vmid)}" if guest_type == "vm" else f"pct start {int(vmid)}"
    result = _run_on_host(host, cmd)
    if result.returncode != 0:
        tool = "qm" if guest_type == "vm" else "pct"
        raise ProxmoxManageError(
            f"{tool} start {vmid} failed on {host.address}: "
            f"{(result.stderr or '').strip() or 'unknown error'}"
        )


def stop_container(
    host: ProxmoxHost,
    vmid: int,
    *,
    force: bool = False,
    timeout: Optional[int] = None,
) -> None:
    """Stop a guest.

    By default uses guest shutdown (graceful). When ``force`` is True it uses
    the immediate stop command. ``timeout`` is passed to graceful shutdown.
    Idempotent if already stopped.
    """
    if timeout is not None and timeout < 0:
        raise ValueError("timeout must be >= 0")
    guest_type, current = _get_guest_status(host, vmid)
    if current == "stopped":
        return
    if guest_type == "vm":
        cmd = f"qm stop {int(vmid)}" if force else f"qm shutdown {int(vmid)}"
    else:
        cmd = f"pct stop {int(vmid)}" if force else f"pct shutdown {int(vmid)}"
    if timeout is not None and not force:
        cmd += f" --timeout {timeout}"
    result = _run_on_host(host, cmd)
    if result.returncode != 0:
        raise ProxmoxManageError(
            f"{cmd} failed on {host.address}: "
            f"{(result.stderr or '').strip() or 'unknown error'}"
        )


def reboot_guest(
    host: ProxmoxHost,
    vmid: int,
    *,
    timeout: Optional[int] = None,
) -> None:
    """Cleanly reboot a running guest and apply pending configuration."""
    if timeout is not None and timeout < 0:
        raise ValueError("timeout must be >= 0")
    guest_type, current = _get_guest_status(host, vmid)
    if current == "stopped":
        raise ProxmoxManageError(
            f"Guest {vmid} is stopped; use the start command instead."
        )
    tool = "qm" if guest_type == "vm" else "pct"
    cmd = f"{tool} reboot {int(vmid)}"
    if timeout is not None:
        cmd += f" --timeout {timeout}"
    result = _run_on_host(host, cmd)
    if result.returncode != 0:
        raise ProxmoxManageError(
            f"{cmd} failed on {host.address}: "
            f"{(result.stderr or result.stdout or '').strip() or 'unknown error'}"
        )


def suspend_guest(host: ProxmoxHost, vmid: int) -> None:
    """Pause a running guest, using ``pct suspend`` or ``qm suspend``."""
    guest_type, current = _get_guest_status(host, vmid)
    if guest_type == "vm" and current in {"paused", "suspended"}:
        return
    if current == "stopped":
        raise ProxmoxManageError(
            f"Guest {vmid} is stopped; start it before pausing it."
        )
    cmd = f"qm suspend {int(vmid)}" if guest_type == "vm" else f"pct suspend {int(vmid)}"
    result = _run_on_host(host, cmd)
    if result.returncode != 0:
        raise ProxmoxManageError(
            f"{cmd} failed on {host.address}: "
            f"{(result.stderr or result.stdout or '').strip() or 'unknown error'}"
        )


def resume_guest(host: ProxmoxHost, vmid: int) -> None:
    """Resume a paused guest, using ``pct resume`` or ``qm resume``."""
    guest_type, current = _get_guest_status(host, vmid)
    if guest_type == "vm" and current == "running":
        return
    if current == "stopped":
        raise ProxmoxManageError(
            f"Guest {vmid} is stopped; use the start command instead."
        )
    cmd = f"qm resume {int(vmid)}" if guest_type == "vm" else f"pct resume {int(vmid)}"
    result = _run_on_host(host, cmd)
    if result.returncode != 0:
        raise ProxmoxManageError(
            f"{cmd} failed on {host.address}: "
            f"{(result.stderr or result.stdout or '').strip() or 'unknown error'}"
        )


def destroy_container(
    host: ProxmoxHost,
    vmid: int,
    *,
    purge: bool = True,
    force: bool = False,
) -> None:
    """Destroy a guest.

    The guest is stopped first if needed. ``purge`` removes the guest
    from backup/HA configs as well. Pass ``force`` to skip stop verification
    and use ``pct stop`` instead of shutdown.
    """
    # Status must be known before choosing the provider-specific destructive
    # command.  Treating an unavailable host as an LXC guest could otherwise
    # turn a transient SSH failure into an unsafe `pct destroy` attempt.
    guest_type, current = _get_guest_status(host, vmid)
    if current == "running":
        stop_container(host, vmid, force=force)
    flags: StrList = []
    if guest_type == "vm":
        if purge:
            flags.extend(["--purge", "1", "--destroy-unreferenced-disks", "1"])
        cmd = shlex.join(["qm", "destroy", str(int(vmid)), *flags])
    else:
        if purge:
            flags.append("--purge")
        if force:
            flags.append("--force")
        cmd = f"pct destroy {int(vmid)}"
        if flags:
            cmd += " " + " ".join(flags)
    result = _run_on_host(host, cmd)
    if result.returncode != 0:
        tool = "qm" if guest_type == "vm" else "pct"
        raise ProxmoxManageError(
            f"{tool} destroy {vmid} failed on {host.address}: "
            f"{(result.stderr or '').strip() or 'unknown error'}"
        )


def get_container_config(
    host: ProxmoxHost, vmid: int, *, dry_run: bool = False
) -> dict[str, str]:
    """Return the current guest configuration for ``vmid`` as a key/value dict."""
    if dry_run:
        return {}
    result, guest_type = _run_guest_command(
        host,
        vmid,
        f"pct config {int(vmid)}",
        f"qm config {int(vmid)}",
    )
    if result.returncode != 0:
        tool = "qm" if guest_type == "vm" else "pct"
        raise ProxmoxManageError(
            f"{tool} config {vmid} failed on {host.address}: "
            f"{(result.stderr or result.stdout or '').strip() or 'no output'}"
        )
    config: dict[str, str] = {}
    for line in (result.stdout or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            key, _, value = line.partition(":")
            config[key.strip()] = value.strip()
    return config


def _parse_startup_config(value: str) -> dict[str, int]:
    parsed: dict[str, int] = {}
    for item in value.split(","):
        key, separator, raw_value = item.strip().partition("=")
        if separator and key in {"order", "up", "down"}:
            try:
                parsed[key] = max(0, int(raw_value))
            except ValueError:
                continue
    return parsed


def get_guest_autostart(host: ProxmoxHost, vmid: int) -> GuestAutostart:
    """Return typed guest start-at-boot and ordering settings."""
    config = get_container_config(host, vmid)
    startup = _parse_startup_config(config.get("startup", ""))
    enabled = config.get("onboot", "0").strip().lower() in {
        "1",
        "on",
        "true",
        "yes",
    }
    return GuestAutostart(
        enabled=enabled,
        order=startup.get("order"),
        start_delay=startup.get("up"),
        shutdown_timeout=startup.get("down"),
    )


def configure_guest_autostart(
    host: ProxmoxHost,
    vmid: int,
    *,
    enabled: bool,
    order: Optional[int] = None,
    start_delay: Optional[int] = None,
    shutdown_timeout: Optional[int] = None,
) -> None:
    """Set typed start-at-boot and optional staggered startup settings."""
    schedule = {
        "order": order,
        "up": start_delay,
        "down": shutdown_timeout,
    }
    if any(value is not None and value < 0 for value in schedule.values()):
        raise ValueError("autostart order and delays must be >= 0")
    if not enabled and any(value is not None for value in schedule.values()):
        raise ValueError("autostart order and delays require enabled=True")

    options = {"onboot": "1" if enabled else "0"}
    if any(value is not None for value in schedule.values()):
        current = get_guest_autostart(host, vmid)
        merged = {
            "order": current.order,
            "up": current.start_delay,
            "down": current.shutdown_timeout,
        }
        for key, value in schedule.items():
            if value is not None:
                merged[key] = value
        startup = ",".join(
            f"{key}={merged[key]}"
            for key in ("order", "up", "down")
            if merged[key] is not None
        )
        options["startup"] = startup
    reconfigure_container(host, vmid, options)


def get_container_pending(
    host: ProxmoxHost, vmid: int, *, dry_run: bool = False
) -> dict[str, str]:
    """Return pending (unapplied) configuration changes for ``vmid``.

    Returns a dict of option -> new-value for options that require a guest
    restart to take effect.
    """
    if dry_run:
        return {}
    result, guest_type = _run_guest_command(
        host,
        vmid,
        f"pct pending {int(vmid)}",
        f"qm pending {int(vmid)}",
    )
    if result.returncode != 0:
        tool = "qm" if guest_type == "vm" else "pct"
        raise ProxmoxManageError(
            f"{tool} pending {vmid} failed on {host.address}: "
            f"{(result.stderr or result.stdout or '').strip() or 'no output'}"
        )
    pending: dict[str, str] = {}
    for line in (result.stdout or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            key, _, value = line.partition(":")
            pending[key.strip()] = value.strip()
    return pending


def reconfigure_container(
    host: ProxmoxHost,
    vmid: int,
    options: dict[str, str],
    *,
    dry_run: bool = False,
) -> None:
    """Apply ``pct set``/``qm set`` options to ``vmid`` on ``host``.

    ``options`` maps option names (e.g. ``"cores"``) to string values.
    Changes that require a guest restart are queued as pending by Proxmox.
    """
    if not options:
        return
    for name in options:
        _validate_guest_option(name)
    pct_parts: list[str] = ["pct", "set", str(int(vmid))]
    qm_parts: list[str] = ["qm", "set", str(int(vmid))]
    for name, value in options.items():
        pct_parts.extend([f"--{name}", value])
        qm_parts.extend([f"--{name}", value])
    pct_cmd = shlex.join(pct_parts)
    qm_cmd = shlex.join(qm_parts)
    result, guest_type = _run_guest_command(
        host,
        vmid,
        pct_cmd,
        qm_cmd,
        dry_run=dry_run,
    )
    if result.returncode != 0:
        tool = "qm" if guest_type == "vm" else "pct"
        raise ProxmoxManageError(
            f"{tool} set {vmid} failed on {host.address}: "
            f"{(result.stderr or result.stdout or '').strip() or 'unknown error'}"
        )


def modify_container(
    host: ProxmoxHost,
    vmid: int,
    *,
    cores: Optional[int] = None,
    memory_mb: Optional[int] = None,
    dry_run: bool = False,
) -> None:
    """Change CPU cores and/or memory allocation for ``vmid`` on ``host``.

    ``cores`` sets the number of vCPU cores. ``memory_mb`` sets RAM in
    mebibytes (1 GiB = 1024 MiB). Both changes may require a guest
    restart to take effect on a running guest.
    """
    options: dict[str, str] = {}
    if cores is not None:
        if cores < 1:
            raise ValueError(f"cores must be >= 1, got {cores}")
        options["cores"] = str(cores)
    if memory_mb is not None:
        if memory_mb < 16:
            raise ValueError(f"memory_mb must be >= 16, got {memory_mb}")
        options["memory"] = str(memory_mb)
    if not options:
        raise ValueError("At least one of cores or memory_mb must be provided")
    reconfigure_container(host, vmid, options, dry_run=dry_run)


def resize_container_disk(
    host: ProxmoxHost,
    vmid: int,
    volume: str,
    size: str,
    *,
    dry_run: bool = False,
) -> None:
    """Increase a guest disk volume to ``size`` using ``pct resize``/``qm resize``.

    ``volume`` is the volume name (e.g. ``"rootfs"``). ``size`` is the new
    absolute size with a unit suffix, e.g. ``"20G"``. Proxmox only supports
    increasing disk size.
    """
    _validate_guest_option(volume)
    if not _DISK_SIZE_RE.match(size):
        raise ValueError(
            f"Invalid disk size: {size!r}. Use a positive integer followed by "
            "K, M, G, or T (e.g. '20G')."
        )
    pct_cmd = shlex.join(["pct", "resize", str(int(vmid)), volume, size])
    qm_cmd = shlex.join(["qm", "resize", str(int(vmid)), volume, size])
    result, guest_type = _run_guest_command(
        host,
        vmid,
        pct_cmd,
        qm_cmd,
        dry_run=dry_run,
    )
    if result.returncode != 0:
        tool = "qm" if guest_type == "vm" else "pct"
        raise ProxmoxManageError(
            f"{tool} resize {vmid} {volume} {size} failed on {host.address}: "
            f"{(result.stderr or result.stdout or '').strip() or 'unknown error'}"
        )


def unlock_guest(
    host: ProxmoxHost,
    vmid: int,
    *,
    dry_run: bool = False,
) -> None:
    """Remove a management lock from ``vmid`` on ``host``.

    Proxmox sets a lock during backup, migration, and other operations; if
    a job aborts mid-run the lock can get stuck. ``pct unlock``/``qm unlock``
    clears it.
    """
    result, guest_type = _run_guest_command(
        host,
        vmid,
        shlex.join(["pct", "unlock", str(int(vmid))]),
        shlex.join(["qm", "unlock", str(int(vmid))]),
        dry_run=dry_run,
    )
    if result.returncode != 0:
        tool = "qm" if guest_type == "vm" else "pct"
        raise ProxmoxManageError(
            f"{tool} unlock {vmid} failed on {host.address}: "
            f"{(result.stderr or result.stdout or '').strip() or 'unknown error'}"
        )


def _validate_snapshot_name(name: str) -> None:
    if not _SNAPSHOT_NAME_RE.match(name):
        raise ValueError(
            f"Invalid snapshot name: {name!r}. "
            "Use letters, digits, and underscores only (1–40 characters)."
        )


def _parse_listsnapshot(stdout: str) -> list[SnapshotInfo]:
    """Parse ``pct listsnapshot``/``qm listsnapshot`` output into snapshot rows."""
    snapshots: list[SnapshotInfo] = []
    for line in (stdout or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # Both pct and qm prefix the current state with "->" or "->".
        is_current = stripped.startswith("->")
        text = stripped.lstrip("-> ").strip()
        parts = text.split(None, 1)
        if not parts:
            continue
        snap_name = parts[0]
        if snap_name.lower() in {"name", "snapname"}:
            continue
        description = parts[1].strip() if len(parts) > 1 else ""
        snapshots.append(
            SnapshotInfo(name=snap_name, description=description, is_current=is_current)
        )
    return snapshots


def list_snapshots(
    host: ProxmoxHost,
    vmid: int,
    *,
    dry_run: bool = False,
) -> list[SnapshotInfo]:
    """Return snapshots for ``vmid`` on ``host`` (sorted by name)."""
    if dry_run:
        return []
    result, _guest_type = _run_guest_command(
        host,
        vmid,
        f"pct listsnapshot {int(vmid)}",
        f"qm listsnapshot {int(vmid)}",
    )
    if result.returncode != 0:
        raise ProxmoxManageError(
            f"listsnapshot {vmid} failed on {host.address}: "
            f"{(result.stderr or result.stdout or '').strip() or 'unknown error'}"
        )
    return sorted(_parse_listsnapshot(result.stdout), key=lambda s: s.name)


def snapshot_guest(
    host: ProxmoxHost,
    vmid: int,
    name: str,
    *,
    description: str = "",
    dry_run: bool = False,
) -> None:
    """Create a snapshot named ``name`` for ``vmid`` on ``host``."""
    _validate_snapshot_name(name)
    pct_parts = ["pct", "snapshot", str(int(vmid)), name]
    qm_parts = ["qm", "snapshot", str(int(vmid)), name]
    if description:
        pct_parts += ["--description", description]
        qm_parts += ["--description", description]
    result, guest_type = _run_guest_command(
        host,
        vmid,
        shlex.join(pct_parts),
        shlex.join(qm_parts),
        dry_run=dry_run,
    )
    if result.returncode != 0:
        tool = "qm" if guest_type == "vm" else "pct"
        raise ProxmoxManageError(
            f"{tool} snapshot {vmid} {name!r} failed on {host.address}: "
            f"{(result.stderr or result.stdout or '').strip() or 'unknown error'}"
        )


def rollback_guest(
    host: ProxmoxHost,
    vmid: int,
    name: str,
    *,
    dry_run: bool = False,
) -> None:
    """Roll back ``vmid`` to snapshot ``name`` on ``host``."""
    _validate_snapshot_name(name)
    result, guest_type = _run_guest_command(
        host,
        vmid,
        shlex.join(["pct", "rollback", str(int(vmid)), name]),
        shlex.join(["qm", "rollback", str(int(vmid)), name]),
        dry_run=dry_run,
    )
    if result.returncode != 0:
        tool = "qm" if guest_type == "vm" else "pct"
        raise ProxmoxManageError(
            f"{tool} rollback {vmid} {name!r} failed on {host.address}: "
            f"{(result.stderr or result.stdout or '').strip() or 'unknown error'}"
        )


def delete_snapshot(
    host: ProxmoxHost,
    vmid: int,
    name: str,
    *,
    dry_run: bool = False,
) -> None:
    """Delete snapshot ``name`` for ``vmid`` on ``host``."""
    _validate_snapshot_name(name)
    result, guest_type = _run_guest_command(
        host,
        vmid,
        shlex.join(["pct", "delsnapshot", str(int(vmid)), name]),
        shlex.join(["qm", "delsnapshot", str(int(vmid)), name]),
        dry_run=dry_run,
    )
    if result.returncode != 0:
        tool = "qm" if guest_type == "vm" else "pct"
        raise ProxmoxManageError(
            f"{tool} delsnapshot {vmid} {name!r} failed on {host.address}: "
            f"{(result.stderr or result.stdout or '').strip() or 'unknown error'}"
        )


def install_webhook_notifications(
    host: ProxmoxHost,
    url: str,
    *,
    endpoint_name: str = DEFAULT_NOTIFICATION_ENDPOINT,
    matcher_name: str = DEFAULT_NOTIFICATION_MATCHER,
    severities: Optional[list[str]] = None,
    send_test: bool = False,
    dry_run: bool = False,
) -> ProxmoxWebhookNotificationConfig:
    """Configure Proxmox's native webhook notifier for system notifications.

    This uses Proxmox VE's notification API through ``pvesh``. It creates or
    updates a webhook endpoint with a JSON body compatible with basaltwater'
    notification payload shape, then creates or updates a matcher that routes
    matching severities to that endpoint. No local hook script is installed.
    """
    config = _validate_webhook_config(
        url=url,
        endpoint_name=endpoint_name,
        matcher_name=matcher_name,
        severities=severities,
    )
    commands = _build_webhook_notification_commands(config)
    redacted = _build_webhook_notification_commands(
        ProxmoxWebhookNotificationConfig(
            endpoint_name=config.endpoint_name,
            matcher_name=config.matcher_name,
            url=_redact_url(config.url),
            severities=config.severities,
        )
    )
    for command, display in zip(commands, redacted):
        result = _run_on_host(host, command, dry_run=dry_run, log_cmd=display)
        if result.returncode != 0:
            raise ProxmoxManageError(
                f"Proxmox notification command failed on {host.address}: "
                f"{(result.stderr or result.stdout or '').strip() or 'unknown error'}"
            )
    if send_test:
        send_webhook_test_notification(
            host,
            config.endpoint_name,
            dry_run=dry_run,
        )
    return config


def send_webhook_test_notification(
    host: ProxmoxHost,
    endpoint_name: str = DEFAULT_NOTIFICATION_ENDPOINT,
    *,
    dry_run: bool = False,
) -> None:
    """Ask Proxmox to send a native test notification to a webhook endpoint."""
    _validate_notification_id(endpoint_name, "endpoint name")
    cmd = shlex.join([
        "pvesh", "create", f"/cluster/notifications/targets/{endpoint_name}/test",
    ])
    result = _run_on_host(host, cmd, dry_run=dry_run)
    if result.returncode != 0:
        raise ProxmoxManageError(
            f"Proxmox test notification failed on {host.address}: "
            f"{(result.stderr or result.stdout or '').strip() or 'unknown error'}"
        )


def _validate_webhook_config(
    *,
    url: str,
    endpoint_name: str,
    matcher_name: str,
    severities: Optional[list[str]],
) -> ProxmoxWebhookNotificationConfig:
    _validate_notification_id(endpoint_name, "endpoint name")
    _validate_notification_id(matcher_name, "matcher name")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"Invalid webhook URL: {url}")

    selected = severities or list(DEFAULT_NOTIFICATION_SEVERITIES)
    allowed = set(DEFAULT_NOTIFICATION_SEVERITIES)
    bad = [sev for sev in selected if sev not in allowed]
    if bad:
        raise ValueError(f"Invalid Proxmox notification severities: {', '.join(bad)}")

    return ProxmoxWebhookNotificationConfig(
        endpoint_name=endpoint_name,
        matcher_name=matcher_name,
        url=url,
        severities=selected,
    )


def _validate_notification_id(value: str, label: str) -> None:
    if not _NOTIFICATION_ID_RE.match(value):
        raise ValueError(
            f"Invalid Proxmox notification {label}: {value!r}. "
            "Use a letter followed by letters, numbers, '_' or '-' (max 63 chars)."
        )


def _build_webhook_notification_commands(
    config: ProxmoxWebhookNotificationConfig,
) -> list[str]:
    endpoint_path = f"/cluster/notifications/endpoints/webhook/{config.endpoint_name}"
    matcher_path = f"/cluster/notifications/matchers/{config.matcher_name}"

    endpoint_create = shlex.join([
        "pvesh", "create", "/cluster/notifications/endpoints/webhook",
        "--name", config.endpoint_name,
        "--url", config.url,
        "--method", "post",
        "--header", _pve_property("Content-Type", "application/json"),
        "--body", _base64(_webhook_body_template()),
        "--comment", "basaltwater Proxmox system notifications",
    ])
    endpoint_set = shlex.join([
        "pvesh", "set", endpoint_path,
        "--url", config.url,
        "--method", "post",
        "--header", _pve_property("Content-Type", "application/json"),
        "--body", _base64(_webhook_body_template()),
        "--comment", "basaltwater Proxmox system notifications",
    ])

    matcher_args = [
        "--target", config.endpoint_name,
        "--comment", "Route Proxmox system notifications to basaltwater",
    ]
    for severity in config.severities:
        matcher_args.extend(["--match-severity", severity])
    matcher_create = shlex.join([
        "pvesh", "create", "/cluster/notifications/matchers",
        "--name", config.matcher_name,
        *matcher_args,
    ])
    matcher_set = shlex.join([
        "pvesh", "set", matcher_path,
        *matcher_args,
    ])

    return [
        _upsert_pvesh_command(endpoint_path, endpoint_set, endpoint_create),
        _upsert_pvesh_command(matcher_path, matcher_set, matcher_create),
    ]


def _upsert_pvesh_command(path: str, set_cmd: str, create_cmd: str) -> str:
    return (
        f"if pvesh get {shlex.quote(path)} >/dev/null 2>&1; "
        f"then {set_cmd}; else {create_cmd}; fi"
    )


def _webhook_body_template() -> str:
    """Return Proxmox Handlebars JSON body for basaltwater-style webhooks."""
    return """{
  "subject": "Proxmox: {{ escape title }}",
  "job": "proxmox",
  "status": "{{ severity }}",
  "message": "{{ escape message }}",
  "details": "severity={{ severity }}\\ntype={{ fields.type }}\\nfields={{ json fields }}",
  "hostname": "{{ fields.hostname }}"
}"""


def _pve_property(name: str, value: str) -> str:
    return f"name={name},value={_base64(value)}"


def _base64(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def _redact_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if not parsed.netloc:
        return "<redacted-url>"
    return urllib.parse.urlunparse((
        parsed.scheme,
        parsed.netloc,
        parsed.path,
        "",
        "<redacted-query>" if parsed.query else "",
        "",
    ))


def health_check(
    host: ProxmoxHost,
    vmid: int,
    *,
    probe_ssh: bool = True,
    timeout: int = 3,
) -> HealthReport:
    """Run a best-effort health check against ``vmid`` on ``host``.

    Combines guest status with a ping from the host and (optionally) a
    TCP probe on port 22. Network probes are recorded but never raise — the
    Proxmox host occasionally lacks ``ping`` or the guest may be on an
    isolated bridge.
    """
    report = HealthReport(vmid=vmid, status="unknown")
    try:
        report.guest_type, report.status = _get_guest_status(host, vmid)
    except ProxmoxManageError as exc:
        report.notes.append(str(exc))
        return report

    ip = get_container_ip(host, vmid)
    report.ip = ip
    if not ip:
        report.notes.append("No IPv4 address configured on net0")
        return report

    if report.status != "running":
        report.notes.append(f"Guest is not running (status={report.status})")
        return report

    ping_cmd = (
        f"ping -c 1 -W {int(timeout)} {shlex.quote(ip)} >/dev/null 2>&1 "
        f"&& echo OK || echo FAIL"
    )
    ping_result = _run_on_host(host, ping_cmd)
    if ping_result.returncode == 0:
        report.pingable = "OK" in (ping_result.stdout or "")
    else:
        report.notes.append("ping probe could not be executed")

    if probe_ssh:
        ssh_cmd = (
            f"timeout {int(timeout)} bash -c "
            f"'</dev/tcp/{shlex.quote(ip)}/22' && echo OK || echo FAIL"
        )
        ssh_result = _run_on_host(host, ssh_cmd)
        if ssh_result.returncode == 0:
            report.ssh_open = "OK" in (ssh_result.stdout or "")
        else:
            report.notes.append("SSH probe could not be executed")

    return report
