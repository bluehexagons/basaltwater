"""Proxmox orphaned-volume detection and cleanup.

An "orphaned" volume is a guest disk (images or rootdir content type) whose
recorded VMID does not match any currently defined guest on the node.  These
accumulate after VM or LXC destruction when ``--purge`` was not used, or
after failed imports.
"""

from __future__ import annotations

import shlex
import subprocess
import json
from dataclasses import dataclass

from lib.proxmox_guest import _ssh_opts, _ssh_run
from lib.proxmox_hosts import ProxmoxHost


class ProxmoxStorageError(Exception):
    """Raised when a storage management operation fails."""


@dataclass
class OrphanedVolume:
    """A volume in guest storage whose VMID no longer exists."""

    volid: str
    storage: str
    vmid: int
    size: str
    format: str = ""


def _run(
    host: ProxmoxHost,
    cmd: str,
    *,
    dry_run: bool = False,
) -> subprocess.CompletedProcess[str]:
    return _ssh_run(host.address, host.user, _ssh_opts(host.ssh_key), cmd, dry_run=dry_run)


def _active_vmids(host: ProxmoxHost) -> set[int]:
    """Return all VMIDs currently defined on ``host`` (VMs + LXC)."""
    vmids: set[int] = set()
    for cmd in ("qm list", "pct list"):
        result = _run(host, cmd)
        if result.returncode != 0:
            raise ProxmoxStorageError(f'Incomplete guest inventory on {host.address}: {cmd} failed')
        lines = result.stdout.splitlines()
        if not lines or not lines[0].split() or lines[0].split()[0] != 'VMID':
            raise ProxmoxStorageError(f'Invalid guest inventory from {cmd} on {host.address}')
        for line in lines[1:]:
            parts = line.split()
            if parts:
                try:
                    vmid = int(parts[0])
                    if vmid <= 0:
                        raise ValueError('nonpositive VMID')
                    vmids.add(vmid)
                except ValueError:
                    raise ProxmoxStorageError(f'Invalid VMID in {cmd} inventory on {host.address}')
    # Shared pools can contain disks belonging to a guest on another node.
    result = _run(host, 'pvesh get /cluster/resources --type vm --output-format json')
    if result.returncode != 0:
        raise ProxmoxStorageError(f'Incomplete cluster guest inventory on {host.address}')
    try:
        guests = json.loads(result.stdout)
        if not isinstance(guests, list):
            raise ValueError('cluster inventory must be a list')
        for guest in guests:
            if not isinstance(guest, dict) or type(guest.get('vmid')) is not int or guest['vmid'] <= 0:
                raise ValueError('invalid cluster VMID')
            vmids.add(guest['vmid'])
    except ValueError as exc:
        raise ProxmoxStorageError(f'Invalid cluster guest inventory on {host.address}') from exc
    return vmids


def _guest_storage_names(host: ProxmoxHost) -> list[str]:
    """Return active storage pools that hold guest disks (images or rootdir)."""
    result = _run(host, "pvesm status")
    if result.returncode != 0:
        raise ProxmoxStorageError(
            f"pvesm status failed on {host.address}: "
            f"{(result.stderr or '').strip() or 'unknown error'}"
        )
    pools: list[str] = []
    lines = result.stdout.splitlines()
    if not lines or lines[0].split()[:3] != ['Name', 'Type', 'Status']:
        raise ProxmoxStorageError(f'Invalid storage inventory on {host.address}')
    for line in lines[1:]:
        parts = line.split()
        if not parts:
            continue
        if len(parts) < 3 or parts[2] not in {'active', 'inactive', 'disabled'}:
            raise ProxmoxStorageError(f'Invalid storage inventory row on {host.address}: {line}')
        # pvesm status columns: Name Type Status Total Used Available %
        if len(parts) >= 3 and parts[2] == "active":
            pools.append(parts[0])
    return pools


def _parse_pvesm_list(stdout: str) -> list[tuple[str, int, str, str]]:
    """Parse ``pvesm list <storage>`` into (volid, vmid, size, format) tuples.

    Only includes guest disk content types (images, rootdir).
    """
    entries: list[tuple[str, int, str, str]] = []
    lines = stdout.splitlines()
    if not lines or lines[0].split()[:5] != ['Volid', 'Format', 'Type', 'Size', 'VMID']:
        raise ProxmoxStorageError('Invalid volume inventory header')
    for line in lines[1:]:
        parts = line.split()
        if not parts:
            continue
        if len(parts) < 4:
            raise ProxmoxStorageError(f'Invalid volume inventory row: {line}')
        volid = parts[0]
        fmt = parts[1]
        content_type = parts[2]
        size = parts[3]
        vmid_str = parts[4] if len(parts) >= 5 else ""
        if content_type not in ("images", "rootdir"):
            continue
        try:
            vmid = int(vmid_str)
            if vmid <= 0:
                raise ValueError('nonpositive VMID')
        except ValueError:
            raise ProxmoxStorageError(f'Invalid guest volume VMID: {line}')
        entries.append((volid, vmid, size, fmt))
    return entries


def list_orphaned_volumes(host: ProxmoxHost) -> list[OrphanedVolume]:
    """Return volumes in guest-storage pools whose VMID no longer exists."""
    active = _active_vmids(host)
    orphans: list[OrphanedVolume] = []
    for storage in _guest_storage_names(host):
        result = _run(host, f"pvesm list {shlex.quote(storage)}")
        if result.returncode != 0:
            raise ProxmoxStorageError(f'Incomplete volume inventory: pvesm list {storage} failed on {host.address}')
        for volid, vmid, size, fmt in _parse_pvesm_list(result.stdout):
            if vmid not in active:
                orphans.append(OrphanedVolume(
                    volid=volid,
                    storage=storage,
                    vmid=vmid,
                    size=size,
                    format=fmt,
                ))
    return orphans


def delete_volume(
    host: ProxmoxHost,
    volid: str,
    *,
    dry_run: bool = False,
) -> None:
    """Delete a storage volume by its volid (e.g. ``local-lvm:vm-999-disk-0``)."""
    if not dry_run and volid not in {volume.volid for volume in list_orphaned_volumes(host)}:
        raise ProxmoxStorageError(f'Refusing to delete volume absent from fresh orphan inventory: {volid}')
    result = _run(host, shlex.join(["pvesm", "free", volid]), dry_run=dry_run)
    if result.returncode != 0:
        raise ProxmoxStorageError(
            f"pvesm free {volid!r} failed on {host.address}: "
            f"{(result.stderr or result.stdout or '').strip() or 'unknown error'}"
        )


__all__ = [
    "OrphanedVolume",
    "ProxmoxStorageError",
    "delete_volume",
    "list_orphaned_volumes",
]
