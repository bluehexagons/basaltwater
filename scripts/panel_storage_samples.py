"""Synthetic storage observations for local panel development only."""

from __future__ import annotations

from lib.storage_diagnostics import SCAN_LIMITS


def storage_snapshot(*, loaded: bool, degraded: bool = False, mode: str = "quick") -> dict:
    """Return a fresh fixture without inspecting any host paths."""
    if not loaded:
        return {"state": "idle", "started_at": None, "finished_at": None, "scan_mode": mode,
                "message": "Run a diagnostic to estimate storage usage.", "report": None}
    gib = 1024 ** 3

    def row(label: str, path: str, size: float, files: int, *, partial: bool = False) -> dict:
        return {"id": path, "label": label, "path": path, "allocated_bytes": int(size * gib),
                "apparent_bytes": int(size * gib * 1.1), "files": files, "complete": not partial,
                "issues": ["Synthetic permission restriction; this estimate is incomplete."] if partial else []}

    report = {
        "generated_at": "2026-01-01T09:00:00+00:00", "duration_seconds": 3.8,
        "entries_scanned": 62420, "files_scanned": 58304, "scanned_allocated_bytes": int(38.5 * gib),
        "scanned_apparent_bytes": int(42.3 * gib),
        "root_filesystem": {"total_bytes": 80 * gib, "used_bytes": int((76.8 if degraded else 41.6) * gib),
                            "available_bytes": int((1.2 if degraded else 36.4) * gib), "reserved_bytes": 2 * gib},
        "areas": [row("Home directories", "/home", 21.4, 32080), row("System data", "/var", 8.8, 12840, partial=degraded),
                  row("System software", "/usr", 6.2, 10042), row("Optional software", "/opt", 2.1, 3342)],
        "services": [row("T3 Code", "/home/operator/.t3", 4.7, 8042), row("Containers", "/var/lib/docker", 3.2, 6402),
                     row("Browser testing", "/home/operator/.cache/ms-playwright", 2.3, 1402),
                     row("System logs", "/var/log", 1.2, 328, partial=degraded)],
        "projects": [row("infra-tools", "/home/operator/repos/infra-tools", 5.1, 12420),
                     row("sample-game", "/home/operator/repos/sample-game", 3.2, 8640),
                     row("client-portal", "/home/operator/repos/client-portal", 1.4, 3402)],
        "issues": ["Synthetic permission restriction: some system data could not be inspected."] if degraded else [],
        "limits": dict(SCAN_LIMITS[mode]),
    }
    return {"state": "partial" if degraded else "complete", "started_at": "2026-01-01T08:59:56+00:00", "scan_mode": mode,
            "finished_at": report["generated_at"], "message": "Synthetic diagnostic only; no host paths were inspected.", "report": report}
