#!/usr/bin/env python3
"""Proxmox VM provisioning via SSH using ``qm`` + cloud-init.

Mirrors :mod:`lib.proxmox_node`'s LXC provisioning so that the rest of the
setup pipeline can hand off to ``remote_setup`` regardless of whether the
target is an LXC container or a VM.

The flow is:

1. Resolve the cloud image (curated catalog, explicit URL, or pre-uploaded
   ``storage:import/...`` / ``storage:iso/...`` reference).
2. Download the qcow2 onto the Proxmox node, verify SHA-512 when known.
3. Allocate the next VMID, detect the bridge / gateway / nameservers (reused
   helpers from :mod:`lib.proxmox_node`).
4. ``qm create`` with a recovery console + virtio-scsi. Desktop/RDP guests
   receive VirtIO-GPU for noVNC; server guests retain the serial console.
5. ``qm disk import`` (or ``--import-from``) the qcow2 into the root storage,
   attach as ``scsi0``, set boot order, attach a cloud-init drive.
6. Cloud-init: user/SSH key/IP from basaltwater, then resize to the requested
   size and ``qm start``.
7. Wait for SSH on the target IP.
"""

from __future__ import annotations

from collections import Counter
import hashlib
import ipaddress
import json
import os
import re
import secrets
import shlex
import time
from dataclasses import dataclass
from typing import Optional, cast

from lib.cloud_images import (
    CloudImage,
    is_local_image_ref,
    parse_image_argument,
    resolve_cloud_image,
)
from lib.config import SetupConfig
from lib.proxmox_guest import (
    ProvisionError,
    _build_guest_hostname,
    _create_with_vmid_retry,
    _get_bridge_prefix_length,
    _get_guest_gateway,
    _get_host_nameservers,
    _get_next_vmid,
    _resolve_public_key_path,
    _resolve_storage_pool,
    _ssh_opts,
    _ssh_run,
    _wait_for_guest_ssh,
    auto_detect_bridge,
    enroll_provisioned_guest_host_keys,
    guest_provisioning_locks,
    remote_proxmox_locks,
)
from lib.proxmox_memory import (
    DEFAULT_BALLOON_TARGET_PERCENT,
    GuestMemoryAllocation,
    format_gib,
    parse_guest_memory_config,
)
from lib.types import NestedStrList, StrList
from lib.validation import parse_memory_mib
from lib.validators import validate_username
from lib.vm_storage import (
    VMDataDisk,
    VMDiskHardware,
    data_disks,
    disk_hardware,
    has_home_mount,
    storage_disk_serial,
    storage_size_kib,
)


class VMAlreadyExists(Exception):
    """Raised when a VM with the target IP already exists on the Proxmox node."""


@dataclass
class _ResolvedImage:
    """Either a remote URL with optional sha512, or an existing storage ref."""
    url: Optional[str]
    sha512: Optional[str]
    filename: Optional[str]
    storage_ref: Optional[str]


@dataclass(frozen=True)
class _ExistingVM:
    """Identity fields read from one existing Proxmox VM configuration."""

    vmid: int
    name: str
    ipv4_addresses: tuple[str, ...]
    cores: Optional[int]
    memory_mib: Optional[int]
    balloon_min_mib: Optional[int]
    balloon_shares: Optional[int]
    cpu_type: Optional[str]
    scsi_disks: tuple[tuple[str, str], ...]
    network_bridges: tuple[tuple[str, str], ...]


_UNIT_TO_KIB = {"K": 1, "M": 1024, "G": 1024 * 1024, "T": 1024 * 1024 * 1024}
_CPU_TYPE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]*$")


def _validate_cpu_type(value: str) -> str:
    """Return a safe Proxmox CPU model token."""

    cpu_type = (value or "").strip()
    if not _CPU_TYPE_RE.fullmatch(cpu_type):
        raise ProvisionError(
            "VM CPU type must be a Proxmox model name containing only letters, "
            "numbers, dots, underscores, plus signs, and hyphens"
        )
    return cpu_type


def _disk_hardware_value(
    volume: str,
    *,
    discard: bool,
    ssd: bool,
    backup: bool,
    serial: Optional[str] = None,
) -> str:
    """Apply basaltwater-managed hardware hints to a Proxmox disk value."""

    source_parts = volume.split(",")
    parts = source_parts[:1]
    has_iothread = False
    has_serial = False
    for part in source_parts[1:]:
        key, separator, _option_value = part.partition("=")
        if separator and key in {"discard", "ssd", "backup"}:
            continue
        if separator and key == "iothread":
            if not has_iothread:
                parts.append("iothread=1")
                has_iothread = True
            continue
        if serial is not None and separator and key == "serial":
            if not has_serial:
                parts.append(f"serial={serial}")
                has_serial = True
            continue
        parts.append(part)
    if not has_iothread:
        parts.append("iothread=1")
    if serial is not None and not has_serial:
        parts.append(f"serial={serial}")
    if discard:
        parts.append("discard=on")
    if ssd:
        parts.append("ssd=1")
    if not backup:
        parts.append("backup=0")
    return ",".join(parts)


def _disk_hardware_matches(
    value: str, *, discard: bool, ssd: bool, backup: bool
) -> bool:
    """Return whether one Proxmox disk has the requested managed hints."""

    options = {
        key: option_value
        for item in value.split(",")[1:]
        for key, separator, option_value in [item.partition("=")]
        if separator
    }
    return (
        (options.get("discard") == "on") == discard
        and (options.get("ssd") == "1") == ssd
        and (options.get("backup", "1") == "1") == backup
        and options.get("iothread") == "1"
    )


_MANAGED_DISK_OPTIONS = {"discard", "ssd", "backup", "iothread"}


def _disk_hardware_update_verified(
    original: str,
    observed: str,
    *,
    discard: bool,
    ssd: bool,
    backup: bool,
) -> bool:
    """Verify managed hints changed without losing provider disk metadata."""

    original_parts = original.split(",")
    observed_parts = observed.split(",")
    if (
        not original_parts
        or not observed_parts
        or original_parts[0] != observed_parts[0]
    ):
        return False

    def unowned_options(parts: list[str]) -> Counter[str]:
        return Counter(
            part
            for part in parts[1:]
            if part.partition("=")[0] not in _MANAGED_DISK_OPTIONS
        )

    original_options = unowned_options(original_parts)
    observed_options = unowned_options(observed_parts)
    if any(
        observed_options[option] < count
        for option, count in original_options.items()
    ):
        return False
    return _disk_hardware_matches(
        observed,
        discard=discard,
        ssd=ssd,
        backup=backup,
    )


def _disk_option(value: str, name: str) -> Optional[str]:
    """Return one comma-delimited Proxmox disk option."""

    for item in value.split(",")[1:]:
        key, separator, option_value = item.partition("=")
        if separator and key == name:
            return option_value
    return None


def _existing_managed_disks(
    vm: _ExistingVM,
    desired: dict[str, VMDiskHardware],
    *,
    allow_missing: Optional[set[str]] = None,
) -> dict[str, tuple[str, str]]:
    """Map declared logical disks to provider devices without adopting extras."""

    expected_serials = {
        storage_disk_serial(name): name
        for name in desired
        if name != "root"
    }
    managed: dict[str, tuple[str, str]] = {}
    for device, value in vm.scsi_disks:
        logical_name: Optional[str] = None
        if device == "scsi0" and "root" in desired:
            logical_name = "root"
        else:
            serial = _disk_option(value, "serial")
            if serial is not None:
                logical_name = expected_serials.get(serial)
        if logical_name is None:
            continue
        if logical_name in managed:
            raise ProvisionError(
                f"VM {vm.vmid} has multiple provider disks identified as "
                f"'{logical_name}'"
            )
        managed[logical_name] = (device, value)

    allowed_missing = allow_missing or set()
    missing = [
        name
        for name in desired
        if name not in managed and name not in allowed_missing
    ]
    if missing:
        raise ProvisionError(
            f"VM {vm.vmid} is missing declared disk identities: "
            + ", ".join(missing)
        )
    return managed


def _verify_existing_storage_layout(
    vm: _ExistingVM,
    managed_disks: dict[str, tuple[str, str]],
    desired: dict[str, VMDataDisk],
) -> None:
    """Verify a GUI-migrated VM uses the newly declared storage pools."""

    for logical_name, disk in desired.items():
        _device, value = managed_disks[logical_name]
        volume = value.split(",", 1)[0]
        observed_pool, separator, _volume_name = volume.partition(":")
        if not separator or observed_pool != disk.pool:
            raise ProvisionError(
                f"VM {vm.vmid} disk '{logical_name}' uses storage "
                f"'{observed_pool or volume}', expected '{disk.pool}'"
            )
        observed_size = _disk_option(value, "size")
        if observed_size is None:
            raise ProvisionError(
                f"VM {vm.vmid} disk '{logical_name}' did not report its size"
            )
        if storage_size_kib(observed_size) < storage_size_kib(disk.size):
            raise ProvisionError(
                f"VM {vm.vmid} disk '{logical_name}' is {observed_size}, "
                f"smaller than the saved {disk.size} declaration"
            )


def _verify_existing_bridge(vm: _ExistingVM, desired_bridge: str) -> None:
    """Verify the primary NIC of a GUI-migrated VM uses the declared bridge."""

    bridges = dict(vm.network_bridges)
    observed = bridges.get("net0")
    if observed != desired_bridge:
        raise ProvisionError(
            f"VM {vm.vmid} net0 uses bridge '{observed or 'none'}', "
            f"expected '{desired_bridge}'"
        )


def _storage_specs_with_resolved_pools(
    specs: NestedStrList,
    root_pool: str,
    data_disk_specs: list[VMDataDisk],
) -> NestedStrList:
    """Replace logical disk pool declarations with resolved provider pools."""

    resolved_by_name = {disk.name: disk.pool for disk in data_disk_specs}
    resolved_specs: NestedStrList = []
    for spec in specs:
        if len(spec) == 3 and spec[0] == "root":
            resolved_specs.append(["root", root_pool, spec[2]])
        elif len(spec) == 3 and spec[0] in resolved_by_name:
            resolved_specs.append(
                [spec[0], resolved_by_name[spec[0]], spec[2]]
            )
        else:
            resolved_specs.append(list(spec))
    return resolved_specs


def _parse_size_kib(value: str, *, label: str) -> int:
    s = (value or "").strip()
    if not s:
        raise ProvisionError(f"{label} must be a non-empty string like '2G'")
    unit = s[-1].upper()
    if unit in _UNIT_TO_KIB:
        digits, multiplier = s[:-1], _UNIT_TO_KIB[unit]
    else:
        digits, multiplier = s, _UNIT_TO_KIB["M"]  # bare number = MiB
    try:
        n = int(digits)
    except ValueError as exc:
        raise ProvisionError(f"Invalid {label}: {value!r}") from exc
    if n <= 0:
        raise ProvisionError(f"{label} must be positive (got {value!r})")
    return n * multiplier


def _parse_memory_mb(value: str) -> int:
    """Convert a memory string like ``2G`` / ``512M`` to mebibytes."""
    try:
        return parse_memory_mib(value, "VM memory")
    except ValueError as exc:
        raise ProvisionError(str(exc)) from exc


def _parse_disk_size_gib(value: str) -> int:
    """Convert a storage amount like ``32G`` / ``2T`` / ``8192M`` to GiB."""
    size_kib = _parse_size_kib(value, label="VM disk size")
    if size_kib < 1024 * 1024:
        raise ProvisionError(f"VM disk must be at least 1G (got {value!r})")
    gib = (size_kib + (1024 * 1024) - 1) // (1024 * 1024)
    return gib


def _json_command(
    node_ip: str,
    user: str,
    ssh_opts: StrList,
    command: str,
) -> object:
    """Run a Proxmox JSON command or raise a capacity-inspection error."""
    result = _ssh_run(node_ip, user, ssh_opts, command, dry_run=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip() or "unknown error"
        raise ProvisionError(f"{command} failed: {detail}")
    try:
        return json.loads(result.stdout or "")
    except json.JSONDecodeError as exc:
        raise ProvisionError(f"{command} returned invalid JSON") from exc


def _running_guest_memory(
    node_ip: str,
    user: str,
    ssh_opts: StrList,
    node_name: str,
) -> list[GuestMemoryAllocation]:
    """Return balloon floors and ceilings for running guests on one node."""
    resources = _json_command(
        node_ip,
        user,
        ssh_opts,
        "pvesh get /cluster/resources --type vm --output-format json",
    )
    if not isinstance(resources, list):
        raise ProvisionError("Proxmox cluster resources response was not a list")

    allocations: list[GuestMemoryAllocation] = []
    for raw_resource in resources:
        if not isinstance(raw_resource, dict):
            continue
        if raw_resource.get("node") != node_name:
            continue
        if raw_resource.get("status") != "running":
            continue
        guest_type = str(raw_resource.get("type", ""))
        if guest_type not in {"qemu", "lxc"}:
            continue
        try:
            vmid = int(raw_resource["vmid"])
        except (KeyError, TypeError, ValueError):
            continue
        command = "qm" if guest_type == "qemu" else "pct"
        result = _ssh_run(
            node_ip,
            user,
            ssh_opts,
            f"{command} config {vmid}",
            dry_run=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip() or "unknown error"
            raise ProvisionError(
                f"Could not inspect memory for running {guest_type} guest "
                f"{vmid}: {detail}"
            )
        allocation = parse_guest_memory_config(
            result.stdout or "",
            guest_type=guest_type,
            vmid=vmid,
        )
        if allocation:
            allocations.append(allocation)
    return allocations


def _report_memory_capacity(
    *,
    node_ip: str,
    user: str,
    ssh_opts: StrList,
    proposed_minimum_mib: int,
    proposed_maximum_mib: int,
    replacing_vmid: Optional[int] = None,
) -> bool:
    """Print advisory host-capacity comparisons for a VM allocation."""
    try:
        hostname = _ssh_run(
            node_ip,
            user,
            ssh_opts,
            "hostname -s",
            dry_run=False,
        )
        if hostname.returncode != 0 or not (hostname.stdout or "").strip():
            raise ProvisionError("Could not determine the Proxmox node name")
        node_name = (hostname.stdout or "").strip()
        status = _json_command(
            node_ip,
            user,
            ssh_opts,
            f"pvesh get /nodes/{shlex.quote(node_name)}/status --output-format json",
        )
        node_config = _json_command(
            node_ip,
            user,
            ssh_opts,
            f"pvesh get /nodes/{shlex.quote(node_name)}/config --output-format json",
        )
        if not isinstance(status, dict) or not isinstance(node_config, dict):
            raise ProvisionError("Proxmox node memory response was not an object")
        memory = status.get("memory")
        if not isinstance(memory, dict):
            raise ProvisionError("Proxmox node status did not include memory values")
        total_mib = int(memory.get("total", 0)) // (1024 * 1024)
        used_mib = int(memory.get("used", 0)) // (1024 * 1024)
        if total_mib <= 0:
            raise ProvisionError("Proxmox reported zero total host memory")
        target_percent = int(
            node_config.get(
                "ballooning-target",
                DEFAULT_BALLOON_TARGET_PERCENT,
            )
        )
        allocations = _running_guest_memory(
            node_ip,
            user,
            ssh_opts,
            node_name,
        )
        if replacing_vmid is not None:
            allocations = [
                allocation
                for allocation in allocations
                if allocation.vmid != replacing_vmid
            ]
    except (ProvisionError, TypeError, ValueError) as exc:
        print(f"  ⚠ Could not calculate Proxmox memory capacity: {exc}")
        return True

    target_mib = (total_mib * target_percent) // 100
    current_minimum_mib = sum(item.minimum_mib for item in allocations)
    current_maximum_mib = sum(item.maximum_mib for item in allocations)
    after_minimum_mib = current_minimum_mib + proposed_minimum_mib
    after_maximum_mib = current_maximum_mib + proposed_maximum_mib

    def target_ratio(value: int) -> int:
        return (value * 100) // target_mib if target_mib else 0

    print("  Proxmox memory capacity:")
    print(
        f"    Host: {format_gib(total_mib)} total, {format_gib(used_mib)} used; "
        f"target {target_percent}% = {format_gib(target_mib)}"
    )
    print(
        f"    Running guests ({len(allocations)}): "
        f"floors {format_gib(current_minimum_mib)}, "
        f"burst maxima {format_gib(current_maximum_mib)}"
    )
    proposed_label = (
        f"updated VM {replacing_vmid}"
        if replacing_vmid is not None
        else "proposed VM"
    )
    print(
        f"    After {proposed_label}: "
        f"floors {format_gib(after_minimum_mib)} "
        f"({target_ratio(after_minimum_mib)}% of target), "
        f"burst maxima {format_gib(after_maximum_mib)} "
        f"({target_ratio(after_maximum_mib)}% of target)"
    )
    if used_mib > target_mib:
        print(
            "  ⚠ Current host memory use is already above the balloon target by "
            f"{format_gib(used_mib - target_mib)}"
        )
    if after_minimum_mib > target_mib:
        print(
            "  ⚠ Running guest floors plus this allocation exceed the balloon target by "
            f"{format_gib(after_minimum_mib - target_mib)}; ballooning cannot "
            "reclaim below those floors"
        )
    elif after_maximum_mib > target_mib:
        print(
            "  ⚠ Guest burst maxima exceed the balloon target by "
            f"{format_gib(after_maximum_mib - target_mib)}; simultaneous peaks "
            "will contend for memory"
        )
    return after_minimum_mib <= target_mib


def _enforce_memory_floor(memory_floor_safe: bool, allow_overcommit: bool) -> None:
    """Require explicit authorization for an unreclaimable floor over target."""
    if memory_floor_safe:
        return
    if allow_overcommit:
        print(
            "  ⚠ Continuing because --allow-memory-overcommit explicitly "
            "permits the unsafe floor"
        )
        return
    raise ProvisionError(
        "Refusing to provision a VM whose running memory floor would exceed "
        "the Proxmox balloon target; lower --balloon-min, stop another guest, "
        "or use --allow-memory-overcommit"
    )


def _preflight_data_disk_capacity(
    disks: list[VMDataDisk],
    node_ip: str,
    user: str,
    ssh_opts: StrList,
) -> None:
    """Conservatively require enough reported free capacity for data disks."""

    requested_by_pool: dict[str, int] = {}
    for disk in disks:
        requested_by_pool[disk.pool] = (
            requested_by_pool.get(disk.pool, 0) + storage_size_kib(disk.size)
        )

    for pool, requested_kib in requested_by_pool.items():
        result = _ssh_run(
            node_ip,
            user,
            ssh_opts,
            f"pvesm status --storage {shlex.quote(pool)}",
            dry_run=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip() or "unknown error"
            raise ProvisionError(
                f"Could not inspect free capacity for storage pool '{pool}': {detail}"
            )

        available_kib: Optional[int] = None
        for line in (result.stdout or "").splitlines()[1:]:
            fields = line.split()
            if len(fields) < 6 or fields[0] != pool or fields[2] != "active":
                continue
            try:
                available_kib = int(fields[5])
            except ValueError as exc:
                raise ProvisionError(
                    f"Proxmox returned an invalid available-capacity value for '{pool}'"
                ) from exc
            break
        if available_kib is None:
            raise ProvisionError(
                f"Storage pool '{pool}' is not active or did not report available capacity"
            )
        if requested_kib > available_kib:
            requested_gib = (requested_kib + 1024 * 1024 - 1) // (1024 * 1024)
            available_gib = available_kib // (1024 * 1024)
            raise ProvisionError(
                f"Storage pool '{pool}' has {available_gib}G available but "
                f"{requested_gib}G of VM data disks were requested"
            )


def _needs_graphical_console(config: SetupConfig) -> bool:
    """Return whether a hosted VM needs a Proxmox graphical console."""
    return config.include_desktop or config.enable_rdp


def _parse_existing_vm(vmid: int, config_text: str) -> _ExistingVM:
    """Extract provider identity and reconciled hardware fields for a VM."""

    name = ""
    addresses: list[str] = []
    cores: Optional[int] = None
    memory_mib: Optional[int] = None
    balloon_min_mib: Optional[int] = None
    balloon_shares: Optional[int] = None
    cpu_type: Optional[str] = None
    scsi_disks: list[tuple[str, str]] = []
    network_bridges: list[tuple[str, str]] = []
    for line in config_text.splitlines():
        key, separator, value = line.partition(":")
        if not separator:
            continue
        normalized_key = key.strip()
        if normalized_key == "name":
            name = value.strip()
            continue
        if normalized_key == "cpu":
            cpu_type = value.strip().split(",", 1)[0] or None
            continue
        if re.fullmatch(r"scsi\d+", normalized_key):
            scsi_disks.append((normalized_key, value.strip()))
            continue
        if re.fullmatch(r"net\d+", normalized_key):
            bridge = _disk_option(value.strip(), "bridge")
            if bridge:
                network_bridges.append((normalized_key, bridge))
            continue
        if normalized_key == "cores":
            try:
                parsed_cores = int(value.strip())
            except ValueError:
                continue
            cores = parsed_cores if parsed_cores > 0 else None
            continue
        if normalized_key in {"memory", "balloon", "shares"}:
            try:
                parsed_value = int(value.strip())
            except ValueError:
                continue
            if normalized_key == "memory" and parsed_value > 0:
                memory_mib = parsed_value
            elif normalized_key == "balloon" and parsed_value >= 0:
                balloon_min_mib = parsed_value
            elif normalized_key == "shares" and parsed_value > 0:
                balloon_shares = parsed_value
            continue
        if not normalized_key.startswith("ipconfig"):
            continue
        for item in value.split(","):
            option, equals, option_value = item.strip().partition("=")
            if option != "ip" or not equals:
                continue
            try:
                interface = ipaddress.ip_interface(option_value)
            except ValueError:
                continue
            if isinstance(interface, ipaddress.IPv4Interface):
                addresses.append(str(interface.ip))
    return _ExistingVM(
        vmid=vmid,
        name=name,
        ipv4_addresses=tuple(addresses),
        cores=cores,
        memory_mib=memory_mib,
        balloon_min_mib=balloon_min_mib,
        balloon_shares=balloon_shares,
        cpu_type=cpu_type,
        scsi_disks=tuple(scsi_disks),
        network_bridges=tuple(network_bridges),
    )


def _list_existing_vms(
    node_ip: str,
    user: str,
    ssh_opts: StrList,
) -> list[_ExistingVM]:
    """Return identity data for every QEMU VM on ``node_ip``."""

    result = _ssh_run(node_ip, user, ssh_opts, "qm list", dry_run=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip() or "unknown error"
        raise ProvisionError(f"Failed to query VMs on {node_ip}: {detail}")
    vmids: list[int] = []
    for line in (result.stdout or "").splitlines()[1:]:
        parts = line.split()
        if not parts:
            continue
        try:
            vmids.append(int(parts[0]))
        except ValueError:
            continue
    existing: list[_ExistingVM] = []
    for vmid in vmids:
        cfg = _ssh_run(node_ip, user, ssh_opts, f"qm config {vmid}", dry_run=False)
        if cfg.returncode != 0:
            detail = (cfg.stderr or cfg.stdout or "").strip() or "unknown error"
            raise ProvisionError(f"Failed to inspect VM {vmid} on {node_ip}: {detail}")
        existing.append(_parse_existing_vm(vmid, cfg.stdout or ""))
    return existing


def _reconcile_existing_vm(
    node_ip: str,
    target_ip: str,
    desired_name: Optional[str],
    user: str,
    ssh_opts: StrList,
    *,
    desired_cores: Optional[int] = None,
    desired_memory_mib: Optional[int] = None,
    desired_balloon_min_mib: Optional[int] = None,
    desired_balloon_shares: Optional[int] = None,
    desired_cpu_type: Optional[str] = None,
    desired_disk_hardware: Optional[dict[str, VMDiskHardware]] = None,
    desired_storage_layout: Optional[dict[str, VMDataDisk]] = None,
    desired_bridge: Optional[str] = None,
    require_existing: bool = False,
    require_existing_name: bool = False,
    start_existing: bool = False,
    allow_managed_data_disks: bool = False,
    attach_missing_data_disks: Optional[set[str]] = None,
    allow_memory_overcommit: bool = False,
    dry_run: bool = False,
) -> bool:
    """Reuse the VM at ``target_ip`` and reconcile safe provider settings.

    A desired name owned by another VM is rejected before provisioning so a
    typo or copied setup command cannot create ambiguous duplicate names.
    """

    if dry_run:
        return False
    if desired_cores is not None and desired_cores < 1:
        raise ProvisionError("VM cores must be at least 1")
    if (desired_memory_mib is None) != (desired_balloon_min_mib is None):
        raise ProvisionError(
            "VM memory reconciliation requires both a maximum and balloon minimum"
        )
    if (
        desired_memory_mib is not None
        and desired_balloon_min_mib is not None
        and (
            desired_memory_mib < 1
            or desired_balloon_min_mib < 0
            or desired_balloon_min_mib > desired_memory_mib
        )
    ):
        raise ProvisionError("Invalid VM memory reconciliation values")
    if desired_balloon_shares is not None and not (
        1 <= desired_balloon_shares <= 50000
    ):
        raise ProvisionError("VM balloon shares must be between 1 and 50000")
    if desired_cpu_type is not None:
        desired_cpu_type = _validate_cpu_type(desired_cpu_type)
    if desired_disk_hardware is not None:
        for name, hardware in desired_disk_hardware.items():
            if (
                not isinstance(hardware, VMDiskHardware)
                or hardware.name != name
                or not isinstance(hardware.discard, bool)
                or not isinstance(hardware.ssd, bool)
                or not isinstance(hardware.backup, bool)
            ):
                raise ProvisionError("Invalid per-device VM disk hardware settings")
    authorized_attachments = set(attach_missing_data_disks or set())
    if "root" in authorized_attachments:
        raise ProvisionError("The existing VM root disk cannot be created additively")
    if authorized_attachments and (
        desired_disk_hardware is None
        or desired_storage_layout is None
        or not authorized_attachments.issubset(desired_disk_hardware)
        or not authorized_attachments.issubset(desired_storage_layout)
    ):
        raise ProvisionError(
            "Additive VM disks require complete storage and hardware declarations"
        )
    existing = _list_existing_vms(node_ip, user, ssh_opts)
    ip_matches = [vm for vm in existing if target_ip in vm.ipv4_addresses]
    if len(ip_matches) > 1:
        ids = ", ".join(str(vm.vmid) for vm in ip_matches)
        raise ProvisionError(
            f"Multiple VMs on {node_ip} are configured with IP {target_ip} "
            f"(VMIDs: {ids}); repair the duplicate addresses before provisioning"
        )

    name_matches = [
        vm
        for vm in existing
        if desired_name and vm.name.lower() == desired_name.lower()
    ]
    if not ip_matches:
        if name_matches:
            ids = ", ".join(str(vm.vmid) for vm in name_matches)
            raise ProvisionError(
                f"VM name '{desired_name}' already exists on {node_ip} "
                f"(VMIDs: {ids}) but is not configured with IP {target_ip}; "
                "choose a unique name or repair the existing VM explicitly"
            )
        if require_existing:
            raise ProvisionError(
                f"No VM matching saved name '{desired_name}' and IP {target_ip} "
                f"was found on rebind destination {node_ip}"
            )
        return False

    matched = ip_matches[0]
    conflicting_names = [vm for vm in name_matches if vm.vmid != matched.vmid]
    if conflicting_names:
        ids = ", ".join(str(vm.vmid) for vm in conflicting_names)
        raise ProvisionError(
            f"Cannot rename VM {matched.vmid} to '{desired_name}': that name is "
            f"already used on {node_ip} by VMID(s) {ids}"
        )
    if (
        require_existing_name
        and desired_name
        and matched.name.lower() != desired_name.lower()
    ):
        raise ProvisionError(
            f"VM {matched.vmid} at {target_ip} is named '{matched.name}', "
            f"expected saved name '{desired_name}' for provider rebind"
        )

    if (
        desired_disk_hardware is not None
        and any(name != "root" for name in desired_disk_hardware)
        and not allow_managed_data_disks
    ):
        raise ProvisionError(
            "named VM data disks, caches, and swap disks are provisioning-only; "
            "refusing to adopt disks on an existing unsaved VM"
        )

    managed_disks = (
        _existing_managed_disks(
            matched,
            desired_disk_hardware,
            allow_missing=authorized_attachments,
        )
        if desired_disk_hardware is not None
        else {}
    )
    if desired_storage_layout is not None:
        if (
            desired_disk_hardware is None
            or set(desired_storage_layout) != set(desired_disk_hardware)
        ):
            raise ProvisionError(
                "VM storage verification requires every declared disk"
            )
        _verify_existing_storage_layout(
            matched,
            managed_disks,
            {
                name: desired_storage_layout[name]
                for name in managed_disks
            },
        )
    if desired_bridge is not None:
        _verify_existing_bridge(matched, desired_bridge)

    status = _ssh_run(
        node_ip, user, ssh_opts, f"qm status {matched.vmid}", dry_run=False
    )
    if status.returncode != 0:
        detail = (status.stderr or status.stdout or "").strip() or "unknown error"
        raise ProvisionError(
            f"Could not verify VM {matched.vmid} on {node_ip}: {detail}"
        )
    status_text = status.stdout or ""
    start_after_reconcile = False
    if "status: stopped" in status_text and start_existing:
        start_after_reconcile = True
    elif "status: running" in status_text:
        probe = _ssh_run(
            node_ip,
            user,
            ssh_opts,
            f"timeout 3 bash -c '</dev/tcp/{shlex.quote(target_ip)}/22' && echo READY",
            dry_run=False,
        )
        if probe.returncode != 0 or "READY" not in (probe.stdout or ""):
            raise ProvisionError(
                f"VM {matched.vmid} is configured with IP {target_ip} but is not "
                "reachable on SSH; repair it before retrying provisioning"
            )
    else:
        raise ProvisionError(
            f"VM {matched.vmid} is not reachable because it is not running; "
            "start or repair it before retrying provisioning"
        )

    missing_attachments = [
        name
        for name in (desired_storage_layout or {})
        if name not in managed_disks
    ]
    memory_changed = bool(
        desired_memory_mib is not None
        and desired_balloon_min_mib is not None
        and (
            matched.memory_mib != desired_memory_mib
            or matched.balloon_min_mib != desired_balloon_min_mib
        )
    )
    if memory_changed:
        assert desired_memory_mib is not None
        assert desired_balloon_min_mib is not None
        memory_floor_safe = _report_memory_capacity(
            node_ip=node_ip,
            user=user,
            ssh_opts=ssh_opts,
            proposed_minimum_mib=desired_balloon_min_mib,
            proposed_maximum_mib=desired_memory_mib,
            replacing_vmid=matched.vmid,
        )
        _enforce_memory_floor(memory_floor_safe, allow_memory_overcommit)

    attached_disks: list[tuple[str, str]] = []
    if missing_attachments:
        assert desired_storage_layout is not None
        assert desired_disk_hardware is not None
        used_devices = {device for device, _value in matched.scsi_disks}
        available_devices = [
            f"scsi{index}"
            for index in range(1, 31)
            if f"scsi{index}" not in used_devices
        ]
        if len(available_devices) < len(missing_attachments):
            raise ProvisionError(
                f"VM {matched.vmid} has only {len(available_devices)} free "
                f"SCSI slot(s) for {len(missing_attachments)} requested disks: "
                + ", ".join(missing_attachments)
            )
        _preflight_data_disk_capacity(
            [desired_storage_layout[name] for name in missing_attachments],
            node_ip,
            user,
            ssh_opts,
        )
        for logical_name, available_device in zip(
            missing_attachments,
            available_devices,
        ):
            disk = desired_storage_layout[logical_name]
            hardware = desired_disk_hardware[logical_name]
            disk_value = _disk_hardware_value(
                f"{disk.pool}:{_parse_disk_size_gib(disk.size)}",
                discard=hardware.discard,
                ssd=hardware.ssd,
                backup=hardware.backup,
                serial=disk.serial,
            )
            attach = _ssh_run(
                node_ip,
                user,
                ssh_opts,
                f"qm set {matched.vmid} --{available_device} "
                f"{shlex.quote(disk_value)}",
                dry_run=False,
            )
            if attach.returncode != 0:
                detail = (attach.stderr or attach.stdout or "").strip()
                raise ProvisionError(
                    f"Could not attach VM data disk '{logical_name}' at "
                    f"{available_device}: {detail or 'unknown error'}"
                )
            attached_disks.append((logical_name, available_device))

        verify_attachments = _ssh_run(
            node_ip,
            user,
            ssh_opts,
            f"qm config {matched.vmid}",
            dry_run=False,
        )
        if verify_attachments.returncode != 0:
            detail = (
                verify_attachments.stderr or verify_attachments.stdout or ""
            ).strip()
            raise ProvisionError(
                f"Could not verify attached disks on VM {matched.vmid}: "
                f"{detail or 'unknown error'}"
            )
        matched = _parse_existing_vm(
            matched.vmid,
            verify_attachments.stdout or "",
        )
        managed_disks = _existing_managed_disks(
            matched,
            desired_disk_hardware,
        )
        _verify_existing_storage_layout(
            matched,
            managed_disks,
            desired_storage_layout,
        )

    name_changed = bool(
        desired_name and matched.name.lower() != desired_name.lower()
    )
    cores_changed = bool(
        desired_cores is not None and matched.cores != desired_cores
    )
    shares_changed = bool(
        desired_balloon_shares is not None
        and (matched.balloon_shares or 1000) != desired_balloon_shares
    )
    cpu_changed = bool(
        desired_cpu_type is not None and matched.cpu_type != desired_cpu_type
    )
    disk_changes = [
        (logical_name, device, value, desired_disk_hardware[logical_name])
        for logical_name, (device, value) in managed_disks.items()
        if desired_disk_hardware is not None
        and not _disk_hardware_matches(
            value,
            discard=desired_disk_hardware[logical_name].discard,
            ssd=desired_disk_hardware[logical_name].ssd,
            backup=desired_disk_hardware[logical_name].backup,
        )
    ]
    set_options: list[str] = []
    if name_changed:
        set_options.extend(["--name", shlex.quote(cast(str, desired_name))])
    if cores_changed:
        set_options.extend(["--cores", str(desired_cores)])
    if memory_changed:
        set_options.extend(
            [
                "--memory",
                str(desired_memory_mib),
                "--balloon",
                str(desired_balloon_min_mib),
            ]
        )
    if shares_changed:
        set_options.extend(["--shares", str(desired_balloon_shares)])
    if cpu_changed:
        set_options.extend(["--cpu", shlex.quote(cast(str, desired_cpu_type))])

    if set_options:
        update = _ssh_run(
            node_ip,
            user,
            ssh_opts,
            f"qm set {matched.vmid} {' '.join(set_options)}",
            dry_run=False,
        )
        if update.returncode != 0:
            detail = (update.stderr or update.stdout or "").strip() or "unknown error"
            raise ProvisionError(
                f"Failed to reconcile VM {matched.vmid} provider settings: {detail}"
            )

    for _logical_name, disk_name, disk_value, hardware in disk_changes:
        updated_value = _disk_hardware_value(
            disk_value,
            discard=hardware.discard,
            ssd=hardware.ssd,
            backup=hardware.backup,
        )
        update = _ssh_run(
            node_ip,
            user,
            ssh_opts,
            f"qm set {matched.vmid} --{disk_name} {shlex.quote(updated_value)}",
            dry_run=False,
        )
        if update.returncode != 0:
            detail = (update.stderr or update.stdout or "").strip() or "unknown error"
            raise ProvisionError(
                f"Failed to reconcile VM {matched.vmid} disk {disk_name}: {detail}"
            )

    if set_options or disk_changes:
        verify = _ssh_run(
            node_ip,
            user,
            ssh_opts,
            f"qm config {matched.vmid}",
            dry_run=False,
        )
        observed = _parse_existing_vm(matched.vmid, verify.stdout or "")
        name_verified = (
            not name_changed
            or observed.name.lower() == cast(str, desired_name).lower()
        )
        cores_verified = not cores_changed or observed.cores == desired_cores
        memory_verified = not memory_changed or (
            observed.memory_mib == desired_memory_mib
            and observed.balloon_min_mib == desired_balloon_min_mib
        )
        shares_verified = not shares_changed or (
            (observed.balloon_shares or 1000) == desired_balloon_shares
        )
        cpu_verified = not cpu_changed or observed.cpu_type == desired_cpu_type
        observed_disks = dict(observed.scsi_disks)
        disks_verified = not disk_changes or all(
            disk_name in observed_disks
            and _disk_hardware_update_verified(
                _disk_value,
                observed_disks[disk_name],
                discard=hardware.discard,
                ssd=hardware.ssd,
                backup=hardware.backup,
            )
            for _logical_name, disk_name, _disk_value, hardware in disk_changes
        )
        if (
            verify.returncode != 0
            or not name_verified
            or not cores_verified
            or not memory_verified
            or not shares_verified
            or not cpu_verified
            or not disks_verified
        ):
            raise ProvisionError(
                f"Proxmox did not preserve the requested provider settings "
                f"for VM {matched.vmid}"
            )

    if start_after_reconcile:
        started = _ssh_run(
            node_ip,
            user,
            ssh_opts,
            f"qm start {matched.vmid}",
            dry_run=False,
        )
        if started.returncode != 0:
            detail = (started.stderr or started.stdout or "").strip() or "unknown error"
            raise ProvisionError(
                f"Failed to start migrated VM {matched.vmid} on {node_ip}: {detail}"
            )
        _wait_for_guest_ssh(
            target_ip,
            node_ip,
            user,
            ssh_opts,
            timeout=300,
            dry_run=False,
            label="Migrated VM",
        )
        print(f"  ✓ Started migrated VM {matched.vmid} on {node_ip}")

    if name_changed:
        print(
            f"  ✓ Renamed existing VM {matched.vmid} from "
            f"'{matched.name or '<unnamed>'}' to '{desired_name}'"
        )
    if cores_changed:
        print(
            f"  ✓ Reconfigured existing VM {matched.vmid} from "
            f"{matched.cores or 'an unspecified number of'} to {desired_cores} cores"
        )
        if not start_after_reconcile:
            print(
                f"  ⚠ VM {matched.vmid} is running; restart it for the vCPU "
                "change to take effect in the guest"
            )
    if memory_changed:
        previous_maximum = matched.memory_mib or "an unspecified maximum"
        previous_minimum = (
            matched.balloon_min_mib
            if matched.balloon_min_mib is not None
            else "an unspecified floor"
        )
        print(
            f"  ✓ Reconfigured existing VM {matched.vmid} memory from "
            f"{previous_maximum}/{previous_minimum} MiB max/floor to "
            f"{desired_memory_mib}/{desired_balloon_min_mib} MiB"
        )
        if matched.memory_mib != desired_memory_mib and not start_after_reconcile:
            print(
                f"  ⚠ VM {matched.vmid} is running; restart it if the guest "
                "does not observe the new memory maximum"
            )
    if shares_changed:
        print(
            f"  ✓ Reconfigured existing VM {matched.vmid} balloon shares from "
            f"{matched.balloon_shares or 1000} to {desired_balloon_shares}"
        )
    if cpu_changed:
        print(
            f"  ✓ Reconfigured existing VM {matched.vmid} CPU model from "
            f"{matched.cpu_type or 'the Proxmox default'} to {desired_cpu_type}"
        )
    if disk_changes:
        details = ", ".join(
            f"{logical_name} (discard={'on' if hardware.discard else 'off'}, "
            f"SSD={'on' if hardware.ssd else 'off'}, "
            f"backup={'on' if hardware.backup else 'off'})"
            for logical_name, _device, _value, hardware in disk_changes
        )
        print(
            f"  ✓ Reconfigured {len(disk_changes)} managed disk(s) on VM "
            f"{matched.vmid}: {details}"
        )
    if attached_disks:
        details = ", ".join(
            f"{name} ({device})" for name, device in attached_disks
        )
        print(
            f"  ✓ Attached {len(attached_disks)} managed data disk(s) to "
            f"VM {matched.vmid}: {details}"
        )
    disk_model_changed = any(
        ((_disk_option(value, "discard") == "on") != hardware.discard)
        or ((_disk_option(value, "ssd") == "1") != hardware.ssd)
        or _disk_option(value, "iothread") != "1"
        for _logical_name, _device, value, hardware in disk_changes
    )
    if (cpu_changed or disk_model_changed) and not start_after_reconcile:
        print(
            f"  ⚠ VM {matched.vmid} is running; restart it for all hardware "
            "model changes to take effect in the guest"
        )

    print(
        f"  ✓ VM {matched.vmid} already exists and is reachable at IP {target_ip}"
    )
    return True


def check_vm_exists(
    node_ip: str,
    target_ip: str,
    user: str,
    ssh_opts: StrList,
    *,
    dry_run: bool = False,
) -> bool:
    """Return True if a reachable VM is configured with ``target_ip``."""

    return _reconcile_existing_vm(
        node_ip,
        target_ip,
        None,
        user,
        ssh_opts,
        dry_run=dry_run,
    )


def verify_vm_rebind_source_stopped(
    config: SetupConfig,
    *,
    dry_run: bool = False,
) -> None:
    """Refuse a provider rebind while the saved source VM can still run."""

    node_ip = cast(str, config.hosted_node)
    if dry_run:
        print(f"[DRY RUN] Would verify the saved VM is stopped or absent on {node_ip}")
        return

    target_ip = (
        str(ipaddress.ip_interface(config.static_ipv4).ip)
        if config.static_ipv4
        else config.host
    )
    desired_name = config.system_hostname or _build_guest_hostname(
        target_ip,
        config.friendly_name,
        default_prefix="vm",
    )
    ssh_opts = _ssh_opts(config.hosted_key)
    matches = [
        vm
        for vm in _list_existing_vms(node_ip, config.hosted_user, ssh_opts)
        if target_ip in vm.ipv4_addresses
        or vm.name.lower() == desired_name.lower()
    ]
    if len(matches) > 1:
        ids = ", ".join(str(vm.vmid) for vm in matches)
        raise ProvisionError(
            f"Saved source {node_ip} has multiple VMs matching {desired_name} / "
            f"{target_ip} (VMIDs: {ids})"
        )
    if not matches:
        print(f"  ✓ Saved source {node_ip} no longer contains the VM")
        return

    source = matches[0]
    status = _ssh_run(
        node_ip,
        config.hosted_user,
        ssh_opts,
        f"qm status {source.vmid}",
        dry_run=False,
    )
    if status.returncode != 0:
        detail = (status.stderr or status.stdout or "").strip() or "unknown error"
        raise ProvisionError(
            f"Could not verify source VM {source.vmid} on {node_ip}: {detail}"
        )
    if "status: stopped" not in (status.stdout or ""):
        raise ProvisionError(
            f"Refusing provider rebind while source VM {source.vmid} on "
            f"{node_ip} is not stopped"
        )
    print(f"  ✓ Saved source VM {source.vmid} on {node_ip} is stopped")


def _resolve_image(
    config: SetupConfig,
    explicit: Optional[str],
) -> tuple[_ResolvedImage, CloudImage | None]:
    """Resolve the image source from ``--image`` or the catalog.

    Returns the resolved image reference plus the catalog entry that backed it
    (if any) for logging purposes.
    """
    if explicit:
        url, storage_ref = parse_image_argument(explicit)
        if storage_ref:
            return (
                _ResolvedImage(url=None, sha512=None, filename=None, storage_ref=storage_ref),
                None,
            )
        if url:
            filename = url.rsplit("/", 1)[-1]
            sha512 = getattr(config, "vm_image_sha512", None)
            if not isinstance(sha512, str) or not re.fullmatch(r"[0-9A-Fa-f]{128}", sha512):
                raise ProvisionError(
                    "Custom VM image URLs require --image-sha512 with 128 hexadecimal characters"
                )
            return (
                _ResolvedImage(url=url, sha512=sha512.lower(), filename=filename, storage_ref=None),
                None,
            )
    image = resolve_cloud_image(config.container_base or "debian")
    return (
        _ResolvedImage(
            url=image["url"],
            sha512=image["sha512"] or None,
            filename=image["filename"],
            storage_ref=None,
        ),
        image,
    )


def _iso_staging_filename(filename: str) -> str:
    """Return a Proxmox-compatible ISO-content name for a qcow2 source."""
    stem, _suffix = os.path.splitext(filename)
    return f"{stem}.img"


def _resolve_image_storage(
    pool_arg: Optional[str],
    node_ip: str,
    user: str,
    ssh_opts: StrList,
    *,
    dry_run: bool,
) -> tuple[str, str]:
    """Choose storage and content type for a downloaded VM image.

    Proxmox VE's file-based ``import`` content type is the native home for
    qcow2 sources.  Older or more restricted nodes may only expose ``iso``;
    in that case the source is staged with a valid ``.img`` volume name and
    still imported by content detection from its qcow2 data.
    """
    requested_pool = pool_arg or "auto"
    for content_type in ("import", "iso"):
        try:
            pool = _resolve_storage_pool(
                requested_pool,
                node_ip,
                user,
                ssh_opts,
                content_type,
                dry_run=dry_run,
                strict_content=True,
            )
        except ProvisionError:
            continue
        print(f"  ✓ Image source storage: {pool} ({content_type})")
        return pool, content_type

    if requested_pool == "auto":
        raise ProvisionError(
            "No active Proxmox storage supports VM image import; enable the "
            "'import' or 'iso' content type, or specify --image-storage STORAGE"
        )
    raise ProvisionError(
        f"Storage pool '{requested_pool}' does not support 'import' or 'iso' "
        "content; choose a file-based storage with --image-storage STORAGE"
    )


def _warn_zfs_swap_storage(
    disks: list[VMDataDisk],
    swap_disk_names: set[str],
    node_ip: str,
    user: str,
    ssh_opts: StrList,
) -> None:
    """Warn when a guest swap disk is allocated from a Proxmox ZFS pool."""

    checked: dict[str, str | None] = {}
    for disk in disks:
        if disk.name not in swap_disk_names:
            continue
        if disk.pool not in checked:
            result = _ssh_run(
                node_ip,
                user,
                ssh_opts,
                f"pvesh get /storage/{shlex.quote(disk.pool)} --output-format json",
                dry_run=False,
            )
            storage_type: str | None = None
            if result.returncode == 0:
                try:
                    payload = json.loads(result.stdout or "{}")
                except json.JSONDecodeError:
                    payload = {}
                if isinstance(payload, dict) and isinstance(payload.get("type"), str):
                    storage_type = payload["type"]
            checked[disk.pool] = storage_type
        if checked[disk.pool] == "zfspool":
            print(
                f"  ⚠ Swap disk '{disk.name}' uses ZFS pool '{disk.pool}'. This "
                "is guest block I/O, not host zvol swap, but heavy swapping can "
                "still increase ZFS I/O and memory pressure; this layout is not "
                "yet qualified by basaltwater."
            )


def _download_image_to_host(
    image: _ResolvedImage,
    storage_pool: str,
    storage_content: str,
    node_ip: str,
    user: str,
    ssh_opts: StrList,
    *,
    dry_run: bool,
) -> str:
    """Fetch ``image`` onto the Proxmox node; return the absolute remote path."""
    if not image.url or not image.filename:
        raise ProvisionError("Internal error: download requested without URL")
    if storage_content not in {"import", "iso"}:
        raise ProvisionError(
            f"Unsupported VM image storage content type: {storage_content}"
        )
    staged_filename = (
        image.filename
        if storage_content == "import"
        else _iso_staging_filename(image.filename)
    )
    image_ref = f"{storage_pool}:{storage_content}/{staged_filename}"
    if dry_run:
        remote_dir = (
            "/var/lib/vz/import"
            if storage_content == "import"
            else "/var/lib/vz/template/iso"
        )
        remote_path = f"{remote_dir}/{staged_filename}"
    else:
        path_result = _ssh_run(
            node_ip,
            user,
            ssh_opts,
            f"pvesm path {shlex.quote(image_ref)}",
            dry_run=False,
        )
        remote_path = (path_result.stdout or "").strip()
        if path_result.returncode != 0 or not remote_path or not remote_path.startswith("/"):
            raise ProvisionError(
                f"Could not resolve image storage path for {image_ref}: "
                f"{(path_result.stderr or path_result.stdout or '').strip() or 'pvesm path failed'}"
            )
    remote_dir = remote_path.rsplit("/", 1)[0]

    if dry_run:
        print(f"  [DRY-RUN] Would download {image.url} → {remote_path}")
        return remote_path

    mkdir_result = _ssh_run(
        node_ip, user, ssh_opts, f"mkdir -p {shlex.quote(remote_dir)}"
    )
    if mkdir_result.returncode != 0:
        raise ProvisionError(
            f"Failed to prepare image storage path on {node_ip}: "
            f"{(mkdir_result.stderr or mkdir_result.stdout or '').strip() or 'mkdir failed'}"
        )
    partial_path = f"{remote_path}.part"
    fetch = (
        f"if [ ! -f {shlex.quote(remote_path)} ]; then "
        f"trap {shlex.quote(f'rm -f {shlex.quote(partial_path)}')} EXIT; "
        f"wget -q --https-only --show-progress -O {shlex.quote(partial_path)} "
        f"{shlex.quote(image.url)} && "
        f"mv {shlex.quote(partial_path)} {shlex.quote(remote_path)}; "
        "trap - EXIT; "
        f"fi"
    )
    commands = [fetch]
    if image.sha512:
        commands.append(
            f"echo {shlex.quote(image.sha512 + '  ' + remote_path)} "
            "| sha512sum -c -"
        )
    image_lock = hashlib.sha256(remote_path.encode("utf-8")).hexdigest()[:24]
    locked_fetch = shlex.join(
        [
            "flock",
            "--exclusive",
            f"/run/lock/basaltwater-image-{image_lock}.lock",
            "/bin/sh",
            "-c",
            " && ".join(commands),
        ]
    )
    print(f"  Downloading cloud image: {image.url}")
    result = _ssh_run(node_ip, user, ssh_opts, locked_fetch, timeout=1800)
    if result.returncode != 0:
        raise ProvisionError(
            f"Failed to stage cloud image on {node_ip}: "
            f"{(result.stderr or result.stdout or '').strip() or 'unknown error'}"
        )
    if image.sha512:
        print(f"  ✓ SHA-512 verified")
    else:
        print(f"  ⚠ No SHA-512 pinned for {image.filename}; skipping verification")

    return remote_path


def _render_user_data(
    *,
    username: str,
    pubkey_contents: Optional[str],
    create_setup_user: bool = True,
    nopasswd: bool = False,
) -> str:
    """Build a minimal cloud-init user-data document.

    Optionally creates ``username`` and installs the SSH key. An explicit
    ``nopasswd`` request also installs the managed sudo rule. The rest of
    basaltwater' setup runs over key-only root SSH, so standard users do not
    need a temporary passwordless bootstrap rule.
    """
    if not validate_username(username):
        raise ProvisionError(f"Invalid VM setup username: {username!r}")
    normalized_pubkey = pubkey_contents.strip() if pubkey_contents else None
    if normalized_pubkey and any(
        ord(char) < 32 or ord(char) == 127 for char in normalized_pubkey
    ):
        raise ProvisionError(
            "SSH public key must be a single line without control characters"
        )
    pubkey_yaml = (
        "'" + normalized_pubkey.replace("'", "''") + "'"
        if normalized_pubkey
        else None
    )
    lines = [
        "#cloud-config",
        f"hostname: __HOSTNAME__",
        "manage_etc_hosts: true",
        "package_update: true",
        "packages:",
        "  - qemu-guest-agent",
        "write_files:",
        "  - path: /etc/modules-load.d/basaltwater-virtio-balloon.conf",
        "    permissions: '0644'",
        "    content: |",
        "      virtio_balloon",
    ]
    if nopasswd and create_setup_user and username and username != "root":
        lines.extend([
            "  - path: /etc/sudoers.d/basaltwater-" + username,
            "    owner: root:root",
            "    permissions: '0440'",
            "    content: |",
            f"      {username} ALL=(ALL) NOPASSWD:ALL",
        ])
    lines.extend([
        "users:",
        "  - name: root",
        "    lock_passwd: false",
    ])
    if pubkey_yaml:
        lines.append("    ssh_authorized_keys:")
        lines.append(f"      - {pubkey_yaml}")
    if create_setup_user and username and username != "root":
        lines.extend([
            f"  - name: {username}",
            "    groups: sudo",
            "    shell: /bin/bash",
            "    lock_passwd: false",
        ])
        if pubkey_yaml:
            lines.append("    ssh_authorized_keys:")
            lines.append(f"      - {pubkey_yaml}")
    lines.append("ssh_pwauth: false")
    lines.extend([
        "runcmd:",
        "  - modprobe virtio_balloon",
        "  - systemctl enable --now qemu-guest-agent",
    ])
    lines.append("")
    return "\n".join(lines)


def _upload_user_data(
    user_data: str,
    hostname: str,
    storage_pool: str,
    node_ip: str,
    user: str,
    ssh_opts: StrList,
    *,
    dry_run: bool,
) -> Optional[str]:
    """Write the rendered user-data to a snippet on the node and return its path."""
    filename = f"basaltwater-{hostname}-{secrets.token_hex(8)}.yaml"
    snippet_ref = f"{storage_pool}:snippets/{filename}"
    if dry_run:
        return "/var/lib/vz/snippets/basaltwater-userdata.dryrun.yaml"

    rendered = user_data.replace("__HOSTNAME__", hostname)
    path_result = _ssh_run(
        node_ip,
        user,
        ssh_opts,
        f"pvesm path {shlex.quote(snippet_ref)}",
        dry_run=False,
    )
    remote_path = (path_result.stdout or "").strip()
    if path_result.returncode != 0 or not remote_path or not remote_path.startswith("/"):
        raise ProvisionError(
            f"Could not resolve snippet storage path for {snippet_ref}: "
            f"{(path_result.stderr or path_result.stdout or '').strip() or 'pvesm path failed'}"
        )
    snippets_dir = remote_path.rsplit("/", 1)[0]
    mkdir_result = _ssh_run(
        node_ip, user, ssh_opts, f"mkdir -p {shlex.quote(snippets_dir)}"
    )
    if mkdir_result.returncode != 0:
        raise ProvisionError(
            f"Failed to prepare snippet storage path on {node_ip}: "
            f"{(mkdir_result.stderr or mkdir_result.stdout or '').strip() or 'mkdir failed'}"
        )
    proc = _ssh_run(
        node_ip,
        user,
        ssh_opts,
        f"cat > {shlex.quote(remote_path)}",
        dry_run=False,
        input_data=rendered,
    )
    if proc.returncode != 0:
        raise ProvisionError(
            f"Failed to upload cloud-init user-data to {node_ip}: "
            f"{proc.stderr.strip() or 'unknown error'}"
        )
    return remote_path


def _destroy_vm_best_effort(
    vmid: int,
    node_ip: str,
    user: str,
    ssh_opts: StrList,
) -> None:
    """Remove a VM created by this run after a failed provisioning attempt."""
    print(f"  ⚠ Cleaning up partially provisioned VM {vmid}")
    failures: list[str] = []
    for command in (
        f"qm stop {vmid} --skiplock 1",
        f"qm destroy {vmid} --purge 1 --skiplock 1",
    ):
        result = _ssh_run(node_ip, user, ssh_opts, command, dry_run=False)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            failures.append(f"{command}: {detail or f'exit {result.returncode}'}")
    if failures:
        print("  ⚠ VM cleanup incomplete; inspect the Proxmox guest before retrying:")
        for failure in failures:
            print(f"    {failure}")
    else:
        print(f"  ✓ Removed partially provisioned VM {vmid}")


def _create_vm(
    *,
    vmid: int,
    target_ip: str,
    image_remote_path: Optional[str],
    storage_ref: Optional[str],
    memory_mb: int,
    balloon_min_mb: int,
    balloon_shares: int = 1000,
    cores: int,
    root_pool: str,
    disk_size_gib: int,
    data_disk_specs: list[VMDataDisk],
    cidr_prefix: str,
    bridge: str,
    gateway: str,
    nameservers: StrList,
    hostname: str,
    user_data_path: Optional[str],
    user_data_ref: Optional[str],
    graphical_console: bool,
    node_ip: str,
    user: str,
    ssh_opts: StrList,
    dry_run: bool = False,
    ipv6_cidr: Optional[str] = None,
    gateway6: Optional[str] = None,
    cpu_type: str = "host",
    disk_discard: bool = True,
    disk_ssd: bool = False,
    disk_hardware_settings: Optional[dict[str, VMDiskHardware]] = None,
) -> bool:
    """Build, populate, and start the VM on ``node_ip``."""
    cpu_type = _validate_cpu_type(cpu_type)
    ipconfig_parts = [
        f"ip={target_ip}/{cidr_prefix}",
        f"gw={gateway}",
    ]
    if ipv6_cidr:
        ipconfig_parts.append(f"ip6={ipv6_cidr}")
        if gateway6:
            ipconfig_parts.append(f"gw6={gateway6}")

    create_parts = [
        f"qm create {vmid}",
        f"--name {shlex.quote(hostname)}",
        f"--memory {memory_mb}",
        f"--balloon {balloon_min_mb}",
        f"--shares {balloon_shares}",
        f"--cores {cores}",
        f"--cpu {shlex.quote(cpu_type)}",
        "--ostype l26",
        "--scsihw virtio-scsi-single",
        "--serial0 socket",
        "--vga virtio" if graphical_console else "--vga serial0",
        "--agent enabled=1,freeze-fs=1",
        "--rng0 source=/dev/urandom",
        (
            f"--net0 virtio,bridge={shlex.quote(bridge)}"
        ),
        f"--ipconfig0 {shlex.quote(','.join(ipconfig_parts))}",
        f"--nameserver {shlex.quote(' '.join(nameservers))}",
        "--onboot 1",
    ]
    if user_data_ref:
        create_parts.append(f"--cicustom user={shlex.quote(user_data_ref)}")

    create_cmd = " ".join(create_parts)
    result = _ssh_run(node_ip, user, ssh_opts, create_cmd, dry_run=dry_run)
    if result.returncode != 0:
        raise ProvisionError(
            f"qm create {vmid} failed: "
            f"{(result.stderr or result.stdout or '').strip() or 'unknown error'}"
        )
    created = True

    # Attach the disk: prefer importing the qcow2; otherwise reference the
    # pre-uploaded storage volume directly.
    if image_remote_path:
        # Let Proxmox choose the target's native image format. Block-backed
        # pools such as LVM-thin only support raw volumes, while directory
        # pools commonly use qcow2.
        import_cmd = (
            f"qm disk import {vmid} {shlex.quote(image_remote_path)} "
            f"{shlex.quote(root_pool)}"
        )
        imported = _ssh_run(node_ip, user, ssh_opts, import_cmd, dry_run=dry_run)
        if imported.returncode != 0:
            if not dry_run and created:
                _destroy_vm_best_effort(vmid, node_ip, user, ssh_opts)
            raise ProvisionError(
                f"qm disk import for VM {vmid} failed: "
                f"{(imported.stderr or imported.stdout or '').strip() or 'unknown error'}"
            )
        # Proxmox names the imported volume {pool}:vm-{vmid}-disk-0.
        disk_volume = f"{root_pool}:vm-{vmid}-disk-0"
    elif storage_ref:
        # Caller has uploaded a qcow2 to e.g. local:import/foo.qcow2; let qm
        # import-from copy it into the root pool during set.
        disk_volume = f"{root_pool}:0,import-from={storage_ref}"
    else:
        if not dry_run and created:
            _destroy_vm_best_effort(vmid, node_ip, user, ssh_opts)
        raise ProvisionError("No image source available to attach to VM disk")

    default_hardware = VMDiskHardware("root", disk_discard, disk_ssd, True)
    root_hardware = (disk_hardware_settings or {}).get(
        "root",
        default_hardware,
    )
    root_disk_value = _disk_hardware_value(
        disk_volume,
        discard=root_hardware.discard,
        ssd=root_hardware.ssd,
        backup=root_hardware.backup,
    )
    set_cmd = (
        f"qm set {vmid} "
        f"--scsi0 {shlex.quote(root_disk_value)} "
        f"--ide2 {shlex.quote(root_pool)}:cloudinit "
        f"--boot order=scsi0"
    )
    set_result = _ssh_run(node_ip, user, ssh_opts, set_cmd, dry_run=dry_run)
    if set_result.returncode != 0:
        if not dry_run and created:
            _destroy_vm_best_effort(vmid, node_ip, user, ssh_opts)
        raise ProvisionError(
            f"qm set for VM {vmid} failed: "
            f"{(set_result.stderr or set_result.stdout or '').strip() or 'unknown error'}"
        )

    resize_cmd = f"qm resize {vmid} scsi0 {disk_size_gib}G"
    resize_result = _ssh_run(node_ip, user, ssh_opts, resize_cmd, dry_run=dry_run)
    if resize_result.returncode != 0:
        if not dry_run and created:
            _destroy_vm_best_effort(vmid, node_ip, user, ssh_opts)
        raise ProvisionError(
            f"qm resize for VM {vmid} failed: "
            f"{(resize_result.stderr or resize_result.stdout or '').strip() or 'the requested disk may be smaller than the image'}"
        )

    for index, disk in enumerate(data_disk_specs, 1):
        data_size_gib = _parse_disk_size_gib(disk.size)
        hardware = (disk_hardware_settings or {}).get(
            disk.name,
            VMDiskHardware(disk.name, disk_discard, disk_ssd, True),
        )
        # For a newly allocated volume, qm's STORAGE:SIZE syntax takes a bare
        # GiB count. A suffix such as ``32G`` is parsed as an existing LVM
        # volume name instead of a requested size.
        disk_option = _disk_hardware_value(
            f"{disk.pool}:{data_size_gib}",
            discard=hardware.discard,
            ssd=hardware.ssd,
            backup=hardware.backup,
            serial=disk.serial,
        )
        attach_result = _ssh_run(
            node_ip,
            user,
            ssh_opts,
            f"qm set {vmid} --scsi{index} {shlex.quote(disk_option)}",
            dry_run=dry_run,
        )
        if attach_result.returncode != 0:
            if not dry_run and created:
                _destroy_vm_best_effort(vmid, node_ip, user, ssh_opts)
            raise ProvisionError(
                f"Could not attach VM data disk '{disk.name}' at scsi{index}: "
                f"{(attach_result.stderr or attach_result.stdout or '').strip() or 'unknown error'}"
            )

    if data_disk_specs and not dry_run:
        config_result = _ssh_run(
            node_ip,
            user,
            ssh_opts,
            f"qm config {vmid}",
            dry_run=False,
        )
        config_text = config_result.stdout or ""
        if config_result.returncode != 0 or any(
            not re.search(
                rf"^scsi{index}: .*serial={re.escape(disk.serial)}(?:,|$)",
                config_text,
                flags=re.MULTILINE,
            )
            for index, disk in enumerate(data_disk_specs, 1)
        ):
            if created:
                _destroy_vm_best_effort(vmid, node_ip, user, ssh_opts)
            raise ProvisionError(
                f"Proxmox did not preserve the declared data-disk identities for VM {vmid}"
            )

    start_result = _ssh_run(
        node_ip, user, ssh_opts, f"qm start {vmid}", dry_run=dry_run
    )
    if start_result.returncode != 0:
        if not dry_run and created:
            _destroy_vm_best_effort(vmid, node_ip, user, ssh_opts)
        raise ProvisionError(
            f"qm start {vmid} failed: "
            f"{(start_result.stderr or start_result.stdout or '').strip() or 'unknown error'}"
        )

    print(f"  ✓ VM {vmid} created and started ({hostname}, {target_ip})")
    return True


def _wait_for_guest_agent(
    vmid: int,
    node_ip: str,
    user: str,
    ssh_opts: StrList,
    *,
    timeout: int = 180,
    poll_interval: int = 5,
    dry_run: bool = False,
) -> None:
    """Wait for qemu-guest-agent while cloud-init finishes guest setup."""
    if dry_run:
        print(f"  [DRY-RUN] Would wait for qemu-guest-agent in VM {vmid}")
        return

    print(
        "  Waiting for qemu-guest-agent "
        "(cloud-init may take a few minutes to install and start it)..."
    )
    deadline = time.time() + timeout
    attempts = 0
    progress_interval = max(1, 30 // max(1, poll_interval))
    while time.time() < deadline:
        attempts += 1
        result = _ssh_run(
            node_ip,
            user,
            ssh_opts,
            f"qm agent {vmid} ping",
            dry_run=False,
            quiet=True,
        )
        if result.returncode == 0:
            print("  ✓ qemu-guest-agent is responding")
            return
        if attempts % progress_interval == 0:
            elapsed = min(timeout, int(timeout - max(0, deadline - time.time())))
            print(
                "  Still waiting for qemu-guest-agent "
                f"({elapsed}s elapsed; cloud-init may still be completing)..."
            )
        time.sleep(poll_interval)

    print(
        "  ⚠ qemu-guest-agent was not ready after "
        f"{timeout}s; continuing with the SSH handoff"
    )


def provision_vm(
    config: SetupConfig,
    *,
    image: Optional[str] = None,
    allow_existing_data_disks: bool = False,
    require_existing_vm: bool = False,
    require_existing_name: bool = False,
    start_existing_vm: bool = False,
    verify_existing_bridge: bool = False,
    verify_existing_storage: bool = False,
    attach_missing_data_disks: Optional[set[str]] = None,
) -> None:
    """Reserve this guest identity and run the VM provisioning workflow."""
    call_options = {
        "image": image,
        "allow_existing_data_disks": allow_existing_data_disks,
        "require_existing_vm": require_existing_vm,
        "require_existing_name": require_existing_name,
        "start_existing_vm": start_existing_vm,
        "verify_existing_bridge": verify_existing_bridge,
        "verify_existing_storage": verify_existing_storage,
        "attach_missing_data_disks": attach_missing_data_disks,
    }
    if config.dry_run:
        return _provision_vm_locked(config, **call_options)
    node_ip = cast(str, config.hosted_node)
    static_ipv4 = ipaddress.ip_interface(config.static_ipv4) if config.static_ipv4 else None
    target_ip = str(static_ipv4.ip) if static_ipv4 else config.host
    hostname = config.system_hostname or _build_guest_hostname(
        target_ip,
        config.friendly_name,
        default_prefix="vm",
    )
    with guest_provisioning_locks(node_ip, target_ip, hostname):
        with remote_proxmox_locks(
            node_ip,
            config.hosted_user,
            _ssh_opts(config.hosted_key),
            [f"address|{target_ip}", f"hostname|{hostname.lower()}"],
            wait=False,
            description=f"guest {hostname} ({target_ip})",
        ):
            return _provision_vm_locked(config, **call_options)


def _provision_vm_locked(
    config: SetupConfig,
    *,
    image: Optional[str] = None,
    allow_existing_data_disks: bool = False,
    require_existing_vm: bool = False,
    require_existing_name: bool = False,
    start_existing_vm: bool = False,
    verify_existing_bridge: bool = False,
    verify_existing_storage: bool = False,
    attach_missing_data_disks: Optional[set[str]] = None,
) -> None:
    """Orchestrate Proxmox VM provisioning.

    Args:
        config: SetupConfig with hosted_node, container_memory, container_storage, etc.
        image: Optional override; either an http(s) URL or a Proxmox storage
            reference like ``local:import/foo.qcow2``.
        allow_existing_data_disks: Permit reconciliation of named disks only
            when the caller has matched this guest to saved provisioning state.
        require_existing_vm: Refuse to create a replacement when rebind lookup fails.
        require_existing_name: Require an existing VM to retain the saved name.
        start_existing_vm: Start a verified existing VM when it is stopped.
        verify_existing_bridge: Require an existing VM's net0 bridge to match.
        verify_existing_storage: Require existing disks to match declared pools.
        attach_missing_data_disks: Exact named data disks that may be created on
            a saved existing VM when their managed identities are absent.

    Raises:
        VMAlreadyExists: if a VM with the target IP already exists on the node.
        ProvisionError: on any provisioning failure.
    """
    node_ip = cast(str, config.hosted_node)
    memory_str = cast(str, config.container_memory)
    storage_specs = cast(NestedStrList, config.container_storage)
    user: str = config.hosted_user
    static_ipv4 = ipaddress.ip_interface(config.static_ipv4) if config.static_ipv4 else None
    if static_ipv4 is not None and not isinstance(static_ipv4, ipaddress.IPv4Interface):
        raise ProvisionError("VM provisioning requires an IPv4 setup target")
    target_ip = str(static_ipv4.ip) if static_ipv4 else config.host
    ssh_opts = _ssh_opts(config.hosted_key)
    dry_run = config.dry_run

    root_spec: Optional[StrList] = None
    for spec in storage_specs or []:
        if spec and spec[0] == "root":
            root_spec = list(spec)
            break
    if not root_spec or len(root_spec) < 3:
        raise ProvisionError("Missing root storage specification (--storage root POOL AMOUNT)")
    root_pool_arg, disk_amount = root_spec[1], root_spec[2]
    declared_data_disks = data_disks(config)

    memory_mb = _parse_memory_mb(memory_str)
    balloon_min_mb = (
        _parse_memory_mb(config.vm_balloon_min)
        if config.vm_balloon_min
        else memory_mb
    )
    balloon_shares = getattr(config, "vm_balloon_shares", 1000)
    disk_size_gib = _parse_disk_size_gib(disk_amount)
    cpu_type = _validate_cpu_type(getattr(config, "vm_cpu_type", "host"))
    disk_discard = getattr(config, "vm_disk_discard", True)
    disk_ssd = getattr(config, "vm_disk_ssd", False)
    disk_hardware_settings = disk_hardware(config)

    hostname = config.system_hostname or _build_guest_hostname(
        target_ip,
        config.friendly_name,
        default_prefix="vm",
    )

    resolved, catalog_entry = _resolve_image(config, image)

    needs_public_key = not dry_run and not require_existing_vm
    pub_path = _resolve_public_key_path(config.ssh_key) if needs_public_key else None
    pubkey_contents: Optional[str] = None
    if needs_public_key:
        if not pub_path:
            raise ProvisionError(
                "VM provisioning requires a readable SSH private key with a matching .pub file"
            )
        try:
            with open(pub_path, "r", encoding="utf-8") as fh:
                pubkey_contents = fh.read().strip()
        except OSError as exc:
            raise ProvisionError(f"Failed to read public key {pub_path}: {exc}")
        print(f"  Using public key for VM access: {pub_path}")

    if dry_run:
        print("[DRY RUN] Would provision Proxmox VM:")
        if attach_missing_data_disks:
            print(
                "  Existing VM storage extension: attach "
                + ", ".join(sorted(attach_missing_data_disks))
            )
        if require_existing_name or start_existing_vm:
            print(
                "  Existing VM rebind: require and verify the destination, then "
                "start it if stopped"
            )
        elif verify_existing_bridge or verify_existing_storage:
            print(
                "  Existing VM verification: require the saved guest and "
                "verify its declared provider layout"
            )
        print(f"  Proxmox node: {node_ip}")
        print(f"  Target IP: {target_ip}")
        if config.static_ipv6:
            print(f"  Static IPv6: {config.static_ipv6}")
        if config.network_gateway4:
            print(f"  IPv4 gateway: {config.network_gateway4}")
        else:
            print("  IPv4 gateway: auto-detect from selected Proxmox bridge")
        if config.network_gateway6:
            print(f"  IPv6 gateway: {config.network_gateway6}")
        if config.network_dns:
            print(f"  DNS servers: {', '.join(config.network_dns)}")
        else:
            print("  DNS servers: auto-detect from Proxmox node")
        print(f"  Hostname: {hostname}")
        print(f"  Memory: {memory_mb} MiB")
        if balloon_min_mb < memory_mb:
            print(f"  Balloon minimum: {balloon_min_mb} MiB (dynamic)")
            print(f"  Balloon shares: {balloon_shares} (relative priority)")
        else:
            print(f"  Balloon minimum: {balloon_min_mb} MiB (fixed allocation)")
        print(f"  Cores: {config.container_cores}")
        print(f"  CPU model: {cpu_type}")
        print(
            "  Disk hint defaults: "
            f"discard={'on' if disk_discard else 'off'}, "
            f"SSD={'on' if disk_ssd else 'off'}, "
            f"backup={'on' if getattr(config, 'vm_disk_backup', True) else 'off'}"
        )
        print(
            "  Console: "
            + ("VirtIO-GPU + serial" if _needs_graphical_console(config) else "serial")
        )
        root_hardware = disk_hardware_settings["root"]
        print(
            f"  Root storage: {root_pool_arg} ({disk_size_gib}G, "
            f"discard={'on' if root_hardware.discard else 'off'}, "
            f"SSD={'on' if root_hardware.ssd else 'off'}, "
            f"backup={'on' if root_hardware.backup else 'off'})"
        )
        for index, disk in enumerate(declared_data_disks, 1):
            hardware = disk_hardware_settings[disk.name]
            print(
                f"  Data storage {disk.name}: {disk.pool} "
                f"({_parse_disk_size_gib(disk.size)}G, scsi{index}, "
                f"serial={disk.serial}, "
                f"discard={'on' if hardware.discard else 'off'}, "
                f"SSD={'on' if hardware.ssd else 'off'}, "
                f"backup={'on' if hardware.backup else 'off'})"
            )
        if catalog_entry:
            print(f"  Image (catalog): {catalog_entry['codename']} {catalog_entry['snapshot']} → {catalog_entry['filename']}")
        elif resolved.storage_ref:
            print(f"  Image (storage ref): {resolved.storage_ref}")
        else:
            print(f"  Image (URL): {resolved.url}")
        if not resolved.storage_ref:
            print(
                "  Image source storage: "
                f"{config.vm_image_storage or 'auto (import, then iso fallback)'}"
            )
        return

    print(f"  Hostname: {hostname}")

    bridge = auto_detect_bridge(
        node_ip,
        user,
        config.hosted_key,
        preferred_bridge=getattr(config, "hosted_bridge", None),
    )
    if static_ipv4 is None:
        raise ProvisionError("VM provisioning requires an IPv4 setup target")
    gateway = config.network_gateway4 or _get_guest_gateway(
        node_ip,
        user,
        ssh_opts,
        bridge,
        static_ipv4,
    )
    nameservers = list(config.network_dns or _get_host_nameservers(
        node_ip,
        user,
        ssh_opts,
        bridge=bridge,
        fallback_gateway=gateway,
    ))
    config.hosted_bridge = bridge
    config.network_gateway4 = gateway
    config.network_dns = nameservers
    cidr_prefix = (
        str(static_ipv4.network.prefixlen)
        if static_ipv4
        else _get_bridge_prefix_length(node_ip, user, ssh_opts, bridge)
    )

    verified_storage_layout: Optional[dict[str, VMDataDisk]] = None
    resolved_root_pool: Optional[str] = None
    resolved_data_disks: Optional[list[VMDataDisk]] = None
    if verify_existing_storage:
        resolved_root_pool = _resolve_storage_pool(
            root_pool_arg,
            node_ip,
            user,
            ssh_opts,
            "images",
        )
        resolved_data_disks = [
            VMDataDisk(
                disk.name,
                _resolve_storage_pool(
                    disk.pool,
                    node_ip,
                    user,
                    ssh_opts,
                    "images",
                    strict_content=True,
                ),
                disk.size,
            )
            for disk in declared_data_disks
        ]
        verified_storage_layout = {
            "root": VMDataDisk("root", resolved_root_pool, disk_amount),
            **{disk.name: disk for disk in resolved_data_disks},
        }
        config.container_storage = _storage_specs_with_resolved_pools(
            storage_specs,
            resolved_root_pool,
            resolved_data_disks,
        )
        storage_specs = cast(NestedStrList, config.container_storage)

    with remote_proxmox_locks(
        node_ip,
        user,
        ssh_opts,
        ["guest-admission"],
        wait=True,
        description="guest capacity admission",
    ):
        existing_vm_reconciled = _reconcile_existing_vm(
            node_ip,
            target_ip,
            hostname,
            user,
            ssh_opts,
            desired_cores=config.container_cores,
            desired_memory_mib=memory_mb,
            desired_balloon_min_mib=balloon_min_mb,
            desired_balloon_shares=balloon_shares,
            desired_cpu_type=cpu_type,
            desired_disk_hardware=disk_hardware_settings,
            desired_storage_layout=verified_storage_layout,
            desired_bridge=bridge if verify_existing_bridge else None,
            require_existing=require_existing_vm,
            require_existing_name=require_existing_name,
            start_existing=start_existing_vm,
            allow_managed_data_disks=allow_existing_data_disks,
            attach_missing_data_disks=attach_missing_data_disks,
            allow_memory_overcommit=getattr(
                config,
                "allow_memory_overcommit",
                False,
            ),
        )
    if existing_vm_reconciled:
        raise VMAlreadyExists(
            f"VM with IP {target_ip} already exists on {node_ip}"
        )

    root_pool = resolved_root_pool or _resolve_storage_pool(
        root_pool_arg, node_ip, user, ssh_opts, "images"
    )
    if resolved_data_disks is None:
        resolved_data_disks = [
            VMDataDisk(
                disk.name,
                _resolve_storage_pool(
                    disk.pool,
                    node_ip,
                    user,
                    ssh_opts,
                    "images",
                    strict_content=True,
                ),
                disk.size,
            )
            for disk in declared_data_disks
        ]
    from lib.swap_config import swap_device_disk_names

    _warn_zfs_swap_storage(
        resolved_data_disks,
        swap_device_disk_names(config),
        node_ip,
        user,
        ssh_opts,
    )
    config.container_storage = _storage_specs_with_resolved_pools(
        storage_specs,
        root_pool,
        resolved_data_disks,
    )
    snippet_pool = _resolve_storage_pool(
        "auto", node_ip, user, ssh_opts, "snippets"
    )

    if resolved.url:
        image_pool, image_content = _resolve_image_storage(
            config.vm_image_storage,
            node_ip,
            user,
            ssh_opts,
            dry_run=dry_run,
        )
        image_remote_path = _download_image_to_host(
            resolved,
            image_pool,
            image_content,
            node_ip,
            user,
            ssh_opts,
            dry_run=dry_run,
        )
        storage_ref: Optional[str] = None
    else:
        image_remote_path = None
        storage_ref = resolved.storage_ref
        if storage_ref:
            if not is_local_image_ref(storage_ref):
                raise ProvisionError(
                    f"Invalid --image storage ref: {storage_ref}; expected "
                    "STORAGE:import/FILE or STORAGE:iso/FILE"
                )

    user_data = _render_user_data(
        username=config.username, pubkey_contents=pubkey_contents,
        create_setup_user=not has_home_mount(config),
        nopasswd=config.nopasswd,
    )
    user_data_path = _upload_user_data(
        user_data, hostname, snippet_pool, node_ip, user, ssh_opts, dry_run=dry_run
    )
    user_data_ref = (
        f"{snippet_pool}:snippets/{os.path.basename(user_data_path)}"
        if user_data_path
        else None
    )
    vm_started = False
    provision_complete = False

    try:
        with remote_proxmox_locks(
            node_ip,
            user,
            ssh_opts,
            ["guest-admission"],
            wait=True,
            description="guest capacity admission",
        ):
            memory_floor_safe = _report_memory_capacity(
                node_ip=node_ip,
                user=user,
                ssh_opts=ssh_opts,
                proposed_minimum_mib=balloon_min_mb,
                proposed_maximum_mib=memory_mb,
            )
            _enforce_memory_floor(
                memory_floor_safe,
                getattr(config, "allow_memory_overcommit", False),
            )
            _preflight_data_disk_capacity(
                resolved_data_disks,
                node_ip,
                user,
                ssh_opts,
            )
            vmid = _get_next_vmid(node_ip, user, ssh_opts)
            create_kwargs = {
                "vmid": vmid,
                "target_ip": target_ip,
                "image_remote_path": image_remote_path,
                "storage_ref": storage_ref,
                "memory_mb": memory_mb,
                "balloon_min_mb": balloon_min_mb,
                "balloon_shares": balloon_shares,
                "cores": config.container_cores,
                "root_pool": root_pool,
                "disk_size_gib": disk_size_gib,
                "data_disk_specs": resolved_data_disks,
                "cidr_prefix": cidr_prefix,
                "bridge": bridge,
                "gateway": gateway,
                "nameservers": nameservers,
                "hostname": hostname,
                "user_data_path": user_data_path,
                "user_data_ref": user_data_ref,
                "graphical_console": _needs_graphical_console(config),
                "node_ip": node_ip,
                "user": user,
                "ssh_opts": ssh_opts,
                "dry_run": dry_run,
                "ipv6_cidr": config.static_ipv6,
                "gateway6": config.network_gateway6,
                "cpu_type": cpu_type,
                "disk_discard": disk_discard,
                "disk_ssd": disk_ssd,
                "disk_hardware_settings": disk_hardware_settings,
            }
            vmid = _create_with_vmid_retry(
                _create_vm,
                lambda: _get_next_vmid(node_ip, user, ssh_opts),
                create_kwargs,
            )
        vm_started = True
        _wait_for_guest_agent(
            vmid,
            node_ip,
            user,
            ssh_opts,
            dry_run=dry_run,
        )
        # Cloud-init takes longer than LXC startup; bump the timeout.
        _wait_for_guest_ssh(
            target_ip,
            node_ip,
            user,
            ssh_opts,
            timeout=300,
            dry_run=dry_run,
            label="VM",
        )
        enroll_provisioned_guest_host_keys(
            target_ip,
            node_ip,
            user,
            ssh_opts,
            dry_run=dry_run,
        )
        provision_complete = True
    except Exception:
        if vm_started and not dry_run:
            _destroy_vm_best_effort(vmid, node_ip, user, ssh_opts)
        raise
    finally:
        # Detach custom cloud-init before deleting its snippet. Leaving a
        # cicustom reference to a removed file breaks later cloud-init updates,
        # migration, and some clone workflows.
        if user_data_path and not dry_run:
            if provision_complete:
                detach_result = _ssh_run(
                    node_ip,
                    user,
                    ssh_opts,
                    f"qm set {vmid} --delete cicustom",
                    dry_run=False,
                )
                if detach_result.returncode != 0:
                    print(
                        "  ⚠ Could not detach the cloud-init snippet reference; "
                        f"preserving {user_data_path}"
                    )
                else:
                    _ssh_run(
                        node_ip, user, ssh_opts,
                        f"rm -f {shlex.quote(user_data_path)}",
                        dry_run=False,
                    )
            else:
                # Failed provisioning destroys the partial VM, so the snippet
                # is no longer referenced.
                _ssh_run(
                    node_ip, user, ssh_opts,
                    f"rm -f {shlex.quote(user_data_path)}",
                    dry_run=False,
                )
            # Tiny grace period so qemu-guest-agent / cloud-init finish flushing
            # before subsequent setup steps log in.
            time.sleep(2)
