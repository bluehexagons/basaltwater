#!/usr/bin/env python3
"""CLI surface for ``infra-tools proxmox`` subcommands.

Splits parser construction and command dispatch out of ``infra_tools.py`` so
the management surface can grow independently of the setup/patch flow.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from typing import Optional

from lib.cluster_update import run_cluster_update
from lib.proxmox_backup import BackupInfo, ProxmoxBackupError, create_backup, list_backups
from lib.proxmox_guest import probe_proxmox_cluster, probe_proxmox_host
from lib.proxmox_migrate import ProxmoxMigrateError, migrate_guest
from lib.proxmox_maintenance import (
    collect_maintenance_report,
    format_maintenance_report,
)
from lib.proxmox_storage import OrphanedVolume, ProxmoxStorageError, delete_volume, list_orphaned_volumes
from lib.proxmox_placement import (
    PlacementRequest,
    collect_snapshots,
    format_plan,
    format_rebalance,
    plan_placement,
    plan_rebalance,
)
from lib.proxmox_summary import NodeSummary, ProxmoxSummaryError, format_node_summary, get_node_summary
from lib.proxmox_hosts import (
    ProxmoxHost,
    add_proxmox_host,
    find_proxmox_host,
    load_proxmox_hosts,
    remove_proxmox_host,
    sync_proxmox_host,
)
from lib.proxmox_manage import (
    ContainerInfo,
    DEFAULT_NOTIFICATION_ENDPOINT,
    DEFAULT_NOTIFICATION_MATCHER,
    DEFAULT_NOTIFICATION_SEVERITIES,
    ProxmoxManageError,
    delete_snapshot,
    destroy_container,
    get_container_config,
    get_container_pending,
    get_container_status,
    health_check,
    install_webhook_notifications,
    list_containers,
    list_snapshots,
    modify_container,
    reconfigure_container,
    resume_guest,
    resize_container_disk,
    rollback_guest,
    send_webhook_test_notification,
    snapshot_guest,
    start_container,
    stop_container,
    suspend_guest,
    unlock_guest,
)
from lib.proxmox_shell import ProxmoxShell, run_proxmox_shell
from lib.vm_models import VMHealth, VMRecord, envelope


def add_proxmox_subparser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    """Register the ``proxmox`` command tree on the main argparse subparsers."""
    parser = subparsers.add_parser(
        "proxmox",
        help="Manage Proxmox hosts and the guests running on them",
        description=(
            "Manage Proxmox host registrations and the Proxmox guests "
            "running on them. Run with no subcommand to enter the "
            "interactive shell."
        ),
    )
    parser.add_argument(
        "--workspace",
        help="Workspace root for saved setups, credentials, known_hosts, and history",
    )

    sub = parser.add_subparsers(dest="proxmox_command", help="Proxmox subcommand")

    shell = sub.add_parser("shell", help="Start the interactive Proxmox shell")
    shell.set_defaults(_handler=_cmd_shell)

    hosts = sub.add_parser("hosts", help="List registered Proxmox hosts")
    hosts.add_argument("--json", action="store_true", help="Output JSON")
    hosts.set_defaults(_handler=_cmd_hosts_list)

    add = sub.add_parser("add", help="Register a Proxmox host")
    add.add_argument("name", help="Short name for the host")
    add.add_argument("address", help="IP or hostname for the Proxmox node")
    add.add_argument("-u", "--user", default="root", help="SSH user (default: root)")
    add.add_argument("-k", "--key", dest="ssh_key", help="SSH private key path")
    add.add_argument("--description", help="Optional human-readable description")
    add.add_argument(
        "--default-storage",
        help="Optional default storage pool to suggest when creating guests",
    )
    add.add_argument(
        "--default-template-storage",
        help="Optional default LXC template storage pool to suggest when creating guests",
    )
    add.add_argument(
        "--default-bridge",
        help="Optional default network bridge to suggest when creating guests",
    )
    add.add_argument(
        "--replace",
        action="store_true",
        help="Replace an existing host with the same name",
    )
    add.set_defaults(_handler=_cmd_hosts_add)

    rm = sub.add_parser("remove", aliases=["rm"], help="Remove a registered Proxmox host")
    rm.add_argument("target", help="Host name or address to remove")
    rm.set_defaults(_handler=_cmd_hosts_remove)

    probe = sub.add_parser(
        "probe",
        help="Probe a registered host for storage pools, bridges, and setup defaults",
    )
    probe.add_argument("host", help="Registered host name or address")
    probe.set_defaults(_handler=_cmd_probe)

    probe_cluster = sub.add_parser(
        "probe-cluster",
        help="Probe a Proxmox cluster from one reachable node and seed host entries",
    )
    probe_cluster.add_argument("address", help="Reachable IP or hostname for one cluster node")
    probe_cluster.add_argument(
        "-u", "--user", default="root", help="SSH user for the cluster nodes (default: root)"
    )
    probe_cluster.add_argument("-k", "--key", dest="ssh_key", help="SSH private key path")
    probe_cluster.add_argument(
        "--tag",
        action="append",
        default=[],
        help="Tag to apply to newly discovered nodes; repeatable",
    )
    probe_cluster.set_defaults(_handler=_cmd_probe_cluster)

    rolling_update = sub.add_parser(
        "rolling-update",
        help="Patch saved node configs in order, rebooting each node if needed",
    )
    rolling_update.add_argument(
        "targets",
        nargs="+",
        help="Saved setup names/hosts to update in order",
    )
    rolling_update.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and print patch plans without changing the nodes",
    )
    rolling_update.add_argument(
        "--reboot-timeout",
        type=int,
        default=300,
        help="Seconds to wait for each node to come back after reboot (default: 300)",
    )
    rolling_update.set_defaults(_handler=_cmd_rolling_update)

    ls = sub.add_parser("ls", aliases=["list"], help="List guests on a host")
    ls.add_argument("host", help="Registered host name or address")
    ls.add_argument("--json", action="store_true", help="Output JSON")
    ls.set_defaults(_handler=_cmd_containers_ls)

    status = sub.add_parser("status", help="Show guest status")
    status.add_argument("host", help="Registered host name or address")
    status.add_argument("vmid", type=int, help="Guest VMID")
    status.add_argument("--json", action="store_true", help="Output JSON")
    status.set_defaults(_handler=_cmd_status)

    start = sub.add_parser("start", help="Start a guest")
    start.add_argument("host", help="Registered host name or address")
    start.add_argument("vmid", type=int, help="Guest VMID")
    start.set_defaults(_handler=_cmd_start)

    pause = sub.add_parser(
        "pause",
        aliases=["suspend"],
        help="Pause/suspend a running guest",
    )
    pause.add_argument("host", help="Registered host name or address")
    pause.add_argument("vmid", type=int, help="Guest VMID")
    pause.set_defaults(_handler=_cmd_pause)

    resume = sub.add_parser("resume", help="Resume a paused guest")
    resume.add_argument("host", help="Registered host name or address")
    resume.add_argument("vmid", type=int, help="Guest VMID")
    resume.set_defaults(_handler=_cmd_resume)

    stop = sub.add_parser("stop", help="Shutdown (or force-stop) a guest")
    stop.add_argument("host", help="Registered host name or address")
    stop.add_argument("vmid", type=int, help="Guest VMID")
    stop.add_argument(
        "--force", action="store_true", help="Use an immediate stop instead of graceful shutdown"
    )
    stop.set_defaults(_handler=_cmd_stop)

    destroy = sub.add_parser("destroy", help="Destroy a guest (asks for confirmation)")
    destroy.add_argument("host", help="Registered host name or address")
    destroy.add_argument("vmid", type=int, help="Guest VMID")
    destroy.add_argument(
        "-y", "--yes", action="store_true", help="Skip the confirmation prompt"
    )
    destroy.add_argument(
        "--force", action="store_true", help="Force-stop the guest before destroy if it is running"
    )
    destroy.set_defaults(_handler=_cmd_destroy)

    health = sub.add_parser("health", help="Run a health check against a guest")
    health.add_argument("host", help="Registered host name or address")
    health.add_argument("vmid", type=int, help="Guest VMID")
    health.add_argument(
        "--no-ssh", action="store_true", help="Skip the SSH:22 reachability probe"
    )
    health.add_argument("--json", action="store_true", help="Output JSON")
    health.set_defaults(_handler=_cmd_health)

    config_cmd = sub.add_parser("config", help="Show guest configuration")
    config_cmd.add_argument("host", help="Registered host name or address")
    config_cmd.add_argument("vmid", type=int, help="Guest VMID")
    config_cmd.add_argument(
        "--pending",
        action="store_true",
        help="Show pending (unapplied) configuration changes instead",
    )
    config_cmd.set_defaults(_handler=_cmd_config)

    reconfig = sub.add_parser(
        "reconfigure",
        help="Set arbitrary guest configuration options",
        description=(
            "Apply one or more pct/qm set options to a guest. "
            "Changes that affect a running guest may require a restart."
        ),
    )
    reconfig.add_argument("host", help="Registered host name or address")
    reconfig.add_argument("vmid", type=int, help="Guest VMID")
    reconfig.add_argument(
        "--set",
        action="append",
        dest="options",
        metavar="KEY=VALUE",
        help=(
            "Option to set, as key=value (e.g. --set hostname=mybox). "
            "Repeatable."
        ),
    )
    reconfig.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the remote pct/qm set command without executing it",
    )
    reconfig.set_defaults(_handler=_cmd_reconfigure)

    modify = sub.add_parser(
        "modify",
        help="Change CPU cores or memory allocation for a guest",
    )
    modify.add_argument("host", help="Registered host name or address")
    modify.add_argument("vmid", type=int, help="Guest VMID")
    modify.add_argument(
        "--cores",
        type=int,
        help="Number of vCPU cores",
    )
    modify.add_argument(
        "--memory",
        dest="memory",
        help=(
            "RAM allocation in MiB, or with a unit suffix: "
            "512M / 4G (e.g. --memory 4G)"
        ),
    )
    modify.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the remote pct/qm set command without executing it",
    )
    modify.set_defaults(_handler=_cmd_modify)

    resize = sub.add_parser(
        "resize-disk",
        help="Increase a guest disk volume size",
    )
    resize.add_argument("host", help="Registered host name or address")
    resize.add_argument("vmid", type=int, help="Guest VMID")
    resize.add_argument("volume", help="Volume name (e.g. rootfs)")
    resize.add_argument("size", help="New absolute size with unit (e.g. 20G)")
    resize.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the remote pct/qm resize command without executing it",
    )
    resize.set_defaults(_handler=_cmd_resize_disk)

    top = sub.add_parser(
        "top",
        help="Show CPU, memory, storage, and guest counts for one or more nodes",
    )
    top.add_argument(
        "hosts",
        nargs="+",
        help="Registered host name(s) or address(es)",
    )
    top.add_argument("--json", action="store_true", help="Output JSON")
    top.set_defaults(_handler=_cmd_top)

    audit = sub.add_parser(
        "audit",
        help="Run read-only maintenance and reboot-safety checks on nodes",
    )
    audit.add_argument(
        "hosts",
        nargs="+",
        help="Registered host name(s) or address(es)",
    )
    audit.add_argument(
        "--json",
        action="store_true",
        help="Print a stable JSON report",
    )
    audit.set_defaults(_handler=_cmd_audit)

    plan = sub.add_parser(
        "plan",
        help="Read-only placement and rebalance advisor",
        description=(
            "Suggest where to put a new guest, or which guests on overloaded "
            "nodes could be moved. Read-only: prints rankings, never migrates."
        ),
    )
    plan_sub = plan.add_subparsers(dest="plan_command", help="Plan subcommand")
    plan.set_defaults(_handler=_cmd_plan_missing)

    plan_place = plan_sub.add_parser(
        "place",
        help="Rank registered hosts for a prospective guest",
    )
    plan_place.add_argument(
        "--cores", type=int, default=1, help="Cores the guest will use (default: 1)"
    )
    plan_place.add_argument(
        "--memory",
        type=int,
        default=512,
        dest="memory_mib",
        help="Memory in MiB (default: 512)",
    )
    plan_place.add_argument(
        "--disk",
        type=int,
        default=0,
        dest="disk_gib",
        help="Root disk in GiB (default: 0, no disk constraint)",
    )
    plan_place.add_argument(
        "--prefer-tag",
        action="append",
        default=[],
        dest="prefer_tags",
        help="Boost hosts with this tag (repeatable)",
    )
    plan_place.add_argument(
        "--avoid-tag",
        action="append",
        default=[],
        dest="avoid_tags",
        help="Disqualify hosts with this tag (repeatable)",
    )
    plan_place.add_argument(
        "--exclude",
        action="append",
        default=[],
        dest="exclude_nodes",
        help="Disqualify a host or node name (repeatable)",
    )
    plan_place.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Maximum candidates to print (default: 5)",
    )
    plan_place.set_defaults(_handler=_cmd_plan_place)

    plan_rebalance_p = plan_sub.add_parser(
        "rebalance",
        help="Flag overloaded nodes and suggest destinations for their guests",
    )
    plan_rebalance_p.add_argument(
        "--limit",
        type=int,
        default=3,
        help="Maximum destinations to print per hot node (default: 3)",
    )
    plan_rebalance_p.add_argument(
        "--apply",
        type=int,
        metavar="VMID",
        help="Migrate this guest away from its hot node (requires the guest "
             "to live on a node flagged as hot)",
    )
    plan_rebalance_p.add_argument(
        "--to",
        help="Destination host name or address for --apply "
             "(default: top-ranked candidate)",
    )
    plan_rebalance_p.add_argument(
        "--online",
        action="store_true",
        help="Keep VMs running during --apply migration (requires shared or "
             "migrated storage)",
    )
    plan_rebalance_p.add_argument(
        "--with-local-disks",
        action="store_true",
        help="Migrate local disks during --apply",
    )
    plan_rebalance_p.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip the confirmation prompt for --apply",
    )
    plan_rebalance_p.add_argument(
        "--dry-run",
        action="store_true",
        help="With --apply, print the migrate command without executing it",
    )
    plan_rebalance_p.set_defaults(_handler=_cmd_plan_rebalance)

    backups_list = sub.add_parser(
        "backups",
        help="List backups for a guest",
    )
    backups_list.add_argument("host", help="Registered host name or address")
    backups_list.add_argument("vmid", type=int, help="Guest VMID")
    backups_list.set_defaults(_handler=_cmd_backups_list)

    backup = sub.add_parser(
        "backup",
        help="Create an immediate vzdump backup for a guest",
    )
    backup.add_argument("host", help="Registered host name or address")
    backup.add_argument("vmid", type=int, help="Guest VMID")
    backup.add_argument(
        "--storage",
        help="Target storage pool (auto-selects first backup-capable pool if omitted)",
    )
    backup.add_argument(
        "--mode",
        default="snapshot",
        choices=["snapshot", "suspend", "stop"],
        help="Backup mode (default: snapshot — no downtime for VMs)",
    )
    backup.add_argument(
        "--compress",
        default="zstd",
        choices=["zstd", "gzip", "lzo", "0"],
        help="Compression format (default: zstd)",
    )
    backup.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the remote vzdump command without executing it",
    )
    backup.set_defaults(_handler=_cmd_backup)

    migrate = sub.add_parser(
        "migrate",
        help="Migrate a guest to another cluster node",
    )
    migrate.add_argument("host", help="Source registered host name or address")
    migrate.add_argument("vmid", type=int, help="Guest VMID")
    migrate.add_argument("target", help="Target registered host name or address")
    migrate.add_argument(
        "--online",
        action="store_true",
        help="Keep the VM running during migration (requires shared or migrated storage)",
    )
    migrate.add_argument(
        "--with-local-disks",
        action="store_true",
        help="Migrate local disks to the target node storage",
    )
    migrate.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the remote migrate command without executing it",
    )
    migrate.set_defaults(_handler=_cmd_migrate)

    clean_disks = sub.add_parser(
        "clean-disks",
        help="List orphaned guest volumes (and optionally delete them)",
    )
    clean_disks.add_argument("host", help="Registered host name or address")
    clean_disks.add_argument(
        "--delete",
        action="store_true",
        help="Delete the orphaned volumes after listing them (asks for confirmation)",
    )
    clean_disks.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip the confirmation prompt when used with --delete",
    )
    clean_disks.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be deleted without removing anything",
    )
    clean_disks.set_defaults(_handler=_cmd_clean_disks)

    unlock = sub.add_parser(
        "unlock",
        help="Remove a stuck management lock from a guest",
        description=(
            "Clear a Proxmox management lock left behind by an aborted backup, "
            "migration, or snapshot job."
        ),
    )
    unlock.add_argument("host", help="Registered host name or address")
    unlock.add_argument("vmid", type=int, help="Guest VMID")
    unlock.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the remote unlock command without executing it",
    )
    unlock.set_defaults(_handler=_cmd_unlock)

    snapshots_list = sub.add_parser(
        "snapshots",
        help="List snapshots for a guest",
    )
    snapshots_list.add_argument("host", help="Registered host name or address")
    snapshots_list.add_argument("vmid", type=int, help="Guest VMID")
    snapshots_list.set_defaults(_handler=_cmd_snapshots_list)

    snapshot = sub.add_parser(
        "snapshot",
        help="Create a snapshot of a guest",
    )
    snapshot.add_argument("host", help="Registered host name or address")
    snapshot.add_argument("vmid", type=int, help="Guest VMID")
    snapshot.add_argument("name", help="Snapshot name (letters, digits, underscores; max 40)")
    snapshot.add_argument("--description", default="", help="Optional snapshot description")
    snapshot.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the remote snapshot command without executing it",
    )
    snapshot.set_defaults(_handler=_cmd_snapshot)

    rollback = sub.add_parser(
        "rollback",
        help="Roll back a guest to a snapshot",
    )
    rollback.add_argument("host", help="Registered host name or address")
    rollback.add_argument("vmid", type=int, help="Guest VMID")
    rollback.add_argument("name", help="Snapshot name to roll back to")
    rollback.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the remote rollback command without executing it",
    )
    rollback.set_defaults(_handler=_cmd_rollback)

    delsnapshot = sub.add_parser(
        "delsnapshot",
        aliases=["delete-snapshot"],
        help="Delete a guest snapshot",
    )
    delsnapshot.add_argument("host", help="Registered host name or address")
    delsnapshot.add_argument("vmid", type=int, help="Guest VMID")
    delsnapshot.add_argument("name", help="Snapshot name to delete")
    delsnapshot.add_argument('-y', '--yes', action='store_true', help='Confirm snapshot deletion')
    delsnapshot.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the remote delsnapshot command without executing it",
    )
    delsnapshot.set_defaults(_handler=_cmd_delsnapshot)

    notifications = sub.add_parser(
        "notifications",
        help="Configure native Proxmox notification targets",
        description=(
            "Configure Proxmox's native notification system to send system "
            "notifications to an infra_tools-compatible webhook endpoint."
        ),
    )
    notification_sub = notifications.add_subparsers(
        dest="notification_command",
        help="Notification subcommand",
    )
    notifications.set_defaults(_handler=_cmd_notifications_missing)

    install = notification_sub.add_parser(
        "install-webhook",
        help="Create/update a native Proxmox webhook endpoint and matcher",
    )
    install.add_argument("host", help="Registered host name or address")
    install.add_argument("url", help="Webhook URL to receive Proxmox notifications")
    install.add_argument(
        "--endpoint-name",
        default=DEFAULT_NOTIFICATION_ENDPOINT,
        help=f"Proxmox webhook endpoint name (default: {DEFAULT_NOTIFICATION_ENDPOINT})",
    )
    install.add_argument(
        "--matcher-name",
        default=DEFAULT_NOTIFICATION_MATCHER,
        help=f"Proxmox matcher name (default: {DEFAULT_NOTIFICATION_MATCHER})",
    )
    install.add_argument(
        "--severity",
        action="append",
        choices=DEFAULT_NOTIFICATION_SEVERITIES,
        help=(
            "Severity to route to the webhook. Repeatable. "
            f"Default: {', '.join(DEFAULT_NOTIFICATION_SEVERITIES)}"
        ),
    )
    install.add_argument(
        "--send-test",
        action="store_true",
        help="Ask Proxmox to send a test notification after configuring the endpoint",
    )
    install.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the remote pvesh commands without executing them",
    )
    install.set_defaults(_handler=_cmd_notifications_install_webhook)

    test = notification_sub.add_parser(
        "test-webhook",
        help="Ask Proxmox to send a native test notification to an endpoint",
    )
    test.add_argument("host", help="Registered host name or address")
    test.add_argument(
        "--endpoint-name",
        default=DEFAULT_NOTIFICATION_ENDPOINT,
        help=f"Proxmox webhook endpoint name (default: {DEFAULT_NOTIFICATION_ENDPOINT})",
    )
    test.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the remote pvesh command without executing it",
    )
    test.set_defaults(_handler=_cmd_notifications_test_webhook)

    return parser


def run_proxmox_command(args: argparse.Namespace) -> int:
    """Dispatch a parsed ``proxmox`` argparse namespace."""
    workspace = getattr(args, "workspace", None)
    handler = getattr(args, "_handler", None)
    if handler is None:
        return run_proxmox_shell(workspace)
    try:
        return handler(args, workspace)
    except (
        ProxmoxBackupError,
        ProxmoxManageError,
        ProxmoxMigrateError,
        ProxmoxStorageError,
        ProxmoxSummaryError,
        ValueError,
    ) as exc:
        print(f"Error: {exc}")
        return 1


# ---------------------------------------------------------------------------
# Handlers


def _resolve_host(target: str, workspace: Optional[str]) -> ProxmoxHost:
    host = find_proxmox_host(target, workspace)
    if not host:
        raise ValueError(
            f"No registered Proxmox host matching '{target}'. "
            f"Use 'infra-tools proxmox add' first."
        )
    return host


def _cmd_shell(args: argparse.Namespace, workspace: Optional[str]) -> int:
    return run_proxmox_shell(workspace)


def _cmd_hosts_list(args: argparse.Namespace, workspace: Optional[str]) -> int:
    hosts = load_proxmox_hosts(workspace)
    if args.json:
        payload = envelope(
            provider="proxmox",
            host="workspace",
            operation="hosts",
            resources=[host.to_dict() for host in hosts],
        )
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    if not hosts:
        print("No Proxmox hosts registered.")
        return 0
    print(
        f"{'NAME':<20} {'ADDRESS':<25} {'USER':<12} "
        f"{'ROOT':<12} {'TEMPLATE':<12} {'BRIDGE':<10} DESCRIPTION"
    )
    print("-" * 110)
    for host in hosts:
        print(
            f"{host.name:<20} {host.address:<25} {host.user:<12} "
            f"{(host.default_storage or '-').ljust(12)}"
            f"{(host.default_template_storage or '-').ljust(12)}"
            f"{(host.default_bridge or '-').ljust(10)}"
            f"{host.description or ''}"
            + (f" tags={','.join(host.tags)}" if host.tags else "")
        )
    return 0


def _cmd_hosts_add(args: argparse.Namespace, workspace: Optional[str]) -> int:
    host = ProxmoxHost(
        name=args.name,
        address=args.address,
        user=args.user,
        ssh_key=args.ssh_key,
        description=args.description,
        default_storage=args.default_storage,
        default_template_storage=args.default_template_storage,
        default_bridge=args.default_bridge,
    )
    add_proxmox_host(host, workspace, replace=args.replace)
    print(f"Registered Proxmox host '{host.name}' ({host.address}).")
    return 0


def _cmd_hosts_remove(args: argparse.Namespace, workspace: Optional[str]) -> int:
    if remove_proxmox_host(args.target, workspace):
        print(f"Removed Proxmox host '{args.target}'.")
        return 0
    print(f"No Proxmox host matching '{args.target}'.")
    return 1


def _cmd_probe(args: argparse.Namespace, workspace: Optional[str]) -> int:
    host = _resolve_host(args.host, workspace)
    facts = probe_proxmox_host(host.address, user=host.user, hosted_key=host.ssh_key)
    updated_host = replace(
        host,
        default_storage=host.default_storage or facts.default_root_storage,
        default_template_storage=(
            host.default_template_storage or facts.default_template_storage
        ),
        default_bridge=host.default_bridge or facts.default_bridge,
        facts=facts,
    )
    updated_host = sync_proxmox_host(updated_host, workspace)

    print(f"Probed Proxmox host '{updated_host.name}' ({updated_host.address}).")
    print(f"  node:      {facts.node_name or '-'}")
    print(f"  root:      {updated_host.default_storage or '-'}")
    print(f"  template:  {updated_host.default_template_storage or '-'}")
    print(f"  bridge:    {updated_host.default_bridge or '-'}")
    print(f"  gateway:   {facts.gateway or '-'}")
    print(
        "  dns:       "
        + (", ".join(facts.nameservers) if facts.nameservers else "-")
    )
    print(
        "  bridges:   "
        + (", ".join(facts.bridges) if facts.bridges else "-")
    )
    if facts.storage_pools:
        print("  storage:")
        for pool in facts.storage_pools:
            content = ",".join(pool.content) if pool.content else "-"
            print(
                f"    {pool.name}: {pool.type or '-'} / "
                f"{pool.status or '-'} / {content}"
            )
    return 0


def _cmd_probe_cluster(args: argparse.Namespace, workspace: Optional[str]) -> int:
    discovered_hosts = probe_proxmox_cluster(
        args.address,
        user=args.user,
        hosted_key=args.ssh_key,
        tags=args.tag,
    )
    if not discovered_hosts:
        print("No Proxmox nodes discovered.")
        return 0

    saved_hosts = [sync_proxmox_host(host, workspace) for host in discovered_hosts]
    print(
        f"Discovered {len(saved_hosts)} Proxmox node(s) from {args.address}:"
    )
    for host in saved_hosts:
        tags_text = ",".join(host.tags) if host.tags else "-"
        print(
            f"  {host.name:<20} {host.address:<25} "
            f"root={host.default_storage or '-'} "
            f"template={host.default_template_storage or '-'} "
            f"bridge={host.default_bridge or '-'} "
            f"tags={tags_text}"
        )
    return 0


def _cmd_rolling_update(args: argparse.Namespace, workspace: Optional[str]) -> int:
    return run_cluster_update(
        list(args.targets),
        workspace=workspace,
        dry_run=args.dry_run,
        reboot_timeout=args.reboot_timeout,
    )


def _cmd_containers_ls(args: argparse.Namespace, workspace: Optional[str]) -> int:
    host = _resolve_host(args.host, workspace)
    rows = list_containers(host)
    if args.json:
        payload = envelope(
            provider=host.provider,
            host=host.name,
            operation="list",
            resources=[
                VMRecord(
                    id=str(row.vmid),
                    kind=row.guest_type,
                    name=row.name,
                    state=row.status,
                    lock=row.lock,
                ).to_dict()
                for row in rows
            ],
        )
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    if not rows:
        print(f"No guests on {host.name} ({host.address}).")
        return 0
    print(f"Guests on {host.name} ({host.address}):")
    print(f"  {'VMID':>6} {'TYPE':<6} {'STATUS':<10} {'LOCK':<10} NAME")
    for row in rows:
        print(
            f"  {row.vmid:>6} {row.guest_type:<6} {row.status:<10} {(row.lock or '-'):<10} "
            f"{row.name or '-'}"
        )
    return 0


def _cmd_status(args: argparse.Namespace, workspace: Optional[str]) -> int:
    host = _resolve_host(args.host, workspace)
    state = get_container_status(host, args.vmid)
    if args.json:
        guests = list_containers(host)
        guest = next((item for item in guests if item.vmid == args.vmid), None)
        resource = VMRecord(
            id=str(args.vmid),
            kind=guest.guest_type if guest else "unknown",
            name=guest.name if guest else "",
            state=state,
            lock=guest.lock if guest else None,
        ).to_dict()
        payload = envelope(
            provider=host.provider,
            host=host.name,
            operation="status",
            resources=[resource],
        )
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    print(state)
    return 0


def _cmd_start(args: argparse.Namespace, workspace: Optional[str]) -> int:
    host = _resolve_host(args.host, workspace)
    start_container(host, args.vmid)
    print(f"Started guest {args.vmid} on {host.name}.")
    return 0


def _cmd_pause(args: argparse.Namespace, workspace: Optional[str]) -> int:
    host = _resolve_host(args.host, workspace)
    suspend_guest(host, args.vmid)
    print(f"Paused guest {args.vmid} on {host.name}.")
    return 0


def _cmd_resume(args: argparse.Namespace, workspace: Optional[str]) -> int:
    host = _resolve_host(args.host, workspace)
    resume_guest(host, args.vmid)
    print(f"Resumed guest {args.vmid} on {host.name}.")
    return 0


def _cmd_stop(args: argparse.Namespace, workspace: Optional[str]) -> int:
    host = _resolve_host(args.host, workspace)
    stop_container(host, args.vmid, force=args.force)
    verb = "Stopped" if args.force else "Shut down"
    print(f"{verb} guest {args.vmid} on {host.name}.")
    return 0


def _cmd_destroy(args: argparse.Namespace, workspace: Optional[str]) -> int:
    host = _resolve_host(args.host, workspace)
    if not args.yes:
        prompt = (
            f"Destroy guest {args.vmid} on {host.name} ({host.address})? "
            f"Type 'yes' to confirm: "
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
    destroy_container(host, args.vmid, force=args.force)
    print(f"Destroyed guest {args.vmid} on {host.name}.")
    return 0


def _cmd_health(args: argparse.Namespace, workspace: Optional[str]) -> int:
    host = _resolve_host(args.host, workspace)
    report = health_check(host, args.vmid, probe_ssh=not args.no_ssh)
    if args.json:
        payload = envelope(
            provider=host.provider,
            host=host.name,
            operation="health",
            resources=[
                VMHealth(
                    id=str(report.vmid),
                    kind=report.guest_type,
                    state=report.status,
                    healthy=report.healthy,
                    address=report.ip,
                    pingable=report.pingable,
                    ssh_open=report.ssh_open,
                    notes=list(report.notes),
                ).to_dict()
            ],
        )
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if report.healthy else 1
    print(f"VMID {report.vmid} on {host.name} ({host.address}):")
    print(f"  status:  {report.status}")
    print(f"  ip:      {report.ip or 'n/a'}")
    print(f"  ping:    {_tristate(report.pingable)}")
    print(f"  ssh:22:  {_tristate(report.ssh_open)}")
    print(f"  result:  {'HEALTHY' if report.healthy else 'UNHEALTHY'}")
    for note in report.notes:
        print(f"  note:    {note}")
    return 0 if report.healthy else 1


def _cmd_config(args: argparse.Namespace, workspace: Optional[str]) -> int:
    host = _resolve_host(args.host, workspace)
    if args.pending:
        data = get_container_pending(host, args.vmid)
        label = "Pending configuration"
    else:
        data = get_container_config(host, args.vmid)
        label = "Configuration"
    if not data:
        print(f"{label} for VMID {args.vmid} on {host.name}: (empty)")
        return 0
    print(f"{label} for VMID {args.vmid} on {host.name}:")
    for key, value in sorted(data.items()):
        print(f"  {key}: {value}")
    return 0


def _cmd_reconfigure(args: argparse.Namespace, workspace: Optional[str]) -> int:
    if not args.options:
        print("Error: At least one --set KEY=VALUE option is required.")
        return 1
    options: dict[str, str] = {}
    for item in args.options:
        if "=" not in item:
            print(f"Error: --set value must be KEY=VALUE, got: {item!r}")
            return 1
        key, _, value = item.partition("=")
        options[key] = value
    host = _resolve_host(args.host, workspace)
    reconfigure_container(host, args.vmid, options, dry_run=args.dry_run)
    prefix = "Would set" if args.dry_run else "Set"
    print(
        f"{prefix} {len(options)} option(s) on VMID {args.vmid} "
        f"on {host.name}."
    )
    return 0


def _parse_memory_mb(value: str) -> int:
    """Parse a memory string to MiB. Accepts plain integers or N[M|G] suffixes."""
    value = value.strip()
    if value.endswith(("G", "g")):
        try:
            return int(value[:-1]) * 1024
        except ValueError:
            pass
    if value.endswith(("M", "m")):
        try:
            return int(value[:-1])
        except ValueError:
            pass
    try:
        return int(value)
    except ValueError:
        raise ValueError(
            f"Invalid memory value: {value!r}. "
            "Use a plain integer (MiB), or add a suffix: 512M or 4G."
        )


def _cmd_modify(args: argparse.Namespace, workspace: Optional[str]) -> int:
    if args.cores is None and args.memory is None:
        print("Error: At least one of --cores or --memory is required.")
        return 1
    memory_mb: Optional[int] = None
    if args.memory is not None:
        try:
            memory_mb = _parse_memory_mb(args.memory)
        except ValueError as exc:
            print(f"Error: {exc}")
            return 1
    host = _resolve_host(args.host, workspace)
    modify_container(
        host,
        args.vmid,
        cores=args.cores,
        memory_mb=memory_mb,
        dry_run=args.dry_run,
    )
    parts = []
    if args.cores is not None:
        parts.append(f"cores={args.cores}")
    if memory_mb is not None:
        parts.append(f"memory={memory_mb}M")
    prefix = "Would modify" if args.dry_run else "Modified"
    print(
        f"{prefix} VMID {args.vmid} on {host.name}: {', '.join(parts)}."
    )
    return 0


def _cmd_resize_disk(args: argparse.Namespace, workspace: Optional[str]) -> int:
    host = _resolve_host(args.host, workspace)
    resize_container_disk(
        host, args.vmid, args.volume, args.size, dry_run=args.dry_run
    )
    prefix = "Would resize" if args.dry_run else "Resized"
    print(
        f"{prefix} {args.volume} on VMID {args.vmid} on {host.name} "
        f"to {args.size}."
    )
    return 0


def _cmd_top(args: argparse.Namespace, workspace: Optional[str]) -> int:
    any_error = False
    summaries: list[dict[str, object]] = []
    for name in args.hosts:
        host = _resolve_host(name, workspace)
        try:
            summary = get_node_summary(host)
        except ProxmoxSummaryError as exc:
            print(f"Error ({host.name}): {exc}")
            any_error = True
            continue
        summaries.append({"host": host.name, **summary.to_dict()})
        if args.json:
            continue
        print(format_node_summary(summary))
        if len(args.hosts) > 1:
            print()
    if args.json:
        payload = envelope(
            provider="proxmox",
            host="multiple" if len(args.hosts) > 1 else args.hosts[0],
            operation="top",
            resources=summaries,
        )
        print(json.dumps(payload, indent=2, sort_keys=True))
    return 1 if any_error else 0


def _cmd_audit(args: argparse.Namespace, workspace: Optional[str]) -> int:
    reports = [
        collect_maintenance_report(_resolve_host(name, workspace))
        for name in args.hosts
    ]
    if args.json:
        payload = envelope(
            provider="proxmox",
            host="multiple" if len(reports) > 1 else reports[0].host_name,
            operation="audit",
            resources=[report.to_dict() for report in reports],
        )
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        for index, report in enumerate(reports):
            if index:
                print()
            print(format_maintenance_report(report))
    return 0 if all(report.healthy for report in reports) else 1


def _cmd_plan_missing(args: argparse.Namespace, workspace: Optional[str]) -> int:
    print("Usage: infra-tools proxmox plan {place,rebalance} [...]")
    return 1


def _cmd_plan_place(args: argparse.Namespace, workspace: Optional[str]) -> int:
    hosts = load_proxmox_hosts(workspace)
    if not hosts:
        print("No Proxmox hosts registered. Use 'proxmox add' first.")
        return 1
    request = PlacementRequest(
        cores=args.cores,
        memory_mib=args.memory_mib,
        disk_gib=args.disk_gib,
        prefer_tags=list(args.prefer_tags or []),
        avoid_tags=list(args.avoid_tags or []),
        exclude_nodes=list(args.exclude_nodes or []),
    )
    snapshots, warnings = collect_snapshots(hosts)
    plan = plan_placement(snapshots, request)
    plan.warnings.extend(warnings)
    print(format_plan(plan, limit=args.limit))
    return 0 if any(c.fits for c in plan.candidates) else 1


def _cmd_plan_rebalance(args: argparse.Namespace, workspace: Optional[str]) -> int:
    hosts = load_proxmox_hosts(workspace)
    if not hosts:
        print("No Proxmox hosts registered. Use 'proxmox add' first.")
        return 1
    snapshots, warnings = collect_snapshots(hosts)
    guests_by_host: dict[str, list[ContainerInfo]] = {}
    for snap in snapshots:
        try:
            guests = list_containers(snap.host)
        except ProxmoxManageError as exc:
            warnings.append(f"{snap.host.name}: list_containers failed: {exc}")
            continue
        guests_by_host[snap.host.name] = [
            g for g in guests if g.status.lower() == "running"
        ]

    guest_descriptions = {
        host_name: [
            f"{g.vmid:>4} {g.guest_type:<3} {g.status:<8} {g.name}"
            for g in guests
        ]
        for host_name, guests in guests_by_host.items()
    }
    suggestions = plan_rebalance(snapshots, guest_descriptions)
    print(format_rebalance(suggestions, limit=args.limit))
    if warnings:
        print("\nWarnings:")
        for w in warnings:
            print(f"  - {w}")

    if args.apply is None:
        return 0

    # --apply: locate the requested guest on a hot node, pick a destination,
    # confirm, then call migrate_guest. Refuse if the source node is not hot
    # — rebalance is the wrong tool for ad-hoc migrations (use `proxmox
    # migrate` for that).
    hot_hosts = {sug.hot_host: sug for sug in suggestions}
    if not hot_hosts:
        print("\n--apply: no nodes are over the rebalance thresholds; nothing to do.")
        return 1

    source_host: Optional[ProxmoxHost] = None
    source_guest: Optional[ContainerInfo] = None
    for host_name, guests in guests_by_host.items():
        if host_name not in hot_hosts:
            continue
        for g in guests:
            if g.vmid == args.apply:
                source_guest = g
                source_host = next(
                    s.host for s in snapshots if s.host.name == host_name
                )
                break
        if source_guest:
            break

    if not source_guest or not source_host:
        print(
            f"\n--apply: VMID {args.apply} is not running on any hot node. "
            f"Hot nodes: {', '.join(sorted(hot_hosts)) or '(none)'}."
        )
        return 1

    sug = hot_hosts[source_host.name]
    if args.to:
        target_host = _resolve_host(args.to, workspace)
        if target_host.name == source_host.name:
            print(f"\n--apply: target {args.to} is the source host.")
            return 1
    else:
        fits = [c for c in sug.destinations if c.fits]
        if not fits:
            print(
                f"\n--apply: no destination on the cluster has room for "
                f"VMID {args.apply}. Pass --to to override."
            )
            return 1
        target_host = _resolve_host(fits[0].host_name, workspace)

    print(
        f"\n--apply: migrate VMID {args.apply} ({source_guest.name}, "
        f"{source_guest.guest_type}) from {source_host.name} to {target_host.name}"
        + (" [online]" if args.online else "")
        + (" [with-local-disks]" if args.with_local_disks else "")
        + (" [dry-run]" if args.dry_run else "")
    )
    if not args.yes and not args.dry_run:
        try:
            response = input("Proceed? [y/N] ").strip().lower()
        except EOFError:
            response = ""
        if response not in {"y", "yes"}:
            print("Aborted.")
            return 1

    migrate_guest(
        source_host,
        args.apply,
        target_host,
        online=args.online,
        with_local_disks=args.with_local_disks,
        dry_run=args.dry_run,
    )
    prefix = "Would migrate" if args.dry_run else "Migrated"
    print(
        f"{prefix} VMID {args.apply} from {source_host.name} to {target_host.name}."
    )
    return 0


def _cmd_backups_list(args: argparse.Namespace, workspace: Optional[str]) -> int:
    host = _resolve_host(args.host, workspace)
    backups = list_backups(host, args.vmid)
    if not backups:
        print(f"No backups found for VMID {args.vmid} on {host.name}.")
        return 0
    print(f"Backups for VMID {args.vmid} on {host.name}:")
    for b in backups:
        size_str = _fmt_bytes(b.size) if b.size else "-"
        date_str = _fmt_timestamp(b.ctime) if b.ctime else "-"
        desc = f"  {b.notes}" if b.notes else ""
        print(f"  {date_str}  {size_str:<10}  {b.filename}{desc}")
    return 0


def _cmd_backup(args: argparse.Namespace, workspace: Optional[str]) -> int:
    host = _resolve_host(args.host, workspace)
    create_backup(
        host,
        args.vmid,
        storage=args.storage,
        mode=args.mode,
        compress=args.compress,
        dry_run=args.dry_run,
    )
    prefix = "Would back up" if args.dry_run else "Backed up"
    print(f"{prefix} VMID {args.vmid} on {host.name}.")
    return 0


def _cmd_migrate(args: argparse.Namespace, workspace: Optional[str]) -> int:
    src = _resolve_host(args.host, workspace)
    target = _resolve_host(args.target, workspace)
    migrate_guest(
        src,
        args.vmid,
        target,
        online=args.online,
        with_local_disks=args.with_local_disks,
        dry_run=args.dry_run,
    )
    prefix = "Would migrate" if args.dry_run else "Migrated"
    print(f"{prefix} VMID {args.vmid} from {src.name} to {target.name}.")
    return 0


def _cmd_clean_disks(args: argparse.Namespace, workspace: Optional[str]) -> int:
    host = _resolve_host(args.host, workspace)
    orphans = list_orphaned_volumes(host)
    if not orphans:
        print(f"No orphaned volumes found on {host.name}.")
        return 0

    print(f"Orphaned volumes on {host.name} ({len(orphans)}):")
    for vol in orphans:
        print(f"  {vol.volid}  vmid={vol.vmid}  size={vol.size}  format={vol.format or '-'}")

    if not args.delete:
        print("Run with --delete to remove them.")
        return 0

    if args.dry_run:
        print("Dry run — nothing deleted.")
        return 0

    if not args.yes:
        try:
            response = input(
                f"\nDelete {len(orphans)} orphaned volume(s) on {host.name}? "
                "Type 'yes' to confirm: "
            )
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            return 1
        if response.strip().lower() != "yes":
            print("Aborted.")
            return 1

    errors = 0
    for vol in orphans:
        try:
            delete_volume(host, vol.volid)
            print(f"  Deleted {vol.volid}")
        except ProxmoxStorageError as exc:
            print(f"  Error deleting {vol.volid}: {exc}")
            errors += 1
    return 1 if errors else 0


def _fmt_bytes(b: int) -> str:
    if b >= 1024 ** 3:
        return f"{b / 1024 ** 3:.1f} GiB"
    if b >= 1024 ** 2:
        return f"{b / 1024 ** 2:.0f} MiB"
    return f"{b / 1024:.0f} KiB"


def _fmt_timestamp(ts: int) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def _cmd_unlock(args: argparse.Namespace, workspace: Optional[str]) -> int:
    host = _resolve_host(args.host, workspace)
    unlock_guest(host, args.vmid, dry_run=args.dry_run)
    prefix = "Would unlock" if args.dry_run else "Unlocked"
    print(f"{prefix} VMID {args.vmid} on {host.name}.")
    return 0


def _cmd_snapshots_list(args: argparse.Namespace, workspace: Optional[str]) -> int:
    host = _resolve_host(args.host, workspace)
    snaps = list_snapshots(host, args.vmid)
    if not snaps:
        print(f"No snapshots for VMID {args.vmid} on {host.name}.")
        return 0
    print(f"Snapshots for VMID {args.vmid} on {host.name}:")
    for snap in snaps:
        marker = "*" if snap.is_current else " "
        desc = f"  {snap.description}" if snap.description else ""
        print(f"  {marker} {snap.name}{desc}")
    return 0


def _cmd_snapshot(args: argparse.Namespace, workspace: Optional[str]) -> int:
    host = _resolve_host(args.host, workspace)
    snapshot_guest(
        host, args.vmid, args.name,
        description=args.description,
        dry_run=args.dry_run,
    )
    prefix = "Would create" if args.dry_run else "Created"
    print(f"{prefix} snapshot '{args.name}' for VMID {args.vmid} on {host.name}.")
    return 0


def _cmd_rollback(args: argparse.Namespace, workspace: Optional[str]) -> int:
    host = _resolve_host(args.host, workspace)
    rollback_guest(host, args.vmid, args.name, dry_run=args.dry_run)
    prefix = "Would roll back" if args.dry_run else "Rolled back"
    print(f"{prefix} VMID {args.vmid} on {host.name} to snapshot '{args.name}'.")
    return 0


def _cmd_delsnapshot(args: argparse.Namespace, workspace: Optional[str]) -> int:
    host = _resolve_host(args.host, workspace)
    if not args.dry_run and not getattr(args, 'yes', False):
        try:
            response = input(f"Delete snapshot '{args.name}' for guest {args.vmid} on {host.name}? Type 'yes' to confirm: ")
        except (EOFError, KeyboardInterrupt):
            response = ''
        if response.strip().lower() != 'yes':
            print('Aborted.')
            return 1
    delete_snapshot(host, args.vmid, args.name, dry_run=args.dry_run)
    prefix = "Would delete" if args.dry_run else "Deleted"
    print(f"{prefix} snapshot '{args.name}' for VMID {args.vmid} on {host.name}.")
    return 0


def _cmd_notifications_install_webhook(
    args: argparse.Namespace,
    workspace: Optional[str],
) -> int:
    host = _resolve_host(args.host, workspace)
    config = install_webhook_notifications(
        host,
        args.url,
        endpoint_name=args.endpoint_name,
        matcher_name=args.matcher_name,
        severities=args.severity,
        send_test=args.send_test,
        dry_run=args.dry_run,
    )
    prefix = "Would configure" if args.dry_run else "Configured"
    print(
        f"{prefix} Proxmox webhook notifications on {host.name} "
        f"using endpoint '{config.endpoint_name}' and matcher '{config.matcher_name}'."
    )
    return 0


def _cmd_notifications_missing(args: argparse.Namespace, workspace: Optional[str]) -> int:
    raise ValueError("Choose a notifications subcommand: install-webhook or test-webhook")


def _cmd_notifications_test_webhook(
    args: argparse.Namespace,
    workspace: Optional[str],
) -> int:
    host = _resolve_host(args.host, workspace)
    send_webhook_test_notification(
        host,
        args.endpoint_name,
        dry_run=args.dry_run,
    )
    prefix = "Would send" if args.dry_run else "Sent"
    print(f"{prefix} Proxmox test notification via endpoint '{args.endpoint_name}'.")
    return 0


def _tristate(value: Optional[bool]) -> str:
    if value is None:
        return "skipped"
    return "ok" if value else "fail"


__all__ = [
    "ProxmoxShell",
    "add_proxmox_subparser",
    "run_proxmox_command",
]
