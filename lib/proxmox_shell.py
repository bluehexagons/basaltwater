#!/usr/bin/env python3
"""Interactive command-line shell for managing Proxmox-hosted guests.

Designed to feel like a small REPL on top of :mod:`lib.proxmox_manage` and
:mod:`lib.proxmox_hosts`. The shell starts with no host selected; ``use``
chooses one from the workspace registry, then commands like ``ls``, ``status``,
``start``, ``stop``, ``destroy``, and ``health`` operate on it.

The shell is fully driver-agnostic: tests construct one with a custom
``input_func``/``output_func`` to drive command sequences without a TTY.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field, replace
from typing import Callable, Optional

from lib.proxmox_backup import ProxmoxBackupError, create_backup, list_backups
from lib.proxmox_guest import probe_proxmox_cluster, probe_proxmox_host
from lib.proxmox_migrate import ProxmoxMigrateError, migrate_guest
from lib.proxmox_storage import ProxmoxStorageError, delete_volume, list_orphaned_volumes
from lib.proxmox_summary import ProxmoxSummaryError, format_node_summary, get_node_summary
from lib.proxmox_hosts import (
    ProxmoxHost,
    add_proxmox_host,
    find_proxmox_host,
    load_proxmox_hosts,
    remove_proxmox_host,
    sync_proxmox_host,
)


def _shell_fmt_bytes(b: int) -> str:
    if b >= 1024 ** 3:
        return f"{b / 1024 ** 3:.1f}G"
    if b >= 1024 ** 2:
        return f"{b / 1024 ** 2:.0f}M"
    return f"{b / 1024:.0f}K"


def _shell_fmt_ts(ts: int) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
from lib.proxmox_manage import (
    ContainerInfo,
    HealthReport,
    ProxmoxManageError,
    delete_snapshot,
    destroy_container,
    get_container_config,
    get_container_pending,
    health_check,
    list_containers,
    list_snapshots,
    modify_container,
    reconfigure_container,
    resize_container_disk,
    rollback_guest,
    snapshot_guest,
    start_container,
    stop_container,
    unlock_guest,
)


InputFn = Callable[[str], str]
OutputFn = Callable[[str], None]


HELP_TEXT = """\
Available commands:
  hosts                       List registered Proxmox hosts
  use <name|address>          Select a host for subsequent commands
  add <name> <addr> [user] [key]
                               Register a new Proxmox host
  remove <name|address>       Remove a host from the registry
  host                        Show details for the active host
  probe                       Probe the active host and cache setup defaults
  probe-cluster <addr> [--user USER] [--key PATH] [--tag TAG ...]
                               Discover and register every node in a Proxmox cluster
  ls                          List guests on the active host
  status <vmid>               Show guest status
  start <vmid>                Start a guest
  stop <vmid> [--force]       Shutdown (or force-stop) a guest
  destroy <vmid> [--force]    Destroy a guest (asks for confirmation)
  health <vmid>               Run a health check
  config <vmid> [--pending]   Show guest configuration (or pending changes)
  set <vmid> key=value [...]  Set pct/qm configuration options
  modify <vmid> [--cores N] [--memory N[M|G]]
                              Change CPU cores or memory allocation
  resize <vmid> <volume> <size>
                              Increase a disk volume (e.g. resize 100 rootfs 20G)
  snapshots <vmid>            List snapshots for a guest
  snapshot <vmid> <name>      Create a snapshot
  rollback <vmid> <name>      Roll back a guest to a snapshot
  delsnapshot <vmid> <name> [--yes] [--dry-run]
                              Delete a snapshot (asks for confirmation)
  unlock <vmid>               Remove a stuck management lock from a guest
  rolling-update <name> [...] [--resume] [--dry-run]
                              Patch evacuated nodes using durable checkpoints
  top                         Show CPU, memory, storage, and guest counts
  backups <vmid>              List backups for a guest
  backup <vmid> [--storage POOL] [--mode snapshot|suspend|stop]
                              Create an immediate vzdump backup
  migrate <vmid> <target>     Migrate a guest to another registered host
  clean-disks [--delete] [--yes]
                              List (and optionally delete) orphaned volumes
  help                        Show this help text
  quit / exit                 Leave the shell
"""


def _format_host_row(host: ProxmoxHost) -> str:
    bits = [host.name, host.address, host.user]
    if host.description:
        bits.append(f"({host.description})")
    defaults = []
    if host.default_storage:
        defaults.append(f"root={host.default_storage}")
    if host.default_template_storage:
        defaults.append(f"template={host.default_template_storage}")
    if host.default_bridge:
        defaults.append(f"bridge={host.default_bridge}")
    if defaults:
        bits.append("[" + ", ".join(defaults) + "]")
    if host.tags:
        bits.append("tags=" + ",".join(host.tags))
    return "  " + " | ".join(bits)


def _format_container_row(info: ContainerInfo) -> str:
    parts = [
        f"{info.vmid:>6}",
        f"{info.guest_type:<6}",
        f"{info.status:<10}",
        f"{(info.lock or '-'):<10}",
        info.name or "-",
    ]
    return "  " + " ".join(parts)


def _format_health(report: HealthReport) -> str:
    state = "HEALTHY" if report.healthy else "UNHEALTHY"
    lines = [
        f"  VMID:    {report.vmid}",
        f"  Type:    {report.guest_type or 'unknown'}",
        f"  Status:  {report.status}",
        f"  IP:      {report.ip or 'n/a'}",
        f"  Ping:    {_tristate(report.pingable)}",
        f"  SSH:22:  {_tristate(report.ssh_open)}",
        f"  Result:  {state}",
    ]
    for note in report.notes:
        lines.append(f"  Note:    {note}")
    return "\n".join(lines)


def _tristate(value: Optional[bool]) -> str:
    if value is None:
        return "skipped"
    return "ok" if value else "fail"


def _format_host_details(host: ProxmoxHost) -> str:
    lines = [
        f"  Name:      {host.name}",
        f"  Address:   {host.address}",
        f"  User:      {host.user}",
        f"  SSH key:   {host.ssh_key or 'default SSH config'}",
        f"  Root pool: {host.default_storage or 'auto'}",
        f"  Template:  {host.default_template_storage or 'auto'}",
        f"  Bridge:    {host.default_bridge or 'auto'}",
    ]
    if host.tags:
        lines.append(f"  Tags:      {', '.join(host.tags)}")
    if host.description:
        lines.append(f"  Notes:     {host.description}")
    if host.facts:
        lines.append(f"  Node:      {host.facts.node_name or '-'}")
        lines.append(f"  Gateway:   {host.facts.gateway or '-'}")
        lines.append(
            "  DNS:       "
            + (", ".join(host.facts.nameservers) if host.facts.nameservers else "-")
        )
        lines.append(
            "  Bridges:   "
            + (", ".join(host.facts.bridges) if host.facts.bridges else "-")
        )
        if host.facts.storage_pools:
            lines.append("  Storage:")
            for pool in host.facts.storage_pools:
                content = ",".join(pool.content) if pool.content else "-"
                lines.append(
                    f"    {pool.name}: {pool.type or '-'} / "
                    f"{pool.status or '-'} / {content}"
                )
    return "\n".join(lines)


def _parse_memory_mb_shell(value: str) -> int:
    """Parse a memory string to MiB for shell commands."""
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
            "Use a plain integer (MiB) or a suffix: 512M or 4G."
        )


@dataclass
class ShellState:
    workspace: Optional[str] = None
    active_host: Optional[ProxmoxHost] = None
    history: list[str] = field(default_factory=list)


class ProxmoxShell:
    """Small REPL wrapper around the Proxmox management helpers."""

    def __init__(
        self,
        *,
        workspace: Optional[str] = None,
        input_func: InputFn = input,
        output_func: OutputFn = print,
        confirm_destroy: Optional[Callable[[ContainerInfo, ProxmoxHost], bool]] = None,
    ) -> None:
        self.state = ShellState(workspace=workspace)
        self._input = input_func
        self._output = output_func
        self._confirm_destroy = confirm_destroy or self._default_confirm_destroy

    # ------------------------------------------------------------------
    # Public entry points

    def run(self) -> int:
        """Drive the REPL until the user quits or input ends."""
        self._output("basaltw proxmox shell — type 'help' for commands.")
        while True:
            prompt = self._make_prompt()
            try:
                raw = self._input(prompt)
            except (EOFError, KeyboardInterrupt):
                self._output("")
                return 0
            line = raw.strip()
            if not line:
                continue
            self.state.history.append(line)
            if line in {"quit", "exit"}:
                return 0
            try:
                self.dispatch(line)
            except (
                ProxmoxManageError,
                ProxmoxStorageError,
                ProxmoxBackupError,
                ProxmoxMigrateError,
            ) as exc:
                self._output(f"Error: {exc}")
            except ValueError as exc:
                self._output(f"Error: {exc}")

    def dispatch(self, line: str) -> None:
        """Parse and execute a single shell command line."""
        try:
            tokens = shlex.split(line)
        except ValueError as exc:
            raise ValueError(f"Could not parse command: {exc}")
        if not tokens:
            return
        cmd, *args = tokens
        handler = self._handlers().get(cmd)
        if handler is None:
            raise ValueError(
                f"Unknown command '{cmd}'. Type 'help' for the command list."
            )
        handler(args)

    # ------------------------------------------------------------------
    # Internal helpers

    def _handlers(self) -> dict[str, Callable[[list[str]], None]]:
        return {
            "help": self._cmd_help,
            "hosts": self._cmd_hosts,
            "use": self._cmd_use,
            "add": self._cmd_add,
            "remove": self._cmd_remove,
            "rm": self._cmd_remove,
            "host": self._cmd_host,
            "probe": self._cmd_probe,
            "probe-cluster": self._cmd_probe_cluster,
            "cluster-probe": self._cmd_probe_cluster,
            "discover": self._cmd_probe,
            "ls": self._cmd_ls,
            "list": self._cmd_ls,
            "status": self._cmd_status,
            "start": self._cmd_start,
            "stop": self._cmd_stop,
            "destroy": self._cmd_destroy,
            "rmct": self._cmd_destroy,
            "health": self._cmd_health,
            "check": self._cmd_health,
            "config": self._cmd_config,
            "set": self._cmd_set,
            "modify": self._cmd_modify,
            "resize": self._cmd_resize,
            "snapshots": self._cmd_snapshots,
            "snapshot": self._cmd_snapshot,
            "rollback": self._cmd_rollback,
            "delsnapshot": self._cmd_delsnapshot,
            "unlock": self._cmd_unlock,
            "rolling-update": self._cmd_rolling_update,
            "top": self._cmd_top,
            "backups": self._cmd_backups,
            "backup": self._cmd_backup,
            "migrate": self._cmd_migrate,
            "clean-disks": self._cmd_clean_disks,
        }

    def _make_prompt(self) -> str:
        if self.state.active_host:
            return f"proxmox[{self.state.active_host.name}]> "
        return "proxmox> "

    def _require_host(self) -> ProxmoxHost:
        if not self.state.active_host:
            raise ValueError(
                "No active host. Use 'hosts' to list and 'use <name>' to select one."
            )
        return self.state.active_host

    @staticmethod
    def _default_confirm_destroy(
        info: ContainerInfo, host: ProxmoxHost
    ) -> bool:
        prompt = (
            f"Destroy guest {info.vmid} '{info.name}' on {host.name} "
            f"({host.address})? Type 'yes' to confirm: "
        )
        try:
            response = input(prompt)
        except (EOFError, KeyboardInterrupt):
            return False
        return response.strip().lower() == "yes"

    # ------------------------------------------------------------------
    # Commands

    def _cmd_help(self, args: list[str]) -> None:
        self._output(HELP_TEXT)

    def _cmd_hosts(self, args: list[str]) -> None:
        hosts = load_proxmox_hosts(self.state.workspace)
        if not hosts:
            self._output("No Proxmox hosts registered. Use 'add <name> <addr>'.")
            return
        self._output("Registered Proxmox hosts:")
        for host in hosts:
            marker = "*" if (
                self.state.active_host
                and self.state.active_host.name == host.name
            ) else " "
            self._output(marker + _format_host_row(host))

    def _cmd_use(self, args: list[str]) -> None:
        if len(args) != 1:
            raise ValueError("Usage: use <name|address>")
        host = find_proxmox_host(args[0], self.state.workspace)
        if not host:
            raise ValueError(f"No registered host matching '{args[0]}'")
        self.state.active_host = host
        self._output(f"Using {host.name} ({host.address})")

    def _cmd_add(self, args: list[str]) -> None:
        if len(args) < 2 or len(args) > 4:
            raise ValueError(
                "Usage: add <name> <address> [user] [ssh_key_path]"
            )
        name, address = args[0], args[1]
        user = args[2] if len(args) >= 3 else "root"
        ssh_key = args[3] if len(args) == 4 else None
        host = ProxmoxHost(
            name=name, address=address, user=user, ssh_key=ssh_key
        )
        add_proxmox_host(host, self.state.workspace, replace=True)
        self._output(f"Saved host {name} ({address}) as user {user}.")

    def _cmd_remove(self, args: list[str]) -> None:
        if len(args) != 1:
            raise ValueError("Usage: remove <name|address>")
        target = args[0]
        removed = remove_proxmox_host(target, self.state.workspace)
        if not removed:
            raise ValueError(f"No registered host matching '{target}'")
        if (
            self.state.active_host
            and (
                self.state.active_host.name.lower() == target.lower()
                or self.state.active_host.address == target
            )
        ):
            self.state.active_host = None
        self._output(f"Removed host '{target}'.")

    def _cmd_host(self, args: list[str]) -> None:
        host = self._require_host()
        self._output(_format_host_details(host))

    def _cmd_probe(self, args: list[str]) -> None:
        host = self._require_host()
        facts = probe_proxmox_host(
            host.address,
            user=host.user,
            hosted_key=host.ssh_key,
        )
        updated_host = replace(
            host,
            default_storage=host.default_storage or facts.default_root_storage,
            default_template_storage=(
                host.default_template_storage or facts.default_template_storage
            ),
            default_bridge=host.default_bridge or facts.default_bridge,
            facts=facts,
        )
        self.state.active_host = sync_proxmox_host(updated_host, self.state.workspace)
        self._output("Cached host facts and setup defaults:")
        self._output(_format_host_details(self.state.active_host))

    def _cmd_probe_cluster(self, args: list[str]) -> None:
        if not args:
            raise ValueError(
                "Usage: probe-cluster <address> [--user USER] [--key PATH] [--tag TAG ...]"
            )
        address = args[0]
        user = "root"
        ssh_key: Optional[str] = None
        tags: list[str] = []
        i = 1
        while i < len(args):
            token = args[i]
            if token == "--user":
                i += 1
                if i >= len(args):
                    raise ValueError("--user requires a value")
                user = args[i]
            elif token == "--key":
                i += 1
                if i >= len(args):
                    raise ValueError("--key requires a value")
                ssh_key = args[i]
            elif token == "--tag":
                i += 1
                if i >= len(args):
                    raise ValueError("--tag requires a value")
                tags.append(args[i])
            else:
                raise ValueError(f"Unknown option: {token!r}")
            i += 1

        discovered_hosts = probe_proxmox_cluster(
            address,
            user=user,
            hosted_key=ssh_key,
            tags=tags,
        )
        if not discovered_hosts:
            self._output("No cluster nodes discovered.")
            return

        saved_hosts = [
            sync_proxmox_host(host, self.state.workspace)
            for host in discovered_hosts
        ]
        self.state.active_host = next(
            (host for host in saved_hosts if host.address == address),
            saved_hosts[0],
        )
        self._output(f"Discovered {len(saved_hosts)} Proxmox node(s):")
        for host in saved_hosts:
            tag_text = ",".join(host.tags) if host.tags else "-"
            self._output(f"  {host.name} | {host.address} | tags={tag_text}")

    def _cmd_ls(self, args: list[str]) -> None:
        host = self._require_host()
        rows = list_containers(host)
        if not rows:
            self._output("(no guests)")
            return
        self._output(
            f"  {'VMID':>6} {'Type':<6} {'Status':<10} {'Lock':<10} Name"
        )
        for row in rows:
            self._output(_format_container_row(row))

    def _cmd_status(self, args: list[str]) -> None:
        host = self._require_host()
        vmid = self._parse_vmid(args, "status")
        from lib.proxmox_manage import get_container_status
        status = get_container_status(host, vmid)
        self._output(f"  VMID {vmid}: {status}")

    def _cmd_start(self, args: list[str]) -> None:
        host = self._require_host()
        vmid = self._parse_vmid(args, "start")
        start_container(host, vmid)
        self._output(f"  Started guest {vmid} on {host.name}.")

    def _cmd_stop(self, args: list[str]) -> None:
        host = self._require_host()
        force = "--force" in args
        rest = [a for a in args if a != "--force"]
        vmid = self._parse_vmid(rest, "stop")
        stop_container(host, vmid, force=force)
        self._output(
            f"  {'Stopped' if force else 'Shut down'} guest {vmid} on {host.name}."
        )

    def _cmd_destroy(self, args: list[str]) -> None:
        host = self._require_host()
        force = "--force" in args
        rest = [a for a in args if a != "--force"]
        vmid = self._parse_vmid(rest, "destroy")
        # Look up the guest so the confirmation prompt has its name.
        info: Optional[ContainerInfo] = None
        for row in list_containers(host):
            if row.vmid == vmid:
                info = row
                break
        if info is None:
            raise ValueError(
                f"No guest with VMID {vmid} on {host.name}"
            )
        if not self._confirm_destroy(info, host):
            self._output("  Destroy cancelled.")
            return
        destroy_container(host, vmid, force=force)
        self._output(f"  Destroyed guest {vmid} on {host.name}.")

    def _cmd_health(self, args: list[str]) -> None:
        host = self._require_host()
        vmid = self._parse_vmid(args, "health")
        report = health_check(host, vmid)
        self._output(_format_health(report))

    def _cmd_config(self, args: list[str]) -> None:
        host = self._require_host()
        pending = "--pending" in args
        rest = [a for a in args if a != "--pending"]
        vmid = self._parse_vmid(rest, "config")
        if pending:
            data = get_container_pending(host, vmid)
            label = "Pending"
        else:
            data = get_container_config(host, vmid)
            label = "Config"
        if not data:
            self._output(f"  {label} for VMID {vmid}: (empty)")
            return
        self._output(f"  {label} for VMID {vmid}:")
        for key, value in sorted(data.items()):
            self._output(f"    {key}: {value}")

    def _cmd_set(self, args: list[str]) -> None:
        host = self._require_host()
        if not args:
            raise ValueError("Usage: set <vmid> key=value [key=value ...]")
        vmid = self._parse_vmid([args[0]], "set")
        options: dict[str, str] = {}
        for item in args[1:]:
            if "=" not in item:
                raise ValueError(
                    f"Options must be key=value, got: {item!r}"
                )
            key, _, value = item.partition("=")
            options[key] = value
        if not options:
            raise ValueError("Usage: set <vmid> key=value [key=value ...]")
        reconfigure_container(host, vmid, options)
        self._output(
            f"  Set {len(options)} option(s) on VMID {vmid} on {host.name}."
        )

    def _cmd_modify(self, args: list[str]) -> None:
        host = self._require_host()
        if not args:
            raise ValueError(
                "Usage: modify <vmid> [--cores N] [--memory N[M|G]]"
            )
        vmid = self._parse_vmid([args[0]], "modify")
        rest = args[1:]
        cores: Optional[int] = None
        memory_mb: Optional[int] = None
        i = 0
        while i < len(rest):
            token = rest[i]
            if token in ("--cores", "-c"):
                i += 1
                if i >= len(rest):
                    raise ValueError("--cores requires a value")
                try:
                    cores = int(rest[i])
                except ValueError:
                    raise ValueError(f"--cores must be an integer, got {rest[i]!r}")
            elif token in ("--memory", "-m"):
                i += 1
                if i >= len(rest):
                    raise ValueError("--memory requires a value")
                memory_mb = _parse_memory_mb_shell(rest[i])
            else:
                raise ValueError(f"Unknown option: {token!r}")
            i += 1
        if cores is None and memory_mb is None:
            raise ValueError(
                "Usage: modify <vmid> [--cores N] [--memory N[M|G]]"
            )
        modify_container(host, vmid, cores=cores, memory_mb=memory_mb)
        parts = []
        if cores is not None:
            parts.append(f"cores={cores}")
        if memory_mb is not None:
            parts.append(f"memory={memory_mb}M")
        self._output(
            f"  Modified VMID {vmid} on {host.name}: {', '.join(parts)}."
        )

    def _cmd_resize(self, args: list[str]) -> None:
        host = self._require_host()
        if len(args) != 3:
            raise ValueError("Usage: resize <vmid> <volume> <size>")
        vmid = self._parse_vmid([args[0]], "resize")
        volume, size = args[1], args[2]
        resize_container_disk(host, vmid, volume, size)
        self._output(
            f"  Resized {volume} on VMID {vmid} on {host.name} to {size}."
        )

    def _cmd_unlock(self, args: list[str]) -> None:
        host = self._require_host()
        vmid = self._parse_vmid(args, "unlock")
        unlock_guest(host, vmid)
        self._output(f"  Unlocked VMID {vmid} on {host.name}.")

    def _cmd_rolling_update(self, args: list[str]) -> None:
        if not args:
            raise ValueError("Usage: rolling-update <name> [<name> ...]")
        from lib.cluster_update import run_cluster_update
        dry_run = "--dry-run" in args
        resume = "--resume" in args
        targets = [a for a in args if a not in {"--dry-run", "--resume"}]
        if not targets:
            raise ValueError("Usage: rolling-update <name> [<name> ...]")
        run_cluster_update(targets, workspace=self.state.workspace, dry_run=dry_run, resume=resume)

    def _cmd_snapshots(self, args: list[str]) -> None:
        host = self._require_host()
        vmid = self._parse_vmid(args, "snapshots")
        snaps = list_snapshots(host, vmid)
        if not snaps:
            self._output(f"  No snapshots for VMID {vmid}.")
            return
        self._output(f"  Snapshots for VMID {vmid}:")
        for snap in snaps:
            marker = "*" if snap.is_current else " "
            desc = f"  {snap.description}" if snap.description else ""
            self._output(f"    {marker} {snap.name}{desc}")

    def _cmd_snapshot(self, args: list[str]) -> None:
        host = self._require_host()
        if len(args) < 2:
            raise ValueError("Usage: snapshot <vmid> <name>")
        vmid = self._parse_vmid([args[0]], "snapshot")
        name = args[1]
        snapshot_guest(host, vmid, name)
        self._output(f"  Created snapshot '{name}' for VMID {vmid} on {host.name}.")

    def _cmd_rollback(self, args: list[str]) -> None:
        host = self._require_host()
        if len(args) < 2:
            raise ValueError("Usage: rollback <vmid> <name>")
        vmid = self._parse_vmid([args[0]], "rollback")
        name = args[1]
        rollback_guest(host, vmid, name)
        self._output(f"  Rolled back VMID {vmid} on {host.name} to snapshot '{name}'.")

    def _cmd_delsnapshot(self, args: list[str]) -> None:
        host = self._require_host()
        dry_run = '--dry-run' in args
        yes = '--yes' in args or '-y' in args
        rest = [arg for arg in args if arg not in {'--yes', '-y', '--dry-run'}]
        if len(rest) != 2:
            raise ValueError("Usage: delsnapshot <vmid> <name> [--yes] [--dry-run]")
        vmid = self._parse_vmid([rest[0]], "delsnapshot")
        name = rest[1]
        if not dry_run and not yes:
            try:
                response = self._input(f"Delete snapshot '{name}' for guest {vmid} on {host.name}? Type 'yes' to confirm: ")
            except (EOFError, KeyboardInterrupt):
                response = ''
            if response.strip().lower() != 'yes':
                self._output('  Aborted.')
                return
        delete_snapshot(host, vmid, name, dry_run=dry_run)
        prefix = 'Would delete' if dry_run else 'Deleted'
        self._output(f"  {prefix} snapshot '{name}' for VMID {vmid} on {host.name}.")

    def _cmd_top(self, args: list[str]) -> None:
        host = self._require_host()
        try:
            summary = get_node_summary(host)
        except ProxmoxSummaryError as exc:
            raise ValueError(str(exc))
        self._output(format_node_summary(summary))

    def _cmd_backups(self, args: list[str]) -> None:
        host = self._require_host()
        vmid = self._parse_vmid(args, "backups")
        backups = list_backups(host, vmid)
        if not backups:
            self._output(f"  No backups found for VMID {vmid}.")
            return
        self._output(f"  Backups for VMID {vmid}:")
        for b in backups:
            size_str = _shell_fmt_bytes(b.size) if b.size else "-"
            date_str = _shell_fmt_ts(b.ctime) if b.ctime else "-"
            desc = f"  {b.notes}" if b.notes else ""
            self._output(f"    {date_str}  {size_str:<10}  {b.filename}{desc}")

    def _cmd_backup(self, args: list[str]) -> None:
        host = self._require_host()
        if not args:
            raise ValueError("Usage: backup <vmid> [--storage POOL] [--mode snapshot|suspend|stop]")
        vmid = self._parse_vmid([args[0]], "backup")
        rest = args[1:]
        storage: Optional[str] = None
        mode = "snapshot"
        i = 0
        while i < len(rest):
            if rest[i] == "--storage" and i + 1 < len(rest):
                storage = rest[i + 1]
                i += 2
            elif rest[i] == "--mode" and i + 1 < len(rest):
                mode = rest[i + 1]
                i += 2
            else:
                raise ValueError(f"Unknown option: {rest[i]!r}")
        create_backup(host, vmid, storage=storage, mode=mode)
        self._output(f"  Backup of VMID {vmid} on {host.name} complete.")

    def _cmd_migrate(self, args: list[str]) -> None:
        host = self._require_host()
        if len(args) < 2:
            raise ValueError("Usage: migrate <vmid> <target-host> [--online]")
        vmid = self._parse_vmid([args[0]], "migrate")
        target_name = args[1]
        online = "--online" in args
        target = find_proxmox_host(target_name, self.state.workspace)
        if not target:
            raise ValueError(f"No registered host matching '{target_name}'")
        migrate_guest(host, vmid, target, online=online)
        self._output(
            f"  Migrated VMID {vmid} from {host.name} to {target.name}."
        )

    def _cmd_clean_disks(self, args: list[str]) -> None:
        host = self._require_host()
        do_delete = "--delete" in args
        skip_confirm = "--yes" in args or "-y" in args
        if any(arg not in {'--delete', '--yes', '-y', '--dry-run'} for arg in args):
            raise ValueError('Usage: clean-disks [--delete] [--yes] [--dry-run]')
        orphans = list_orphaned_volumes(host)
        if not orphans:
            self._output("  No orphaned volumes found.")
            return
        self._output(f"  Orphaned volumes ({len(orphans)}):")
        for vol in orphans:
            self._output(
                f"    {vol.volid}  vmid={vol.vmid}  size={vol.size}"
            )
        if not do_delete:
            self._output("  Run 'clean-disks --delete' to remove them.")
            return
        if '--dry-run' in args:
            self._output('  Dry run — nothing deleted.')
            return
        if not skip_confirm:
            try:
                response = self._input(
                    f"  Delete {len(orphans)} volume(s)? Type 'yes' to confirm: "
                )
            except (EOFError, KeyboardInterrupt):
                self._output("  Aborted.")
                return
            if response.strip().lower() != "yes":
                self._output("  Aborted.")
                return
        for vol in orphans:
            try:
                delete_volume(host, vol.volid)
                self._output(f"  Deleted {vol.volid}")
            except ProxmoxStorageError as exc:
                raise ProxmoxStorageError(f'Cleanup stopped after partial failure: {exc}') from exc

    @staticmethod
    def _parse_vmid(args: list[str], cmd: str) -> int:
        if len(args) != 1:
            raise ValueError(f"Usage: {cmd} <vmid>")
        try:
            vmid = int(args[0])
        except ValueError:
            raise ValueError(f"VMID must be an integer, got '{args[0]}'")
        if vmid <= 0:
            raise ValueError(f"VMID must be positive, got {vmid}")
        return vmid


def run_proxmox_shell(workspace: Optional[str] = None) -> int:
    """Entry point used by the CLI ``proxmox`` subcommand without args."""
    shell = ProxmoxShell(workspace=workspace)
    return shell.run()
