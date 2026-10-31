"""Deterministic panel records shared by previews and browser fixtures."""

from __future__ import annotations

from datetime import datetime, timezone

from lib.shares_data_inventory import configuration_inventory
from lib.data_actions import finding_revision
from lib.storage_routines import configured_routines, routine_identity
from common.service_tools.web_panel_findings import PAGE_SIZE


def shares_data_snapshot(scenario: str, *, timestamp: str | None = None) -> dict:
    """Model storage configuration and findings without inspecting the host."""
    empty = scenario in {"empty", "minimal", "proxmox"}
    now = timestamp or datetime.now(timezone.utc).isoformat()
    inventory = configuration_inventory({"enable_samba": not empty,
        "samba_shares": [] if empty else [["write", "projects", "/srv/projects", "operator,maintainer"],
                                          ["read", "archive", "/srv/archive", "operator"]],
        "backup_specs": [] if empty else [["/srv/projects", "/srv/backups/projects", "daily"]],
        "sync_specs": [] if empty else [["/srv/archive", "/mnt/replica/archive", "weekly"]]})
    findings = [{"path": "/srv/archive/" + path, "category": category, "first_seen": now, "last_checked": now}
                for path, category in (("photos/holiday.jpg", "repairable"),
                                       ("documents/notes.txt", "changed_unverified"),
                                       ("media/missing.flac", "missing_file"))]
    for finding in findings:
        finding["revision"] = finding_revision({"state": "open", **finding})
    accounts = {"available": False} if scenario == "degraded" or empty else {"available": True, "accounts": [
        {"username": username, "uid": uid, "disabled": disabled, "locked": False,
         "manageable": uid >= 1000, "revision": "a" * 64}
        for username, uid, disabled in (("operator", 1000, False), ("maintainer", 1001, True), ("root", 0, False))]}
    rows = configured_routines({
        "backup_specs": [] if empty else [["/srv/projects", "/srv/backups/projects", "daily"]],
        "sync_specs": [] if empty else [["/srv/archive", "/mnt/replica/archive", "weekly"]],
        "scrub_specs": [] if empty else [["/srv/archive", "/srv/parity/archive", "10%", "weekly"]],
    }, {routine_identity("sync", ["/srv/archive", "/mnt/replica/archive"])})
    history = {routine_identity(row["kind"], row["spec"]): {
        "cadence_at": None if row["paused"] else 1767258000, "retry": None,
        "parity_at": 1767258000 if row["kind"] == "scrub" else None, "parity_retry": None,
    } for row in rows}
    return {"available": True, "version": 1, "generated_at": now, "inventory": inventory, "samba": accounts,
            "routine_management": {"available": False} if scenario == "degraded" else {
                "available": True, "revision": "d" * 64, "routines": rows, "history": {"available": True, "routines": history}},
            "share_management": {"available": False} if empty or scenario == "degraded" else {"available": True, "revision": "c" * 64}, "omitted_jobs": 0,
            "jobs": [] if empty else [{"directory": "/srv/archive", "database": "/srv/parity/archive",
                "redundancy": "10%", "interval": "weekly", "last_scan": now,
                "status": "unavailable" if scenario == "degraded" else "findings",
                "open_count": 0 if scenario == "degraded" else len(findings), "omitted_findings": 0,
                "findings": [] if scenario == "degraded" else findings}]}


def job_rows() -> list[dict[str, str]]:
    """Return fresh records so interactive fixtures cannot alter static exports."""
    rows = []
    for service, label, status, tone, timer, result in (
        ("auto-update-apt.service", "Package updates", "Last run failed", "error", "active", "exit-code"),
        ("cleanup-maintenance.service", "System cleanup", "Last run succeeded", "warning", "inactive", "success"),
        ("security-monitor.service", "Security monitoring", "Last run succeeded", "info", "active", "success"),
    ):
        rows.append({
            "service": service, "label": label, "status": status, "tone": tone,
            "timer": timer, "enabled": "enabled", "result": result,
            "next": "In about 45 minutes" if timer == "active" else "Timer is not active",
            "triggered": "2026-01-01 09:00:00 UTC", "started": "2026-01-01 09:00:01 UTC",
            "finished": "2026-01-01 09:02:14 UTC", "exit": "1" if tone == "error" else "0",
            "persistent": "Yes",
        })
    return rows


def finding_catalog_page(snapshot: dict, query: dict[str, str]) -> dict:
    """Model a completed catalog query without opening host storage."""
    category, search = query.get("category", ""), query.get("q", "").casefold()
    findings = [(job, finding) for job in snapshot["jobs"] for finding in job["findings"]
                if (not category or finding["category"] == category) and search in finding["path"].casefold()]
    total = len(findings)
    page = min(int(query.get("page", "1")), max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE))
    return {"available": True, "findings": findings[(page - 1) * PAGE_SIZE:page * PAGE_SIZE],
            "total": total, "page": page, "tasks": len(snapshot["jobs"]),
            "unavailable_jobs": sum(job["status"] == "unavailable" for job in snapshot["jobs"])}
