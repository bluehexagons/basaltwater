"""Proxmox backup management: list and trigger vzdump backups for guests."""

from __future__ import annotations

import json
import shlex
import subprocess
from dataclasses import dataclass
from typing import Optional

from lib.proxmox_guest import _proxmox_ssh_command, _ssh_opts, _ssh_run
from lib.proxmox_hosts import ProxmoxHost
from lib.validation import validate_positive_integer, validate_proxmox_storage_name


class ProxmoxBackupError(Exception):
    """Raised when a backup operation fails."""


@dataclass
class BackupInfo:
    """One backup entry from Proxmox storage content."""

    volid: str
    vmid: int
    size: int           # bytes
    ctime: Optional[int] = None
    format: str = ""
    notes: str = ""

    @property
    def storage(self) -> str:
        return self.volid.split(":", 1)[0] if ":" in self.volid else ""

    @property
    def filename(self) -> str:
        return self.volid.split(":", 1)[1] if ":" in self.volid else self.volid


_VALID_MODES = ("snapshot", "suspend", "stop")
_VALID_COMPRESS = ("zstd", "gzip", "lzo", "0")
DEFAULT_BACKUP_TIMEOUT = 86400
MAX_BACKUP_TIMEOUT = 604800


def _run(
    host: ProxmoxHost,
    cmd: str,
    *,
    dry_run: bool = False,
) -> subprocess.CompletedProcess[str]:
    try:
        return _ssh_run(host.address, host.user, _ssh_opts(host.ssh_key), cmd, dry_run=dry_run)
    except subprocess.TimeoutExpired as exc:
        raise ProxmoxBackupError(f"Backup inspection timed out on {host.address}") from exc


def _backup_storages(host: ProxmoxHost) -> list[str]:
    """Return active storage names that support the backup content type."""
    result = _run(
        host,
        "pvesh get /nodes/$(hostname -s)/storage --output-format json",
    )
    data = _inventory(result, f"storage on {host.address}")
    pools: list[str] = []
    for entry in data:
        content = entry.get("content")
        storage = entry.get("storage")
        if not isinstance(content, str) or not isinstance(storage, str) or not storage:
            raise ProxmoxBackupError(f"Invalid backup storage inventory on {host.address}")
        try:
            validate_proxmox_storage_name(storage, "storage")
        except ValueError as exc:
            raise ProxmoxBackupError(f"Invalid backup storage inventory on {host.address}") from exc
        if "backup" in content.split(",") and entry.get("active", 0):
            pools.append(storage)
    return pools


def _inventory(result: subprocess.CompletedProcess[str], label: str) -> list[dict]:
    """Require a complete JSON inventory instead of reporting errors as empty."""
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip() or "unknown error"
        raise ProxmoxBackupError(f"Could not list {label}: {detail}")
    try:
        data = json.loads(result.stdout)
    except (TypeError, ValueError) as exc:
        raise ProxmoxBackupError(f"Invalid inventory for {label}") from exc
    if not isinstance(data, list) or any(not isinstance(entry, dict) for entry in data):
        raise ProxmoxBackupError(f"Invalid inventory for {label}")
    return data


def _storage_backups(host: ProxmoxHost, storage: str, vmid: int) -> list[BackupInfo]:
    """Read one storage's archive inventory, failing closed on incomplete data."""
    backups: list[BackupInfo] = []
    cmd = (
        f"pvesh get /nodes/$(hostname -s)/storage/{shlex.quote(storage)}/content"
        f" --content backup --output-format json"
    )
    entries = _inventory(_run(host, cmd), f"backups in {storage} on {host.address}")
    for entry in entries:
        try:
            entry_vmid = validate_positive_integer(str(entry["vmid"]), "VMID")
            size = int(entry["size"])
            ctime = int(entry["ctime"]) if entry.get("ctime") is not None else None
            volid = entry["volid"]
            if (size < 0 or not isinstance(volid, str)
                    or not volid.startswith(f"{storage}:") or not volid.split(":", 1)[1]):
                raise ValueError("invalid backup fields")
        except (KeyError, TypeError, ValueError) as exc:
            raise ProxmoxBackupError(f"Invalid backup entry in {storage}") from exc
        if entry_vmid != vmid:
            continue
        backups.append(BackupInfo(
            volid=volid,
            vmid=vmid,
            size=size,
            ctime=ctime,
            format=str(entry.get("format", "")),
            notes=str(entry.get("notes", "")),
        ))
    return backups


def list_backups(host: ProxmoxHost, vmid: int) -> list[BackupInfo]:
    """Return all backups for ``vmid`` across every backup-capable storage pool."""
    vmid = validate_positive_integer(str(vmid), "VMID")
    backups = [backup for storage in _backup_storages(host)
               for backup in _storage_backups(host, storage, vmid)]
    return sorted(backups, key=lambda b: b.ctime or 0)


def _require_local_guest(host: ProxmoxHost, vmid: int) -> None:
    """vzdump can silently skip a guest owned by another cluster node."""
    matches = 0
    for kind in ("qemu", "lxc"):
        entries = _inventory(_run(
            host, f"pvesh get /nodes/$(hostname -s)/{kind} --output-format json",
        ), f"local {kind} guests on {host.address}")
        for entry in entries:
            try:
                guest_vmid = validate_positive_integer(str(entry["vmid"]), "VMID")
            except (KeyError, ValueError) as exc:
                raise ProxmoxBackupError(f"Invalid local guest inventory on {host.address}") from exc
            matches += guest_vmid == vmid
    if matches != 1:
        raise ProxmoxBackupError(
            f"Guest {vmid} is not uniquely owned by the local node on {host.address}. "
            "Select the guest's owning host before backing up."
        )


def create_backup(
    host: ProxmoxHost,
    vmid: int,
    *,
    storage: Optional[str] = None,
    mode: str = "snapshot",
    compress: str = "zstd",
    timeout: int = DEFAULT_BACKUP_TIMEOUT,
    dry_run: bool = False,
) -> None:
    """Trigger an immediate vzdump backup for ``vmid``.

    mode: snapshot (no downtime for VMs, default), suspend, or stop.
    compress: zstd (default), gzip, lzo, or 0 (none).
    storage: target pool; auto-selects the first backup-capable pool if omitted.
    timeout: bounded SSH wait in seconds, including interactive sessions.
    """
    vmid = validate_positive_integer(str(vmid), "VMID")
    timeout = validate_positive_integer(str(timeout), "--timeout")
    if timeout > MAX_BACKUP_TIMEOUT:
        raise ValueError(f"--timeout must be at most {MAX_BACKUP_TIMEOUT} seconds")
    if storage is not None:
        validate_proxmox_storage_name(storage, "--storage")
    if mode not in _VALID_MODES:
        raise ValueError(
            f"Invalid backup mode {mode!r}. Use one of: {', '.join(_VALID_MODES)}."
        )
    if compress not in _VALID_COMPRESS:
        raise ValueError(
            f"Invalid compress value {compress!r}. Use one of: {', '.join(_VALID_COMPRESS)}."
        )

    _require_local_guest(host, vmid)
    pools = _backup_storages(host)
    if not pools:
        raise ProxmoxBackupError(
            f"No backup-capable storage found on {host.address}. "
            "Add a storage pool with 'backup' content type in Proxmox."
        )
    target_storage = storage if storage is not None else pools[0]
    if target_storage not in pools:
        raise ProxmoxBackupError(f"Storage {target_storage!r} is not active backup storage on {host.address}")
    before = {backup.volid for backup in _storage_backups(host, target_storage, vmid)}

    cmd = shlex.join([
        "vzdump", str(vmid),
        "--storage", target_storage,
        "--mode", mode,
        "--compress", compress,
        "--remove", "0",   # never auto-remove via retention; let the user manage that
    ])
    if dry_run:
        _run(host, cmd, dry_run=True)
        return
    # Unlike the general SSH helper, backup waits stay bounded in terminals too.
    ssh_cmd = _proxmox_ssh_command(host.address, host.user, _ssh_opts(host.ssh_key), cmd)
    print(f"  $ {cmd}")
    try:
        result = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise ProxmoxBackupError(
            f"vzdump {vmid} timed out after {timeout}s on {host.address}; remote completion "
            "is uncertain. Inspect Proxmox tasks and target storage before retrying; "
            "no retry or cleanup was attempted."
        ) from exc
    if result.returncode != 0:
        raise ProxmoxBackupError(
            f"vzdump {vmid} failed on {host.address}: "
            f"{(result.stderr or result.stdout or '').strip() or 'unknown error'}"
        )
    try:
        after = _storage_backups(host, target_storage, vmid)
    except ProxmoxBackupError as exc:
        raise ProxmoxBackupError(
            f"Could not verify vzdump {vmid} completion on {host.address}: {exc}. "
            "Inspect Proxmox tasks and target storage before retrying."
        ) from exc
    if not any(backup.volid not in before and backup.size > 0 for backup in after):
        raise ProxmoxBackupError(
            f"vzdump {vmid} exited successfully but no new nonempty backup archive "
            f"was found in {target_storage} on {host.address}. Completion is unverified; "
            "inspect Proxmox tasks and target storage before retrying."
        )


__all__ = [
    "BackupInfo",
    "DEFAULT_BACKUP_TIMEOUT",
    "ProxmoxBackupError",
    "create_backup",
    "list_backups",
]
