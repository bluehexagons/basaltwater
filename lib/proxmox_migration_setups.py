"""Prepare narrowly scoped saved-setup changes for a verified guest migration."""

from __future__ import annotations

import re
from typing import Any

from lib.cache import PROVIDER_BINDING_FIELDS, load_all_setup_cache_records, update_setup_provider_binding
from lib.config import SetupConfig
from lib.proxmox_guest import _build_guest_hostname
from lib.proxmox_hosts import ProxmoxHost, find_proxmox_host
from lib.vm_storage import storage_disk_serials, storage_size_kib


def drive_parts(value: str) -> tuple[str, dict[str, str]]:
    """Split a provider drive into its volume reference and named properties."""
    volume, *properties = value.split(",")
    options = {}
    for item in properties:
        key, separator, option = item.partition("=")
        if not separator or key in options:
            raise ValueError("Invalid or duplicate provider drive properties")
        options[key] = option
    return volume, options


def guest_address(identity: dict[str, Any], kind: str) -> str | None:
    value = identity.get("ipconfig0" if kind == "qemu" else "net0", "")
    match = re.search(r"(?:^|,)ip=([^,]+)", str(value))
    return match.group(1).split("/", 1)[0] if match else None


def prepare_setup_updates(
    source: ProxmoxHost,
    target: ProxmoxHost,
    *,
    source_node: str,
    kind: str,
    identity: dict[str, Any],
    disks: dict[str, dict[str, Any]],
    workspace: str,
) -> list[dict[str, Any]]:
    """Match saved guests by provider, name, address, and managed disk identity."""
    updates = []
    name = identity["name" if kind == "qemu" else "hostname"]
    address = guest_address(identity, kind)
    source_names = {value.lower().rstrip(".") for value in (source.name, source.address, source_node)}
    for record in load_all_setup_cache_records(workspace):
        args = record["args"]
        node = args.get("hosted_node")
        if not node:
            continue
        registered = find_proxmox_host(node, workspace)
        binding = registered.address if registered else node
        if binding.lower().rstrip(".") not in source_names:
            continue
        config = SetupConfig.from_dict(record["host"], record["system_type"], args)
        expected_name = config.system_hostname or _build_guest_hostname(
            config.host, config.friendly_name or record.get("name"),
            default_prefix="vm" if config.machine_type == "vm" else "guest",
        )
        if expected_name.lower() != name.lower() and config.host != address:
            continue
        if (expected_name.lower() != name.lower() or config.host != address
                or (config.machine_type == "vm") != (kind == "qemu")):
            raise ValueError(f"Saved setup identity does not match guest {name!r}: {record['host']}")
        storage = []
        for spec in config.container_storage or []:
            if spec[0] == "template":
                storage.append(list(spec))
                continue
            if len(spec) not in (2, 3):
                raise ValueError(f"Invalid saved disk declaration for {record['host']}")
            logical = spec[0]
            if logical == "root":
                candidates = ["scsi0" if kind == "qemu" else "rootfs"]
            else:
                candidates = [
                    device for device, disk in disks.items()
                    if re.fullmatch(r"scsi\d+", device)
                    and disk["options"].get("serial") in storage_disk_serials(logical)
                ]
            if len(candidates) != 1 or candidates[0] not in disks:
                raise ValueError(f"Missing or ambiguous saved disk {logical!r} for {record['host']}")
            disk = disks[candidates[0]]
            if len(spec) == 3 and spec[1] != disk["source_pool"]:
                raise ValueError(f"Saved disk pool disagrees with the source for {logical!r}")
            if disk["bytes"] < storage_size_kib(spec[-1]) * 1024:
                raise ValueError(f"Source disk is smaller than saved disk {logical!r}")
            storage.append([logical, disk["target_pool"], spec[-1]])
        if not storage:
            raise ValueError(f"Saved guest {record['host']} has no verifiable storage declaration")
        if any(update["host"] == record["host"] for update in updates):
            raise ValueError(f"Ambiguous saved setup cache records for {record['host']}")
        bridge_match = re.search(r"(?:^|,)bridge=([^,]+)", str(identity.get("net0", "")))
        bridge = bridge_match.group(1) if bridge_match else None
        if config.hosted_bridge and config.hosted_bridge != bridge:
            raise ValueError(f"Saved bridge disagrees with the source for {record['host']}")
        changed = {
            "hosted_node": target.address, "hosted_user": target.user,
            "hosted_key": target.ssh_key, "hosted_bridge": bridge,
            "container_storage": storage,
        }
        compared = PROVIDER_BINDING_FIELDS | {
            "machine_type", "system_hostname", "friendly_name", "username", "static_ipv4",
        }
        updates.append({
            "host": record["host"], "system_type": record["system_type"],
            "name": config.friendly_name or record.get("name") or record["host"],
            "expected": {key: args.get(key) for key in compared}, "updates": changed,
        })
    return updates


def apply_setup_updates(updates: list[dict[str, Any]], workspace: str) -> None:
    """Apply inspected updates; atomic per file and safe to resume after a failure."""
    for update in updates:
        update_setup_provider_binding(
            update["host"], system_type=update["system_type"],
            expected=update["expected"], updates=update["updates"], workspace=workspace,
        )
        print(f"  Updated saved setup {update['name']} for the destination host and disks")


def validate_setup_updates(plan: dict[str, Any]) -> None:
    """Check recovery updates still describe the guest and verified destination."""
    address = guest_address(plan["identity"], plan["kind"])
    seen = set()
    bridge_match = re.search(r"(?:^|,)bridge=([^,]+)", str(plan["identity"].get("net0", "")))
    bridge = bridge_match.group(1) if bridge_match else None
    for update in plan["setup_updates"]:
        if (not isinstance(update, dict) or update.get("host") != address
                or update["host"] in seen or not isinstance(update.get("expected"), dict)
                or not isinstance(update.get("updates"), dict)):
            raise ValueError("Invalid recorded saved setup identity")
        seen.add(update["host"])
        changed = update["updates"]
        if (changed.keys() != PROVIDER_BINDING_FIELDS or changed["hosted_node"] != plan["target"]
                or changed["hosted_user"] != plan["target_user"] or changed["hosted_key"] != plan["target_key"]
                or changed["hosted_bridge"] != bridge):
            raise ValueError("Recorded setup update disagrees with verified destination")
        old = {spec[0]: spec for spec in update["expected"]["container_storage"]}
        new = {spec[0]: spec for spec in changed["container_storage"]}
        if old.keys() != new.keys() or len(new) != len(changed["container_storage"]):
            raise ValueError("Recorded setup update changes logical disk identities")
        for name, spec in new.items():
            if name == "template":
                if spec != old[name]:
                    raise ValueError("Recorded template storage changed")
                continue
            candidates = (["scsi0" if plan["kind"] == "qemu" else "rootfs"] if name == "root" else [
                device for device, disk in plan["disks"].items()
                if re.fullmatch(r"scsi\d+", device) and disk["options"].get("serial") in storage_disk_serials(name)
            ])
            if (len(spec) != 3 or spec[-1] != old[name][-1] or len(candidates) != 1
                    or spec[1] != plan["disks"][candidates[0]]["target_pool"]):
                raise ValueError("Recorded setup disk update disagrees with verified disk plan")
