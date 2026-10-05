"""Bounded, read-only filesystem and resource-pressure dashboard snapshots."""

from __future__ import annotations

import html
import json
import math
import os
import subprocess
import tempfile
from collections.abc import Callable

from common.service_tools.web_panel_templates import render_heading
from lib.validation import validate_filesystem_path


# Probe only local storage: remote and arbitrary FUSE mounts can block on I/O.
LOCAL_FILESYSTEM_TYPES = (
    "ext2", "ext3", "ext4", "xfs", "btrfs", "zfs", "vfat", "exfat",
    "f2fs", "ntfs", "ntfs3", "fuseblk", "bcachefs", "jfs", "reiserfs",
    "iso9660", "udf", "overlay",
)
_MAX_FILESYSTEMS = 32
_MAX_SNAPSHOT_BYTES = 256 * 1024

FILESYSTEM_STYLE = """
.filesystem-table { width: 100%; border-collapse: collapse; font-size: .85rem; }
.filesystem-table th, .filesystem-table td { padding: 12px; text-align: left; vertical-align: top;
  border-bottom: 1px solid var(--line); overflow-wrap: anywhere; }
.filesystem-table thead th { color: var(--muted); font-weight: 650; }
.filesystem-table caption { text-align: left; padding-bottom: 10px; }
.filesystem-table tbody th { width: 28%; }
.filesystem-table .metric-description { font-weight: 400; }
.filesystem-table .metric-value { font-size: .95rem; }
.filesystem-table .critical { color: var(--bad); }
.filesystem-table .warning { color: var(--warning); }
.filesystem-note { color: var(--muted); font-size: .8rem; }
@media (max-width: 650px) {
  .filesystem-table, .filesystem-table tbody { display: block; }
  .filesystem-table caption { display: block; width: auto; }
  .filesystem-table thead { position: absolute; width: 1px; height: 1px; overflow: hidden;
    clip-path: inset(50%); }
  .filesystem-table tr { display: block; margin-bottom: 12px; padding: 4px;
    border: 1px solid var(--line); border-radius: 12px; background: var(--panel); }
  .filesystem-table tbody th, .filesystem-table td { display: block; width: auto; border: 0; }
  .filesystem-table td::before { content: attr(data-label); display: block; color: var(--muted);
    font-size: .75rem; margin-bottom: 5px; }
}
"""


def _issue(message: str) -> dict[str, str]:
    return {"kind": "filesystem_issue", "label": "Mounted storage", "value": "Unavailable", "description": message}


def _count(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, str) and value.isascii() and value.isdigit() and len(value) <= 20:
        value = int(value)
    return value if isinstance(value, int) and 0 <= value <= 2**64 - 1 else None


def _filesystem(record: object) -> dict[str, str] | None:
    if not isinstance(record, dict) or record.get("fstype") not in LOCAL_FILESYSTEM_TYPES:
        return None
    target, source = record.get("target"), record.get("source")
    if not isinstance(target, str) or len(target) > 4096 or not os.path.isabs(target):
        return None
    try:
        validate_filesystem_path(target)
    except ValueError:
        return None
    if not isinstance(source, str) or not source or len(source) > 1024 or not source.isprintable():
        return None
    # A container's root is useful; its internal overlay layers are not.
    if record["fstype"] == "overlay" and target != "/":
        return None
    total, used, available = (_count(record.get(key)) for key in ("size", "used", "avail"))
    capacity = (
        total is not None and used is not None and available is not None
        and total > 0 and used + available <= total
    )
    # As in df, reserved blocks are excluded from space available to users.
    percent = min(100, math.ceil(used * 100 / (used + available))) if capacity and used + available else None
    inode_total, inode_used, inode_free = (_count(record.get(key)) for key in ("ino.total", "ino.used", "ino.avail"))
    inode_percent = (
        min(100, math.ceil(inode_used * 100 / inode_total))
        if inode_total and inode_used is not None and inode_free is not None
        and inode_used + inode_free <= inode_total else None
    )
    options = record.get("options")
    return {
        "kind": "filesystem", "label": target, "source": source, "fstype": record["fstype"],
        "value": f"{percent}% used" if percent is not None else "Unavailable",
        "description": "Mounted local filesystem", "percent": str(percent) if percent is not None else "",
        "total": str(total) if capacity else "", "available": str(available) if capacity else "",
        "inode_percent": str(inode_percent) if inode_percent is not None else "",
        "inode_free": str(inode_free) if inode_percent is not None else "",
        "inode_total": str(inode_total) if inode_percent is not None else "",
        "inode_status": "Not reported" if inode_total == 0 else "Unavailable",
        "read_only": ("yes" if "ro" in options.split(",") else "no") if isinstance(options, str) else "unknown",
    }


def collect_filesystems() -> list[dict[str, str]]:
    """Read local filesystem capacity in one timed, unprivileged subprocess."""

    # The panel's ProtectSystem/PrivateTmp namespace contains sandbox-only bind
    # mounts. PID 1 supplies the host mount table without entering its namespace.
    command = [
        "findmnt", "--task", "1", "--json", "--list", "--bytes", "--all", "--types", ",".join(LOCAL_FILESYSTEM_TYPES),
        "--output", "TARGET,SOURCE,FSTYPE,SIZE,USED,AVAIL,INO.TOTAL,INO.USED,INO.AVAIL,FSROOT,OPTIONS",
    ]
    # A temporary file bounds the amount read into memory, even on large hosts.
    try:
        with tempfile.TemporaryFile() as output:
            result = subprocess.run(command, stdout=output, stderr=subprocess.DEVNULL, check=False, timeout=2)
            output.seek(0)
            raw = output.read(_MAX_SNAPSHOT_BYTES + 1)
    except subprocess.TimeoutExpired:
        return [_issue("Local filesystem query timed out; a root-only snapshot is shown above.")]
    except OSError:
        return [_issue("Local filesystem query could not run; check that findmnt is installed.")]
    if result.returncode != 0 or len(raw) > _MAX_SNAPSHOT_BYTES:
        return [_issue("Host filesystem query failed or exceeded its output limit; check access to /proc/1/mountinfo and findmnt availability.")]
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError, RecursionError):
        return [_issue("Local filesystem query returned invalid data.")]
    records = payload.get("filesystems") if isinstance(payload, dict) else None
    if not isinstance(records, list):
        return [_issue("Local filesystem query returned no filesystem list.")]
    filesystems: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    rejected = 0
    for record in records:
        filesystem = _filesystem(record)
        if filesystem is None:
            rejected += 1
            continue
        identity = (filesystem["source"], filesystem["fstype"], str(record.get("fsroot", "/")))
        # Keep the root mount when a bind alias appeared first in mountinfo.
        if identity in seen:
            if filesystem["label"] == "/":
                filesystems = [row for row in filesystems if (row["source"], row["fstype"], row["fsroot"]) != identity]
            else:
                continue
        seen.add(identity)
        filesystem["fsroot"] = identity[2]
        filesystems.append(filesystem)
    filesystems.sort(key=lambda row: (
        -int(max(int(row["percent"] or 0), int(row["inode_percent"] or 0)) >= 95),
        -int(max(int(row["percent"] or 0), int(row["inode_percent"] or 0)) >= 80),
        row["label"] != "/", row["label"],
    ))
    issues = []
    if len(filesystems) > _MAX_FILESYSTEMS:
        issues.append(_issue(f"Showing {_MAX_FILESYSTEMS} of {len(filesystems)} local filesystems; inspect remaining mounts over SSH."))
    if rejected:
        issues.append(_issue(f"Omitted {rejected} unsupported or invalid mount records."))
    if not filesystems and not issues:
        issues.append(_issue("No supported mounted local filesystems were reported."))
    return [*filesystems[:_MAX_FILESYSTEMS], *issues]


def collect_pressure() -> list[dict[str, str]]:
    """Read Linux PSI averages; missing metrics are never reported as zero."""

    records = []
    for resource, label in (("cpu", "CPU pressure"), ("memory", "Memory pressure"), ("io", "I/O pressure")):
        try:
            with open(f"/proc/pressure/{resource}", encoding="utf-8") as file_obj:
                lines = file_obj.read(4096).splitlines()
            some = next(line for line in lines if line.startswith("some "))
            fields = dict(field.split("=", 1) for field in some.split()[1:])
            averages = [float(fields[key]) for key in ("avg10", "avg60", "avg300")]
            if any(not math.isfinite(value) or not 0 <= value <= 100 for value in averages):
                raise ValueError("Invalid pressure average")
        except (OSError, ValueError, KeyError, StopIteration):
            records.append({"kind": "pressure", "label": label, "value": "Unavailable", "description": "Kernel pressure metric is unsupported or unreadable"})
            continue
        records.append({
            "kind": "pressure", "label": label, "value": f"{averages[0]:.2f}% stalled",
            "description": f"10s average · 60s {averages[1]:.2f}% · 5m {averages[2]:.2f}%",
        })
    return records


def _usage(percent: str, label: str) -> str:
    if not percent:
        return '<span class="metric-value unavailable">Unavailable</span>'
    value = int(percent)
    tone = "critical" if value >= 95 else "warning" if value >= 80 else ""
    return (
        f'<span class="metric-value {tone}">{value}% used</span>'
        f'<meter min="0" max="100" low="80" high="95" optimum="0" value="{value}" '
        f'aria-label="{html.escape(label, quote=True)}">{value}%</meter>'
    )


def render_filesystems(records: list[dict[str, str]], format_size: Callable[[int], str]) -> str:
    """Render escaped mount records as a table that stacks on small screens."""

    filesystems = [row for row in records if row.get("kind") == "filesystem"]
    issues = [row for row in records if row.get("kind") == "filesystem_issue"]
    if not filesystems and not issues:
        return ""
    rows = []
    attention = 0
    for record in filesystems:
        target = record["label"]
        if max(int(record["percent"] or 0), int(record["inode_percent"] or 0)) >= 80:
            attention += 1
        read_only = ' · <span class="badge warning">Read-only</span>' if record["read_only"] == "yes" else ""
        if record["read_only"] == "unknown":
            read_only = ' · <span class="badge warning">Access mode unavailable</span>'
        capacity = _usage(record["percent"], f"{target} space used")
        if record["total"]:
            capacity += f'<span class="metric-description">{format_size(int(record["available"]))} available of {format_size(int(record["total"]))}</span>'
        inodes = _usage(record["inode_percent"], f"{target} inodes used") if record["inode_percent"] else html.escape(record["inode_status"])
        if record["inode_percent"]:
            inodes += f'<span class="metric-description">{int(record["inode_free"]):,} free of {int(record["inode_total"]):,}</span>'
        rows.append(
            f'<tr><th scope="row"><code>{html.escape(target)}</code>'
            f'<span class="metric-description">{html.escape(record["source"])} · {html.escape(record["fstype"])}{read_only}</span></th>'
            f'<td data-label="Space">{capacity}</td><td data-label="Inodes">{inodes}</td></tr>'
        )
    summary = f"{len(filesystems)} filesystem" + ("" if len(filesystems) == 1 else "s")
    if attention:
        summary += f" · {attention} at or above 80% use"
    unavailable = sum(not row["percent"] or row["inode_status"] == "Unavailable" and not row["inode_percent"] for row in filesystems)
    if unavailable:
        summary += f" · {unavailable} with unavailable metrics"
    notes = "".join(f'<p class="filesystem-note">{html.escape(issue["description"])}</p>' for issue in issues)
    return f'''<section aria-labelledby="filesystems-heading"><div class="section-heading"><div>
{render_heading("Mounted storage", "storage", heading_id="filesystems-heading")}</div><span class="count">{summary}</span></div>
<table class="filesystem-table" role="table"><caption class="filesystem-note">Local filesystems · available space excludes reserved blocks · shared volumes can report the same capacity</caption>
<thead><tr><th scope="col">Mount / device</th><th scope="col">Space</th><th scope="col">Inodes</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
{notes}<p class="filesystem-note">Network mounts, virtual filesystems, and container layers are not probed. Read-only mounts may be intentional. Usage at 80% needs review; at 95% it is critical.</p></section>'''
