#!/usr/bin/env python3
"""Render Proxmox inventory and run its separate, fixed snapshot exporter."""

from __future__ import annotations

import html
import ipaddress
import os
import sys
import urllib.parse
from collections.abc import Callable
from datetime import datetime, timezone

SOURCE_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
if SOURCE_ROOT not in sys.path:
    sys.path.insert(0, SOURCE_ROOT)

from common.service_tools.web_panel_templates import render_heading
from lib.proxmox_panel import export_snapshot
from lib.validators import validate_host


STYLE = """
.proxmox-panel .section-heading { align-items: center; }
.proxmox-panel .section-heading .refresh-link { flex: none; }
.proxmox-panel .overview-grid { margin-bottom: 16px; }
.proxmox-table .row-action { white-space: normal; }
.proxmox-table a { color: var(--accent); text-underline-offset: 4px; }
.proxmox-panel .badge { text-transform: none; }
@media (max-width: 560px) {
  .proxmox-panel .section-heading { align-items: start; }
}
"""


def native_url(manifest: dict) -> str | None:
    """Use the configured native service address, including bracketed IPv6."""
    for service in manifest.get("services", []):
        if not isinstance(service, dict) or service.get("label") != "Proxmox VE":
            continue
        url = service.get("url")
        if not isinstance(url, str) or len(url) > 2048 or any(ord(char) <= 32 for char in url):
            continue
        try:
            parsed = urllib.parse.urlsplit(url)
            if (parsed.scheme == "https" and parsed.hostname and not parsed.username and not parsed.password
                    and parsed.port == 8006 and parsed.path == "/" and not parsed.query and not parsed.fragment):
                return url
        except ValueError:
            continue
    host = manifest.get("host")
    if not isinstance(host, str):
        return None
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if not validate_host(host):
            return None
    else:
        host = f"[{address}]" if address.version == 6 else str(address)
    return f"https://{host}:8006/"


def _resource_url(base: str, resource: str, kind: str) -> str:
    # Proxmox StateProvider v1: view, resource, task tab, then resource tabs.
    tokens = ["0", "=" + urllib.parse.quote(resource, safe=""), "4", "", "", "", "", "", "", ""]
    tokens[{"node": 3, "storage": 4, "qemu": 7, "lxc": 8}[kind]] = "5"
    return base + "#v1:" + ":".join(tokens)


def _badge(status: str) -> str:
    tone = "success" if status in {"running", "available"} else "" if status in {"stopped", "Template"} else "warning"
    return f'<span class="badge {tone}">{html.escape(status.capitalize())}</span>'


def _metric(label: str, value: str, description: str) -> str:
    return (f'<div class="metric"><dt>{html.escape(label)}</dt><dd><span class="metric-value">{html.escape(value)}</span>'
            f'<span class="metric-description">{html.escape(description)}</span></dd></div>')


def _link(base: str | None, resource: str, kind: str, label: str) -> str:
    if base is None:
        return html.escape(label)
    return f'<a class="row-action" href="{html.escape(_resource_url(base, resource, kind), quote=True)}">{html.escape(label)} <span aria-hidden="true">↗</span></a>'


def render_proxmox(snapshot: dict, manifest: dict, format_size: Callable[[int], str], format_uptime: Callable[[float], str]) -> str:
    """Render sanitized local inventory with useful empty and failure states."""
    base = native_url(manifest)
    open_panel = f'<a class="refresh-link" href="{html.escape(base, quote=True)}">Open Proxmox VE ↗</a>' if base else ""
    content = ""
    if snapshot["available"]:
        guests, storage, cluster = snapshot["guests"], snapshot["storage"], snapshot["cluster"]
        stamp = datetime.fromtimestamp(snapshot["generated_at"], timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        content += f'<p class="endpoint">Local node <code>{html.escape(snapshot["node"])}</code> · Snapshot {stamp} · refreshed every minute</p>'
        running = sum(row["status"] == "running" and not row["template"] for row in guests)
        templates = sum(row["template"] is True for row in guests)
        guest_value = f"{running} running" if snapshot["inventory"] else "Unavailable"
        guest_count = len(guests) - templates
        guest_description = (f'{guest_count} guest{"" if guest_count == 1 else "s"} · '
                             f'{templates} template{"" if templates == 1 else "s"}') if snapshot["inventory"] else "Inventory collection failed"
        cluster_value, cluster_description = "Unavailable", "Cluster status could not be collected"
        if cluster is not None:
            cluster_value = ("Quorate" if cluster["quorate"] is True else "No quorum" if cluster["quorate"] is False else "Quorum unknown") if cluster["clustered"] else "Standalone"
            cluster_description = f'{cluster["online"]} of {cluster["nodes"]} node{"" if cluster["nodes"] == 1 else "s"} online'
            if cluster["unknown"]:
                cluster_description += f' · {cluster["unknown"]} unknown'
            if cluster["name"]:
                cluster_description = cluster["name"] + " · " + cluster_description
        content += '<dl class="overview-grid">' + _metric("Guests on this node", guest_value, guest_description)
        content += _metric("Cluster", cluster_value, cluster_description)
        content += _metric("Proxmox VE", snapshot["version"] or "Unavailable", "Installed version") + '</dl>'
        if cluster is not None and (cluster["quorate"] is False or cluster["online"] != cluster["nodes"]):
            content += '<p class="audit-issues" role="status">Cluster needs attention. Review quorum and node status in Proxmox VE.</p>'
        rows = []
        for guest in guests:
            identity, kind = guest["vmid"], guest["type"]
            name = guest["name"] or f"Guest {identity}"
            label = f"{identity} · {name}"
            details = "QEMU VM" if kind == "qemu" else "LXC container"
            if guest["maxdisk"] is not None:
                details += f' · Disk capacity {format_size(guest["maxdisk"])}'
            active = guest["status"] == "running" and not guest["template"]
            cpu = f'{guest["cpu"] * 100:.1f}%' if active and guest["cpu"] is not None else "Unavailable" if active else "—"
            cores = f'{guest["maxcpu"]:g} vCPUs' if guest["maxcpu"] else "vCPU count unavailable"
            memory = format_size(guest["maxmem"]) + " allocated" if guest["maxmem"] else "Unavailable"
            if active and guest["mem"] is not None and guest["maxmem"]:
                memory = f'{format_size(guest["mem"])} / {format_size(guest["maxmem"])}'
            uptime = format_uptime(guest["uptime"]) if active and guest["uptime"] is not None else "Unavailable" if active else "—"
            rows.append(f'<tr><th scope="row">{_link(base, f"{kind}/{identity}", kind, label)}<span class="metric-description">{html.escape(details)}</span></th>'
                        f'<td data-label="State">{_badge("Template" if guest["template"] else guest["status"])}</td>'
                        f'<td data-label="CPU">{cpu}<span class="metric-description">{cores}</span></td>'
                        f'<td data-label="Memory">{memory}</td><td data-label="Uptime">{uptime}</td></tr>')
        if rows:
            content += '<table class="filesystem-table proxmox-table" role="table"><caption>VMs and containers on this node</caption><thead><tr><th scope="col">Guest</th><th scope="col">State</th><th scope="col">CPU</th><th scope="col">Memory</th><th scope="col">Uptime</th></tr></thead><tbody>' + ''.join(rows) + '</tbody></table>'
            content += '<p class="filesystem-note">CPU is a share of the guest’s assigned vCPUs. Memory is Proxmox-reported use / allocation; disk capacity is allocated size, not free space inside the guest.</p>'
        elif snapshot["inventory"]:
            content += '<p class="empty">No VMs or containers are assigned to this node.</p>'
        rows = []
        for pool in storage:
            name = pool["storage"]
            used, total = pool["disk"], pool["maxdisk"]
            capacity = "Unavailable"
            if used is not None and total and used <= total:
                percent = min(100, (used * 100 + total - 1) // total)
                tone = "critical" if percent >= 95 else "warning" if percent >= 80 else ""
                capacity = (f'<span class="metric-value {tone}">{percent}% used</span>'
                            f'<meter min="0" max="100" low="80" high="95" optimum="0" value="{percent}" aria-label="{html.escape(name, quote=True)} storage used">{percent}%</meter>'
                            f'<span class="metric-description">{format_size(total - used)} free of {format_size(total)}</span>')
            details = (pool["plugintype"] or "Type unavailable") + (" · Shared" if pool["shared"] else "")
            rows.append(f'<tr><th scope="row">{_link(base, "storage/" + snapshot["node"] + "/" + name, "storage", name)}<span class="metric-description">{html.escape(details)}</span></th>'
                        f'<td data-label="State">{_badge(pool["status"])}</td><td data-label="Capacity">{capacity}</td></tr>')
        if rows:
            content += '<table class="filesystem-table proxmox-table" role="table"><caption>Proxmox storage on this node</caption><thead><tr><th scope="col">Storage pool</th><th scope="col">State</th><th scope="col">Capacity</th></tr></thead><tbody>' + ''.join(rows) + '</tbody></table>'
            content += '<p class="filesystem-note">Storage pools can share backing capacity; their totals should not be added together.</p>'
        elif snapshot["inventory"]:
            content += '<p class="empty">No Proxmox storage pools were reported for this node.</p>'
    notices = ''.join(f'<li>{html.escape(issue)}</li>' for issue in snapshot["issues"])
    if notices:
        content = f'<aside class="audit-issues" role="status"><strong>Proxmox collection needs attention</strong><ul>{notices}</ul></aside>' + content
    return f'''<section class="proxmox-panel" aria-labelledby="proxmox-heading"><div class="section-heading"><div>
{render_heading("Proxmox overview", "dashboard", heading_id="proxmox-heading")}</div>{open_panel}</div>
{content}<p class="filesystem-note">Open a guest or pool in Proxmox VE for management. Proxmox uses its own login and certificate.</p></section>'''


if __name__ == "__main__":
    if sys.argv[1:] != ["--export"] or os.geteuid() != 0:
        raise SystemExit("Run as root with --export to refresh the fixed Proxmox snapshot")
    export_snapshot()
