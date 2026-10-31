"""Deterministic panel records shared by previews and browser fixtures."""

from __future__ import annotations

from datetime import datetime, timezone

from lib.shares_data_inventory import configuration_inventory


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
    return {"available": True, "version": 1, "generated_at": now, "inventory": inventory, "omitted_jobs": 0,
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
