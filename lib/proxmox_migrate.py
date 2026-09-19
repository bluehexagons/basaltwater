"""Proxmox guest migration between cluster nodes."""

from __future__ import annotations

import shlex
import subprocess
from typing import Optional

from lib.proxmox_guest import _get_node_name, _ssh_opts, _ssh_run
from lib.proxmox_hosts import ProxmoxHost


class ProxmoxMigrateError(Exception):
    """Raised when a guest migration fails."""


def _run(
    host: ProxmoxHost,
    cmd: str,
    *,
    dry_run: bool = False,
) -> subprocess.CompletedProcess[str]:
    return _ssh_run(host.address, host.user, _ssh_opts(host.ssh_key), cmd, dry_run=dry_run)


def _node_name(host: ProxmoxHost) -> str:
    """Return the Proxmox node name for ``host``, querying live if necessary."""
    if host.facts and host.facts.node_name:
        return host.facts.node_name
    name = _get_node_name(
        host.address, host.user, _ssh_opts(host.ssh_key)
    )
    if not name:
        raise ProxmoxMigrateError(f"Could not resolve Proxmox node name for {host.address}")
    return name


def _is_vm(host: ProxmoxHost, vmid: int) -> bool:
    """Return True when ``vmid`` is a QEMU VM; False when it is an LXC container."""
    if _run(host, f"qm status {int(vmid)}").returncode == 0:
        return True
    if _run(host, f"pct status {int(vmid)}").returncode == 0:
        return False
    raise ProxmoxMigrateError(
        f"Could not identify guest {vmid} on {host.address}; migration was not attempted"
    )


def migrate_guest(
    src_host: ProxmoxHost,
    vmid: int,
    target_host: ProxmoxHost,
    *,
    online: bool = False,
    with_local_disks: bool = False,
    dry_run: bool = False,
) -> None:
    """Migrate a guest from ``src_host`` to ``target_host``.

    VMs: uses ``qm migrate``. ``online=True`` keeps the VM running during
    migration (requires shared storage or ``with_local_disks=True``).

    LXC containers: uses ``pct migrate --restart`` (always requires a brief
    restart; online migration is not supported by Proxmox for LXC).
    """
    target_node = _node_name(target_host)
    is_vm = _is_vm(src_host, vmid)

    if is_vm:
        parts = ["qm", "migrate", str(int(vmid)), target_node]
        if online:
            parts += ["--online", "1"]
        if with_local_disks:
            parts += ["--with-local-disks", "1"]
    else:
        if online:
            raise ProxmoxMigrateError(
                "Online migration is not supported for LXC containers. "
                "Remove --online or migrate a VM instead."
            )
        parts = ["pct", "migrate", str(int(vmid)), target_node, "--restart", "1"]

    result = _run(src_host, shlex.join(parts), dry_run=dry_run)
    if result.returncode != 0:
        tool = "qm" if is_vm else "pct"
        raise ProxmoxMigrateError(
            f"{tool} migrate {vmid} → {target_node!r} failed on {src_host.address}: "
            f"{(result.stderr or result.stdout or '').strip() or 'unknown error'}"
        )


__all__ = [
    "ProxmoxMigrateError",
    "migrate_guest",
]
