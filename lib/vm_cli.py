"""Provider-neutral virtual-machine command surface."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from typing import Optional

from lib.cache import load_all_setup_commands
from lib.config import SetupConfig
from lib.proxmox_backup import BackupInfo, ProxmoxBackupError, list_backups
from lib.proxmox_guest import _build_guest_hostname
from lib.proxmox_hosts import ProxmoxHost, find_proxmox_host
from lib.proxmox_manage import (
    ContainerInfo,
    GuestStats,
    HealthReport,
    ProxmoxManageError,
    SnapshotInfo,
    configure_guest_autostart,
    destroy_container,
    get_container_config,
    get_container_ip,
    get_container_status,
    get_guest_autostart,
    get_guest_stats,
    health_check,
    list_containers,
    list_snapshots,
    reboot_guest,
    resume_guest,
    start_container,
    stop_container,
    suspend_guest,
)
from lib.vm_models import VMAutostart, VMHealth, VMRecord, VMStats, envelope


@dataclass(frozen=True)
class _ResolvedVM:
    """A provider VM selected directly or through a saved local setup name."""

    host: ProxmoxHost
    guest: ContainerInfo
    local_name: Optional[str] = None


def add_vm_subparser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    """Register the provider-neutral ``vm`` command tree."""

    parser = subparsers.add_parser(
        "vm",
        help="Inspect and manage virtual machines through a provider-neutral API",
        description=(
            "Provider-neutral guest operations. The first provider is Proxmox; "
            "provider-specific host administration remains under 'proxmox'."
        ),
    )
    parser.add_argument(
        "--workspace",
        help="Workspace root for saved setups, credentials, known_hosts, and history",
    )
    commands = parser.add_subparsers(dest="vm_command", help="VM subcommand")

    list_parser = commands.add_parser("list", aliases=["ls"], help="List guests")
    list_parser.add_argument("host", help="Registered provider host")
    list_parser.add_argument("--json", action="store_true", help="Output JSON")
    list_parser.set_defaults(_handler=_cmd_list)

    show_parser = commands.add_parser("show", help="Show one guest and its configuration")
    _add_vm_target_arguments(show_parser)
    show_parser.add_argument("--json", action="store_true", help="Output JSON")
    show_parser.set_defaults(_handler=_cmd_show)

    health_parser = commands.add_parser("health", help="Check one guest")
    _add_vm_target_arguments(health_parser)
    health_parser.add_argument(
        "--no-ssh",
        action="store_true",
        help="Skip the guest SSH port probe",
    )
    health_parser.add_argument("--json", action="store_true", help="Output JSON")
    health_parser.set_defaults(_handler=_cmd_health)

    stats_parser = commands.add_parser(
        "stats",
        help="Show live CPU, memory, disk, and network counters",
    )
    _add_vm_target_arguments(stats_parser)
    stats_parser.add_argument("--json", action="store_true", help="Output JSON")
    stats_parser.set_defaults(_handler=_cmd_stats)

    status_parser = commands.add_parser("status", help="Show one guest's power state")
    _add_vm_target_arguments(status_parser)
    status_parser.add_argument("--json", action="store_true", help="Output JSON")
    status_parser.set_defaults(_handler=_cmd_status)

    start_parser = commands.add_parser("start", help="Start a stopped guest")
    _add_vm_target_arguments(start_parser)
    start_parser.add_argument("--json", action="store_true", help="Output JSON")
    start_parser.set_defaults(_handler=_cmd_start)

    pause_parser = commands.add_parser(
        "pause",
        aliases=["suspend"],
        help="Pause/suspend a running guest",
    )
    _add_vm_target_arguments(pause_parser)
    pause_parser.add_argument("--json", action="store_true", help="Output JSON")
    pause_parser.set_defaults(_handler=_cmd_pause)

    resume_parser = commands.add_parser("resume", help="Resume a paused guest")
    _add_vm_target_arguments(resume_parser)
    resume_parser.add_argument("--json", action="store_true", help="Output JSON")
    resume_parser.set_defaults(_handler=_cmd_resume)

    shutdown_parser = commands.add_parser(
        "shutdown",
        help="Request a graceful guest shutdown",
    )
    _add_vm_target_arguments(shutdown_parser)
    shutdown_parser.add_argument(
        "--timeout",
        type=_nonnegative_seconds,
        metavar="SECONDS",
        help="Maximum seconds to wait for graceful shutdown",
    )
    shutdown_parser.add_argument("--json", action="store_true", help="Output JSON")
    shutdown_parser.set_defaults(_handler=_cmd_shutdown)

    stop_parser = commands.add_parser(
        "stop",
        help="Immediately stop a guest (like pulling its power plug)",
    )
    _add_vm_target_arguments(stop_parser)
    stop_parser.add_argument("--json", action="store_true", help="Output JSON")
    stop_parser.set_defaults(_handler=_cmd_stop)

    reboot_parser = commands.add_parser(
        "reboot",
        aliases=["restart"],
        help="Cleanly reboot a running guest",
    )
    _add_vm_target_arguments(reboot_parser)
    reboot_parser.add_argument(
        "--timeout",
        type=_nonnegative_seconds,
        metavar="SECONDS",
        help="Maximum seconds to wait for shutdown during reboot",
    )
    reboot_parser.add_argument("--json", action="store_true", help="Output JSON")
    reboot_parser.set_defaults(_handler=_cmd_reboot)

    autostart_parser = commands.add_parser(
        "autostart",
        help="Inspect or configure start-at-boot ordering",
    )
    _add_vm_target_arguments(autostart_parser)
    autostart_mode = autostart_parser.add_mutually_exclusive_group()
    autostart_mode.add_argument(
        "--enable",
        action="store_const",
        const=True,
        dest="autostart_enabled",
        help="Start this guest when its provider host boots",
    )
    autostart_mode.add_argument(
        "--disable",
        action="store_const",
        const=False,
        dest="autostart_enabled",
        help="Do not start this guest when its provider host boots",
    )
    autostart_parser.add_argument(
        "--order",
        type=_nonnegative_order,
        metavar="N",
        help="Startup priority; lower values start first and stop last",
    )
    autostart_parser.add_argument(
        "--start-delay",
        type=_nonnegative_seconds,
        metavar="SECONDS",
        help="Delay after starting this guest before starting the next",
    )
    autostart_parser.add_argument(
        "--shutdown-timeout",
        type=_nonnegative_seconds,
        metavar="SECONDS",
        help="Maximum time allowed for this guest to shut down",
    )
    autostart_parser.add_argument("--json", action="store_true", help="Output JSON")
    autostart_parser.set_defaults(_handler=_cmd_autostart)

    snapshot_parser = commands.add_parser("snapshot", help="Inspect guest snapshots")
    snapshot_commands = snapshot_parser.add_subparsers(
        dest="vm_snapshot_command", help="Snapshot subcommand"
    )
    snapshot_list = snapshot_commands.add_parser("list", help="List snapshots")
    _add_vm_target_arguments(snapshot_list)
    snapshot_list.add_argument("--json", action="store_true", help="Output JSON")
    snapshot_list.set_defaults(_handler=_cmd_snapshot_list)

    backup_parser = commands.add_parser("backup", help="Inspect guest backups")
    backup_commands = backup_parser.add_subparsers(
        dest="vm_backup_command", help="Backup subcommand"
    )
    backup_list = backup_commands.add_parser("list", help="List backups")
    _add_vm_target_arguments(backup_list)
    backup_list.add_argument("--json", action="store_true", help="Output JSON")
    backup_list.set_defaults(_handler=_cmd_backup_list)

    destroy_parser = commands.add_parser(
        "destroy",
        help="Permanently destroy a QEMU VM (asks for confirmation)",
    )
    _add_vm_target_arguments(destroy_parser)
    destroy_parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Skip the confirmation prompt",
    )
    destroy_parser.add_argument(
        "--force",
        action="store_true",
        help="Force-stop a running VM instead of requesting a graceful shutdown",
    )
    destroy_parser.set_defaults(_handler=_cmd_destroy)

    return parser


def _add_vm_target_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "target",
        metavar="TARGET",
        help="Saved local VM name, or a registered provider host when ID is supplied",
    )
    parser.add_argument(
        "vmid",
        nargs="?",
        type=_positive_id,
        metavar="ID",
        help="Provider VM ID; omit when TARGET is a saved local VM name",
    )


def _positive_id(value: str) -> int:
    try:
        vmid = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("ID must be a positive integer") from exc
    if vmid < 1:
        raise argparse.ArgumentTypeError("ID must be a positive integer")
    return vmid


def _nonnegative_seconds(value: str) -> int:
    try:
        seconds = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "SECONDS must be a non-negative integer"
        ) from exc
    if seconds < 0:
        raise argparse.ArgumentTypeError("SECONDS must be a non-negative integer")
    return seconds


def _nonnegative_order(value: str) -> int:
    try:
        order = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("N must be a non-negative integer") from exc
    if order < 0:
        raise argparse.ArgumentTypeError("N must be a non-negative integer")
    return order


def _resolve_host(target: str, workspace: Optional[str]) -> ProxmoxHost:
    host = find_proxmox_host(target, workspace)
    if host is None:
        raise ValueError(
            f"No registered provider host matching '{target}'. "
            "Use 'basaltw proxmox add' first."
        )
    return host


def _print_result(payload: dict, *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))


def _find_guest(host: ProxmoxHost, vmid: int) -> ContainerInfo:
    for guest in list_containers(host):
        if guest.vmid == vmid:
            return guest
    raise ValueError(f"No guest with ID {vmid} found on {host.name}")


def _saved_setup_for_target(
    target: str,
    workspace: Optional[str],
) -> SetupConfig:
    """Resolve an exact saved host or friendly name without accepting tags."""

    configs = load_all_setup_commands(workspace)
    normalized_target = target.lower().rstrip(".")
    host_matches = [
        config
        for config in configs
        if config.host.lower().rstrip(".") == normalized_target
    ]
    name_matches = [
        config
        for config in configs
        if config.friendly_name
        and config.friendly_name.lower() == target.lower()
    ]
    matches = host_matches or name_matches
    if not matches:
        raise ValueError(
            f"No saved VM setup matching '{target}'. "
            "Use 'basaltw list' to see local setup names."
        )
    if len(matches) > 1:
        hosts = ", ".join(sorted(config.host for config in matches))
        raise ValueError(
            f"Saved VM name '{target}' is ambiguous across: {hosts}. "
            "Use the VM's saved IP address instead."
        )
    return matches[0]


def _expected_vm_name(config: SetupConfig) -> str:
    if config.system_hostname:
        return config.system_hostname
    return _build_guest_hostname(
        config.host,
        config.friendly_name,
        default_prefix="vm",
    )


def _resolve_local_vm(target: str, workspace: Optional[str]) -> _ResolvedVM:
    config = _saved_setup_for_target(target, workspace)
    if not config.hosted_node:
        raise ValueError(
            f"Saved setup '{target}' is not attached to a provider host"
        )
    if config.machine_type != "vm":
        raise ValueError(
            f"Saved setup '{target}' is machine type '{config.machine_type}', not a VM"
        )

    host = _resolve_host(config.hosted_node, workspace)
    expected_name = _expected_vm_name(config)
    matches = [
        guest
        for guest in list_containers(host)
        if guest.guest_type == "vm" and guest.name.lower() == expected_name.lower()
    ]
    if not matches:
        raise ValueError(
            f"No QEMU VM named '{expected_name}' found on {host.name} for "
            f"saved setup '{target}'"
        )
    if len(matches) > 1:
        ids = ", ".join(str(guest.vmid) for guest in matches)
        raise ValueError(
            f"Multiple QEMU VMs named '{expected_name}' found on {host.name} "
            f"(IDs: {ids}); use HOST ID explicitly"
        )

    guest = matches[0]
    observed_ip = get_container_ip(host, guest.vmid)
    if observed_ip != config.host:
        observed = observed_ip or "no configured IPv4 address"
        raise ValueError(
            f"Refusing saved setup '{target}': VM {guest.vmid} on {host.name} "
            f"has {observed}, expected {config.host}"
        )
    return _ResolvedVM(
        host=host,
        guest=guest,
        local_name=config.friendly_name or config.host,
    )


def _resolve_vm(
    target: str,
    vmid: Optional[int],
    workspace: Optional[str],
    *,
    require_qemu: bool = False,
) -> _ResolvedVM:
    if vmid is None:
        resolved = _resolve_local_vm(target, workspace)
    else:
        host = _resolve_host(target, workspace)
        resolved = _ResolvedVM(host=host, guest=_find_guest(host, vmid))
    if require_qemu and resolved.guest.guest_type != "vm":
        raise ValueError(
            f"Guest {resolved.guest.vmid} on {resolved.host.name} is "
            f"{resolved.guest.guest_type}, not a QEMU VM"
        )
    return resolved


def _cmd_list(args: argparse.Namespace, workspace: Optional[str]) -> int:
    host = _resolve_host(args.host, workspace)
    records = [
        VMRecord(
            id=str(guest.vmid),
            kind=guest.guest_type,
            name=guest.name,
            state=guest.status,
            lock=guest.lock,
        ).to_dict()
        for guest in list_containers(host)
    ]
    payload = envelope(
        provider=host.provider,
        host=host.name,
        operation="list",
        resources=records,
    )
    _print_result(payload, json_output=args.json)
    if not args.json:
        print(f"Provider: {host.provider}  Host: {host.name}")
        if records:
            for record in records:
                print(
                    f"{record['id']:>6}  {record['kind']:<3}  "
                    f"{record['state']:<10}  {record['name']}"
                )
        else:
            print("No guests found.")
    return 0


def _cmd_show(args: argparse.Namespace, workspace: Optional[str]) -> int:
    resolved = _resolve_vm(args.target, args.vmid, workspace)
    host = resolved.host
    guest = resolved.guest
    config = get_container_config(host, guest.vmid)
    resource = VMRecord(
        id=str(guest.vmid),
        kind=guest.guest_type,
        name=guest.name,
        state=get_container_status(host, guest.vmid),
        lock=guest.lock,
    ).to_dict()
    resource["config"] = config
    payload = envelope(
        provider=host.provider,
        host=host.name,
        operation="show",
        resources=[resource],
    )
    _print_result(payload, json_output=args.json)
    if not args.json:
        print(f"Provider: {host.provider}  Host: {host.name}")
        print(f"ID: {resource['id']}")
        print(f"Kind: {resource['kind']}")
        print(f"Name: {resource['name']}")
        print(f"State: {resource['state']}")
        for key, value in config.items():
            print(f"{key}: {value}")
    return 0


def _vm_state_resource(resolved: _ResolvedVM, state: str) -> dict:
    guest = resolved.guest
    return VMRecord(
        id=str(guest.vmid),
        kind=guest.guest_type,
        name=guest.name,
        state=state,
        lock=guest.lock,
    ).to_dict()


def _print_vm_state(
    resolved: _ResolvedVM,
    *,
    operation: str,
    state: str,
    json_output: bool,
) -> None:
    host = resolved.host
    guest = resolved.guest
    payload = envelope(
        provider=host.provider,
        host=host.name,
        operation=operation,
        resources=[_vm_state_resource(resolved, state)],
    )
    _print_result(payload, json_output=json_output)
    if not json_output:
        suffix = "" if operation == "status" else f" ({operation} complete)"
        print(
            f"{host.name}/{guest.vmid} {guest.guest_type} "
            f"'{guest.name}': {state}{suffix}"
        )


def _complete_lifecycle(
    resolved: _ResolvedVM,
    *,
    operation: str,
    json_output: bool,
) -> int:
    state = get_container_status(resolved.host, resolved.guest.vmid)
    _print_vm_state(
        resolved,
        operation=operation,
        state=state,
        json_output=json_output,
    )
    return 0


def _cmd_status(args: argparse.Namespace, workspace: Optional[str]) -> int:
    resolved = _resolve_vm(args.target, args.vmid, workspace)
    return _complete_lifecycle(
        resolved,
        operation="status",
        json_output=args.json,
    )


def _cmd_start(args: argparse.Namespace, workspace: Optional[str]) -> int:
    resolved = _resolve_vm(args.target, args.vmid, workspace)
    start_container(resolved.host, resolved.guest.vmid)
    return _complete_lifecycle(
        resolved,
        operation="start",
        json_output=args.json,
    )


def _cmd_pause(args: argparse.Namespace, workspace: Optional[str]) -> int:
    resolved = _resolve_vm(args.target, args.vmid, workspace)
    suspend_guest(resolved.host, resolved.guest.vmid)
    return _complete_lifecycle(
        resolved,
        operation="pause",
        json_output=args.json,
    )


def _cmd_resume(args: argparse.Namespace, workspace: Optional[str]) -> int:
    resolved = _resolve_vm(args.target, args.vmid, workspace)
    resume_guest(resolved.host, resolved.guest.vmid)
    return _complete_lifecycle(
        resolved,
        operation="resume",
        json_output=args.json,
    )


def _cmd_shutdown(args: argparse.Namespace, workspace: Optional[str]) -> int:
    resolved = _resolve_vm(args.target, args.vmid, workspace)
    stop_container(
        resolved.host,
        resolved.guest.vmid,
        force=False,
        timeout=args.timeout,
    )
    return _complete_lifecycle(
        resolved,
        operation="shutdown",
        json_output=args.json,
    )


def _cmd_stop(args: argparse.Namespace, workspace: Optional[str]) -> int:
    resolved = _resolve_vm(args.target, args.vmid, workspace)
    stop_container(resolved.host, resolved.guest.vmid, force=True)
    return _complete_lifecycle(
        resolved,
        operation="stop",
        json_output=args.json,
    )


def _cmd_reboot(args: argparse.Namespace, workspace: Optional[str]) -> int:
    resolved = _resolve_vm(args.target, args.vmid, workspace)
    reboot_guest(
        resolved.host,
        resolved.guest.vmid,
        timeout=args.timeout,
    )
    return _complete_lifecycle(
        resolved,
        operation="reboot",
        json_output=args.json,
    )


def _usage_fraction(used: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return used / total


def _stats_warnings(stats: GuestStats) -> list[str]:
    warnings: list[str] = []
    if stats.status != "running":
        warnings.append("Guest is not running; live counters may be zero")
        return warnings
    if stats.cpu_usage >= 0.90:
        warnings.append("CPU usage is at or above 90%")
    if _usage_fraction(stats.memory_used, stats.memory_total) >= 0.85:
        warnings.append("Memory usage is at or above 85%")
    if stats.swap_used > 0:
        warnings.append("Guest is using swap; check memory pressure")
    if _usage_fraction(stats.disk_used, stats.disk_total) >= 0.90:
        warnings.append("Guest disk usage is at or above 90%")
    return warnings


def _stats_model(resolved: _ResolvedVM, stats: GuestStats) -> VMStats:
    guest = resolved.guest
    return VMStats(
        id=str(guest.vmid),
        kind=guest.guest_type,
        name=guest.name,
        state=stats.status,
        cpu_usage=stats.cpu_usage,
        cpu_count=stats.cpu_count,
        memory_used=stats.memory_used,
        memory_total=stats.memory_total,
        swap_used=stats.swap_used,
        swap_total=stats.swap_total,
        disk_used=stats.disk_used,
        disk_total=stats.disk_total,
        disk_read=stats.disk_read,
        disk_written=stats.disk_written,
        network_in=stats.network_in,
        network_out=stats.network_out,
        uptime_seconds=stats.uptime_seconds,
        warnings=_stats_warnings(stats),
    )


def _format_bytes(value: int) -> str:
    if value >= 1024 ** 4:
        return f"{value / 1024 ** 4:.1f} TiB"
    if value >= 1024 ** 3:
        return f"{value / 1024 ** 3:.1f} GiB"
    if value >= 1024 ** 2:
        return f"{value / 1024 ** 2:.1f} MiB"
    if value >= 1024:
        return f"{value / 1024:.1f} KiB"
    return f"{value} B"


def _format_duration(seconds: int) -> str:
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60
    if days:
        return f"{days}d {hours}h {minutes}m"
    return f"{hours}h {minutes}m"


def _cmd_stats(args: argparse.Namespace, workspace: Optional[str]) -> int:
    resolved = _resolve_vm(args.target, args.vmid, workspace)
    model = _stats_model(
        resolved,
        get_guest_stats(resolved.host, resolved.guest.vmid),
    )
    payload = envelope(
        provider=resolved.host.provider,
        host=resolved.host.name,
        operation="stats",
        resources=[model.to_dict()],
    )
    _print_result(payload, json_output=args.json)
    if args.json:
        return 0

    print(
        f"{resolved.host.name}/{model.id} {model.kind} '{model.name}': "
        f"{model.state}"
    )
    cpu_label = "vCPU" if model.cpu_count == 1 else "vCPUs"
    cpu_context = f" across {model.cpu_count} {cpu_label}" if model.cpu_count else ""
    print(f"  CPU:      {model.cpu_usage * 100:.1f}%{cpu_context}")
    print(
        f"  Memory:   {_format_bytes(model.memory_used)} / "
        f"{_format_bytes(model.memory_total)}"
    )
    if model.swap_total or model.swap_used:
        print(
            f"  Swap:     {_format_bytes(model.swap_used)} / "
            f"{_format_bytes(model.swap_total)}"
        )
    print(
        f"  Disk:     {_format_bytes(model.disk_used)} / "
        f"{_format_bytes(model.disk_total)}"
    )
    print(
        f"  Disk I/O: {_format_bytes(model.disk_read)} read, "
        f"{_format_bytes(model.disk_written)} written"
    )
    print(
        f"  Network:  {_format_bytes(model.network_in)} in, "
        f"{_format_bytes(model.network_out)} out"
    )
    print(f"  Uptime:   {_format_duration(model.uptime_seconds)}")
    for warning in model.warnings:
        print(f"  Warning:  {warning}")
    return 0


def _cmd_autostart(args: argparse.Namespace, workspace: Optional[str]) -> int:
    resolved = _resolve_vm(args.target, args.vmid, workspace)
    has_schedule = any(
        value is not None
        for value in (args.order, args.start_delay, args.shutdown_timeout)
    )
    if has_schedule and args.autostart_enabled is not True:
        raise ValueError("Autostart order and delays require --enable")

    operation = "autostart.show"
    if args.autostart_enabled is not None:
        configure_guest_autostart(
            resolved.host,
            resolved.guest.vmid,
            enabled=args.autostart_enabled,
            order=args.order,
            start_delay=args.start_delay,
            shutdown_timeout=args.shutdown_timeout,
        )
        operation = "autostart.configure"

    settings = get_guest_autostart(resolved.host, resolved.guest.vmid)
    model = VMAutostart(
        id=str(resolved.guest.vmid),
        kind=resolved.guest.guest_type,
        name=resolved.guest.name,
        enabled=settings.enabled,
        order=settings.order,
        start_delay=settings.start_delay,
        shutdown_timeout=settings.shutdown_timeout,
    )
    payload = envelope(
        provider=resolved.host.provider,
        host=resolved.host.name,
        operation=operation,
        resources=[model.to_dict()],
    )
    _print_result(payload, json_output=args.json)
    if not args.json:
        start_delay = (
            f"{model.start_delay}s" if model.start_delay is not None else "default"
        )
        shutdown_timeout = (
            f"{model.shutdown_timeout}s"
            if model.shutdown_timeout is not None
            else "default"
        )
        print(
            f"{resolved.host.name}/{model.id} {model.kind} '{model.name}': "
            f"autostart {'enabled' if model.enabled else 'disabled'}"
        )
        print(f"  Order:             {model.order if model.order is not None else 'default'}")
        print(f"  Start delay:       {start_delay}")
        print(f"  Shutdown timeout:  {shutdown_timeout}")
    return 0


def _health_model(report: HealthReport) -> VMHealth:
    return VMHealth(
        id=str(report.vmid),
        kind=report.guest_type,
        state=report.status,
        healthy=report.healthy,
        address=report.ip,
        pingable=report.pingable,
        ssh_open=report.ssh_open,
        notes=list(report.notes),
    )


def _cmd_health(args: argparse.Namespace, workspace: Optional[str]) -> int:
    resolved = _resolve_vm(args.target, args.vmid, workspace)
    host = resolved.host
    report = health_check(host, resolved.guest.vmid, probe_ssh=not args.no_ssh)
    health = _health_model(report)
    payload = envelope(
        provider=host.provider,
        host=host.name,
        operation="health",
        resources=[health.to_dict()],
    )
    _print_result(payload, json_output=args.json)
    if not args.json:
        print(
            f"{host.name}/{resolved.guest.vmid}: "
            f"{'healthy' if health.healthy else 'unhealthy'}"
        )
        for note in health.notes:
            print(f"  - {note}")
    return 0 if health.healthy else 1


def _snapshot_dict(snapshot: SnapshotInfo) -> dict:
    return {
        "name": snapshot.name,
        "description": snapshot.description,
        "current": snapshot.is_current,
    }


def _cmd_snapshot_list(args: argparse.Namespace, workspace: Optional[str]) -> int:
    resolved = _resolve_vm(args.target, args.vmid, workspace)
    host = resolved.host
    snapshots = [
        _snapshot_dict(item)
        for item in list_snapshots(host, resolved.guest.vmid)
    ]
    payload = envelope(
        provider=host.provider,
        host=host.name,
        operation="snapshot.list",
        resources=snapshots,
    )
    _print_result(payload, json_output=args.json)
    if not args.json:
        for snapshot in snapshots:
            marker = "*" if snapshot["current"] else " "
            print(f"{marker} {snapshot['name']}  {snapshot['description']}")
    return 0


def _backup_dict(backup: BackupInfo) -> dict:
    return {
        "volume": backup.volid,
        "storage": backup.storage,
        "filename": backup.filename,
        "vmid": backup.vmid,
        "size": backup.size,
        "created": backup.ctime,
        "format": backup.format,
        "notes": backup.notes,
    }


def _cmd_backup_list(args: argparse.Namespace, workspace: Optional[str]) -> int:
    resolved = _resolve_vm(args.target, args.vmid, workspace)
    host = resolved.host
    backups = [
        _backup_dict(item)
        for item in list_backups(host, resolved.guest.vmid)
    ]
    payload = envelope(
        provider=host.provider,
        host=host.name,
        operation="backup.list",
        resources=backups,
    )
    _print_result(payload, json_output=args.json)
    if not args.json:
        for backup in backups:
            print(f"{backup['storage']}: {backup['filename']} ({backup['size']} bytes)")
    return 0


def _cmd_destroy(args: argparse.Namespace, workspace: Optional[str]) -> int:
    resolved = _resolve_vm(
        args.target,
        args.vmid,
        workspace,
        require_qemu=True,
    )
    host = resolved.host
    guest = resolved.guest
    local_label = (
        f", saved as '{resolved.local_name}'" if resolved.local_name else ""
    )
    if not args.yes:
        prompt = (
            f"Destroy QEMU VM {guest.vmid} ('{guest.name}'{local_label}) on "
            f"{host.name} ({host.address})? Type 'yes' to confirm: "
        )
        try:
            response = input(prompt)
        except (EOFError, KeyboardInterrupt):
            print()
            print("Aborted.")
            return 1
        if response.strip().lower() != "yes":
            print("Aborted.")
            return 1

    destroy_container(host, guest.vmid, force=args.force)
    if any(item.vmid == guest.vmid for item in list_containers(host)):
        raise ProxmoxManageError(
            f"VM {guest.vmid} still exists on {host.name} after destroy completed"
        )
    print(f"Destroyed QEMU VM {guest.vmid} ('{guest.name}') on {host.name}.")
    return 0


def run_vm_command(args: argparse.Namespace) -> int:
    """Dispatch a parsed ``vm`` command."""

    handler = getattr(args, "_handler", None)
    if handler is None:
        print("Use 'basaltw vm --help' for available commands.")
        return 0
    workspace = getattr(args, "workspace", None)
    try:
        return handler(args, workspace)
    except (ProxmoxBackupError, ProxmoxManageError, ValueError) as exc:
        print(f"Error: {exc}")
        return 1


__all__ = ["add_vm_subparser", "run_vm_command"]
