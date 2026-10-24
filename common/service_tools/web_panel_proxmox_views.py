"""Pure, bounded views over the cached Proxmox monitoring snapshot."""

from __future__ import annotations

import base64
import hashlib
import html
import ipaddress
import re
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from lib.validation import validate_no_control_characters
from lib.validators import validate_host


SCRIPT = r"""(() => {
  document.querySelectorAll('[data-pve-copy]').forEach(button => {
    button.hidden = false;
    button.addEventListener('click', async () => {
      const field = button.parentElement.querySelector('[data-pve-address]');
      const status = button.parentElement.querySelector('[data-pve-copy-status]');
      try {
        await navigator.clipboard.writeText(field.value);
        status.textContent = 'Address copied.';
      } catch {
        field.focus();
        field.select();
        status.textContent = 'Address selected. Use your device’s Copy command.';
      }
    });
  });
})();""".strip()
SCRIPT_CSP = "'sha256-" + base64.b64encode(hashlib.sha256(SCRIPT.encode()).digest()).decode() + "'"


@dataclass(frozen=True)
class ProxmoxQuery:
    q: str = ""
    node: str = "all"
    type: str = "all"
    state: str = "all"
    tag: str = ""
    sort: str = "name"


def parse_proxmox_query(raw: str) -> ProxmoxQuery:
    """Reject ambiguous fields; search is casefolded, tags match exact tokens."""
    if len(raw) > 2048 or re.search(r"%(?![0-9A-Fa-f]{2})", raw):
        raise ValueError("Invalid Proxmox query")
    validate_no_control_characters(raw, "Proxmox query")
    values = urllib.parse.parse_qs(raw, keep_blank_values=True, strict_parsing=True,
                                  max_num_fields=6, encoding="utf-8", errors="strict")
    allowed = {"pve_q", "pve_node", "pve_type", "pve_state", "pve_tag", "pve_sort"}
    if set(values) - allowed or any(len(entries) != 1 for entries in values.values()):
        raise ValueError("Invalid Proxmox fields")
    for entries in values.values():
        validate_no_control_characters(entries[0], "Proxmox filter")
        if not entries[0].isprintable() and entries[0]:
            raise ValueError("Invalid Proxmox filter")
    query = ProxmoxQuery(**{key.removeprefix("pve_"): entries[0] for key, entries in values.items()})
    if (len(query.q) > 128 or len(query.node) > 253
            or (query.node != "all" and not validate_host(query.node))
            or query.type not in {"all", "qemu", "lxc", "template"}
            or query.state not in {"all", "running", "stopped", "paused", "unknown"}
            or (query.tag and not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.+\-]{0,63}", query.tag))
            or query.sort not in {"name", "vmid", "cpu", "memory"}):
        raise ValueError("Invalid Proxmox filter")
    return query


def known_nodes(snapshot: dict) -> list[str]:
    return sorted({snapshot["node"], *(row["node"] for group in ("nodes", "guests", "storage")
                                     for row in snapshot.get(group, []))})


def filter_guests(guests: list[dict], query: ProxmoxQuery) -> list[dict]:
    rows = [row for row in guests
            if (query.node == "all" or row["node"] == query.node)
            and (not query.q or query.q.casefold() in (row["name"] or "").casefold()
                 or query.q in str(row["vmid"]))
            and (query.type == "all" or (row["template"] is True if query.type == "template"
                                        else row["type"] == query.type and row["template"] is not True))
            and (query.state == "all" or row["status"] == query.state)
            and (not query.tag or query.tag in row.get("tags", []))]
    def key(row: dict) -> tuple:
        tie = ((row["name"] or "").casefold(), row["vmid"], row["node"])
        if query.sort == "vmid":
            return (row["vmid"], *tie)
        if query.sort in {"cpu", "memory"}:
            value = row.get("cpu" if query.sort == "cpu" else "mem")
            # Stopped guests have no current reading, even if a cached metric exists.
            value = value if row["status"] == "running" and row["template"] is not True else None
            return (value is None, -value if value is not None else 0, *tie)
        return tie
    return sorted(rows, key=key)


def render_filters(snapshot: dict, query: ProxmoxQuery) -> str:
    def select(name: str, label: str, options: list[tuple[str, str]]) -> str:
        selected = getattr(query, name)
        if selected and selected not in {key for key, _ in options}:
            options = [*options, (selected, selected)]
        tags = ''.join(f'<option value="{html.escape(key, quote=True)}"{" selected" if key == selected else ""}>{html.escape(value)}</option>'
                       for key, value in options)
        return f'<label>{label}<select name="pve_{name}">{tags}</select></label>'
    nodes = [("all", "All nodes"), *((node, node) for node in known_nodes(snapshot))]
    tags = sorted({tag for row in snapshot["guests"] for tag in row.get("tags", [])})
    return ('<form class="diagnostic-filters" method="get" action="/#proxmox-heading" aria-label="Proxmox filters">'
            f'<label>Search name or VMID<input name="pve_q" maxlength="128" value="{html.escape(query.q, quote=True)}"></label>'
            + select("node", "Node", nodes)
            + select("type", "Guest type", [("all", "All types"), ("qemu", "QEMU VMs"), ("lxc", "LXC containers"), ("template", "Templates")])
            + select("state", "State", [(key, key.capitalize()) for key in ("all", "running", "stopped", "paused", "unknown")])
            + select("tag", "Tag", [("", "All tags"), *((tag, tag) for tag in tags)])
            + select("sort", "Sort guests", [("name", "Name · A–Z"), ("vmid", "VMID · ascending"), ("cpu", "CPU use · highest first"), ("memory", "Memory use · highest first")])
            + '<div><button type="submit">Apply filters</button> <a class="refresh-link" href="/#proxmox-heading">Reset</a></div></form>'
            '<p class="filesystem-note">Name search ignores case; tags match exact case. Node filters also apply to allocations, pools and tasks. Other filters affect only guests.</p>')


def table(caption: str, headings: tuple[str, ...], rows: list[str]) -> str:
    return ('<table class="filesystem-table proxmox-table" role="table"><caption>' + html.escape(caption)
            + '</caption><thead><tr>' + ''.join(f'<th scope="col">{html.escape(label)}</th>' for label in headings)
            + '</tr></thead><tbody>' + ''.join(rows) + '</tbody></table>')


def cell(label: str, content: str) -> str:
    return f'<td data-label="{html.escape(label, quote=True)}">{content}</td>'


def render_allocations(snapshot: dict, query: ProxmoxQuery, link: Callable, badge: Callable, size: Callable) -> str:
    nodes = {row["node"]: row for row in snapshot.get("nodes", [])}
    rows = []
    for name in known_nodes(snapshot):
        if query.node != "all" and query.node != name:
            continue
        node = nodes.get(name, {})
        guests = [row for row in snapshot["guests"] if row["node"] == name and row["template"] is not True]
        partial = (not snapshot.get("inventory", False) or not snapshot.get("inventory_complete", False)
                   or any(row["template"] is None for row in guests))
        def allocation(field: str, running: bool) -> str:
            if not snapshot.get("inventory", False):
                return "Unavailable"
            selected = [row for row in guests if not running or row["status"] == "running"]
            values = [row.get(field) for row in selected]
            known = [value for value in values if value is not None and value > 0]
            unknown_running = running and any(row["status"] == "unknown" for row in guests)
            incomplete = partial or unknown_running or len(known) != len(values)
            if (values or unknown_running) and not known:
                return "Unavailable"
            value = sum(known)
            total = node.get("maxcpu" if field == "maxcpu" else "maxmem")
            text = f"{value:g} vCPUs" if field == "maxcpu" else size(value)
            if incomplete:
                return f"{text} known · partial allocation"
            return text + (f" · {value / total:.2f}× host" if total else " · host capacity unavailable")
        cpu = f'{node["cpu"] * 100:.1f}% used' if node.get("cpu") is not None else "Unavailable"
        memory = f'{size(node["mem"])} / {size(node["maxmem"])}' if node.get("mem") is not None and node.get("maxmem") else "Unavailable"
        capacity = (f'{node["maxcpu"]:g} logical CPUs' if node.get("maxcpu") else "CPU capacity unavailable")
        capacity += " · " + (size(node["maxmem"]) + " RAM" if node.get("maxmem") else "RAM capacity unavailable")
        offline = node.get("status") == "offline"
        current = "Offline · cached readings" if offline else f"CPU {cpu}<span class=\"metric-description\">RAM {memory}</span>"
        rows.append('<tr><th scope="row">' + link(f"node/{name}", "node", name)
                    + '<span class="metric-description">' + html.escape(capacity) + '</span></th>'
                    + cell("State", badge(node.get("status", "unknown")))
                    + cell("Current host use", current)
                    + cell("Assigned vCPUs", allocation("maxcpu", False) + '<span class="metric-description">Running: ' + allocation("maxcpu", True) + '</span>')
                    + cell("Configured guest RAM", allocation("maxmem", False) + '<span class="metric-description">Running: ' + allocation("maxmem", True) + '</span>') + '</tr>')
    content = table("Node capacity and guest allocations", ("Node / host capacity", "State", "Current host use", "Assigned vCPUs · all guests", "Configured guest RAM · all guests"), rows)
    return content + '<p class="filesystem-note">Allocations use all non-template guests on each selected node, regardless of guest filters. Running allocations include only guests reported running; unknown guest states make that subtotal partial. Unknown or unlimited allocations remain unavailable or partial. Offline host capacities are cached. Ratios describe configured limits, not free capacity: RAM ballooning and LXC ceilings can differ from actual use.</p>'


def render_guest_details(guest: dict, snapshot: dict) -> str:
    if guest["node"] != snapshot["node"]:
        return '<span class="metric-description">Remote node · addresses and Notes links not collected here</span>'
    detail = next((row for row in snapshot.get("guest_details", [])
                   if (row["node"], row["type"], row["vmid"]) == (guest["node"], guest["type"], guest["vmid"])), None)
    if detail is None:
        return '<span class="metric-description">Addresses and Notes links unavailable</span>'
    addresses = []
    for address in detail.get("addresses", []):
        try:
            ipaddress.ip_address(address)
        except ValueError:
            continue
        escaped = html.escape(address, quote=True)
        addresses.append(f'<div class="pve-address"><input data-pve-address readonly value="{escaped}" aria-label="Address for guest {guest["vmid"]}">'
                         '<button type="button" data-pve-copy hidden>Copy</button><span class="metric-description" role="status" data-pve-copy-status></span></div>')
    labels = {"available": "No usable addresses reported", "unavailable": "Address reading unavailable",
              "not_running": "Guest is not running", "not_enabled": "Guest agent is not enabled",
              "budget_exhausted": "Address reading deferred"}
    output = ''.join(addresses) or f'<span class="metric-description">{labels.get(detail.get("address_status"), "Address reading unavailable")}</span>'
    links = []
    for item in detail.get("links", []):
        url = item.get("url")
        if not isinstance(url, str) or len(url) > 2048 or any(ord(char) <= 32 or ord(char) == 127 for char in url):
            continue
        try:
            parsed = urllib.parse.urlsplit(url)
            if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None
                    or parsed.password is not None or (parsed.port is not None and not 1 <= parsed.port <= 65535)):
                continue
        except ValueError:
            continue
        links.append(f'<a class="row-action" rel="noreferrer" href="{html.escape(url, quote=True)}">{html.escape(item["label"])} ↗</a>')
    return output + ('<span class="metric-description">Saved in Notes</span>' + ' · '.join(links) if links else '')


def timestamp(value: int | float | None) -> str:
    return datetime.fromtimestamp(value, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC") if value is not None else "Unavailable"


def render_tasks(snapshot: dict, query: ProxmoxQuery, task_link: Callable, badge: Callable) -> str:
    tasks = snapshot.get("tasks", {})
    output = '<h3>Recent task activity</h3><p class="filesystem-note">Latest small cached task list across the cluster; not a complete archive.</p>'
    if not tasks.get("available"):
        if not tasks.get("items"):
            return output + '<p class="empty">Task activity unavailable.</p>'
        output += '<p class="audit-issues" role="status">Task activity is incomplete; available records follow.</p>'
    items = [row for row in tasks.get("items", []) if query.node == "all" or row["node"] == query.node]
    running = sum(row["status"] == "running" for row in items)
    failed = sum(row["status"] == "failed" for row in items)
    warnings = sum(row["status"] == "warning" for row in items)
    output += f'<p class="count">{len(items)} cached tasks · {running} running · {failed} failed · {warnings} warnings</p>'
    if not items:
        return output + '<p class="empty">No recent tasks reported for the selected nodes.</p>'
    rows = []
    for row in items:
        guest = next((guest for guest in snapshot["guests"] if guest["vmid"] == row["vmid"] and guest["node"] == row["node"]), None)
        rows.append('<tr><th scope="row">' + html.escape(row["type"]) + '</th>'
                    + cell("Result", badge(row["status"])) + cell("Node / guest", task_link(row, guest))
                    + cell("Started", timestamp(row["starttime"]))
                    + cell("Ended", "In progress" if row["status"] == "running" else timestamp(row["endtime"])) + '</tr>')
    return output + table("Recent Proxmox tasks", ("Task type", "Result", "Node / guest", "Started", "Ended"), rows)


def render_health(snapshot: dict, query: ProxmoxQuery, badge: Callable) -> str:
    health = snapshot.get("health", {})
    zfs, ceph = health.get("zfs", {}), health.get("ceph", {})
    output = '<h3>Storage health</h3>'
    local = html.escape(snapshot["node"])
    output += f'<h4>ZFS · local node {local}</h4>'
    if query.node not in {"all", snapshot["node"]}:
        output += '<p class="empty">ZFS readings cover the local collector only; the selected remote node is not covered.</p>'
    elif zfs.get("status") != "available" and not zfs.get("pools"):
        label = "ZFS is not configured on this node." if zfs.get("status") == "not_configured" else "ZFS health unavailable."
        output += f'<p class="empty">{label}</p>'
    elif not zfs.get("pools"):
        output += '<p class="empty">No ZFS pool readings reported; health is unknown.</p>'
    else:
        if zfs.get("status") != "available":
            output += '<p class="empty">ZFS health is incomplete; available pool readings follow.</p>'
        rows = []
        for pool in zfs["pools"]:
            errors = ' · '.join(f'{key.upper()}: {pool[key] if pool.get(key) is not None else "Unavailable"}' for key in ("read", "write", "cksum"))
            warning = pool["state"] != "ONLINE" or any(pool.get(key, 0) for key in ("read", "write", "cksum"))
            state = badge("warning") + " " if warning else ""
            state += html.escape(pool["state"])
            if any(pool.get(key) is None for key in ("read", "write", "cksum")):
                state += '<span class="metric-description">Partial error counters</span>'
            rows.append('<tr><th scope="row">' + html.escape(pool["name"]) + '</th>'
                        + cell("State", state) + cell("Scrub / scan", html.escape(pool.get("scan") or "Unavailable"))
                        + cell("Errors", errors) + '</tr>')
        output += table("Local ZFS pools", ("Pool", "State", "Scrub / scan", "Errors"), rows)
    output += '<h4>Ceph · cluster health</h4><p class="filesystem-note">Reported through Ceph configured on the local collector; independent of the node filter.</p>'
    if ceph.get("status") != "available":
        output += '<p class="empty">' + ("Ceph is not configured on this node." if ceph.get("status") == "not_configured" else "Ceph health unavailable.") + '</p>'
    else:
        state = ceph.get("state")
        output += badge({"HEALTH_OK": "success", "HEALTH_WARN": "warning", "HEALTH_ERR": "failed"}.get(state, "unknown"))
        output += ' ' + html.escape(state or "Ceph health unknown")
        output += '<ul>' + ''.join('<li>' + html.escape(check) + '</li>' for check in ceph.get("checks", [])) + '</ul>'
    return output
