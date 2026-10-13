"""Proxmox guest migration between cluster nodes."""

from __future__ import annotations

import shlex
import subprocess
from lib.proxmox_guest import (
    _get_node_name,
    _ssh_opts,
    _ssh_run,
    _storage_pool_supports_content,
)
from lib.proxmox_hosts import ProxmoxHost
from lib.validation import validate_positive_integer, validate_proxmox_storage_name
from lib.validators import validate_host, validate_username


DEFAULT_MIGRATION_TIMEOUT = 3600


class ProxmoxMigrateError(Exception):
    """Raised when a guest migration fails."""


def _run(
    host: ProxmoxHost,
    cmd: str,
    *,
    dry_run: bool = False,
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    return _ssh_run(
        host.address, host.user, _ssh_opts(host.ssh_key), cmd,
        dry_run=dry_run, timeout=timeout,
    )


def _node_name(host: ProxmoxHost) -> str:
    """Resolve the authenticated host live; cached facts may be stale."""
    name = _get_node_name(
        host.address, host.user, _ssh_opts(host.ssh_key)
    )
    if not name or not validate_host(name):
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
    target_storage: str | None = None,
    timeout: int = DEFAULT_MIGRATION_TIMEOUT,
    dry_run: bool = False,
) -> None:
    """Migrate a guest from ``src_host`` to ``target_host``.

    VMs: uses ``qm migrate``. ``online=True`` keeps the VM running during
    migration (requires shared storage or ``with_local_disks=True``).

    LXC containers: uses ``pct migrate --restart`` (always requires a brief
    restart; online migration is not supported by Proxmox for LXC).

    ``target_storage`` selects the destination pool for migrated local disks.
    Proxmox uses different option names for QEMU and LXC. ``timeout`` bounds
    the noninteractive SSH wait, not the native task. Interactive SSH follows
    the shared passphrase policy and remains interruptible without a deadline.
    """
    vmid = validate_positive_integer(str(vmid), "VMID")
    timeout = validate_positive_integer(str(timeout), "--timeout")
    if target_storage is not None:
        validate_proxmox_storage_name(target_storage, "--target-storage")
        if target_storage == "1":
            raise ValueError("--target-storage '1' is reserved by Proxmox; select a named pool")
    for host in (src_host, target_host):
        if not validate_host(host.address) or not validate_username(host.user):
            raise ProxmoxMigrateError(f"Invalid Proxmox host address or user for {host.name}")
    if src_host.address.lower().rstrip(".") == target_host.address.lower().rstrip("."):
        raise ProxmoxMigrateError("Migration destination is the source host")

    try:
        source_node = _node_name(src_host)
        target_node = _node_name(target_host)
        if source_node == target_node:
            raise ProxmoxMigrateError("Migration destination is the source node")
        is_vm = _is_vm(src_host, vmid)
        if target_storage is not None and not _storage_pool_supports_content(
            target_storage, "images" if is_vm else "rootdir",
            target_host.address, target_host.user, _ssh_opts(target_host.ssh_key),
            strict=True,
        ):
            raise ProxmoxMigrateError(
                f"Destination storage {target_storage!r} on {target_host.address} "
                f"is unavailable or does not support {'VM images' if is_vm else 'LXC rootdirs'}"
            )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise ProxmoxMigrateError(
            f"Migration inspection failed; migration was not attempted: {exc}"
        ) from exc

    if is_vm:
        parts = ["qm", "migrate", str(int(vmid)), target_node]
        if online:
            parts += ["--online", "1"]
        if with_local_disks:
            parts += ["--with-local-disks", "1"]
        if target_storage is not None:
            parts += ["--targetstorage", target_storage]
    else:
        if online:
            raise ProxmoxMigrateError(
                "Online migration is not supported for LXC containers. "
                "Remove --online or migrate a VM instead."
            )
        if with_local_disks:
            raise ProxmoxMigrateError("--with-local-disks is supported only for QEMU VMs")
        parts = ["pct", "migrate", str(int(vmid)), target_node, "--restart", "1"]
        if target_storage is not None:
            parts += ["--target-storage", target_storage]

    command = shlex.join(parts)
    if dry_run:
        print(f"  [DRY-RUN] Migration command: {command}")
    try:
        result = _run(src_host, command, dry_run=dry_run, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError, KeyboardInterrupt) as exc:
        raise ProxmoxMigrateError(
            f"Lost the migration command result for guest {vmid}: {exc}. "
            "The Proxmox task may still be running; inspect its task log and both "
            "nodes before retrying. Do not unlock or start a second copy."
        ) from exc
    if result.returncode != 0:
        tool = "qm" if is_vm else "pct"
        raise ProxmoxMigrateError(
            f"{tool} migrate {vmid} → {target_node!r} failed on {src_host.address}: "
            f"{(result.stderr or result.stdout or '').strip() or 'unknown error'}. "
            "Inspect the Proxmox task log and both nodes before retrying."
        )
    if not dry_run:
        try:
            status = _run(target_host, f"{'qm' if is_vm else 'pct'} status {vmid}")
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise ProxmoxMigrateError(
                f"Migration command completed, but destination verification failed: {exc}. "
                "Inspect both nodes before retrying."
            ) from exc
        if status.returncode != 0 or status.stdout.strip() not in {
            "status: running", "status: stopped", "status: paused",
        }:
            raise ProxmoxMigrateError(
                f"Migration command completed, but guest {vmid} could not be verified "
                f"on {target_host.address}. Inspect both nodes before retrying."
            )


__all__ = [
    "ProxmoxMigrateError",
    "migrate_guest",
]
