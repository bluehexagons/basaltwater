"""Proxmox orphaned-volume detection and cleanup.

An "orphaned" volume has no defined owner in its storage scope, no guest
configuration reference, and no configured replication job protecting it.
These accumulate after guest destruction or failed imports.
"""

from __future__ import annotations

import json
import shlex
import subprocess
from dataclasses import dataclass

from lib.proxmox_guest import _ssh_opts, _ssh_run
from lib.proxmox_hosts import ProxmoxHost
from lib.validation import validate_proxmox_storage_name
from lib.validators import validate_host


class ProxmoxStorageError(Exception):
    """Raised when a storage management operation fails."""


@dataclass
class OrphanedVolume:
    """An unreferenced guest volume without an owner in its storage scope."""

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
    try:
        return _ssh_run(host.address, host.user, _ssh_opts(host.ssh_key), cmd, dry_run=dry_run)
    except subprocess.TimeoutExpired as exc:
        raise ProxmoxStorageError(
            f'Storage command timed out on {host.address}; inspect the remote state before retrying'
        ) from exc


def _json_query(host: ProxmoxHost, cmd: str, label: str) -> object:
    result = _run(host, cmd)
    if result.returncode != 0:
        raise ProxmoxStorageError(f'Incomplete {label} on {host.address}')
    try:
        return json.loads(result.stdout)
    except ValueError as exc:
        raise ProxmoxStorageError(f'Invalid {label} on {host.address}') from exc


def _guest_inventory(host: ProxmoxHost) -> tuple[dict[int, tuple[str, str]], dict[str, set[int]]]:
    """Retain cluster node/type assignments and independent local inventories."""
    local: dict[str, set[int]] = {'qemu': set(), 'lxc': set()}
    for guest_type, cmd in (('qemu', 'qm list'), ('lxc', 'pct list')):
        result = _run(host, cmd)
        if result.returncode != 0:
            raise ProxmoxStorageError(f'Incomplete guest inventory on {host.address}: {cmd} failed')
        # Both native commands omit even the header when no guests exist.
        # The cluster JSON inventory below remains mandatory in this case.
        if not result.stdout.strip():
            continue
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
                    if vmid in local[guest_type]:
                        raise ValueError('duplicate VMID')
                    local[guest_type].add(vmid)
                except ValueError:
                    raise ProxmoxStorageError(f'Invalid VMID in {cmd} inventory on {host.address}')
    guests = _json_query(host, 'pvesh get /cluster/resources --type vm --output-format json',
                         'cluster guest inventory')
    inventory: dict[int, tuple[str, str]] = {}
    try:
        if not isinstance(guests, list):
            raise ValueError('cluster inventory must be a list')
        for guest in guests:
            if not isinstance(guest, dict) or type(guest.get('vmid')) is not int or guest['vmid'] <= 0:
                raise ValueError('invalid cluster VMID')
            node, guest_type = guest.get('node'), guest.get('type')
            if not validate_host(node) or guest_type not in ('qemu', 'lxc') or guest['vmid'] in inventory:
                raise ValueError('invalid or duplicate cluster guest identity')
            inventory[guest['vmid']] = (node, guest_type)
    except ValueError as exc:
        raise ProxmoxStorageError(f'Invalid cluster guest inventory on {host.address}') from exc
    return inventory, local


# Read raw pmxcfs files: native current-config APIs omit pending, unused, or
# snapshot-only references. Explicit directory iteration and reads propagate
# errors; globbing could silently turn inaccessible directories into no guests.
# Resolve host-path aliases too, without returning any guest configuration text.
_REFERENCE_SCRIPT = r"""
import json
from pathlib import Path
import re
import stat
import subprocess
import sys

def identity(path):
    info = path.stat()
    if stat.S_ISBLK(info.st_mode):
        return ('block', info.st_rdev)
    return ('inode', info.st_dev, info.st_ino)

def overlaps(first, second):
    return (first == second or first in second.parents or second in first.parents or
            (first.exists() and second.exists() and identity(first) == identity(second)))

volids = json.loads(sys.argv[1])
root = Path('/etc/pve/nodes')
local = Path('/etc/pve/local').resolve(strict=True)
if local.parent != root.resolve(strict=True):
    raise ValueError('invalid local node directory')
# Include disabled and node-restricted storage IDs: a configured reference can
# use an alias absent from this node's active volume inventory.
result = subprocess.run(['pvesh', 'get', '/storage', '--output-format', 'json'],
                        check=True, capture_output=True, text=True, timeout=30)
data = json.loads(result.stdout)
if not isinstance(data, list):
    raise ValueError('invalid configured storage inventory')
storage_ids = set()
for entry in data:
    name = entry.get('storage') if isinstance(entry, dict) else None
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', name) or name in storage_ids:
        raise ValueError('invalid configured storage identity')
    storage_ids.add(name)
if any(volid.partition(':')[0] not in storage_ids for volid in volids):
    raise ValueError('inconsistent configured storage inventory')
volume_pattern = re.compile(r'(?<![A-Za-z0-9_.-])(?:' + '|'.join(re.escape(name) for name in sorted(storage_ids)) +
                            r'):[^\s,;=\'\"<>]+') if storage_ids else None
guests = []
for node in sorted(root.iterdir()):
    if not node.is_dir():
        raise ValueError('invalid node directory')
    for kind, directory in (('qemu', 'qemu-server'), ('lxc', 'lxc')):
        for path in sorted((node / directory).iterdir()):
            if not re.fullmatch(r'[1-9][0-9]*\.conf', path.name):
                raise ValueError('invalid guest configuration filename')
            text = path.read_text()
            if not text.strip():
                raise ValueError('empty guest configuration')
            paths = {Path(p).resolve() for p in re.findall(r"/[^\s,;='\"<>]+", text)}
            mappings = []
            for value in re.findall(r'^virtiofs[0-9]+:\s*(.*)$', text, re.MULTILINE):
                fields = value.split(',')
                ids = [field[6:] for field in fields if field.startswith('dirid=')]
                if fields and '=' not in fields[0]:
                    ids.append(fields[0])
                if len(ids) != 1 or not ids[0].strip():
                    raise ValueError('invalid VirtioFS mapping reference')
                mappings.append(ids[0])
            native_refs = set(volume_pattern.findall(text)) if volume_pattern else set()
            guests.append((node.name, kind, int(path.stem), text, paths, mappings, native_refs))
directory_maps = {}
if any(guest[5] for guest in guests):
    result = subprocess.run(['pvesh', 'get', '/cluster/mapping/dir', '--output-format', 'json'],
                            check=True, capture_output=True, text=True, timeout=30)
    data = json.loads(result.stdout)
    if not isinstance(data, list):
        raise ValueError('invalid directory mapping inventory')
    for entry in data:
        if not isinstance(entry, dict) or not isinstance(entry.get('id'), str) or not entry['id']:
            raise ValueError('invalid directory mapping identity')
        if entry['id'] in directory_maps or not isinstance(entry.get('map'), list):
            raise ValueError('invalid directory mapping entries')
        maps = {}
        for value in entry['map']:
            if not isinstance(value, str):
                raise ValueError('invalid directory mapping entry')
            pairs = [field.partition('=') for field in value.split(',')]
            if len(pairs) != 2 or any(not separator for _, separator, _ in pairs):
                raise ValueError('invalid directory mapping properties')
            properties = {key: value for key, _, value in pairs}
            if set(properties) != {'node', 'path'} or not properties['node'] or not properties['path'].startswith('/'):
                raise ValueError('invalid directory mapping properties')
            if properties['node'] in maps:
                raise ValueError('duplicate directory mapping node')
            maps[properties['node']] = properties['path']
        directory_maps[entry['id']] = maps
volume_paths = {}
opaque_volumes = set()
if any(guest[4] or guest[5] or guest[6] for guest in guests):
    local_refs = {ref for guest in guests if guest[0] == local.name for ref in guest[6]}
    for volid in sorted(set(volids) | local_refs):
        result = subprocess.run(['pvesm', 'path', volid], check=True, capture_output=True, text=True, timeout=30)
        path = result.stdout.strip()
        if not path or '\n' in path:
            raise ValueError('invalid volume path')
        # Native userspace RBD identifiers are not filesystem paths. Their
        # device aliases cannot be established here, so retain these volumes
        # without preventing comparisons for other storage backends.
        if path.startswith('rbd:') and path[4:]:
            opaque_volumes.add(volid)
            continue
        if not path.startswith('/'):
            raise ValueError('invalid volume path')
        # Inactive LVs can have neither their VG nor device-mapper alias.
        # A missing path cannot establish identity; never compare it lexically.
        volume_paths[volid] = (path, Path(path).resolve(strict=True))
rows = []
for node, kind, vmid, text, paths, mappings, native_refs in guests:
    mapped_paths = []
    for mapping in mappings:
        if mapping not in directory_maps or node not in directory_maps[mapping]:
            raise ValueError('unresolved VirtioFS directory mapping')
        if node == local.name:
            mapped_paths.append(Path(directory_maps[mapping][node]).resolve(strict=True))
    # Native volume IDs are names, not backing identities. Resolve even aliases
    # not listed in the active pools, then propagate protection across names.
    native_paths = {volume_paths[ref][1] for ref in native_refs if ref in volume_paths}
    references = [v for v in volids if v in opaque_volumes or v in text or
                  (v in volume_paths and (volume_paths[v][0] in text or
                   any(overlaps(volume_paths[v][1], path) for path in paths | set(mapped_paths) | native_paths)))]
    if native_refs & opaque_volumes:
        references = list(volids)
    # Custom QEMU arguments can encode paths in quoted strings, JSON, or backend
    # options. Their arbitrary syntax cannot establish a complete disk inventory.
    if re.search(r'^args:', text, re.MULTILINE):
        references = list(volids)
    # Remote host paths cannot be resolved against this node's mounts or aliases.
    # Protect every shared candidate; the caller ignores remote local-storage refs.
    if (paths or mappings or native_refs) and node != local.name:
        references = list(volids)
    rows.append({'node': node, 'type': kind, 'vmid': vmid, 'references': references})
print(json.dumps({'node': local.name, 'guests': rows}))
"""


def _local_node(host: ProxmoxHost) -> str:
    script = (
        'import json; from pathlib import Path; '
        'p = Path("/etc/pve/local").resolve(strict=True); '
        'assert p.parent == Path("/etc/pve/nodes").resolve(strict=True); '
        'print(json.dumps(p.name))'
    )
    node = _json_query(host, shlex.join(['python3', '-c', script]), 'local node identity')
    if not validate_host(node):
        raise ProxmoxStorageError(f'Invalid local node identity on {host.address}')
    return node


def _configuration_references(
    host: ProxmoxHost,
    inventory: dict[int, tuple[str, str]],
    local: dict[str, set[int]],
    volids: set[str],
) -> tuple[str, dict[str, set[str]]]:
    """Require a complete raw-config inventory matching both guest inventories."""
    data = _json_query(host, shlex.join(['python3', '-c', _REFERENCE_SCRIPT, json.dumps(sorted(volids))]),
                       'guest configuration reference inventory')
    try:
        if not isinstance(data, dict) or not validate_host(data.get('node')) or not isinstance(data.get('guests'), list):
            raise ValueError('invalid configuration inventory')
        node = data['node']
        observed: dict[int, tuple[str, str]] = {}
        references: dict[str, set[str]] = {}
        for guest in data['guests']:
            if not isinstance(guest, dict) or type(guest.get('vmid')) is not int or guest['vmid'] <= 0:
                raise ValueError('invalid configured VMID')
            guest_node, kind = guest.get('node'), guest.get('type')
            if not validate_host(guest_node) or kind not in ('qemu', 'lxc') or guest['vmid'] in observed:
                raise ValueError('invalid configured identity')
            refs = guest.get('references')
            if not isinstance(refs, list) or any(not isinstance(ref, str) or ref not in volids for ref in refs):
                raise ValueError('invalid volume references')
            observed[guest['vmid']] = (guest_node, kind)
            references.setdefault(guest_node, set()).update(refs)
        if observed != inventory:
            raise ValueError('cluster and configuration inventories disagree')
        for kind, vmids in local.items():
            if vmids != {vmid for vmid, identity in observed.items() if identity == (node, kind)}:
                raise ValueError('local and configuration inventories disagree')
    except ValueError as exc:
        raise ProxmoxStorageError(f'Invalid or incomplete guest configuration reference inventory on {host.address}') from exc
    return node, references


def _guest_storage_pools(host: ProxmoxHost, node: str) -> dict[str, bool]:
    """Read effective sharing metadata, including backend-implied sharing."""
    data = _json_query(host, shlex.join(['pvesh', 'get', f'/nodes/{node}/storage', '--output-format', 'json']),
                       'storage inventory')
    pools: dict[str, bool] = {}
    try:
        if not isinstance(data, list):
            raise ValueError('storage inventory must be a list')
        seen: set[str] = set()
        for pool in data:
            if not isinstance(pool, dict):
                raise ValueError('invalid storage row')
            name = pool.get('storage')
            validate_proxmox_storage_name(name, 'storage inventory')
            if name in seen or any(type(pool.get(flag)) is not int or pool[flag] not in (0, 1)
                                   for flag in ('active', 'enabled', 'shared')):
                raise ValueError('invalid storage state')
            seen.add(name)
            if pool['active'] and pool['enabled']:
                pools[name] = bool(pool['shared'])
    except ValueError as exc:
        raise ProxmoxStorageError(f'Invalid storage inventory on {host.address}') from exc
    return pools


def _replicated_vmids(host: ProxmoxHost) -> set[int]:
    """Protect all configured jobs, including disabled jobs and pending removal."""
    data = _json_query(host, 'pvesh get /cluster/replication --output-format json', 'replication inventory')
    try:
        if not isinstance(data, list):
            raise ValueError('replication inventory must be a list')
        vmids: set[int] = set()
        for job in data:
            if not isinstance(job, dict) or type(job.get('guest')) is not int or job['guest'] <= 0:
                raise ValueError('invalid replicated VMID')
            vmids.add(job['guest'])
    except ValueError as exc:
        raise ProxmoxStorageError(f'Invalid replication inventory on {host.address}') from exc
    return vmids


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
    """Return unreferenced volumes without an owner in the storage's node scope."""
    inventory, local = _guest_inventory(host)
    node = _local_node(host)
    pools = _guest_storage_pools(host, node)
    replicated = _replicated_vmids(host)
    volumes: list[OrphanedVolume] = []
    volids: set[str] = set()
    for storage in pools:
        result = _run(host, f"pvesm list {shlex.quote(storage)}")
        if result.returncode != 0:
            raise ProxmoxStorageError(f'Incomplete volume inventory: pvesm list {storage} failed on {host.address}')
        for volid, vmid, size, fmt in _parse_pvesm_list(result.stdout):
            if not volid.startswith(storage + ':') or not volid.partition(':')[2] or volid in volids:
                raise ProxmoxStorageError(f'Invalid or duplicate volume identity on {host.address}: {volid}')
            volids.add(volid)
            volumes.append(OrphanedVolume(volid, storage, vmid, size, fmt))
    reference_node, references = _configuration_references(host, inventory, local, volids)
    if reference_node != node:
        raise ProxmoxStorageError(f'Local node identity changed during inventory on {host.address}')

    orphans: list[OrphanedVolume] = []
    for volume in volumes:
        shared = pools[volume.storage]
        owner = inventory.get(volume.vmid)
        if owner and (shared or owner[0] == node):
            continue
        if volume.vmid in replicated:
            continue
        scopes = references.values() if shared else (references.get(node, set()),)
        if any(volume.volid in refs for refs in scopes):
            continue
        orphans.append(volume)
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
