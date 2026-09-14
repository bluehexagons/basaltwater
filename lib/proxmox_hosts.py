#!/usr/bin/env python3
"""Workspace registry of known Proxmox hosts.

Each host record persists the connection details needed to manage Proxmox
guests on it (IP/hostname, SSH user, optional SSH key, optional default
storage pool, optional friendly description). Records are stored in
``proxmox_hosts.json`` inside the active workspace.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from typing import Optional, cast

from lib.atomic_io import write_json_atomic
from lib.concurrency import resource_lock
from lib.types import JSONDict, JSONList
from lib.validation import validate_filesystem_path
from lib.validators import validate_host, validate_username
from lib.workspace import ensure_workspace_dir, normalize_workspace_dir


PROXMOX_HOSTS_FILENAME = "proxmox_hosts.json"
PROXMOX_HOST_SCHEMA_VERSION = 1
PROXMOX_PROVIDER = "proxmox"


@dataclass
class ProxmoxStoragePool:
    """Cached metadata for a storage pool discovered on a Proxmox node."""

    name: str
    type: Optional[str] = None
    status: Optional[str] = None
    content: list[str] = field(default_factory=list)

    def to_dict(self) -> JSONDict:
        return cast(JSONDict, asdict(self))

    @classmethod
    def from_dict(cls, data: JSONDict) -> "ProxmoxStoragePool":
        if "name" not in data:
            raise ValueError("Proxmox storage pool record missing 'name'")
        content_raw = data.get("content") or []
        if not isinstance(content_raw, list):
            raise ValueError("Proxmox storage pool 'content' must be a list")
        return cls(
            name=str(data["name"]),
            type=cast(Optional[str], data.get("type")),
            status=cast(Optional[str], data.get("status")),
            content=[str(value) for value in content_raw],
        )


@dataclass
class ProxmoxHostFacts:
    """Cached discovery data for a Proxmox node."""

    node_name: Optional[str] = None
    bridges: list[str] = field(default_factory=list)
    gateway: Optional[str] = None
    nameservers: list[str] = field(default_factory=list)
    storage_pools: list[ProxmoxStoragePool] = field(default_factory=list)
    default_root_storage: Optional[str] = None
    default_template_storage: Optional[str] = None
    default_bridge: Optional[str] = None

    def to_dict(self) -> JSONDict:
        payload = {
            "node_name": self.node_name,
            "bridges": list(self.bridges),
            "gateway": self.gateway,
            "nameservers": list(self.nameservers),
            "storage_pools": [pool.to_dict() for pool in self.storage_pools],
            "default_root_storage": self.default_root_storage,
            "default_template_storage": self.default_template_storage,
            "default_bridge": self.default_bridge,
        }
        return cast(JSONDict, payload)

    @classmethod
    def from_dict(cls, data: JSONDict) -> "ProxmoxHostFacts":
        bridges_raw = data.get("bridges") or []
        if not isinstance(bridges_raw, list):
            raise ValueError("Proxmox host facts 'bridges' must be a list")
        nameservers_raw = data.get("nameservers") or []
        if not isinstance(nameservers_raw, list):
            raise ValueError("Proxmox host facts 'nameservers' must be a list")
        storage_raw = data.get("storage_pools") or []
        if not isinstance(storage_raw, list):
            raise ValueError("Proxmox host facts 'storage_pools' must be a list")
        storage_pools: list[ProxmoxStoragePool] = []
        for entry in storage_raw:
            if not isinstance(entry, dict):
                raise ValueError("Proxmox host facts storage pool entries must be objects")
            storage_pools.append(ProxmoxStoragePool.from_dict(cast(JSONDict, entry)))
        return cls(
            node_name=cast(Optional[str], data.get("node_name")),
            bridges=[str(value) for value in bridges_raw],
            gateway=cast(Optional[str], data.get("gateway")),
            nameservers=[str(value) for value in nameservers_raw],
            storage_pools=storage_pools,
            default_root_storage=cast(Optional[str], data.get("default_root_storage")),
            default_template_storage=cast(
                Optional[str], data.get("default_template_storage")
            ),
            default_bridge=cast(Optional[str], data.get("default_bridge")),
        )


@dataclass
class ProxmoxHost:
    """Connection details and cached discovery data for a Proxmox node."""

    name: str
    address: str
    schema_version: int = PROXMOX_HOST_SCHEMA_VERSION
    provider: str = PROXMOX_PROVIDER
    user: str = "root"
    ssh_key: Optional[str] = None
    description: Optional[str] = None
    default_storage: Optional[str] = None
    default_template_storage: Optional[str] = None
    default_bridge: Optional[str] = None
    facts: Optional[ProxmoxHostFacts] = None
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> JSONDict:
        return cast(JSONDict, asdict(self))

    @classmethod
    def from_dict(cls, data: JSONDict) -> "ProxmoxHost":
        if data.get("schema_version") != PROXMOX_HOST_SCHEMA_VERSION:
            raise ValueError(
                "Unsupported Proxmox host record schema; remove and re-register "
                "this development record with 'infra-tools proxmox add'"
            )
        if data.get("provider") != PROXMOX_PROVIDER:
            raise ValueError(
                "Proxmox host record must declare provider='proxmox'; remove and "
                "re-register this development record with 'infra-tools proxmox add'"
            )
        if "name" not in data or "address" not in data:
            raise ValueError("Proxmox host record missing 'name' or 'address'")
        tags_raw = data.get("tags") or []
        if not isinstance(tags_raw, list):
            raise ValueError("Proxmox host 'tags' must be a list")
        return cls(
            name=str(data["name"]),
            address=str(data["address"]),
            schema_version=PROXMOX_HOST_SCHEMA_VERSION,
            provider=PROXMOX_PROVIDER,
            user=str(data.get("user") or "root"),
            ssh_key=cast(Optional[str], data.get("ssh_key")),
            description=cast(Optional[str], data.get("description")),
            default_storage=cast(Optional[str], data.get("default_storage")),
            default_template_storage=cast(
                Optional[str], data.get("default_template_storage")
            ),
            default_bridge=cast(Optional[str], data.get("default_bridge")),
            facts=(
                ProxmoxHostFacts.from_dict(cast(JSONDict, data["facts"]))
                if isinstance(data.get("facts"), dict)
                else None
            ),
            tags=[str(t) for t in tags_raw],
        )


def get_proxmox_hosts_path(workspace: Optional[str] = None) -> str:
    """Return the registry JSON path inside the workspace."""
    return os.path.join(normalize_workspace_dir(workspace), PROXMOX_HOSTS_FILENAME)


def _load_raw(path: str) -> JSONList:
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Failed to read Proxmox host registry {path}: {exc}")
    if not isinstance(data, list):
        raise ValueError(
            f"Proxmox host registry {path} must contain a JSON array"
        )
    return cast(JSONList, data)


def _load_proxmox_hosts_unlocked(
    workspace: Optional[str] = None,
) -> list[ProxmoxHost]:
    path = get_proxmox_hosts_path(workspace)
    raw = _load_raw(path)
    hosts: list[ProxmoxHost] = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ValueError(f"Invalid entry in {path}: expected object")
        try:
            host = ProxmoxHost.from_dict(cast(JSONDict, entry))
            _validate_host_record(host)
        except ValueError as exc:
            raise ValueError(
                f"Invalid Proxmox host record {index} in {path}: {exc}"
            ) from exc
        hosts.append(host)
    return hosts


def load_proxmox_hosts(workspace: Optional[str] = None) -> list[ProxmoxHost]:
    """Load all registered Proxmox hosts."""
    path = get_proxmox_hosts_path(workspace)
    with resource_lock("proxmox-hosts", path, wait=True):
        return _load_proxmox_hosts_unlocked(workspace)


def _save_proxmox_hosts_unlocked(
    hosts: list[ProxmoxHost],
    workspace: Optional[str] = None,
) -> str:
    for host in hosts:
        _validate_host_record(host)
    ensure_workspace_dir(workspace)
    path = get_proxmox_hosts_path(workspace)
    payload = [host.to_dict() for host in hosts]
    write_json_atomic(path, payload, mode=0o600, sort_keys=True)
    return path


def save_proxmox_hosts(
    hosts: list[ProxmoxHost],
    workspace: Optional[str] = None,
) -> str:
    """Persist the registry, returning the file path."""
    path = get_proxmox_hosts_path(workspace)
    with resource_lock("proxmox-hosts", path, wait=True):
        return _save_proxmox_hosts_unlocked(hosts, workspace)


def find_proxmox_host(
    name_or_address: str, workspace: Optional[str] = None
) -> Optional[ProxmoxHost]:
    """Find a host by exact name or address match (case-insensitive name)."""
    needle = name_or_address.strip()
    needle_lc = needle.lower()
    for host in load_proxmox_hosts(workspace):
        if (
            host.name.lower() == needle_lc
            or host.address.lower().rstrip(".") == needle_lc.rstrip(".")
        ):
            return host
    return None


def _merge_host_tags(existing: list[str], incoming: list[str]) -> list[str]:
    tags = list(existing)
    for tag in incoming:
        if tag not in tags:
            tags.append(tag)
    return tags


def merge_proxmox_host(existing: ProxmoxHost, incoming: ProxmoxHost) -> ProxmoxHost:
    """Merge discovered host data into an existing registry entry."""
    return ProxmoxHost(
        name=incoming.name,
        address=incoming.address,
        schema_version=PROXMOX_HOST_SCHEMA_VERSION,
        provider=PROXMOX_PROVIDER,
        user=existing.user or incoming.user,
        ssh_key=existing.ssh_key or incoming.ssh_key,
        description=existing.description or incoming.description,
        default_storage=existing.default_storage or incoming.default_storage,
        default_template_storage=(
            existing.default_template_storage or incoming.default_template_storage
        ),
        default_bridge=existing.default_bridge or incoming.default_bridge,
        facts=incoming.facts or existing.facts,
        tags=_merge_host_tags(existing.tags, incoming.tags),
    )


def _validate_host_record(host: ProxmoxHost) -> None:
    if host.schema_version != PROXMOX_HOST_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported Proxmox host schema version: {host.schema_version}"
        )
    if host.provider != PROXMOX_PROVIDER:
        raise ValueError(
            f"Invalid provider for Proxmox host: {host.provider!r}"
        )
    if not host.name or not host.name.strip():
        raise ValueError("Proxmox host name is required")
    if any(ord(char) < 32 or ord(char) == 127 for char in host.name):
        raise ValueError("Proxmox host name must not contain control characters")
    if not host.address or not host.address.strip():
        raise ValueError("Proxmox host address is required")
    if not validate_host(host.address):
        raise ValueError(f"Invalid Proxmox host address: {host.address}")
    if not validate_username(host.user):
        raise ValueError(f"Invalid Proxmox SSH user: {host.user}")
    if host.ssh_key:
        validate_filesystem_path(host.ssh_key, must_exist=False)


def add_proxmox_host(
    host: ProxmoxHost,
    workspace: Optional[str] = None,
    *,
    replace: bool = False,
) -> ProxmoxHost:
    """Add or replace a host in the registry.

    Raises ValueError if the host already exists and ``replace`` is False.
    """
    _validate_host_record(host)

    path = get_proxmox_hosts_path(workspace)
    with resource_lock("proxmox-hosts", path, wait=True):
        hosts = _load_proxmox_hosts_unlocked(workspace)
        name_lc = host.name.lower()
        address_lc = host.address.lower().rstrip(".")
        matching_indexes = [
            index
            for index, existing in enumerate(hosts)
            if existing.name.lower() == name_lc
            or existing.address.lower().rstrip(".") == address_lc
        ]
        if len(matching_indexes) > 1:
            raise ValueError(
                "Proxmox host name or address matches multiple existing records"
            )
        matching_index = matching_indexes[0] if matching_indexes else None
        if matching_index is not None:
            existing = hosts[matching_index]
            if not replace:
                if existing.name.lower() == name_lc:
                    raise ValueError(
                        f"Proxmox host '{host.name}' already exists; "
                        f"use replace=True to update"
                    )
                raise ValueError(
                    f"Proxmox host address '{host.address}' already exists as "
                    f"'{existing.name}'; use replace=True to update"
                )
            hosts[matching_index] = host
            _save_proxmox_hosts_unlocked(hosts, workspace)
            return host
        hosts.append(host)
        _save_proxmox_hosts_unlocked(hosts, workspace)
        return host


def sync_proxmox_host(
    host: ProxmoxHost,
    workspace: Optional[str] = None,
) -> ProxmoxHost:
    """Insert or merge a host by matching either name or address."""
    _validate_host_record(host)

    path = get_proxmox_hosts_path(workspace)
    with resource_lock("proxmox-hosts", path, wait=True):
        hosts = _load_proxmox_hosts_unlocked(workspace)
        name_lc = host.name.lower()
        for index, existing in enumerate(hosts):
            if (
                existing.name.lower() == name_lc
                or existing.address.lower().rstrip(".")
                == host.address.lower().rstrip(".")
            ):
                hosts[index] = merge_proxmox_host(existing, host)
                _save_proxmox_hosts_unlocked(hosts, workspace)
                return hosts[index]
        hosts.append(host)
        _save_proxmox_hosts_unlocked(hosts, workspace)
        return host


def remove_proxmox_host(
    name_or_address: str, workspace: Optional[str] = None
) -> bool:
    """Remove a host by name or address, including an incompatible record."""
    needle = name_or_address.strip()
    needle_lc = needle.lower()
    path = get_proxmox_hosts_path(workspace)
    with resource_lock("proxmox-hosts", path, wait=True):
        raw = _load_raw(path)
        retained: JSONList = []
        for entry in raw:
            if not isinstance(entry, dict):
                retained.append(entry)
                continue
            name = entry.get("name")
            address = entry.get("address")
            if (
                isinstance(name, str)
                and name.lower() == needle_lc
            ) or (
                isinstance(address, str)
                and address.lower().rstrip(".") == needle_lc.rstrip(".")
            ):
                continue
            retained.append(entry)
        if len(retained) == len(raw):
            return False
        ensure_workspace_dir(workspace)
        write_json_atomic(path, retained, mode=0o600, sort_keys=True)
        return True
