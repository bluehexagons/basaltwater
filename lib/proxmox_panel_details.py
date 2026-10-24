"""Bounded public details from read-only Proxmox APIs, without persistent state.

Tasks cover the cluster's recent task list, not its full task archive. Retain
running tasks first, then recent failures/warnings, then other recent tasks.
Guest probes and ZFS pools cover this node only. Ceph is cluster health as seen
through this node's Ceph installation. Missing fields never imply healthy data.
"""

from __future__ import annotations

import ipaddress
import math
import re
import subprocess
import time
from collections.abc import Callable
from itertools import islice
from urllib.parse import unquote, urlsplit

from lib.validation import (
    validate_network_ip,
    validate_no_control_characters,
    validate_proxmox_storage_name,
)
from lib.validators import validate_host, validate_ip_address


MAX_TASKS = 50
MAX_GUESTS = 16
MAX_POOLS = 8
MAX_OPERATIONS = 48
MAX_INPUT_ROWS = 10000
MAX_ADDRESSES = 32
MAX_LINKS = 8
MAX_CHECKS = 32
_FAILED = object()
_EXHAUSTED = object()
_ADDRESS_STATUSES = {"available", "unavailable", "not_running", "not_enabled", "budget_exhausted"}
_HEALTH_STATUSES = {"available", "unavailable", "not_configured"}
_TASK_STATUSES = {"running", "success", "warning", "failed", "unknown"}
_CEPH_STATES = {"HEALTH_OK", "HEALTH_WARN", "HEALTH_ERR"}
_ZFS_STATES = {"ONLINE", "DEGRADED", "FAULTED", "OFFLINE", "REMOVED", "UNAVAIL", "SUSPENDED", "UNKNOWN"}
_SCANS = {"none requested", "scrub completed", "scrub in progress", "scrub canceled",
          "resilver completed", "resilver in progress", "resilver canceled"}
_UPID = re.compile(
    r"UPID:([A-Za-z0-9][A-Za-z0-9-]{0,62}):[0-9A-Fa-f]{8}:"
    r"[0-9A-Fa-f]{8,9}:([0-9A-Fa-f]{8}):([A-Za-z0-9_-]{1,64}):"
    r"([A-Za-z0-9_.-]{0,64}):[A-Za-z0-9_.+-]{1,64}@[A-Za-z0-9_.-]{1,64}"
    r"(?:![A-Za-z0-9_.-]{1,64})?:"
)
_MARKDOWN_LINK = re.compile(r"(?<!!)\[([^\[\]\n]{1,128})\]\((https?://[^\s()]{1,2048})\)", re.I)


def _text(value: object, maximum: int) -> str | None:
    return value if isinstance(value, str) and 0 < len(value) <= maximum and value.isprintable() else None


def _integer(value: object, maximum: int = 2**64 - 1) -> int | None:
    return value if type(value) is int and 0 <= value <= maximum else None


def _node(value: object) -> bool:
    return (isinstance(value, str) and validate_host(value)
            and re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", value) is not None)


def _vmid(value: object) -> bool:
    return type(value) is int and 100 <= value <= 999999999


def _rows(value: object, maximum: int = MAX_INPUT_ROWS) -> list[dict]:
    return [row for row in value[:maximum] if isinstance(row, dict)] if isinstance(value, list) else []


def _mapping(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _choice(value: object, choices: set[str], fallback: str | None) -> str | None:
    return value if isinstance(value, str) and value in choices else fallback


def _pool_name(value: object) -> str | None:
    if _text(value, 64) is None:
        return None
    try:
        validate_proxmox_storage_name(value)
    except ValueError:
        return None
    return value


def _locals(guests: object, node: str) -> list[dict]:
    seen, result = set(), []
    for row in _rows(guests):
        kind, vmid = row.get("type"), row.get("vmid")
        if row.get("node") != node or kind not in ("qemu", "lxc") or not _vmid(vmid):
            continue
        if vmid not in seen:
            seen.add(vmid)
            result.append({"node": node, "type": kind, "vmid": vmid,
                           "status": _choice(row.get("status"), {"running", "stopped", "paused"}, "unknown")})
    return sorted(result, key=lambda row: row["vmid"])


def _address(value: object) -> str | None:
    if _text(value, 64) is None or "%" in value:
        return None
    try:
        # LXC's deprecated inet/inet6 fields include prefix lengths.
        address = ipaddress.ip_interface(value).ip if "/" in value else ipaddress.ip_address(value)
        if address.version == 4 and not validate_ip_address(str(address)):
            return None
        normalized = validate_network_ip(str(address))
        if any((address.is_loopback, address.is_link_local, address.is_unspecified, address.is_multicast)):
            return None
        if address.version == 6 and address.ipv4_mapped is not None:
            return _address(str(address.ipv4_mapped))
        return normalized
    except ValueError:
        return None


def _addresses(values: object) -> list[str]:
    result = []
    if isinstance(values, list):
        for value in values[:256]:
            address = _address(value)
            if address is not None and address not in result:
                result.append(address)
                if len(result) == MAX_ADDRESSES:
                    break
    return result


def _url(value: object) -> str | None:
    if _text(value, 2048) is None:
        return None
    try:
        # Reject characters browsers can reinterpret before parsing the authority.
        decoded = unquote(value)
        validate_no_control_characters(decoded, "application URL")
        if any(char.isspace() for char in value) or any(char in decoded for char in "\\<>\"'"):
            return None
        parsed = urlsplit(value)
        if (parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc
                or parsed.username is not None or parsed.password is not None
                or parsed.hostname is None or parsed.port == 0):
            return None
        host = parsed.hostname
        if ":" in host:
            if "%" in host or ipaddress.ip_address(host).version != 6:
                return None
        elif not validate_host(host):
            return None
    except ValueError:
        return None
    return value


def _links(value: object) -> list[dict]:
    result, seen = [], set()
    for row in _rows(value, 128):
        label, url = _text(row.get("label"), 128), _url(row.get("url"))
        if label is not None and url is not None and url not in seen:
            seen.add(url)
            result.append({"label": label, "url": url})
            if len(result) == MAX_LINKS:
                break
    return result


def _notes_links(value: object) -> list[dict]:
    if not isinstance(value, str) or len(value) > 65536:
        return []
    return _links([{"label": match[1], "url": match[2]}
                   for match in islice(_MARKDOWN_LINK.finditer(value), 128)])


def _tasks(value: object, *, saved: bool = False) -> list[dict]:
    result, seen = [], set()
    for row in _rows(value):
        upid = _text(row.get("upid"), 512)
        match = _UPID.fullmatch(upid) if upid is not None else None
        if match is None or not _node(match[1]) or upid in seen:
            continue
        if row.get("node", match[1]) != match[1] or row.get("type", match[3]) != match[3]:
            continue
        seen.add(upid)
        start = _integer(row.get("starttime"), 253402300799)
        end = _integer(row.get("endtime"), 253402300799)
        raw = row.get("status")
        if saved:
            status = _choice(raw, _TASK_STATUSES, "unknown")
        elif raw == "OK":
            status = "success"
        elif isinstance(raw, str) and re.fullmatch(r"WARNINGS: [0-9]{1,10}", raw):
            status = "warning"
        elif raw == "running" or raw is None and "endtime" not in row:
            status = "running"
        elif raw is None or raw == "unexpected status" or not isinstance(raw, str) or not raw:
            status = "unknown"
        else:
            status = "failed"
        identity = match[4]
        vmid = int(identity) if re.fullmatch(r"[0-9]{3,9}", identity) else None
        result.append({"upid": upid, "node": match[1], "type": match[3],
                       "vmid": vmid if _vmid(vmid) else None,
                       "starttime": start, "endtime": end, "status": status})
    priority = {"running": 0, "failed": 1, "warning": 1}
    result.sort(key=lambda row: (priority.get(row["status"], 2),
                                 -(row["endtime"] or row["starttime"] or 0), row["upid"]))
    return result[:MAX_TASKS]


def _pools(value: object) -> list[dict]:
    result, seen = [], set()
    for row in _rows(value, 256):
        name = _pool_name(row.get("name"))
        state = _choice(row.get("state"), _ZFS_STATES, None)
        if name is None or state is None or name in seen:
            continue
        seen.add(name)
        result.append({"name": name, "state": state, "scan": _choice(row.get("scan"), _SCANS, None),
                       **{key: _integer(row.get(key)) for key in ("read", "write", "cksum")}})
        if len(result) == MAX_POOLS:
            break
    return result


def _checks(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(code for code in value[:256]
                             if isinstance(code, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,95}", code)))[:MAX_CHECKS]


def sanitize_details(value: object, node: str, guests: list[dict]) -> dict:
    """Revalidate persisted public details, dropping extra fields and identities.

    Link labels and URLs are plain data, not HTML; the renderer must escape both.
    Invalid node identity raises ValueError before any local paths can be built.
    """
    if not _node(node):
        raise ValueError("Invalid Proxmox local node")
    value = _mapping(value)
    tasks, health = _mapping(value.get("tasks")), _mapping(value.get("health"))
    zfs, ceph = _mapping(health.get("zfs")), _mapping(health.get("ceph"))
    allowed = {(row["type"], row["vmid"]): row["status"] for row in _locals(guests, node)}
    details, seen = [], set()
    for row in _rows(value.get("guest_details"), 256):
        kind, vmid = row.get("type"), row.get("vmid")
        if (row.get("node") != node or kind not in ("qemu", "lxc") or not _vmid(vmid)
                or (kind, vmid) not in allowed or vmid in seen):
            continue
        seen.add(vmid)
        status = _choice(row.get("address_status"), _ADDRESS_STATUSES, "unavailable")
        if allowed[kind, vmid] in ("stopped", "paused"):
            status = "not_running"
        details.append({"node": node, "type": kind, "vmid": vmid,
                        "addresses": _addresses(row.get("addresses")) if status == "available" else [],
                        "address_status": status, "links": _links(row.get("links"))})
        if len(details) == MAX_GUESTS:
            break
    zfs_status = _choice(zfs.get("status"), _HEALTH_STATUSES, "unavailable")
    if zfs.get("node") != node:
        zfs_status = "unavailable"
    ceph_status = _choice(ceph.get("status"), _HEALTH_STATUSES, "unavailable")
    state = _choice(ceph.get("state"), _CEPH_STATES, None)
    if ceph_status == "available" and state is None:
        ceph_status = "unavailable"
    available = tasks.get("available") is True
    return {
        "tasks": {"available": available, "items": _tasks(tasks.get("items"), saved=True) if available else []},
        "guest_details": details,
        "health": {
            "zfs": {"status": zfs_status, "node": node,
                    "pools": _pools(zfs.get("pools")) if zfs_status != "not_configured" and zfs.get("node") == node else []},
            "ceph": {"status": ceph_status, "state": state if ceph_status == "available" else None,
                     "checks": _checks(ceph.get("checks")) if ceph_status == "available" else []},
        },
    }


def _agent_enabled(value: object) -> bool | None:
    if value is None:
        return False
    if type(value) in (int, bool) and value in (0, 1):
        return bool(value)
    if not isinstance(value, str) or len(value) > 512:
        return None
    enabled = None
    seen = set()
    for part in value.split(","):
        if part in {"0", "1"}:
            part = "enabled=" + part
        key, separator, setting = part.partition("=")
        if not separator or key in seen:
            return None
        seen.add(key)
        if key == "enabled":
            if setting not in {"0", "1"}:
                return None
            enabled = setting == "1"
        elif key == "type":
            if setting not in {"virtio", "isa"}:
                return None
        elif key in {"fstrim_cloned_disks", "freeze-fs-on-backup", "guest-fsfreeze", "freeze-fs"}:
            if setting not in {"0", "1"}:
                return None
        else:
            return None
    # Proxmox's property-string default for omitted enabled is false.
    return enabled if enabled is not None else False


def _interface_addresses(value: object, kind: str) -> list[str] | None:
    if kind == "qemu":
        value = _mapping(value).get("result")
    if not isinstance(value, list) or len(value) > 256 or any(not isinstance(row, dict) for row in value):
        return None
    addresses = []
    for row in value:
        for item in _rows(row.get("ip-addresses"), 64):
            addresses.append(item.get("ip-address"))
        if kind == "lxc":
            addresses.extend((row.get("inet"), row.get("inet6")))
    return _addresses(addresses)


def _scan(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 2048:
        return None
    if value == "none requested":
        return value
    if value.startswith("resilvered "):
        return "resilver completed"
    for operation in ("scrub", "resilver"):
        if value.startswith(operation + " "):
            if "in progress" in value:
                return operation + " in progress"
            if "canceled" in value:
                return operation + " canceled"
            if "repaired" in value or "resilvered" in value:
                return operation + " completed"
    return None


def collect_details(
    query: Callable[..., object], node: str, guests: list[dict], storage: list[dict], *, deadline: float,
) -> dict:
    """Probe fixed local read APIs with <=3s per call and one absolute deadline.

    At most 48 operations, eight ZFS pools and 16 local guests per invocation.
    Rotate the first guest every wall-clock minute, including when the deadline
    permits fewer than 16 probes. No previous snapshot is retained here.
    """
    if not _node(node):
        raise ValueError("Invalid Proxmox local node")
    if (type(deadline) not in (float, int) or not -2**64 < deadline < 2**64
            or not math.isfinite(deadline)):
        raise ValueError("Invalid Proxmox details deadline")
    calls = 0

    def read(path: str) -> object:
        nonlocal calls
        remaining = deadline - time.monotonic()
        if calls >= MAX_OPERATIONS or remaining <= 0:
            return _EXHAUSTED
        calls += 1
        try:
            return query(path, timeout=min(3.0, remaining))
        except (OSError, ValueError, subprocess.SubprocessError):
            return _FAILED

    local_storage = [row for row in _rows(storage) if row.get("node") == node]
    zfs_configured = any(row.get("plugintype") == "zfspool" for row in local_storage)
    ceph_configured = any(row.get("plugintype") in ("rbd", "cephfs") for row in local_storage)
    zfs = {"status": "unavailable" if zfs_configured else "not_configured", "node": node, "pools": []}
    ceph = {"status": "unavailable" if ceph_configured else "not_configured", "state": None, "checks": []}
    tasks = read("/cluster/tasks")
    result = {"tasks": {"available": isinstance(tasks, list), "items": _tasks(tasks)},
              "guest_details": [], "health": {"zfs": zfs, "ceph": ceph}}
    if ceph_configured:
        health = _mapping(_mapping(read(f"/nodes/{node}/ceph/status")).get("health"))
        state = _choice(health.get("status"), _CEPH_STATES, None)
        if state is not None:
            ceph.update(status="available", state=state,
                        checks=_checks(list(islice(_mapping(health.get("checks")), 256))))
    if zfs_configured:
        pools = read(f"/nodes/{node}/disks/zfs")
        if isinstance(pools, list):
            valid = len(pools) <= MAX_INPUT_ROWS and all(isinstance(pool, dict) for pool in pools)
            zfs["status"] = "available" if valid else "unavailable"
            seen = set()
            for pool in _rows(pools):
                name = _pool_name(pool.get("name"))
                state = _choice(pool.get("health"), _ZFS_STATES, None)
                if name is None or state is None:
                    zfs["status"] = "unavailable"
                    continue
                if name in seen:
                    continue
                seen.add(name)
                if len(zfs["pools"]) == MAX_POOLS:
                    zfs["status"] = "unavailable"
                    break
                record = {"name": name, "state": state, "scan": None,
                          "read": None, "write": None, "cksum": None}
                detail = _mapping(read(f"/nodes/{node}/disks/zfs/{name}"))
                detail_state = _choice(detail.get("state"), _ZFS_STATES, None)
                if detail.get("name") == name and detail_state is not None:
                    record.update(state=detail_state, scan=_scan(detail.get("scan")))
                    # Counters belong to the top pool vdev, not the outer object;
                    # summing child vdevs would double count nested mirrors/RAIDZ.
                    top = next((row for row in _rows(detail.get("children"), 256) if row.get("name") == name), {})
                    record.update({key: _integer(top.get(key)) for key in ("read", "write", "cksum")})
                else:
                    zfs["status"] = "unavailable"
                zfs["pools"].append(record)
    candidates = _locals(guests, node)
    if candidates:
        offset = int(time.time() // 60) % len(candidates)
        candidates = (candidates[offset:] + candidates[:offset])[:MAX_GUESTS]
    for guest in candidates:
        record = {key: guest[key] for key in ("node", "type", "vmid")}
        record.update(addresses=[], address_status="unavailable", links=[])
        base = f"/nodes/{node}/{guest['type']}/{guest['vmid']}"
        config = read(base + "/config")
        if isinstance(config, dict):
            record["links"] = _notes_links(config.get("description"))
        if guest["status"] in ("stopped", "paused"):
            record["address_status"] = "not_running"
        elif config is _EXHAUSTED:
            record["address_status"] = "budget_exhausted"
        elif guest["status"] != "running":
            pass
        elif guest["type"] == "qemu" and not isinstance(config, dict):
            pass
        elif guest["type"] == "qemu" and _agent_enabled(config.get("agent")) is not True:
            if _agent_enabled(config.get("agent")) is False:
                record["address_status"] = "not_enabled"
        else:
            suffix = "/agent/network-get-interfaces" if guest["type"] == "qemu" else "/interfaces"
            interfaces = read(base + suffix)
            if interfaces is _EXHAUSTED:
                record["address_status"] = "budget_exhausted"
            else:
                addresses = _interface_addresses(interfaces, guest["type"])
                if addresses is not None:
                    record.update(addresses=addresses, address_status="available")
        result["guest_details"].append(record)
    return sanitize_details(result, node, guests)
