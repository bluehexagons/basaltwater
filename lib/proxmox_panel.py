"""Bounded Proxmox inventory snapshots shared with the unprivileged panel."""

from __future__ import annotations

import json
import math
import os
import re
import resource
import signal
import socket
import subprocess
import tempfile
import time

from common.web_panel_events import WEB_PANEL_DATA_DIR
from lib.atomic_io import parse_json, read_json_file, write_text_atomic
from lib.validation import validate_proxmox_storage_name
from lib.validators import validate_host


SNAPSHOT_DIR = f"{WEB_PANEL_DATA_DIR}/proxmox"
SNAPSHOT = f"{SNAPSHOT_DIR}/inventory.json"
MAX_BYTES = 2 * 1024 * 1024
MAX_ROWS = 512
MAX_NODES = 128
STALE_SECONDS = 180
COLLECTION_SECONDS = 45


def _text(value: object, maximum: int = 255) -> str | None:
    return value if isinstance(value, str) and 0 < len(value) <= maximum and value.isprintable() else None


def _number(value: object, maximum: int = 2**64 - 1) -> int | float | None:
    if type(value) not in {int, float} or not 0 <= value <= maximum or not math.isfinite(value):
        return None
    return value


def _flag(value: object) -> bool | None:
    return bool(value) if type(value) in {int, bool} and value in (0, 1) else None


def _counter(value: object) -> int | None:
    number = _number(value)
    return int(number) if number is not None and int(number) == number else None


def _rows(value: object) -> list[dict]:
    if not isinstance(value, list) or len(value) > 10000 or any(not isinstance(row, dict) for row in value):
        raise ValueError("Invalid Proxmox inventory")
    return value


def _tags(value: object) -> list[str]:
    if isinstance(value, str) and len(value) <= 4096:
        value = value.split(";")
    if not isinstance(value, list) or len(value) > 256:
        return []
    return sorted({tag for tag in value if isinstance(tag, str)
                   and re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.+-]{0,63}", tag)})[:32]


def sanitize_resources(value: object) -> tuple[list[dict], list[dict], list[str]]:
    """Copy only public inventory fields; never publish config or raw output."""
    guests, storage, issues = [], [], []
    seen = set()
    for row in _rows(value):
        kind = row.get("type")
        if not isinstance(kind, str) or kind not in {"qemu", "lxc", "storage"}:
            continue
        node = row.get("node")
        if not validate_host(node):
            issues.append("Omitted an inventory record with an invalid node.")
            continue
        if kind == "storage":
            identity = _text(row.get("storage"))
            try:
                validate_proxmox_storage_name(identity)
            except ValueError:
                issues.append("Omitted an invalid storage record.")
                continue
        else:
            identity = _number(row.get("vmid"), 999999999)
            if type(identity) is not int or identity < 100:
                issues.append("Omitted an invalid guest record.")
                continue
        key = ("storage", node, identity) if kind == "storage" else ("guest", identity)
        if key in seen:
            issues.append("Omitted a duplicate inventory record.")
            continue
        seen.add(key)
        record = {"type": kind, "node": node}
        if kind == "storage":
            record.update(storage=identity, plugintype=_text(row.get("plugintype"), 64), shared=_flag(row.get("shared")))
            statuses = {"available", "unavailable", "unknown"}
        else:
            record.update(vmid=identity, name=_text(row.get("name")), template=_flag(row.get("template")),
                          tags=_tags(row.get("tags")))
            statuses = {"running", "stopped", "paused", "unknown"}
            record["cpu"] = _number(row.get("cpu"), 1)
            record.update({key: _counter(row.get(key)) for key in ("maxcpu", "mem", "maxmem", "uptime")})
        status = row.get("status")
        record["status"] = status if isinstance(status, str) and status in statuses else "unknown"
        record.update({key: _counter(row.get(key)) for key in ("disk", "maxdisk")})
        (storage if kind == "storage" else guests).append(record)
    guests.sort(key=lambda row: row["vmid"])
    storage.sort(key=lambda row: (row["node"], row["storage"]))
    if len(guests) > MAX_ROWS or len(storage) > MAX_ROWS:
        issues.append(f"Inventory limited to {MAX_ROWS} guests and {MAX_ROWS} storage pools; open Proxmox for the full list.")
    return guests[:MAX_ROWS], storage[:MAX_ROWS], list(dict.fromkeys(issues))


def sanitize_nodes(value: object) -> list[dict]:
    """Keep cluster node capacity separate from guest allocation and storage."""
    nodes = {}
    for row in _rows(value):
        node = row.get("node")
        if row.get("type") != "node" or not validate_host(node) or node in nodes:
            continue
        status = row.get("status")
        nodes[node] = {
            "type": "node", "node": node,
            "status": status if isinstance(status, str) and status in {"online", "offline"} else "unknown",
            "cpu": _number(row.get("cpu"), 1),
            **{key: _counter(row.get(key)) for key in ("maxcpu", "mem", "maxmem", "disk", "maxdisk", "uptime")},
        }
    return [nodes[node] for node in sorted(nodes)[:MAX_NODES]]


def _members(value: object) -> list[dict]:
    nodes = {}
    for row in _rows(value):
        name = row.get("name")
        if row.get("type") != "node" or not validate_host(name) or name in nodes:
            continue
        online = _flag(row.get("online"))
        nodes[name] = {"type": "node", "node": name,
                       "status": "online" if online is True else "offline" if online is False else "unknown"}
    return list(nodes.values())


def sanitize_cluster(value: object) -> dict:
    rows = _rows(value)
    nodes = [row for row in rows if row.get("type") == "node"]
    local = [row.get("name") for row in nodes if _flag(row.get("local")) is True]
    if len(local) != 1 or not validate_host(local[0]):
        raise ValueError("Proxmox did not identify the local node")
    clusters = [row for row in rows if row.get("type") == "cluster"]
    if len(clusters) > 1 or len(_members(rows)) != len(nodes):
        raise ValueError("Invalid Proxmox cluster status")
    cluster = clusters[0] if clusters else {}
    return {
        "node": local[0], "name": _text(cluster.get("name")), "clustered": bool(clusters),
        "quorate": _flag(cluster.get("quorate")), "nodes": len(nodes),
        "online": sum(_flag(row.get("online")) is True for row in nodes),
        "unknown": sum(_flag(row.get("online")) is None for row in nodes),
    }


def _limit_output() -> None:
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_BYTES + 1, MAX_BYTES + 1))


def _allowed_query(path: str, local_node: str | None) -> bool:
    if not isinstance(path, str):
        return False
    if path in {"/cluster/status", "/cluster/resources", "/cluster/tasks", "/version"}:
        return True
    if not validate_host(local_node) or not path.startswith(f"/nodes/{local_node}/"):
        return False
    suffix = path.removeprefix(f"/nodes/{local_node}/")
    if suffix in {"disks/zfs", "ceph/status"}:
        return True
    if suffix.startswith("disks/zfs/"):
        try:
            validate_proxmox_storage_name(suffix.removeprefix("disks/zfs/"))
            return True
        except ValueError:
            return False
    parts = suffix.split("/")
    if len(parts) not in {3, 4} or parts[0] not in {"qemu", "lxc"}:
        return False
    if not re.fullmatch(r"[0-9]{3,9}", parts[1]) or not 100 <= int(parts[1]) <= 999999999:
        return False
    return (parts[2:] == ["config"] or parts[0] == "lxc" and parts[2:] == ["interfaces"]
            or parts[0] == "qemu" and parts[2:] == ["agent", "network-get-interfaces"])


def _query(path: str, *, timeout: float = 8, local_node: str | None = None) -> object:
    """Read fixed cached API endpoints locally, with no SSH or API credentials."""
    if not _allowed_query(path, local_node):
        raise ValueError("Proxmox snapshot query is not allowed")
    if type(timeout) not in {int, float} or not 0 < timeout <= 8 or not math.isfinite(timeout):
        raise ValueError("Invalid Proxmox query deadline")
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen(
            ["/usr/bin/pvesh", "get", path, "--output-format", "json"],
            stdout=output, stderr=subprocess.DEVNULL, start_new_session=True, preexec_fn=_limit_output,
        )
        try:
            returncode = process.wait(timeout=timeout)
        except BaseException:
            # pvesh can spawn local helpers. End the whole probe on a timeout
            # or interruption; systemd also owns its enclosing control group.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=1)
            raise
        output.seek(0)
        raw = output.read(MAX_BYTES + 1)
    if returncode != 0 or len(raw) > MAX_BYTES:
        raise ValueError("Proxmox query failed or exceeded its output limit")
    return parse_json(raw)


def _public_details(value: object, node: str, guests: list[dict], *, inventory_complete: bool) -> dict:
    from lib.proxmox_panel_details import sanitize_details

    details = sanitize_details(value, node, guests)
    if not inventory_complete:
        # An incomplete storage list cannot establish that a backend is absent.
        for backend in details["health"].values():
            if backend["status"] == "not_configured":
                backend["status"] = "unavailable"
    return details


def collect_snapshot() -> dict:
    from lib.proxmox_panel_details import collect_details

    deadline = time.monotonic() + COLLECTION_SECONDS
    issues = []
    cluster = None
    members = []
    node = socket.gethostname().split(".", 1)[0]
    try:
        status = _query("/cluster/status")
        cluster = sanitize_cluster(status)
        members = _members(status)
        node = cluster["node"]
    except (OSError, ValueError, subprocess.SubprocessError):
        issues.append("Cluster status could not be collected.")
    guests, storage, nodes = [], [], []
    inventory = False
    inventory_complete = False
    try:
        if not validate_host(node):
            raise ValueError("Invalid local node name")
        resources = _query("/cluster/resources")
        guests, storage, notices = sanitize_resources(resources)
        nodes = sanitize_nodes(resources)
        node_count = sum(row.get("type") == "node" for row in _rows(resources))
        if node_count > MAX_NODES:
            notices.append(f"Node inventory limited to {MAX_NODES} nodes.")
        elif node_count != len(nodes):
            notices.append("Omitted invalid or duplicate node records.")
        issues.extend(notices)
        inventory = True
        inventory_complete = not notices
    except (OSError, ValueError, subprocess.SubprocessError):
        issues.append("Guest and storage inventory could not be collected.")
    version = None
    try:
        payload = _query("/version")
        version = _text(payload.get("version"), 64) if isinstance(payload, dict) else None
        if version is None:
            raise ValueError("Missing Proxmox version")
    except (OSError, ValueError, subprocess.SubprocessError):
        issues.append("Proxmox version could not be collected.")
    # Membership remains useful when cached resource metrics are unavailable.
    indexed_nodes = {row["node"]: row for row in nodes}
    for member in members:
        if member["node"] in indexed_nodes:
            indexed_nodes[member["node"]]["status"] = member["status"]
        else:
            indexed_nodes[member["node"]] = member
    for record in [*guests, *storage]:
        indexed_nodes.setdefault(record["node"], {"type": "node", "node": record["node"], "status": "unknown"})
    if len(indexed_nodes) > MAX_NODES:
        issues.append(f"Node inventory limited to {MAX_NODES} nodes.")
        inventory_complete = False
    nodes = sanitize_nodes(list(indexed_nodes.values()))

    def query_details(path: str, *, timeout: float) -> object:
        return _query(path, timeout=timeout, local_node=node)

    details = _public_details(collect_details(query_details, node, guests, storage, deadline=deadline),
                             node, guests, inventory_complete=inventory_complete)
    return {
        "schema": 2, "generated_at": time.time(), "node": node, "version": version,
        "scope": "node" if cluster is not None and not cluster["clustered"] else "cluster",
        "inventory": inventory, "inventory_complete": inventory_complete,
        "guests": guests, "storage": storage, "nodes": nodes, "cluster": cluster,
        **details,
        "issues": issues,
    }


def export_snapshot() -> None:
    """Publish only to the fixed root-owned, panel-readable snapshot directory."""
    content = json.dumps(collect_snapshot(), separators=(",", ":"), allow_nan=False) + "\n"
    if len(content.encode("utf-8")) > MAX_BYTES:
        # Keep the last readable snapshot; its age warning exposes the failed
        # refresh instead of replacing it with a file the panel cannot read.
        raise ValueError("Proxmox snapshot exceeded its publication size limit")
    write_text_atomic(SNAPSHOT, content, mode=0o640, gid=os.getegid())


def load_snapshot(path: str = SNAPSHOT) -> dict:
    """Validate saved data again; stale readings retain a visible warning."""
    try:
        value = read_json_file(path, max_bytes=MAX_BYTES)
        if (not isinstance(value, dict) or type(value.get("schema")) is not int
                or value["schema"] != 2 or type(value.get("inventory")) is not bool
                or type(value.get("inventory_complete")) is not bool or value.get("scope") not in {"node", "cluster"}):
            raise ValueError("Invalid snapshot schema")
        stamp = _number(value.get("generated_at"))
        if stamp is None or stamp > time.time() + 60 or stamp > 253402300799 or not validate_host(value.get("node")):
            raise ValueError("Invalid snapshot identity or time")
        guests, storage, issues = sanitize_resources([*_rows(value.get("guests")), *_rows(value.get("storage"))])
        node_rows = _rows(value.get("nodes"))
        nodes = sanitize_nodes(node_rows)
        if len(nodes) != len(node_rows):
            issues.append("Node inventory is incomplete.")
        inventory_complete = value["inventory_complete"] and not issues
        saved_issues = value.get("issues")
        if not isinstance(saved_issues, list) or len(saved_issues) > 20 or any(_text(item, 512) is None for item in saved_issues):
            raise ValueError("Invalid snapshot notices")
        issues.extend(saved_issues)
        cluster = value.get("cluster")
        if cluster is not None:
            if (not isinstance(cluster, dict) or cluster.get("node") != value["node"]
                    or type(cluster.get("clustered")) is not bool
                    or type(cluster.get("nodes")) is not int or not 1 <= cluster["nodes"] <= 10000
                    or any(type(cluster.get(key)) is not int or not 0 <= cluster[key] <= cluster["nodes"] for key in ("online", "unknown"))
                    or cluster["online"] + cluster["unknown"] > cluster["nodes"]
                    or cluster.get("quorate") is not None and type(cluster["quorate"]) is not bool):
                raise ValueError("Invalid cluster snapshot")
            cluster = {**{key: cluster.get(key) for key in ("node", "clustered", "nodes", "online", "unknown", "quorate")},
                       "name": _text(cluster.get("name"))}
        if time.time() - stamp > STALE_SECONDS:
            issues.append("Snapshot is more than three minutes old. Check the Proxmox snapshot timer.")
        return {"available": True, "generated_at": stamp, "node": value["node"], "version": _text(value.get("version"), 64),
                "scope": value["scope"], "inventory": value["inventory"],
                "inventory_complete": inventory_complete,
                "guests": guests, "storage": storage, "nodes": nodes, "cluster": cluster,
                **_public_details(value, value["node"], guests, inventory_complete=inventory_complete),
                "issues": list(dict.fromkeys(issues))}
    except (OSError, ValueError, TypeError):
        return {"available": False, "issues": ["Proxmox snapshot is unavailable. Rerun panel setup or inspect the Proxmox snapshot service."]}
