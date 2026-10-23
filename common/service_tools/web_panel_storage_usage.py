"""Server-rendered, on-demand storage usage dashboard."""

from __future__ import annotations

import html
from urllib.parse import parse_qs

from common.service_tools.web_panel_templates import render_document
from lib.storage_diagnostics import SCAN_LIMITS


VIEWS = {"areas": "Host areas", "services": "Services", "projects": "Projects"}
STYLE = """
.storage-heading,.storage-actions {display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap}
.storage-actions form {margin:0;display:flex;align-items:end;gap:12px;flex-wrap:wrap;min-width:0}
.storage-actions label {display:grid;gap:6px;font-size:.85rem;min-width:0}
.storage-actions select {max-width:100%;min-height:44px}.storage-actions button {margin:0;min-height:44px}
.storage-actions a {min-height:44px;display:inline-flex;align-items:center;color:var(--accent)}
.storage-status {padding:14px 18px;border:1px solid var(--line);border-left:4px solid var(--accent);border-radius:8px;margin:20px 0}
.storage-status p {margin:4px 0}.storage-status.partial,.storage-status.failed {border-left-color:var(--warning)}
.storage-metrics {display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:1px;background:var(--line);border:1px solid var(--line);border-radius:12px;overflow:hidden;margin:24px 0}
.storage-metric {background:var(--panel);padding:20px;min-width:0;overflow-wrap:anywhere}
.storage-metric small,.storage-metric span {display:block;color:var(--muted)}
.storage-metric strong {display:block;font-size:clamp(1.4rem,2.5vw,2rem);margin:8px 0;font-variant-numeric:tabular-nums}
.storage-capacity {display:grid;grid-template-columns:minmax(240px,.8fr) minmax(0,1.2fr);gap:32px;padding:24px;border:1px solid var(--line);border-radius:12px;background:var(--panel);align-items:center}
.storage-ring {width:200px;height:200px;border-radius:50%;display:grid;place-items:center;margin:8px auto}
.storage-ring-center {width:154px;height:154px;border-radius:50%;background:var(--panel);display:grid;place-content:center;text-align:center}
.storage-ring-center strong {font-size:2rem}.storage-ring-center span {color:var(--muted);font-size:.85rem}
.storage-legend {padding:0;margin:20px 0;list-style:none;display:grid;gap:12px}
.storage-legend li {display:flex;gap:10px;align-items:center}.storage-legend strong {margin-left:auto;font-variant-numeric:tabular-nums}
.storage-swatch {width:10px;height:10px;border-radius:2px;flex:none;background:var(--accent)}
.storage-tabs {display:flex;gap:8px;flex-wrap:wrap;margin:20px 0}
.storage-tabs a {display:inline-flex;align-items:center;min-height:44px;padding:8px 16px;border:1px solid var(--line);border-radius:8px;text-decoration:none;font-weight:650;color:var(--muted)}
.storage-tabs a[aria-current] {background:var(--accent-soft);border-color:var(--accent);color:var(--accent)}
.storage-table-wrap {overflow-x:auto;border:1px solid var(--line);border-radius:12px;background:var(--panel)}
.storage-table {border-collapse:collapse;width:100%;font-size:.9rem}
.storage-table th,.storage-table td {padding:16px;text-align:right;border-bottom:1px solid var(--line);vertical-align:top;font-variant-numeric:tabular-nums}
.storage-table th:first-child,.storage-table td:first-child {text-align:left;width:42%}
.storage-table thead th {font-size:.75rem;text-transform:uppercase;letter-spacing:.06em;color:var(--muted)}
.storage-table tr:last-child td {border-bottom:0}.storage-table small {display:block;color:var(--muted);margin-top:4px;overflow-wrap:anywhere}
.storage-table code {font-size:.8rem;overflow-wrap:anywhere;white-space:normal}
.storage-bar {height:5px;border-radius:3px;background:var(--line);margin-top:12px;overflow:hidden}
.storage-bar span {height:100%;display:block;background:var(--accent);border-radius:3px}
.storage-table .storage-coverage {text-align:left}.storage-coverage details {font-size:.8rem;max-width:240px}
.storage-coverage summary {cursor:pointer;color:var(--warning)}
.storage-footnote {font-size:.85rem;color:var(--muted);line-height:1.6;margin:16px 0}
.storage-empty {padding:48px 24px;text-align:center;border:1px dashed var(--line);border-radius:12px;margin:24px 0;background:var(--panel)}
.storage-empty p {max-width:560px;margin:12px auto;color:var(--muted);line-height:1.6}
.storage-notes {margin:24px 0;padding:20px;border:1px solid var(--line);border-radius:12px}
.storage-notes summary {cursor:pointer;font-weight:650}.storage-notes li {margin:8px 0;overflow-wrap:anywhere}
@media(max-width:1050px) {.storage-metrics {grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:650px) {.storage-capacity {grid-template-columns:1fr;gap:8px;padding:18px}.storage-metric {padding:16px}.storage-table th,.storage-table td {padding:12px}.storage-table {min-width:650px}.storage-heading {align-items:flex-start}.storage-actions {width:100%}.storage-actions form {flex:1}.storage-actions button {width:100%}}
"""


def parse_storage_query(query: str) -> str:
    """Accept only one fixed breakdown selector; never accept scan paths."""
    if len(query) > 64:
        raise ValueError("Invalid storage view")
    values = parse_qs(query, keep_blank_values=True, strict_parsing=True, max_num_fields=1)
    view = values.get("view", ["areas"])[0]
    if set(values) - {"view"} or view not in VIEWS:
        raise ValueError("Invalid storage view")
    return view


def parse_storage_scan(values: dict[str, list[str]]) -> str:
    """Accept a fixed scan mode, never caller-provided paths or budgets."""
    if set(values) - {"csrf", "mode"} or any(len(entries) != 1 for entries in values.values()):
        raise ValueError("Invalid storage request")
    mode = values.get("mode", ["quick"])[0]
    if mode not in SCAN_LIMITS:
        raise ValueError("Invalid storage request")
    return mode


def _escape(value: object) -> str:
    # POSIX filenames can contain bytes decoded with surrogateescape. Keep
    # those names inspectable without breaking the handler's UTF-8 response.
    return html.escape(str(value).encode("utf-8", errors="backslashreplace").decode("utf-8"), quote=True)


def _size(value: int) -> str:
    size = float(max(0, value))
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if size < 1024 or unit == "PiB":
            return f"{size:,.0f} {unit}" if unit == "B" else f"{size:,.1f} {unit}"
        size /= 1024
    raise AssertionError("Unreachable size unit")


def _metric(label: str, value: str, note: str) -> str:
    return f'<div class="storage-metric"><small>{_escape(label)}</small><strong>{_escape(value)}</strong><span>{_escape(note)}</span></div>'


def _capacity(report: dict) -> str:
    capacity = report.get("root_filesystem")
    if not capacity or capacity["total_bytes"] <= 0:
        return '<section class="storage-capacity"><div><h2>Root filesystem</h2><p>Capacity unavailable</p></div><p class="storage-footnote">Directory estimates remain available below. Capacity could not be measured.</p></section>'
    total = capacity["total_bytes"]
    used, available, reserved = (capacity[key] for key in ("used_bytes", "available_bytes", "reserved_bytes"))
    used_pct = min(100, 100 * used / total)
    free_end = min(100, used_pct + 100 * available / total)
    segments = (("Used", used, "var(--accent)"), ("Available", available, "var(--sea)"), ("Reserved", reserved, "var(--line)"))
    legend = "".join(f'<li><span class="storage-swatch" style="background:{color}" aria-hidden="true"></span>{label}<strong>{_size(value)}</strong></li>' for label, value, color in segments)
    return f'''<section class="storage-capacity" aria-labelledby="capacity-heading">
<div class="storage-ring" role="img" aria-label="Root filesystem: {used_pct:.1f}% used" style="background:conic-gradient(var(--accent) 0% {used_pct:.3f}%,var(--sea) {used_pct:.3f}% {free_end:.3f}%,var(--line) {free_end:.3f}% 100%)">
<div class="storage-ring-center"><strong>{used_pct:.0f}%</strong><span>root filesystem used</span></div></div>
<div><p class="eyebrow">Capacity at scan time</p><h2 id="capacity-heading">Root filesystem</h2><ul class="storage-legend">{legend}</ul>
<p class="storage-footnote">{_size(total)} total · Available space excludes filesystem reserves. Directory estimates below explain only the files the diagnostic could inspect.</p></div></section>'''


def _breakdown(report: dict, view: str) -> str:
    rows = sorted(report.get(view, []), key=lambda row: (-row["allocated_bytes"], row["label"]))
    measured = sum(row["allocated_bytes"] for row in rows)
    largest = max((row["allocated_bytes"] for row in rows), default=0)
    tabs = "".join('<a href="/storage?view=' + key + '"' + (' aria-current="page"' if key == view else '') + '>' + label + '</a>' for key, label in VIEWS.items())
    if not rows:
        content = f'<div class="storage-empty"><h3>No {VIEWS[view].lower()} measured</h3><p>The diagnostic found no attributable entries in this view. Check the coverage notes for inaccessible or omitted locations.</p></div>'
    else:
        body = []
        for row in rows:
            missing = not row["complete"] and not row["files"] and not row["allocated_bytes"]
            allocated = "Unavailable" if missing else ("≥ " if not row["complete"] else "") + _size(row["allocated_bytes"])
            logical = "—" if missing else ("≥ " if not row["complete"] else "") + _size(row["apparent_bytes"])
            share = "Usage unknown" if missing else f'{100 * row["allocated_bytes"] / measured:.1f}% of observed allocation' if measured else "No allocation measured"
            width = 100 * row["allocated_bytes"] / largest if largest else 0
            notes = "".join(f'<li>{_escape(issue)}</li>' for issue in row.get("issues", []))
            coverage = ("Not present" if any(issue.startswith("Not present;") for issue in row.get("issues", [])) else "Measured") if row["complete"] else "Partial estimate" if not missing else "Unavailable"
            if notes:
                coverage = f'<details><summary>{coverage}</summary><ul>{notes}</ul></details>'
            body.append(f'''<tr><td><strong>{_escape(row["label"])}</strong><small><code>{_escape(row["path"])}</code></small><div class="storage-bar" aria-hidden="true"><span style="width:{width:.3f}%"></span></div></td>
<td>{allocated}<small>{share}</small></td><td>{logical}</td><td>{"—" if missing else format(row["files"], ",")}</td><td class="storage-coverage">{coverage}</td></tr>''')
        content = '<div class="storage-table-wrap" tabindex="0" role="region" aria-label="Storage estimates, scroll horizontally on small screens"><table class="storage-table"><thead><tr><th scope="col">Location</th><th scope="col">Allocated</th><th scope="col">Logical size</th><th scope="col">Files</th><th scope="col">Coverage</th></tr></thead><tbody>' + "".join(body) + '</tbody></table></div>'
    explanation = "Host areas are disjoint directory groups on the root filesystem." if view == "areas" else "Services and projects are alternate views of files already counted in host areas; do not add these totals together."
    return f'''<section aria-labelledby="breakdown-heading"><div class="section-heading"><h2 id="breakdown-heading">Where space is used</h2><span class="meta">{_size(measured)} attributed in this view</span></div>
<nav class="storage-tabs" aria-label="Storage breakdown">{tabs}</nav><p class="storage-footnote">{explanation} Bars compare the observed allocations. Partial estimates are lower bounds.</p>{content}</section>'''


def render_storage_usage(state, base_style: str, view: str = "areas") -> str:
    """Render cached observations only. A page load never starts collection."""
    snapshot = state.storage_diagnostics.snapshot()
    status = snapshot["state"]
    report = snapshot.get("report")
    running = status == "running"
    title = {"idle": "Ready to inspect", "running": "Diagnostic running", "complete": "Diagnostic complete", "partial": "Partial estimate", "failed": "Diagnostic could not finish"}[status]
    button = "Diagnostic running…" if running else "Run again" if report else "Run diagnostic"
    mode = snapshot.get("scan_mode", "quick")
    options = "".join(f'<option value="{key}"{" selected" if key == mode else ""}>{key.capitalize()} · {limits["duration_seconds"]:g}s / {limits["entry_limit"]:,} entries</option>' for key, limits in SCAN_LIMITS.items())
    actions = f'''<div class="storage-actions"><a href="/storage?view={view}">Refresh status</a><form method="post" action="/actions/storage-scan"><input type="hidden" name="csrf" value="{_escape(state.csrf_token)}"><label>Scan depth<select name="mode"{" disabled" if running else ""}>{options}</select></label><button type="submit"{" disabled" if running else ""}>{button}</button></form></div>'''
    content = f'''<div class="storage-heading"><div><p class="eyebrow">On-demand diagnostic</p><h2>Understand this host’s storage</h2></div>{actions}</div>
<div class="storage-status {status}" role="status"><strong>{title}</strong><p>{_escape(snapshot.get("message", ""))}</p>{'<p>Use Refresh status to check progress. Previous results remain visible below.</p>' if running and report else '<p>Use Refresh status to check progress.</p>' if running else ''}</div>'''
    if report:
        content += f'<p class="storage-footnote">Showing scan recorded at <time>{_escape(report["generated_at"])}</time>.</p>'
        limits = report["limits"]
        if (limits == SCAN_LIMITS["quick"] and
                (report["entries_scanned"] >= limits["entry_limit"]
                 or report["duration_seconds"] >= limits["duration_seconds"])):
            content += '<p class="storage-footnote"><strong>Quick scan limit reached.</strong> Choose Extended and run again for a larger time and entry budget. Permission restrictions and excluded filesystems still apply.</p>'
        capacity = report.get("root_filesystem") or {}
        observed = report["scanned_allocated_bytes"]
        content += '<div class="storage-metrics">' + "".join((
            _metric("Root used", _size(capacity["used_bytes"]) if capacity else "Unavailable", "Filesystem capacity"),
            _metric("Root available", _size(capacity["available_bytes"]) if capacity else "Unavailable", "Usable by this account"),
            _metric("Observed allocation", _size(observed), "Lower bound from inspected entries"),
            _metric("Files inspected", f'{report["files_scanned"]:,}', f'{report["duration_seconds"]:.1f}s · {report["entries_scanned"]:,} entries'),
        )) + '</div>'
        content += _capacity(report) + _breakdown(report, view)
        issues = "".join(f'<li>{_escape(issue)}</li>' for issue in report.get("issues", []))
        content += f'''<details class="storage-notes"{" open" if issues else ""}><summary>Coverage &amp; measurement notes{f" · {len(report.get('issues', []))} notices" if issues else ""}</summary>
<p>Scan recorded at <time>{_escape(report["generated_at"])}</time>. Limits: {limits["duration_seconds"]:g} seconds and {limits["entry_limit"]:,} entries.</p>
<ul>{issues}<li>Allocated size uses filesystem blocks; logical size includes file lengths and directory metadata. Sparse files, shared blocks, filesystem metadata and deleted files still held open can make these estimates differ from capacity usage.</li>
<li>The diagnostic uses the panel account’s access. Permission-denied paths remain partial even with an extended scan. Symlinks and nested mounts are skipped, except verified same-path projections of the root filesystem used by service isolation. Other filesystems, including shared storage, are outside this scan.</li>
<li>Service attribution uses conventional paths. Project estimates cover recognized project directories, not every possible checkout on this host.</li></ul></details>'''
    else:
        content += '''<section class="storage-empty"><h2>A clear view of your disk</h2><p>Run a diagnostic to estimate storage used by host areas, known service data and project directories. You’ll get ranked usage, allocated and logical sizes, and coverage notes.</p><p>The scan reads file metadata using this account’s access. It does not read file contents or remove data. Results stay in memory until the panel restarts.</p></section>'''
    header = f'<header class="dashboard-header"><div><p class="eyebrow">Inspect</p><h1>Storage</h1><p class="lede">Capacity, usage estimates and the places taking up space.</p><p class="meta">{_escape(state.manifest["host"])}</p></div></header>'
    return render_document(title="Storage", style=base_style + STYLE, header=header, content=content,
                           navigation=state.navigation("storage"), footer="Read-only storage diagnostic · Estimates reflect the last requested scan.")
