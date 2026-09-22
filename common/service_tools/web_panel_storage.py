"""Bounded storage health export and an unprivileged, read-only panel view."""

from __future__ import annotations

import html
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../.."))

from common.web_panel_events import WEB_PANEL_AUDIT_DIR
from lib.atomic_io import read_json_file

SNAPSHOT_PATH = os.path.join(WEB_PANEL_AUDIT_DIR, "storage.json")
MAX_JOBS = 8
MAX_FINDINGS = 20
MAX_BYTES = 512 * 1024
STATUSES = {"unavailable", "never_scanned", "incomplete", "findings", "verified"}


def _text(value: object, limit: int = 1024) -> str:
    if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 for c in value):
        raise ValueError("Invalid storage snapshot text")
    return value


def _timestamp(value: object) -> str:
    text = _text(value, 64)
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError("Storage timestamp must include timezone")
    return text


def collect_storage_snapshot() -> dict:
    """Read configured reports only; never run PAR2 or change storage state."""
    from lib.machine_state import load_setup_config
    from lib.runtime_config import RuntimeConfig
    from lib.validation import validate_scrub_specs
    from sync.service_tools.scrub_findings import ADVICE, Findings
    from sync.service_tools.storage_ops import resolve_scrub_database_path, validate_mounts_for_operation

    result = {"version": 1, "generated_at": datetime.now(timezone.utc).isoformat(),
              "available": False, "omitted_jobs": 0, "jobs": []}
    try:
        saved = load_setup_config()
        if saved is None:
            return result
        config = RuntimeConfig.from_dict(saved)
        validate_scrub_specs(config.scrub_specs)
        result["omitted_jobs"] = max(0, len(config.scrub_specs) - MAX_JOBS)
        for directory, database, _redundancy, interval in config.scrub_specs[:MAX_JOBS]:
            job = {"directory": _text(directory), "interval": _text(interval),
                   "status": "unavailable", "last_scan": "", "open_count": 0,
                   "omitted_findings": 0, "findings": []}
            result["jobs"].append(job)
            try:
                database = resolve_scrub_database_path(directory, database)
                valid, _error = validate_mounts_for_operation([directory, database], config, "storage snapshot")
                if not valid:
                    continue
                report = Findings(directory, database)
                scan = report.data.get("last_full_scan")
                if scan is not None:
                    if not isinstance(scan, dict) or type(scan.get("completed")) is not bool:
                        raise ValueError("Invalid scan record")
                    job["last_scan"] = _timestamp(scan.get("finished_at"))
                for relative, finding in report.data["files"].items():
                    if finding["state"] != "open":
                        continue
                    job["open_count"] += 1
                    if len(job["findings"]) == MAX_FINDINGS:
                        continue
                    try:
                        path = _text(os.path.join(directory, relative))
                        first = _timestamp(finding.get("first_seen"))
                        checked = _timestamp(finding.get("last_checked"))
                    except ValueError:
                        continue
                    category = finding["category"] if finding["category"] in ADVICE else "tool_error"
                    job["findings"].append({"path": path, "category": category,
                                            "first_seen": first, "last_checked": checked})
                job["omitted_findings"] = job["open_count"] - len(job["findings"])
                job["status"] = ("findings" if job["open_count"] else "never_scanned" if scan is None
                                 else "verified" if scan["completed"] else "incomplete")
            except (OSError, ValueError, RuntimeError):
                job["status"] = "unavailable"
        result["available"] = True
    except (OSError, ValueError, RuntimeError):
        pass
    return result


def load_storage_snapshot(path: str = SNAPSHOT_PATH, *, now: datetime | None = None) -> dict:
    """Reject stale or malformed exported reports without touching NAS files."""
    try:
        data = read_json_file(path, max_bytes=MAX_BYTES)
        if (not isinstance(data, dict) or data.get("version") != 1 or data.get("available") is not True
                or not isinstance(data.get("jobs"), list) or len(data["jobs"]) > MAX_JOBS):
            raise ValueError("Unavailable snapshot")
        generated = datetime.fromisoformat(_timestamp(data.get("generated_at")))
        age = ((now or datetime.now(timezone.utc)) - generated).total_seconds()
        if not -60 <= age <= 900:
            raise ValueError("Stale snapshot")
        if type(data.get("omitted_jobs")) is not int or data["omitted_jobs"] < 0:
            raise ValueError("Invalid count")
        for job in data["jobs"]:
            if not isinstance(job, dict) or job.get("status") not in STATUSES:
                raise ValueError("Invalid job")
            _text(job.get("directory"))
            _text(job.get("interval"))
            if job.get("last_scan") != "":
                _timestamp(job.get("last_scan"))
            if (not isinstance(job.get("findings"), list) or len(job["findings"]) > MAX_FINDINGS
                    or any(type(job.get(key)) is not int or job[key] < 0 for key in ("open_count", "omitted_findings"))
                    or job["open_count"] != len(job["findings"]) + job["omitted_findings"]):
                raise ValueError("Invalid findings")
            if job["status"] == "verified" and (not job["last_scan"] or job["open_count"]):
                raise ValueError("Inconsistent health")
            for finding in job["findings"]:
                if not isinstance(finding, dict):
                    raise ValueError("Invalid finding")
                _text(finding.get("path"))
                _text(finding.get("category"), 64)
                _timestamp(finding.get("first_seen"))
                _timestamp(finding.get("last_checked"))
        return data
    except (OSError, ValueError, TypeError, OverflowError):
        return {"available": False, "jobs": []}


def render_storage() -> str:
    data = load_storage_snapshot()
    body = '<section aria-labelledby="storage-heading"><h2 id="storage-heading">Storage integrity</h2>'
    if not data["available"]:
        return body + '<p class="empty">Storage snapshot unavailable or stale. Inspect with basaltw scrub status HOST.</p></section>'
    body += f'<p>Snapshot: {html.escape(data["generated_at"])}. Exported every five minutes; loading this page never starts a scrub.</p>'
    labels = {"unavailable": "Report unavailable", "never_scanned": "No full scan recorded",
              "incomplete": "Last full scan incomplete", "findings": "Unresolved findings",
              "verified": "Last recorded full scan completed without open findings"}
    if not data["jobs"]:
        body += '<p>No scrub jobs configured in this snapshot.</p>'
    for job in data["jobs"]:
        body += f'<article class="event"><h3>{html.escape(job["directory"])}</h3><p>{labels[job["status"]]}</p>'
        body += f'<p>Full scan interval: {html.escape(job["interval"])} · Last full scan: {html.escape(job["last_scan"] or "Not recorded")} · Open findings: {job["open_count"]}</p>'
        for finding in job["findings"]:
            body += (f'<details><summary>{html.escape(finding["category"])}: {html.escape(finding["path"])}</summary>'
                     f'<p>First detected: {html.escape(finding["first_seen"])} · Last checked: {html.escape(finding["last_checked"])}</p>'
                     '<p>Use basaltw scrub inspect HOST --file PATH for evidence and recovery advice.</p></details>')
        if job["omitted_findings"]:
            body += f'<p>{job["omitted_findings"]} additional findings omitted; use the CLI for the complete report.</p>'
        body += '</article>'
    if data["omitted_jobs"]:
        body += f'<p>{data["omitted_jobs"]} additional jobs omitted.</p>'
    return body + '<p>Job execution status above is separate from file integrity. A recorded scan does not verify later changes.</p></section>'


if __name__ == "__main__":
    if sys.argv[1:] != ["--export"] or os.geteuid() != 0:
        raise SystemExit("Storage export requires root and --export")
    print(json.dumps(collect_storage_snapshot()))
