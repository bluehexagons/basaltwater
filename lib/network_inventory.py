"""Workspace-backed generic network inventory records."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from typing import Optional, Union, cast

from lib.atomic_io import write_json_atomic
from lib.concurrency import resource_lock
from lib.types import JSONDict
from lib.validation import (
    validate_network_cidr,
    validate_network_ip,
    validate_network_ip_or_cidr,
    validate_network_name,
    validate_network_provider,
    validate_network_vlan_id,
)
from lib.workspace import ensure_workspace_dir, normalize_workspace_dir


NETWORK_INVENTORY_FILENAME = "network_inventory.json"
NetworkVlanId = Optional[Union[int, str]]


@dataclass
class NetworkSubnet:
    """Named subnet or single address range in a network profile."""

    name: str
    cidr: str
    zone: Optional[str] = None
    vlan_id: NetworkVlanId = None
    gateway: Optional[str] = None

    def to_dict(self) -> JSONDict:
        return cast(JSONDict, asdict(self))

    @classmethod
    def from_dict(cls, data: JSONDict) -> "NetworkSubnet":
        vlan_id_raw = data.get("vlan_id")
        vlan_id: NetworkVlanId
        if vlan_id_raw is None:
            vlan_id = None
        elif isinstance(vlan_id_raw, (int, str)):
            vlan_id = vlan_id_raw
        else:
            raise ValueError("Network subnet vlan_id must be a string or integer")
        return cls(
            name=str(data.get("name") or ""),
            cidr=str(data.get("cidr") or ""),
            zone=cast(Optional[str], data.get("zone")),
            vlan_id=vlan_id,
            gateway=cast(Optional[str], data.get("gateway")),
        )


@dataclass
class NetworkHost:
    """A host known to a network profile."""

    name: str
    address: str
    provider: str = "generic"
    roles: list[str] = field(default_factory=list)
    profile_ref: Optional[str] = None

    def to_dict(self) -> JSONDict:
        return cast(JSONDict, asdict(self))

    @classmethod
    def from_dict(cls, data: JSONDict) -> "NetworkHost":
        roles_raw = data.get("roles") or []
        if not isinstance(roles_raw, list):
            raise ValueError("Network host roles must be a list")
        return cls(
            name=str(data.get("name") or ""),
            address=str(data.get("address") or ""),
            provider=str(data.get("provider") or "generic"),
            roles=[str(role) for role in roles_raw],
            profile_ref=cast(Optional[str], data.get("profile_ref")),
        )


@dataclass
class NetworkProfile:
    """A provider-neutral network environment description."""

    name: str
    management_sources: list[str] = field(default_factory=list)
    control_plane: list[str] = field(default_factory=list)
    guest_networks: list[str] = field(default_factory=list)
    subnets: list[NetworkSubnet] = field(default_factory=list)
    hosts: list[NetworkHost] = field(default_factory=list)

    def to_dict(self) -> JSONDict:
        payload = {
            "name": self.name,
            "management_sources": list(self.management_sources),
            "control_plane": list(self.control_plane),
            "guest_networks": list(self.guest_networks),
            "subnets": [subnet.to_dict() for subnet in self.subnets],
            "hosts": [host.to_dict() for host in self.hosts],
        }
        return cast(JSONDict, payload)

    @classmethod
    def from_dict(cls, data: JSONDict) -> "NetworkProfile":
        management_raw = data.get("management_sources") or []
        control_raw = data.get("control_plane") or []
        guest_raw = data.get("guest_networks") or []
        subnets_raw = data.get("subnets") or []
        hosts_raw = data.get("hosts") or []
        if not isinstance(management_raw, list):
            raise ValueError("Network profile management_sources must be a list")
        if not isinstance(control_raw, list):
            raise ValueError("Network profile control_plane must be a list")
        if not isinstance(guest_raw, list):
            raise ValueError("Network profile guest_networks must be a list")
        if not isinstance(subnets_raw, list):
            raise ValueError("Network profile subnets must be a list")
        if not isinstance(hosts_raw, list):
            raise ValueError("Network profile hosts must be a list")
        profile = cls(
            name=str(data.get("name") or ""),
            management_sources=[str(value) for value in management_raw],
            control_plane=[str(value) for value in control_raw],
            guest_networks=[str(value) for value in guest_raw],
            subnets=[
                NetworkSubnet.from_dict(cast(JSONDict, entry))
                for entry in subnets_raw
                if isinstance(entry, dict)
            ],
            hosts=[
                NetworkHost.from_dict(cast(JSONDict, entry))
                for entry in hosts_raw
                if isinstance(entry, dict)
            ],
        )
        validate_network_profile(profile)
        for subnet in profile.subnets:
            if subnet.vlan_id is not None:
                subnet.vlan_id = validate_network_vlan_id(subnet.vlan_id)
        return profile


def get_network_inventory_path(workspace: Optional[str] = None) -> str:
    """Return the network inventory path inside the workspace."""

    return os.path.join(normalize_workspace_dir(workspace), NETWORK_INVENTORY_FILENAME)


def _load_network_profiles_unlocked(
    workspace: Optional[str] = None,
) -> list[NetworkProfile]:
    path = get_network_inventory_path(workspace)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as file_obj:
            data = json.load(file_obj)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Failed to read network inventory {path}: {exc}")
    if not isinstance(data, dict):
        raise ValueError(f"Network inventory {path} must contain a JSON object")
    profiles_raw = data.get("profiles") or []
    if not isinstance(profiles_raw, list):
        raise ValueError(f"Network inventory {path} profiles must be a list")
    return [
        NetworkProfile.from_dict(cast(JSONDict, entry))
        for entry in profiles_raw
        if isinstance(entry, dict)
    ]


def load_network_profiles(workspace: Optional[str] = None) -> list[NetworkProfile]:
    """Load every saved network profile."""

    path = get_network_inventory_path(workspace)
    with resource_lock("network-inventory", path, wait=True):
        return _load_network_profiles_unlocked(workspace)


def _save_network_profiles_unlocked(
    profiles: list[NetworkProfile],
    workspace: Optional[str] = None,
) -> str:
    ensure_workspace_dir(workspace)
    for profile in profiles:
        validate_network_profile(profile)
    path = get_network_inventory_path(workspace)
    payload = {"profiles": [profile.to_dict() for profile in profiles]}
    write_json_atomic(path, payload, mode=0o600, sort_keys=True)
    return path


def save_network_profiles(
    profiles: list[NetworkProfile],
    workspace: Optional[str] = None,
) -> str:
    """Persist network profiles and return the inventory path."""

    path = get_network_inventory_path(workspace)
    with resource_lock("network-inventory", path, wait=True):
        return _save_network_profiles_unlocked(profiles, workspace)


def find_network_profile(
    name: str,
    workspace: Optional[str] = None,
) -> Optional[NetworkProfile]:
    """Find a network profile by case-insensitive name."""

    needle = name.strip().lower()
    for profile in load_network_profiles(workspace):
        if profile.name.lower() == needle:
            return profile
    return None


def upsert_network_profile(
    profile: NetworkProfile,
    workspace: Optional[str] = None,
    *,
    replace: bool = False,
) -> NetworkProfile:
    """Add or replace a network profile."""

    validate_network_profile(profile)
    path = get_network_inventory_path(workspace)
    with resource_lock("network-inventory", path, wait=True):
        profiles = _load_network_profiles_unlocked(workspace)
        name_lc = profile.name.lower()
        for index, existing in enumerate(profiles):
            if existing.name.lower() == name_lc:
                if not replace:
                    raise ValueError(
                        f"Network profile '{profile.name}' already exists; use --replace"
                    )
                profiles[index] = profile
                _save_network_profiles_unlocked(profiles, workspace)
                return profile
        profiles.append(profile)
        _save_network_profiles_unlocked(profiles, workspace)
        return profile


def save_network_profile(
    profile: NetworkProfile,
    workspace: Optional[str] = None,
) -> NetworkProfile:
    """Persist one profile, replacing any profile with the same name."""

    validate_network_profile(profile)
    path = get_network_inventory_path(workspace)
    with resource_lock("network-inventory", path, wait=True):
        profiles = _load_network_profiles_unlocked(workspace)
        name_lc = profile.name.lower()
        for index, existing in enumerate(profiles):
            if existing.name.lower() == name_lc:
                if existing.to_dict() == profile.to_dict():
                    return existing
                profiles[index] = profile
                _save_network_profiles_unlocked(profiles, workspace)
                return profile
        profiles.append(profile)
        _save_network_profiles_unlocked(profiles, workspace)
        return profile


def add_network_host(
    profile_name: str,
    host: NetworkHost,
    workspace: Optional[str] = None,
    *,
    replace: bool = False,
) -> NetworkProfile:
    """Add or replace a host inside a network profile."""

    validate_network_host(host)
    path = get_network_inventory_path(workspace)
    with resource_lock("network-inventory", path, wait=True):
        profiles = _load_network_profiles_unlocked(workspace)
        profile_lc = profile_name.strip().lower()
        for profile_index, profile in enumerate(profiles):
            if profile.name.lower() != profile_lc:
                continue
            host_lc = host.name.lower()
            for host_index, existing in enumerate(profile.hosts):
                if existing.name.lower() == host_lc or existing.address == host.address:
                    if not replace:
                        raise ValueError(
                            f"Host '{host.name}' already exists in profile "
                            f"'{profile.name}'; use --replace"
                        )
                    profile.hosts[host_index] = host
                    validate_network_profile(profile)
                    profiles[profile_index] = profile
                    _save_network_profiles_unlocked(profiles, workspace)
                    return profile
            profile.hosts.append(host)
            validate_network_profile(profile)
            profiles[profile_index] = profile
            _save_network_profiles_unlocked(profiles, workspace)
            return profile
        raise ValueError(f"No network profile named '{profile_name}'")


def validate_network_profile(profile: NetworkProfile) -> None:
    """Validate a network profile and all nested records."""

    validate_network_name(profile.name, "Network profile name")
    _validate_endpoint_list(profile.management_sources, "management source")
    _validate_endpoint_list(profile.control_plane, "control-plane address")
    _validate_endpoint_list(profile.guest_networks, "guest network")
    seen_subnets: set[str] = set()
    for subnet in profile.subnets:
        validate_network_subnet(subnet)
        subnet_lc = subnet.name.lower()
        if subnet_lc in seen_subnets:
            raise ValueError(
                f"Duplicate subnet name in profile '{profile.name}': {subnet.name}"
            )
        seen_subnets.add(subnet_lc)
    seen_hosts: set[str] = set()
    for host in profile.hosts:
        validate_network_host(host)
        host_lc = host.name.lower()
        if host_lc in seen_hosts:
            raise ValueError(
                f"Duplicate host name in profile '{profile.name}': {host.name}"
            )
        seen_hosts.add(host_lc)


def validate_network_subnet(subnet: NetworkSubnet) -> None:
    """Validate a network subnet record."""

    validate_network_name(subnet.name, "Subnet name")
    validate_network_cidr(subnet.cidr, "subnet CIDR")
    if subnet.zone:
        validate_network_name(subnet.zone, "Subnet zone")
    if subnet.vlan_id is not None:
        validate_network_vlan_id(subnet.vlan_id)
    if subnet.gateway:
        validate_network_ip(subnet.gateway, "subnet gateway")


def validate_network_host(host: NetworkHost) -> None:
    """Validate a network host record."""

    validate_network_name(host.name, "Host name")
    validate_network_ip(host.address, "host address")
    validate_network_provider(host.provider)
    for role in host.roles:
        validate_network_name(role, "Host role")


def _validate_endpoint_list(values: list[str], label: str) -> None:
    for value in values:
        validate_network_ip_or_cidr(value, label)


__all__ = [
    "NETWORK_INVENTORY_FILENAME",
    "NetworkHost",
    "NetworkProfile",
    "NetworkSubnet",
    "add_network_host",
    "find_network_profile",
    "get_network_inventory_path",
    "load_network_profiles",
    "save_network_profile",
    "save_network_profiles",
    "upsert_network_profile",
    "validate_network_host",
    "validate_network_profile",
    "validate_network_subnet",
]
