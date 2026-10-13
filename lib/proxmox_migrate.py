"""Verified Proxmox migration with power-state restoration and saved bindings."""

from __future__ import annotations

import json
import os
import re
import shlex
from contextlib import ExitStack
from typing import Any

from lib.concurrency import ResourceBusyError, resource_lock
from lib.operation_state import OperationStateError, OperationStateStore
from lib.proxmox_guest import ProvisionError, remote_proxmox_locks
from lib.proxmox_hosts import ProxmoxHost
from lib.proxmox_migration_setups import (
    apply_setup_updates, drive_parts, guest_address, prepare_setup_updates, validate_setup_updates,
)
from lib.remote_utils import CommandTimeoutError, run
from lib.ssh_utils import build_ssh_command, get_ssh_control_path, ssh_process_timeout
from lib.validation import (
    validate_filesystem_path, validate_network_ip, validate_positive_integer, validate_proxmox_storage_name,
)
from lib.validators import validate_host, validate_ip_address, validate_username
from lib.vm_storage import storage_size_kib
from lib.workspace import get_known_hosts_path, normalize_workspace_dir


DEFAULT_MIGRATION_TIMEOUT = 3600
DEFAULT_SHUTDOWN_TIMEOUT = 180
_DRIVE = re.compile(r"(?:scsi|sata|ide|virtio|mp|unused)\d+|efidisk0|tpmstate0|rootfs")
_IDENTITY = re.compile(r"(?:net|ipconfig|serial)\d+|name|hostname|smbios1|vmgenid|memory|balloon|cpu|cores|sockets|bios|machine|scsihw|ostype|onboot|startup|agent|vga|protection")


class ProxmoxMigrateError(Exception):
    """Migration was refused, failed, or needs inspected recovery."""


def _ssh(host: ProxmoxHost, workspace: str, command: str | None = None) -> list[str]:
    trust = get_known_hosts_path(workspace)
    return build_ssh_command(
        host.address, host.user, host.ssh_key, remote_command=command,
        known_hosts_path=trust,
        control_path=get_ssh_control_path(host.address, host.user, host.ssh_key, known_hosts_path=trust),
    )


def _run(host: ProxmoxHost, command: str, workspace: str, *, timeout: int = 120,
         mutation: bool = False) -> str:
    result = run(
        _ssh(host, workspace, command), check=False, capture_output=True,
        timeout=timeout if mutation else ssh_process_timeout(timeout),
        display_cmd=f"Proxmox migration on {host.name}: {command}",
    )
    if result.returncode:
        raise ProxmoxMigrateError(
            f"Command failed on {host.name}: {command}: "
            f"{(result.stderr or result.stdout or 'unknown error').strip()[-1000:]}"
        )
    return result.stdout or ""


def _api(host: ProxmoxHost, path: str, workspace: str, *options: str) -> Any:
    output = _run(host, shlex.join(["pvesh", "get", path, *options, "--output-format", "json"]), workspace)
    try:
        return json.loads(output)
    except ValueError as exc:
        raise ProxmoxMigrateError(f"Invalid Proxmox JSON for {path} on {host.name}") from exc


def _rows(value: Any, label: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
        raise ProxmoxMigrateError(f"Invalid {label} inventory")
    return value


def _cluster(host: ProxmoxHost, workspace: str) -> dict[str, Any]:
    rows = _rows(_api(host, "/cluster/status", workspace), "cluster")
    clusters = [row for row in rows if row.get("type") == "cluster"]
    nodes = [row for row in rows if row.get("type") == "node"]
    local = [row for row in nodes if row.get("local") == 1]
    if (len(clusters) != 1 or clusters[0].get("quorate") != 1 or len(local) != 1
            or local[0].get("online") != 1 or not validate_host(local[0].get("name"))
            or "." in local[0]["name"]):
        raise ProxmoxMigrateError(f"Cannot verify a quorate Proxmox cluster on {host.name}")
    names = set()
    for node in nodes:
        if not validate_host(node.get("name")) or node["name"] in names or node.get("online") not in (0, 1):
            raise ProxmoxMigrateError("Invalid or duplicate cluster node")
        names.add(node["name"])
        if node.get("ip") is not None:
            validate_network_ip(node["ip"], "Proxmox node IP")
    ca = _run(host, "sha256sum /etc/pve/pve-root-ca.pem", workspace).split()
    if not ca or not re.fullmatch(r"[a-f0-9]{64}", ca[0]):
        raise ProxmoxMigrateError(f"Invalid cluster CA identity on {host.name}")
    return {"name": local[0]["name"], "ip": local[0].get("ip"), "nodes": nodes, "ca": ca[0]}


def _verify_cluster(source: dict[str, Any], target: dict[str, Any]) -> None:
    if source["name"] == target["name"]:
        raise ProxmoxMigrateError("Migration destination is the source node")
    if source["ca"] != target["ca"]:
        raise ProxmoxMigrateError("Source and destination belong to different Proxmox clusters")
    for state, peer in ((source, target), (target, source)):
        matches = [row for row in state["nodes"] if row.get("name") == peer["name"]]
        if (len(matches) != 1 or matches[0].get("online") != 1 or not peer["ip"]
                or matches[0].get("ip") != peer["ip"]):
            raise ProxmoxMigrateError("Cannot verify reciprocal online cluster membership")


def _guests(host: ProxmoxHost, workspace: str) -> list[dict[str, Any]]:
    rows = _rows(_api(host, "/cluster/resources", workspace, "--type", "vm"), "guest")
    seen = set()
    for row in rows:
        vmid = row.get("vmid")
        if (type(vmid) is not int or vmid <= 0 or vmid in seen or row.get("type") not in {"qemu", "lxc"}
                or not validate_host(row.get("node")) or not isinstance(row.get("name"), str)):
            raise ProxmoxMigrateError("Invalid or duplicate cluster guest identity")
        seen.add(vmid)
    return rows


def _guest(rows: list[dict[str, Any]], selector: int | str) -> dict[str, Any]:
    if str(selector).isdigit():
        matches = [row for row in rows if row["vmid"] == int(selector)]
    else:
        matches = [row for row in rows if row["name"] == selector]
    if len(matches) != 1:
        raise ProxmoxMigrateError(f"Guest {selector!r} is missing or ambiguous; use a unique VMID")
    guest = matches[0]
    if guest.get("lock") or guest.get("hastate") or guest.get("template"):
        raise ProxmoxMigrateError("Locked guests, HA-managed guests, and templates require operator-managed migration")
    return guest


def _no_tasks(source: ProxmoxHost, target: ProxmoxHost, plan: dict[str, Any], workspace: str) -> None:
    for host, node in ((source, plan["source_node"]), (target, plan["target_node"])):
        rows = _rows(_api(host, f"/nodes/{node}/tasks", workspace, "--source", "active",
                         "--vmid", str(plan["vmid"])), "native task")
        if any(type(row.get("endtime")) is not int or row["endtime"] <= 0
               or row.get("status") == "RUNNING" for row in rows):
            raise ProxmoxMigrateError("A native guest task is still active; inspect its log before resuming")


def _config(host: ProxmoxHost, node: str, kind: str, vmid: int, workspace: str) -> dict[str, Any]:
    path = f"/nodes/{node}/{kind}/{vmid}"
    config = _api(host, path + "/config", workspace, "--current", "1")
    pending = _rows(_api(host, path + "/pending", workspace), "pending configuration")
    if not isinstance(config, dict) or not config.get("name" if kind == "qemu" else "hostname"):
        raise ProxmoxMigrateError("Invalid guest configuration")
    if config.get("lock") or any("pending" in row or row.get("delete") for row in pending):
        raise ProxmoxMigrateError("Guest has a lock or pending configuration changes; resolve them before migration")
    return config


def _pools(host: ProxmoxHost, node: str, workspace: str) -> dict[str, dict[str, Any]]:
    rows = _rows(_api(host, f"/nodes/{node}/storage", workspace), "storage")
    pools = {}
    for row in rows:
        name = row.get("storage")
        validate_proxmox_storage_name(name, "Proxmox storage")
        if name in pools or not isinstance(row.get("content"), str):
            raise ProxmoxMigrateError("Invalid or duplicate storage pool")
        pools[name] = row
    return pools


def _disk_plan(source: ProxmoxHost, target: ProxmoxHost, config: dict[str, Any],
               plan: dict[str, Any], workspace: str) -> dict[str, dict[str, Any]]:
    source_pools = _pools(source, plan["source_node"], workspace)
    target_pools = _pools(target, plan["target_node"], workspace)
    volumes = {}
    disks = {}
    required: dict[str, int] = {}
    for device, value in config.items():
        if not _DRIVE.fullmatch(device):
            continue
        if not isinstance(value, str):
            raise ProxmoxMigrateError("Invalid guest disk configuration")
        volume, options = drive_parts(value)
        if volume == "none" and options.get("media") == "cdrom":
            continue
        pool, separator, _ = volume.partition(":")
        if not separator or pool not in source_pools:
            raise ProxmoxMigrateError("Host paths and passthrough disks require operator-managed migration")
        if source_pools[pool].get("active") != 1 or source_pools[pool].get("enabled") != 1:
            raise ProxmoxMigrateError(f"Source pool {pool!r} is unavailable")
        shared = source_pools[pool].get("shared") == 1
        cloudinit = re.search(rf"(?:^|/)vm-{plan['vmid']}-cloudinit(?:\.[a-z0-9]+)?$", volume.partition(":")[2])
        is_iso = options.get("media") == "cdrom" and not cloudinit
        if is_iso and not shared:
            raise ProxmoxMigrateError("Detach local ISO media before migration")
        destination = pool if shared or not plan["target_storage"] else plan["target_storage"]
        target_pool = target_pools.get(destination, {})
        content = "iso" if is_iso else "images" if plan["kind"] == "qemu" else "rootdir"
        if (target_pool.get("active") != 1 or target_pool.get("enabled") != 1
                or content not in target_pool.get("content", "").split(",")):
            raise ProxmoxMigrateError(f"Destination pool {destination!r} is unavailable or does not permit {content}")
        volume_key = (pool, is_iso)
        if volume_key not in volumes:
            filters = ("--content", "iso") if is_iso else ("--vmid", str(plan["vmid"]))
            rows = _rows(_api(source, f"/nodes/{plan['source_node']}/storage/{pool}/content",
                              workspace, *filters), "volume")
            sizes = {}
            for row in rows:
                volid, size = row.get("volid"), row.get("size")
                if (not isinstance(volid, str) or not volid.startswith(pool + ":") or volid in sizes
                        or type(size) is not int or size <= 0):
                    raise ProxmoxMigrateError("Invalid or duplicate storage volume")
                sizes[volid] = size
            volumes[volume_key] = sizes
        size = volumes[volume_key].get(volume)
        if type(size) is not int or size <= 0:
            raise ProxmoxMigrateError(f"Cannot verify the size of guest volume {volume!r}")
        if options.get("size"):
            size = max(size, storage_size_kib(options["size"]) * 1024)
        disks[device] = {"source_volume": volume, "source_pool": pool, "target_pool": destination,
                         "shared": shared, "bytes": size, "options": options, "content": content}
        if not shared:
            required[destination] = required.get(destination, 0) + size
    if not disks:
        raise ProxmoxMigrateError("Guest has no verifiable storage volumes")
    for pool, size in required.items():
        available = target_pools[pool].get("avail")
        if type(available) is not int or available < size:
            raise ProxmoxMigrateError(f"Destination pool {pool!r} lacks verified free space for all guest disks")
    if plan["online"] and plan["was_running"] and any(not disk["shared"] for disk in disks.values()) and not plan["with_local_disks"]:
        raise ProxmoxMigrateError("Live migration of local disks requires --with-local-disks")
    return disks


def _verify_bridges(target: ProxmoxHost, identity: dict[str, Any], workspace: str) -> None:
    rows = _rows(json.loads(_run(target, "ip -j link show type bridge", workspace)), "network bridge")
    bridges = {row.get("ifname") for row in rows}
    for key, value in identity.items():
        if re.fullmatch(r"net\d+", key):
            bridge = re.search(r"(?:^|,)bridge=([^,]+)", str(value))
            if bridge and bridge.group(1) not in bridges:
                raise ProxmoxMigrateError(f"Destination bridge {bridge.group(1)!r} is unavailable")


def _check_conflicts(target: ProxmoxHost, plan: dict[str, Any], workspace: str) -> None:
    address = guest_address(plan["identity"], plan["kind"])
    if not address or not validate_ip_address(address):
        address = None
    for guest in _guests(target, workspace):
        if guest["vmid"] == plan["vmid"] or guest["node"] != plan["target_node"]:
            continue
        if guest["name"].lower() == plan["guest_name"].lower():
            raise ProxmoxMigrateError(f"Destination guest {guest['vmid']} already uses the VM name")
        if address is None:
            continue
        config = _api(target, f"/nodes/{plan['target_node']}/{guest['type']}/{guest['vmid']}/config",
                      workspace, "--current", "1")
        if not isinstance(config, dict):
            raise ProxmoxMigrateError("Cannot inspect destination guest addresses")
        if address and guest_address(config, guest["type"]) == address:
            raise ProxmoxMigrateError(f"Destination guest {guest['vmid']} already uses the configured address {address}")


def _verify_config(config: dict[str, Any], plan: dict[str, Any], *, destination: bool) -> None:
    if {key: value for key, value in config.items() if _IDENTITY.fullmatch(key)} != plan["identity"]:
        raise ProxmoxMigrateError("Guest identity or hardware changed during migration")
    observed = {key for key in config if _DRIVE.fullmatch(key)
                and not str(config[key]).startswith("none,media=cdrom")}
    if observed != plan["disks"].keys():
        raise ProxmoxMigrateError("Guest disk attachments changed during migration")
    for device, disk in plan["disks"].items():
        volume, properties = drive_parts(config[device])
        expected_pool = disk["target_pool"] if destination else disk["source_pool"]
        if (volume.partition(":")[0] != expected_pool or properties != disk["options"]
                or ((not destination or disk["shared"]) and volume != disk["source_volume"])):
            raise ProxmoxMigrateError(f"Guest disk {device} does not match the verified migration plan")


def _location(target: ProxmoxHost, plan: dict[str, Any], workspace: str) -> dict[str, Any]:
    guest = _guest(_guests(target, workspace), plan["vmid"])
    if guest["type"] != plan["kind"] or guest["name"] != plan["guest_name"]:
        raise ProxmoxMigrateError("Cluster guest identity changed during migration")
    return guest


def _verify_destination_volumes(target: ProxmoxHost, config: dict[str, Any],
                                plan: dict[str, Any], workspace: str) -> None:
    pools = _pools(target, plan["target_node"], workspace)
    inventories = {}
    for device, disk in plan["disks"].items():
        pool = disk["target_pool"]
        status = pools.get(pool, {})
        if status.get("active") != 1 or status.get("enabled") != 1:
            raise ProxmoxMigrateError(f"Destination storage {pool!r} became unavailable")
        key = (pool, disk["content"] == "iso")
        if key not in inventories:
            filters = ("--content", "iso") if key[1] else ("--vmid", str(plan["vmid"]))
            rows = _rows(_api(target, f"/nodes/{plan['target_node']}/storage/{pool}/content",
                              workspace, *filters), "destination volume")
            inventory = {}
            for row in rows:
                volume, size = row.get("volid"), row.get("size")
                if not isinstance(volume, str) or volume in inventory or type(size) is not int or size <= 0:
                    raise ProxmoxMigrateError("Invalid destination volume inventory")
                inventory[volume] = size
            inventories[key] = inventory
        volume, _options = drive_parts(config[device])
        minimum = storage_size_kib(disk["options"]["size"]) * 1024 if "size" in disk["options"] else disk["bytes"]
        if inventories[key].get(volume, 0) < minimum:
            raise ProxmoxMigrateError(f"Destination volume for {device} is missing or smaller than the verified disk")


def _verify_source_absent(source: ProxmoxHost, plan: dict[str, Any], workspace: str) -> None:
    directory = "qemu-server" if plan["kind"] == "qemu" else "lxc"
    command = f"test ! -e /etc/pve/local/{directory}/{plan['vmid']}.conf"
    if plan["kind"] == "qemu":
        pidfile = f"/run/qemu-server/{plan['vmid']}.pid"
        command += (
            f" && if test -e {pidfile}; then pid=$(cat {pidfile}); "
            "case \"$pid\" in ''|*[!0-9]*) exit 1;; esac; "
            "test \"$pid\" -gt 1 && ! kill -0 \"$pid\" 2>/dev/null; fi"
        )
    _run(source, command, workspace)


def _status(host: ProxmoxHost, node: str, plan: dict[str, Any], workspace: str) -> str:
    data = _api(host, f"/nodes/{node}/{plan['kind']}/{plan['vmid']}/status/current", workspace)
    if not isinstance(data, dict) or data.get("status") not in {"running", "stopped"}:
        raise ProxmoxMigrateError("Cannot verify guest power state")
    if data.get("qmpstatus") in {"paused", "suspended"}:
        raise ProxmoxMigrateError("Resume or shut down a paused guest before migration")
    return data["status"]


def _migration_command(plan: dict[str, Any]) -> str:
    parts = ["qm" if plan["kind"] == "qemu" else "pct", "migrate", str(plan["vmid"]), plan["target_node"]]
    if plan["online"]:
        parts += ["--online", "1"]
    if plan["with_local_disks"]:
        parts += ["--with-local-disks", "1"]
    if plan["target_storage"]:
        parts += ["--targetstorage" if plan["kind"] == "qemu" else "--target-storage", plan["target_storage"]]
    return shlex.join(parts)


def _execute(source: ProxmoxHost, target: ProxmoxHost, plan: dict[str, Any], workspace: str,
             store: OperationStateStore, operation_id: str, phase: str) -> None:
    guest = _location(target, plan, workspace)
    if guest["node"] == plan["source_node"]:
        if phase not in {"prepared", "shutting_down", "stopped"}:
            raise ProxmoxMigrateError("Previous migration has an uncertain outcome on the source; inspect its task log. Resume will not replay it")
        _verify_config(_config(source, plan["source_node"], plan["kind"], plan["vmid"], workspace), plan, destination=False)
        if phase == "prepared" and (_status(source, plan["source_node"], plan, workspace) == "running") != plan["was_running"]:
            raise ProxmoxMigrateError("Guest power state changed after inspection; inspect it before resuming")
        tool = "qm" if plan["kind"] == "qemu" else "pct"
        if not plan["online"]:
            store.transition(operation_id, "shutting_down")
            if _status(source, plan["source_node"], plan, workspace) == "running":
                print(f"  Gracefully shutting down {plan['guest_name']} ({plan['vmid']})")
                _run(source, f"{tool} shutdown {plan['vmid']} --timeout {plan['shutdown_timeout']}", workspace,
                     timeout=plan["shutdown_timeout"] + 30, mutation=True)
            if _status(source, plan["source_node"], plan, workspace) != "stopped":
                raise ProxmoxMigrateError("Guest did not stop; migration was not attempted")
            store.transition(operation_id, "stopped")
        _verify_config(_config(source, plan["source_node"], plan["kind"], plan["vmid"], workspace), plan, destination=False)
        store.transition(operation_id, "migrating")
        print(f"  Migrating {plan['guest_name']} ({plan['vmid']}) to {target.name}")
        _run(source, _migration_command(plan), workspace, timeout=plan["timeout"], mutation=True)
    elif guest["node"] != plan["target_node"]:
        raise ProxmoxMigrateError("Guest is on an unexpected cluster node")

    store.transition(operation_id, "verifying")
    _no_tasks(source, target, plan, workspace)
    if _location(target, plan, workspace)["node"] != plan["target_node"]:
        raise ProxmoxMigrateError("Migration did not place the guest exclusively on the destination")
    destination_config = _config(target, plan["target_node"], plan["kind"], plan["vmid"], workspace)
    _verify_config(destination_config, plan, destination=True)
    _verify_destination_volumes(target, destination_config, plan, workspace)
    _verify_source_absent(source, plan, workspace)
    _check_conflicts(target, plan, workspace)
    print("  Verified destination identity, network, hardware, and disk configuration")

    store.transition(operation_id, "updating_setups")
    apply_setup_updates(plan["setup_updates"], workspace)
    if plan["was_running"]:
        store.transition(operation_id, "starting")
        if _status(target, plan["target_node"], plan, workspace) == "stopped":
            print(f"  Starting {plan['guest_name']} on {target.name}")
            tool = "qm" if plan["kind"] == "qemu" else "pct"
            _run(target, f"{tool} start {plan['vmid']}", workspace, timeout=plan["timeout"], mutation=True)
        if _status(target, plan["target_node"], plan, workspace) != "running":
            raise ProxmoxMigrateError("Guest did not reach running state on the destination")
    elif _status(target, plan["target_node"], plan, workspace) != "stopped":
        raise ProxmoxMigrateError("Originally stopped guest unexpectedly started on the destination")
    _verify_config(_config(target, plan["target_node"], plan["kind"], plan["vmid"], workspace), plan, destination=True)


def migrate_guest(
    src_host: ProxmoxHost, vmid: int | str, target_host: ProxmoxHost, *,
    online: bool = False, with_local_disks: bool = False, target_storage: str | None = None,
    timeout: int = DEFAULT_MIGRATION_TIMEOUT, shutdown_timeout: int = DEFAULT_SHUTDOWN_TIMEOUT,
    dry_run: bool = False, workspace: str | None = None, resume: bool = False,
) -> None:
    """Move one guest, restore its power state, and update this workspace's setups.

    Read-only dry runs inspect the full plan. A durable marker guards uncertain
    operations; explicit resume can finish a verified transfer without replaying
    a migration with an unknown result. Native Proxmox guest locks remain required.
    """
    timeout = validate_positive_integer(str(timeout), "--timeout")
    shutdown_timeout = validate_positive_integer(str(shutdown_timeout), "--shutdown-timeout")
    if type(vmid) is not int and (not isinstance(vmid, str) or not validate_host(vmid)):
        raise ValueError("Guest must be a positive VMID or an exact Proxmox guest name")
    if type(vmid) is int or str(vmid).isdigit():
        validate_positive_integer(str(vmid), "VMID")
    if target_storage is not None:
        validate_proxmox_storage_name(target_storage, "--target-storage")
        if target_storage == "1":
            raise ValueError("--target-storage '1' is reserved by Proxmox; select a named pool")
    for host in (src_host, target_host):
        if not validate_host(host.address) or not validate_username(host.user):
            raise ValueError("Invalid Proxmox connection identity")
        if host.ssh_key is not None:
            validate_filesystem_path(host.ssh_key)
    if src_host.address.lower().rstrip(".") == target_host.address.lower().rstrip("."):
        raise ProxmoxMigrateError("Migration destination is the source host")
    workspace = normalize_workspace_dir(workspace)
    store = None
    operation_id = None
    try:
        source_cluster, target_cluster = _cluster(src_host, workspace), _cluster(target_host, workspace)
        _verify_cluster(source_cluster, target_cluster)
        guest = _guest(_guests(src_host, workspace), vmid)
        marker = os.path.join(workspace, "migrations", f"{source_cluster['ca'][:24]}-{guest['vmid']}.json")
        store = OperationStateStore(marker)
        record = store.load()
        request = {
            "source": src_host.address, "target": target_host.address,
            "source_user": src_host.user, "source_key": src_host.ssh_key,
            "target_user": target_host.user, "target_key": target_host.ssh_key,
            "source_node": source_cluster["name"], "target_node": target_cluster["name"],
            "cluster_ca": source_cluster["ca"], "vmid": guest["vmid"], "guest_name": guest["name"],
            "kind": guest["type"], "online": online, "with_local_disks": with_local_disks,
            "target_storage": target_storage, "timeout": timeout, "shutdown_timeout": shutdown_timeout,
        }
        if guest["type"] == "lxc" and (online or with_local_disks):
            raise ProxmoxMigrateError("--online and --with-local-disks are supported only for QEMU VMs")
        if record is not None:
            if not resume:
                raise ProxmoxMigrateError(f"Unfinished migration at {record.phase}; inspect {marker} and use --resume")
            plan = record.context
            if (record.operation_type != "proxmox-migration" or record.resource != str(guest["vmid"])
                    or any(plan.get(key) != value for key, value in request.items())):
                raise ProxmoxMigrateError("Resume arguments or cluster/guest identity differ from the recorded migration")
            if (type(plan.get("was_running")) is not bool or not isinstance(plan.get("identity"), dict)
                    or not isinstance(plan.get("disks"), dict) or not plan["disks"]
                    or not isinstance(plan.get("setup_updates"), list)):
                raise ProxmoxMigrateError("Invalid recorded migration plan; preserve it for inspection")
            phase = record.phase
            if phase not in {"prepared", "shutting_down", "stopped", "migrating", "verifying", "updating_setups", "starting"}:
                raise ProxmoxMigrateError("Unknown recorded migration phase; preserve it for inspection")
        else:
            if resume:
                raise ProxmoxMigrateError("No unfinished migration to resume")
            if guest["node"] != source_cluster["name"]:
                raise ProxmoxMigrateError(f"Guest is on {guest['node']}, not the requested source")
            config = _config(src_host, source_cluster["name"], guest["type"], guest["vmid"], workspace)
            if config["name" if guest["type"] == "qemu" else "hostname"] != guest["name"]:
                raise ProxmoxMigrateError("Guest inventory and configuration names disagree")
            if config.get("cicustom") or config.get("hookscript") or any(re.fullmatch(r"hostpci\d+|usb\d+|virtiofs\d+", key) for key in config):
                raise ProxmoxMigrateError("External snippets, hooks, and passthrough devices require operator-managed migration")
            plan = {**request, "identity": {key: value for key, value in config.items() if _IDENTITY.fullmatch(key)}}
            plan["was_running"] = _status(src_host, plan["source_node"], plan, workspace) == "running"
            plan["disks"] = _disk_plan(src_host, target_host, config, plan, workspace)
            plan["setup_updates"] = prepare_setup_updates(
                src_host, target_host, source_node=plan["source_node"], kind=plan["kind"],
                identity=plan["identity"], disks=plan["disks"], workspace=workspace,
            )
            phase = "prepared"
        validate_setup_updates(plan)
        _verify_bridges(target_host, plan["identity"], workspace)
        _check_conflicts(target_host, plan, workspace)
        _no_tasks(src_host, target_host, plan, workspace)
        print(f"  Migration plan: {plan['guest_name']} ({plan['vmid']}) {src_host.name} → {target_host.name}")
        print(f"  Power state after migration: {'running' if plan['was_running'] else 'stopped'}")
        print(f"  Saved setups to update in {workspace}: {len(plan['setup_updates'])}")
        if dry_run:
            print(f"  [DRY-RUN] {'Graceful shutdown → ' if not online and plan['was_running'] else ''}"
                  f"{_migration_command(plan)} → verify configuration → update saved setups"
                  f"{' → start' if plan['was_running'] and not online else ''}")
            return
        with ExitStack() as locks:
            for update in sorted(plan["setup_updates"], key=lambda update: update["host"]):
                locks.enter_context(resource_lock("setup-target", update["host"].lower().rstrip(".")))
            for host in sorted((src_host, target_host), key=lambda host: host.address):
                resources = [f"migration|{plan['vmid']}", f"hostname|{plan['guest_name'].lower()}"]
                address = guest_address(plan["identity"], plan["kind"])
                if address and validate_ip_address(address):
                    resources.append(f"address|{address}")
                locks.enter_context(remote_proxmox_locks(
                    host.address, host.user, _ssh(host, workspace)[1:-1],
                    resources, wait=False, description=f"guest {plan['vmid']} migration",
                ))
            if record is None:
                record = store.begin("proxmox-migration", str(plan["vmid"]), "prepared", context=plan)
            else:
                record = store.transition(record.operation_id, phase, expected_record=record)
            operation_id = record.operation_id
            _no_tasks(src_host, target_host, plan, workspace)
            _check_conflicts(target_host, plan, workspace)
            _execute(src_host, target_host, plan, workspace, store, operation_id, phase)
        store.complete(operation_id)
    except (ProxmoxMigrateError, ProvisionError, ResourceBusyError, CommandTimeoutError, OSError, ValueError,
            KeyError, TypeError, AttributeError, KeyboardInterrupt) as exc:
        if store is not None and operation_id is not None:
            try:
                current = store.load()
                if current is not None:
                    store.transition(operation_id, current.phase, status="recovery_required")
            except (OSError, OperationStateError):
                pass  # Preserve the existing guard when storage itself has failed.
            raise ProxmoxMigrateError(
                f"Migration did not complete: {exc}. Preserve {store.path}; inspect the native "
                "task log and both nodes before using --resume. No automatic rollback or replay was attempted."
            ) from exc
        raise ProxmoxMigrateError(f"Migration inspection failed; no guest mutation was attempted: {exc}") from exc
    finally:
        if store is not None:
            store.close()


__all__ = ["ProxmoxMigrateError", "migrate_guest"]
