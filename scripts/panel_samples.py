"""Deterministic panel records shared by previews and browser fixtures."""

from __future__ import annotations


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
