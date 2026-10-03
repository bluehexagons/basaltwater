"""Read-only Proxmox node maintenance and reboot preflight checks."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import asdict, dataclass, field
from typing import Callable, Optional

from lib.proxmox_hosts import ProxmoxHost
from lib.proxmox_manage import ContainerInfo
from lib.proxmox_memory import (
    SWAPON_STATUS_COMMAND,
    HostSwapDevice,
    parse_swapon_output,
)
from lib.ssh_utils import build_ssh_command, get_ssh_control_path, ssh_batch_mode
from lib.remote_utils import run
from lib.validation import validate_proxmox_storage_name


MIN_ROOT_FREE_BYTES = 4 * 1024 ** 3
MIN_BOOT_FREE_BYTES = 256 * 1024 ** 2
MIN_ROOT_FREE_INODES = 1024
MIN_BOOT_FREE_INODES = 128
CORE_SERVICES = (
    "pve-cluster",
    "pvedaemon",
    "pveproxy",
    "pvestatd",
    "pvescheduler",
)
_PREVIOUS_BOOT_PATTERN = (
    "oom-kill|out of memory|killed process|blocked for more than|"
    "soft lockup|hard lockup|watchdog|i/o error|timed out|timeout|"
    "nvme.*reset|ata.*error|zfs.*(error|fault)|mce|edac|"
    "hardware error|thermal"
)
CommandRunner = Callable[[ProxmoxHost, str], subprocess.CompletedProcess[str]]


@dataclass
class ProxmoxMaintenanceReport:
    """Observed node state used by audits and rolling-update safety gates."""

    host_name: str
    address: str
    node_name: str = ""
    clustered: Optional[bool] = None
    quorate: Optional[bool] = None
    service_states: dict[str, str] = field(default_factory=dict)
    active_tasks: list[str] = field(default_factory=list)
    running_guests: list[ContainerInfo] = field(default_factory=list)
    locked_guests: list[ContainerInfo] = field(default_factory=list)
    storage_states: dict[str, str] = field(default_factory=dict)
    root_free_bytes: Optional[int] = None
    boot_free_bytes: Optional[int] = None
    root_total_inodes: Optional[int] = None
    root_free_inodes: Optional[int] = None
    boot_total_inodes: Optional[int] = None
    boot_free_inodes: Optional[int] = None
    reboot_required: Optional[bool] = None
    memory_used_bytes: Optional[int] = None
    memory_total_bytes: Optional[int] = None
    swap_used_bytes: Optional[int] = None
    swap_total_bytes: Optional[int] = None
    swap_devices: list[HostSwapDevice] = field(default_factory=list)
    swappiness: Optional[int] = None
    previous_boot_available: Optional[bool] = None
    previous_boot_findings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def healthy(self) -> bool:
        """Return whether all required maintenance checks passed."""
        return not self.errors

    @property
    def reboot_safe(self) -> bool:
        """Return whether the node is healthy and has no active guests."""
        return self.healthy and not self.running_guests and not self.locked_guests

    def reboot_blockers(self) -> list[str]:
        """Return concise reasons an automatic node reboot must not proceed."""
        blockers = list(self.errors)
        if self.running_guests:
            guest_ids = ", ".join(str(guest.vmid) for guest in self.running_guests)
            blockers.append(f"running guests: {guest_ids}")
        if self.locked_guests:
            locked_ids = ", ".join(str(guest.vmid) for guest in self.locked_guests)
            blockers.append(f"locked guests: {locked_ids}")
        return blockers

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable maintenance report."""
        return {
            "host_name": self.host_name,
            "address": self.address,
            "node_name": self.node_name,
            "healthy": self.healthy,
            "reboot_safe": self.reboot_safe,
            "clustered": self.clustered,
            "quorate": self.quorate,
            "service_states": dict(self.service_states),
            "active_tasks": list(self.active_tasks),
            "running_guests": [asdict(guest) for guest in self.running_guests],
            "locked_guests": [asdict(guest) for guest in self.locked_guests],
            "storage_states": dict(self.storage_states),
            "root_free_bytes": self.root_free_bytes,
            "boot_free_bytes": self.boot_free_bytes,
            "root_total_inodes": self.root_total_inodes,
            "root_free_inodes": self.root_free_inodes,
            "boot_total_inodes": self.boot_total_inodes,
            "boot_free_inodes": self.boot_free_inodes,
            "reboot_required": self.reboot_required,
            "memory_used_bytes": self.memory_used_bytes,
            "memory_total_bytes": self.memory_total_bytes,
            "swap_used_bytes": self.swap_used_bytes,
            "swap_total_bytes": self.swap_total_bytes,
            "swap_devices": [asdict(device) for device in self.swap_devices],
            "swappiness": self.swappiness,
            "previous_boot_available": self.previous_boot_available,
            "previous_boot_findings": list(self.previous_boot_findings),
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }


def _run(host: ProxmoxHost, command: str) -> subprocess.CompletedProcess[str]:
    """Run a read-only command on a registered Proxmox host."""
    return subprocess.run(
        build_ssh_command(
            host.address,
            host.user,
            host.ssh_key,
            remote_command=command,
            batch_mode=ssh_batch_mode(),
            connect_timeout=10,
            server_alive_interval=10,
            control_path=get_ssh_control_path(
                host.address, host.user, host.ssh_key
            ),
        ),
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def _failure_detail(result: subprocess.CompletedProcess[str]) -> str:
    return (result.stderr or result.stdout or "").strip() or f"exit {result.returncode}"


def _format_task(task: dict[str, object]) -> str:
    task_type = str(task.get("type") or "task")
    task_id = str(task.get("id") or "").strip()
    user = str(task.get("user") or "").strip()
    label = f"{task_type}:{task_id}" if task_id else task_type
    return f"{label} ({user})" if user else label


def _parse_storage_status(stdout: str) -> tuple[dict[str, str], set[str]]:
    """Validate the complete node storage API response before trusting it."""
    payload = json.loads(stdout)
    if not isinstance(payload, list):
        raise ValueError("Storage response was not a JSON list")
    states: dict[str, str] = {}
    network_backups: set[str] = set()
    for entry in payload:
        if not isinstance(entry, dict):
            raise ValueError("Storage response contains an invalid entry")
        name = entry.get("storage")
        if not isinstance(name, str):
            raise ValueError("Storage response contains an invalid name")
        validate_proxmox_storage_name(name)
        if name in states:
            raise ValueError(f"Storage response repeats {name}")
        for flag in ("enabled", "active"):
            value = entry.get(flag)
            if type(value) not in (int, bool) or value not in (0, 1):
                raise ValueError(f"Storage {name} has invalid {flag} data")
        states[name] = (
            "disabled" if not entry["enabled"]
            else "active" if entry["active"] else "inactive"
        )
        # Only an exact backup-only content declaration qualifies. Missing or
        # unexpected metadata must never exempt guest disks, ISO images, or
        # snippets from the storage gate.
        if entry.get("type") in ("cifs", "nfs", "pbs") and entry.get("content") == "backup":
            network_backups.add(name)
    return states, network_backups


def _parse_guest_inventory(stdout: str, guest_type: str) -> list[ContainerInfo]:
    """Read the current node API inventory, including locks omitted by qm list."""
    payload = json.loads(stdout)
    if not isinstance(payload, list):
        raise ValueError("Guest inventory was not a JSON list")
    guests = []
    seen = set()
    for entry in payload:
        if not isinstance(entry, dict):
            raise ValueError("Guest inventory contains an invalid entry")
        vmid = entry.get("vmid")
        status = entry.get("status")
        lock = entry.get("lock")
        name = entry.get("name", "")
        if type(vmid) is not int or not 100 <= vmid <= 999999999 or vmid in seen:
            raise ValueError("Guest inventory contains an invalid or duplicate VMID")
        if status not in ("running", "stopped"):
            raise ValueError(f"Guest {vmid} has an unknown status")
        if not isinstance(name, str) or (lock is not None and not isinstance(lock, str)):
            raise ValueError(f"Guest {vmid} has invalid name or lock data")
        seen.add(vmid)
        guests.append(ContainerInfo(vmid=vmid, status=status, name=name, guest_type=guest_type, lock=lock))
    return guests


def _collect_filesystem_capacity(
    host: ProxmoxHost,
    report: ProxmoxMaintenanceReport,
    run_command: CommandRunner,
) -> None:
    """Check kernel-installation headroom, including a separate /boot mount."""
    result = run_command(host, "LC_ALL=C df -k --output=avail,itotal,iavail / /boot")
    if result.returncode != 0:
        report.errors.append(f"Could not read root and boot capacity: {_failure_detail(result)}")
        return
    try:
        lines = result.stdout.splitlines()
        if len(lines) != 3 or lines[0].split() != ["Avail", "Inodes", "IFree"]:
            raise ValueError("Incomplete filesystem capacity response")
        capacity = []
        for line in lines[1:]:
            fields = line.split()
            if len(fields) != 3 or not all(field.isascii() and field.isdecimal() for field in fields):
                raise ValueError("Invalid filesystem capacity values")
            available, total_inodes, free_inodes = map(int, fields)
            if free_inodes > total_inodes:
                raise ValueError("Free inode count exceeds total")
            capacity.append((available * 1024, total_inodes, free_inodes))
    except (TypeError, ValueError) as exc:
        report.errors.append(f"Could not parse root and boot capacity: {exc}")
        return

    report.root_free_bytes, report.root_total_inodes, report.root_free_inodes = capacity[0]
    report.boot_free_bytes, report.boot_total_inodes, report.boot_free_inodes = capacity[1]
    if report.root_free_bytes < MIN_ROOT_FREE_BYTES:
        report.errors.append("Root filesystem has less than 4 GiB free")
    if report.boot_free_bytes < MIN_BOOT_FREE_BYTES:
        report.errors.append("Boot filesystem has less than 256 MiB free")
    # Btrfs and FAT can report 0/0 because they have no fixed inode table.
    if report.root_total_inodes and report.root_free_inodes < MIN_ROOT_FREE_INODES:
        report.errors.append("Root filesystem has fewer than 1024 free inodes")
    if report.boot_total_inodes and report.boot_free_inodes < MIN_BOOT_FREE_INODES:
        report.errors.append("Boot filesystem has fewer than 128 free inodes")


def _collect_memory_diagnostics(
    host: ProxmoxHost,
    report: ProxmoxMaintenanceReport,
    run_command: CommandRunner,
) -> None:
    """Add read-only host memory and previous-boot diagnostics to an audit."""
    node_status = run_command(
        host,
        "pvesh get /nodes/$(hostname -s)/status --output-format json",
    )
    if node_status.returncode != 0:
        report.warnings.append(
            f"Could not inspect host memory: {_failure_detail(node_status)}"
        )
    else:
        try:
            status_data = json.loads(node_status.stdout or "{}")
            memory_data = status_data.get("memory") or {}
            swap_data = status_data.get("swap") or {}
            report.memory_used_bytes = int(memory_data.get("used") or 0)
            report.memory_total_bytes = int(memory_data.get("total") or 0)
            report.swap_used_bytes = int(swap_data.get("used") or 0)
            report.swap_total_bytes = int(swap_data.get("total") or 0)
        except (AttributeError, TypeError, ValueError, json.JSONDecodeError) as exc:
            report.warnings.append(f"Could not parse host memory status: {exc}")
        else:
            if (
                report.memory_total_bytes
                and report.memory_used_bytes is not None
                and report.memory_used_bytes / report.memory_total_bytes >= 0.9
            ):
                report.warnings.append("Host memory use is at least 90%")
            if (
                report.swap_total_bytes
                and report.swap_used_bytes is not None
                and report.swap_used_bytes / report.swap_total_bytes >= 0.5
            ):
                report.warnings.append("Host swap use is at least 50%")

    swap_status = run_command(host, SWAPON_STATUS_COMMAND)
    if swap_status.returncode != 0:
        report.warnings.append(
            f"Could not inspect host swap devices: {_failure_detail(swap_status)}"
        )
    else:
        report.swap_devices = parse_swapon_output(swap_status.stdout or "")
        if not report.swap_devices:
            report.warnings.append("No active host swap")
        zfs_devices = [
            device.name for device in report.swap_devices if device.zfs_backed
        ]
        if zfs_devices:
            report.errors.append(
                "ZFS zvol-backed swap can block the host under memory pressure: "
                + ", ".join(zfs_devices)
            )

    swappiness = run_command(host, "sysctl -n vm.swappiness")
    if swappiness.returncode != 0:
        report.warnings.append(
            f"Could not inspect vm.swappiness: {_failure_detail(swappiness)}"
        )
    else:
        try:
            report.swappiness = int(swappiness.stdout.strip())
        except ValueError:
            report.warnings.append("Could not parse vm.swappiness")
        else:
            if report.swappiness > 10:
                report.warnings.append(
                    f"vm.swappiness is {report.swappiness}; Proxmox host policy is 10"
                )

    boots = run_command(host, "journalctl --list-boots --no-pager")
    if boots.returncode != 0:
        report.previous_boot_available = None
        report.warnings.append(
            f"Could not inspect persistent boot journals: {_failure_detail(boots)}"
        )
    else:
        report.previous_boot_available = any(
            line.lstrip().startswith("-1 ")
            for line in boots.stdout.splitlines()
        )
        if not report.previous_boot_available:
            report.warnings.append(
                "Previous boot journal is unavailable; forced-lockup evidence may be lost"
            )

    findings = run_command(
        host,
        "journalctl -b -1 -k --no-pager -o short-monotonic "
        f"| grep -Ei '{_PREVIOUS_BOOT_PATTERN}' | tail -n 40",
    )
    if findings.stdout.strip():
        report.previous_boot_findings = [
            line.strip() for line in findings.stdout.splitlines() if line.strip()
        ]
        report.warnings.append(
            f"Previous boot kernel log has {len(report.previous_boot_findings)} "
            "possible lockup indicator(s)"
        )


def collect_maintenance_report(
    host: ProxmoxHost, *, command_runner: CommandRunner | None = None,
    allow_inactive_backup_storage: bool = False,
) -> ProxmoxMaintenanceReport:
    """Collect a read-only audit, optionally tolerating restart backup outages.

    Explicit setup restarts may stop the guest serving backup-only network
    storage. Normal audits and scheduled maintenance keep the strict default.
    """
    report = ProxmoxMaintenanceReport(host_name=host.name, address=host.address)
    run_command = command_runner or _run

    try:
        identity = run_command(host, "hostname -s")
    except (OSError, TimeoutError, subprocess.TimeoutExpired) as exc:
        report.errors.append(f"Node probe failed: {exc}")
        return report
    if identity.returncode != 0 or not identity.stdout.strip():
        report.errors.append(f"Node probe failed: {_failure_detail(identity)}")
        return report
    report.node_name = identity.stdout.strip().splitlines()[0]

    try:
        services = run_command(host, "systemctl is-active " + " ".join(CORE_SERVICES))
        if services.returncode != 0:
            report.errors.append(f"Core service probe failed: {_failure_detail(services)}")
        service_lines = [line.strip().lower() for line in services.stdout.splitlines()]
        if len(service_lines) != len(CORE_SERVICES):
            report.errors.append("Could not determine every core Proxmox service state")
        for index, service_name in enumerate(CORE_SERVICES):
            state = service_lines[index] if index < len(service_lines) else "unknown"
            report.service_states[service_name] = state
            if state != "active":
                report.errors.append(f"Core service {service_name} is {state}")

        cluster_config = run_command(host, "test -s /etc/pve/corosync.conf")
        if cluster_config.returncode == 0:
            report.clustered = True
            cluster_status = run_command(host, "pvecm status")
            if cluster_status.returncode != 0:
                report.quorate = False
                report.errors.append(
                    f"Could not read cluster status: {_failure_detail(cluster_status)}"
                )
            else:
                match = re.search(
                    r"^Quorate:\s*(Yes|No)\s*$",
                    cluster_status.stdout,
                    re.IGNORECASE | re.MULTILINE,
                )
                report.quorate = bool(match and match.group(1).lower() == "yes")
                if not report.quorate:
                    report.errors.append("Cluster is not quorate")
        elif cluster_config.returncode == 1:
            report.clustered = False
        else:
            report.errors.append(
                f"Could not determine cluster membership: {_failure_detail(cluster_config)}"
            )

        tasks = run_command(
            host,
            "pvenode task list --source active --output-format json",
        )
        if tasks.returncode != 0:
            report.errors.append(f"Could not list active tasks: {_failure_detail(tasks)}")
        else:
            try:
                task_data = json.loads(tasks.stdout)
            except (TypeError, ValueError) as exc:
                report.errors.append(f"Could not parse active task list: {exc}")
            else:
                if not isinstance(task_data, list):
                    report.errors.append("Active task response was not a JSON list")
                elif any(not isinstance(task, dict) for task in task_data):
                    report.errors.append("Active task response contains an invalid entry")
                else:
                    report.active_tasks = [_format_task(task) for task in task_data]
                    if report.active_tasks:
                        report.errors.append(
                            f"{len(report.active_tasks)} active Proxmox task(s)"
                        )

        guests: list[ContainerInfo] = []
        for endpoint, guest_type in (("lxc", "lxc"), ("qemu", "vm")):
            result = run_command(
                host, f"pvesh get /nodes/$(hostname -s)/{endpoint} --output-format json",
            )
            if result.returncode != 0:
                report.errors.append(f"Could not list {guest_type} guests: {_failure_detail(result)}")
                continue
            try:
                guests.extend(_parse_guest_inventory(result.stdout, guest_type))
            except (TypeError, ValueError) as exc:
                report.errors.append(f"Could not parse {guest_type} guest inventory: {exc}")
        if len({guest.vmid for guest in guests}) != len(guests):
            report.errors.append("Guest inventory contains duplicate VMIDs across types")
        guests.sort(key=lambda guest: guest.vmid)
        report.running_guests = [
            guest for guest in guests if guest.status.lower() == "running"
        ]
        report.locked_guests = [guest for guest in guests if guest.lock]
        if report.running_guests:
            report.warnings.append(f"{len(report.running_guests)} running guest(s)")
        if report.locked_guests:
            report.errors.append(f"{len(report.locked_guests)} locked guest(s)")

        storage = run_command(
            host, "pvesh get /nodes/$(hostname -s)/storage --output-format json",
        )
        if storage.returncode != 0:
            report.errors.append(f"Could not read storage status: {_failure_detail(storage)}")
        else:
            try:
                report.storage_states, network_backups = _parse_storage_status(storage.stdout)
            except (TypeError, ValueError) as exc:
                report.errors.append(f"Could not parse storage status: {exc}")
            else:
                if not any(state != "disabled" for state in report.storage_states.values()):
                    report.errors.append("No enabled Proxmox storage pools were reported")
                for storage_name, state in report.storage_states.items():
                    if state == "inactive":
                        if allow_inactive_backup_storage and storage_name in network_backups:
                            report.warnings.append(
                                f"Storage {storage_name} is inactive (backup-only network storage); "
                                "explicit restart can proceed, but backups remain unavailable"
                            )
                        else:
                            report.errors.append(f"Storage {storage_name} is {state}")
                if allow_inactive_backup_storage and not any(
                    state == "active" for state in report.storage_states.values()
                ):
                    report.errors.append("No active Proxmox storage pools were reported")

        _collect_filesystem_capacity(host, report, run_command)

        reboot_required = run_command(host, "test -f /var/run/reboot-required")
        if reboot_required.returncode in (0, 1):
            report.reboot_required = reboot_required.returncode == 0
            if report.reboot_required:
                report.warnings.append("Node already requires a reboot")
        else:
            report.errors.append(
                f"Could not check reboot-required state: {_failure_detail(reboot_required)}"
            )
        _collect_memory_diagnostics(host, report, run_command)
    except (OSError, TimeoutError, subprocess.TimeoutExpired) as exc:
        report.errors.append(f"Maintenance probe failed: {exc}")

    return report


def collect_local_maintenance_report(
    *, allow_inactive_backup_storage: bool = False,
) -> ProxmoxMaintenanceReport:
    """Run the same maintenance checks locally without requiring SSH to self."""
    def run_local(_host: ProxmoxHost, command: str) -> subprocess.CompletedProcess[str]:
        return run(command, check=False, capture_output=True, timeout=60)

    return collect_maintenance_report(
        ProxmoxHost(name="localhost", address="127.0.0.1"), command_runner=run_local,
        allow_inactive_backup_storage=allow_inactive_backup_storage,
    )


def _format_bytes(value: Optional[int]) -> str:
    if value is None:
        return "unknown"
    return f"{value / 1024 ** 3:.1f} GiB"


def _format_free_inodes(free: Optional[int], total: Optional[int]) -> str:
    if total is None:
        return "unknown"
    return "no fixed limit" if total == 0 else f"{free} free"


def format_maintenance_report(report: ProxmoxMaintenanceReport) -> str:
    """Return a compact operator-facing maintenance report."""
    cluster = "unknown"
    if report.clustered is False:
        cluster = "standalone"
    elif report.clustered:
        cluster = "quorate" if report.quorate else "not quorate"

    service_text = ", ".join(
        f"{name}={state}" for name, state in report.service_states.items()
    ) or "unknown"
    storage_text = ", ".join(
        f"{name}={state}" for name, state in report.storage_states.items()
    ) or "unknown"
    lines = [
        f"Proxmox maintenance audit: {report.host_name} ({report.address})",
        f"  node:           {report.node_name or 'unknown'}",
        f"  result:         {'HEALTHY' if report.healthy else 'UNHEALTHY'}",
        f"  reboot safety:  {'READY' if report.reboot_safe else 'BLOCKED'}",
        f"  cluster:        {cluster}",
        f"  services:       {service_text}",
        f"  active tasks:   {len(report.active_tasks)}",
        f"  running guests: {len(report.running_guests)}",
        f"  locked guests:  {len(report.locked_guests)}",
        f"  storage:        {storage_text}",
        f"  root free:      {_format_bytes(report.root_free_bytes)}",
        f"  boot free:      {_format_bytes(report.boot_free_bytes)}",
        f"  root inodes:    {_format_free_inodes(report.root_free_inodes, report.root_total_inodes)}",
        f"  boot inodes:    {_format_free_inodes(report.boot_free_inodes, report.boot_total_inodes)}",
        "  host memory:    "
        f"{_format_bytes(report.memory_used_bytes)} / "
        f"{_format_bytes(report.memory_total_bytes)}",
        "  host swap:      "
        f"{_format_bytes(report.swap_used_bytes)} / "
        f"{_format_bytes(report.swap_total_bytes)}",
        "  swappiness:     "
        + ("unknown" if report.swappiness is None else str(report.swappiness)),
        "  prior boot log: "
        + (
            "unknown"
            if report.previous_boot_available is None
            else ("available" if report.previous_boot_available else "unavailable")
        ),
        "  reboot needed:  "
        + (
            "unknown"
            if report.reboot_required is None
            else str(report.reboot_required).lower()
        ),
    ]
    if report.active_tasks:
        lines.append("  tasks:          " + ", ".join(report.active_tasks))
    if report.running_guests:
        guests = ", ".join(
            f"{guest.vmid} ({guest.guest_type}, {guest.name or '-'})"
            for guest in report.running_guests
        )
        lines.append(f"  guests:         {guests}")
    for finding in report.previous_boot_findings:
        lines.append(f"  PRIOR BOOT: {finding}")
    for error in report.errors:
        lines.append(f"  ERROR: {error}")
    for warning in report.warnings:
        lines.append(f"  WARNING: {warning}")
    return "\n".join(lines)


__all__ = [
    "CORE_SERVICES",
    "MIN_ROOT_FREE_BYTES",
    "MIN_BOOT_FREE_BYTES",
    "MIN_ROOT_FREE_INODES",
    "MIN_BOOT_FREE_INODES",
    "ProxmoxMaintenanceReport",
    "collect_maintenance_report",
    "collect_local_maintenance_report",
    "format_maintenance_report",
]
