"""Bounded passive panel snapshots, independent of HTML and mutation routes."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
import hashlib
import json
import math
import threading
import time
from typing import Any


API_PREFIX = "/api/panel/v1/"
MAX_RESPONSE_BYTES = 24 * 1024
MAX_REQUESTS = 8
MAX_COLLECTORS = 2


class StatusBusy(Exception):
    """No cached value is available within the nonblocking collection budget."""


def serve(handler: Any, resource: str, query: str) -> None:
    """Dispatch only authenticated, gated, bounded passive GET resources."""
    # The private listener trusts only the identity supplied by the panel proxy.
    state = handler.state
    if handler.headers.get_all("X-Basaltwater-User") != [state.manifest.get("username")]:
        handler._send(HTTPStatus.FORBIDDEN, "Authenticated panel proxy required\n", "text/plain")
        return
    if not state.status.available(resource):
        handler._send(HTTPStatus.NOT_FOUND, "Status resource unavailable\n", "text/plain")
        return
    if query:
        handler._send(HTTPStatus.BAD_REQUEST, "Invalid status query\n", "text/plain")
        return
    if not state.status.requests.acquire(blocking=False):
        handler._send(HTTPStatus.SERVICE_UNAVAILABLE, "Status busy\n", "text/plain", headers={"Retry-After": "5"})
        return
    try:
        try:
            value = state.status.read(resource)
        except (StatusBusy, OSError, RuntimeError, ValueError):
            handler._send(HTTPStatus.SERVICE_UNAVAILABLE, "Status temporarily unavailable\n", "text/plain", headers={"Retry-After": "5"})
            return
        validator = '"' + value["revision"] + '"'
        if handler.headers.get_all("If-None-Match") == [validator]:
            handler._send(HTTPStatus.NOT_MODIFIED, "", "application/json", headers={"ETag": validator})
        else:
            handler._send(HTTPStatus.OK, json.dumps(value, separators=(",", ":"), allow_nan=False),
                          "application/json", headers={"ETag": validator})
    finally:
        state.status.requests.release()


@dataclass(frozen=True)
class Reading:
    value: Any
    observed_at: float | None
    failed: bool = False


class StatusCache:
    """Single flight per source, with at most two collectors across this panel.

    Followers get the previous reading immediately, or a retry response on a
    cold cache. Failed collections are negatively cached without dating an old
    value as new. No background thread, queue, or per-client cache is created.
    """

    def __init__(self) -> None:
        self._collectors = threading.BoundedSemaphore(MAX_COLLECTORS)
        self._lock = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}
        self._readings: dict[str, Reading] = {}
        self._attempts: dict[str, float] = {}

    def peek(self, name: str) -> Reading | None:
        with self._lock:
            return self._readings.get(name)

    def read(self, name: str, seconds: float, collect: Callable[[], Any]) -> Reading:
        now = time.monotonic()
        with self._lock:
            previous = self._readings.get(name)
            if previous is not None and now - self._attempts[name] < seconds:
                return previous
            lock = self._locks.setdefault(name, threading.Lock())
        if not lock.acquire(blocking=False):
            if previous is not None:
                return previous
            raise StatusBusy
        try:
            with self._lock:
                current = self._readings.get(name)
                if current is not None and now - self._attempts[name] < seconds:
                    return current
            if not self._collectors.acquire(blocking=False):
                if previous is not None:
                    return previous
                raise StatusBusy
            try:
                try:
                    # Date the observation window conservatively, before any
                    # multi-step collection or independently cached probe.
                    observed_at = time.time()
                    reading = Reading(collect(), observed_at)
                except (OSError, ValueError, RuntimeError):
                    reading = Reading(previous.value if previous else None,
                                      previous.observed_at if previous else None, True)
                with self._lock:
                    self._readings[name] = reading
                    self._attempts[name] = time.monotonic()
                return reading
            finally:
                self._collectors.release()
        finally:
            lock.release()


def timestamp(value: object) -> float | None:
    """Accept only finite Unix seconds or timezone-aware ISO observations."""
    try:
        if isinstance(value, str) and len(value) <= 64:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                return None
            value = parsed.timestamp()
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 253402300799:
            return None
        return float(value)
    except (ValueError, OverflowError, OSError):
        return None


def snapshot(resource: str, data: dict[str, Any], observed_at: object, *, interval: int,
             stale_after: int, incomplete: bool = False, unavailable: bool = False,
             warnings: tuple[str, ...] = (), now: float | None = None) -> dict[str, Any]:
    """Make freshness and a stable private validator from allowlisted data.

    Missing/future observations are unknown, never healthy zeroes. Staleness
    takes precedence over partial coverage; warnings retain both conditions.
    A browser request time is deliberately absent from this schema.
    """
    reference = time.time() if now is None else now
    observed = timestamp(observed_at)
    notices = list(warnings)
    if observed is not None and observed > reference:
        observed = None
        notices.append("Source clock is ahead; observation time is unknown.")
    freshness = ("unavailable" if unavailable else "unknown" if observed is None else
                 "stale" if reference - observed > stale_after else "incomplete" if incomplete else "fresh")
    if incomplete:
        notices.append("Collection is incomplete; inspect the full report.")
    value = {
        "schema": 1, "resource": resource,
        "observed_at": datetime.fromtimestamp(observed, timezone.utc).isoformat().replace("+00:00", "Z")
        if observed is not None else None,
        "freshness": freshness, "interval_seconds": interval, "stale_after_seconds": stale_after,
        "warnings": list(dict.fromkeys(notices))[:4], "data": data,
    }
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    if len(encoded) > MAX_RESPONSE_BYTES - 128:
        raise ValueError("Status response exceeds its budget")
    value["revision"] = hashlib.sha256(encoded).hexdigest()
    return value


HOST_LABELS = ("Uptime", "Load average (1m)", "CPU load", "Memory", "Swap", "Root disk", "Root inodes",
               "Kernel", "Maintenance", "CPU pressure", "Memory pressure", "I/O pressure")


def host_data(records: list[dict[str, str]]) -> dict[str, Any]:
    # Mounted paths, device names, and collector diagnostics stay in the HTML.
    return {"metrics": [{"label": label, "value": row.get("value", "Unavailable")[:160],
                         "description": row.get("description", "")[:200],
                         "status": row.get("status", "") if row.get("status") in {"warning", "unavailable"} else ""}
                        for label in HOST_LABELS for row in records
                        if row.get("label") == label and row.get("kind") not in {"filesystem", "filesystem_issue"}][:12]}


def web_data(records: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {key: 0 for key in ("ready", "attention", "unavailable", "unchecked")}
    for row in records:
        counts[row["status"]] += 1
    return {"counts": counts, "total": len(records), "services": [
        {"id": row["id"], "status": row["status"], "text": row["status_text"]} for row in records
    ]}


def agent_summary(manager: Any) -> dict[str, Any]:
    value = manager.snapshot()
    tasks, runs = value["tasks"], value["runs"]
    active = next((run for run in runs if run["status"] == "running"), None)
    latest = next((run for run in reversed(runs) if run["status"] != "running"), None)
    return {"running": sum(run["status"] == "running" for run in runs),
            "queued": sum(bool(task["queued"]) for task in tasks), "saved": len(tasks),
            "paused": sum(bool(task.get("auto_paused")) for task in tasks),
            "latest": latest["status"] if latest else "not-run",
            "started_at": active["started_at"] if active else None,
            "scheduler_error": bool(manager.error)}


class PanelStatus:
    """Project existing owners into fixed, safe, read-only resources."""

    def __init__(self, state: Any) -> None:
        self.state = state
        self.requests = threading.BoundedSemaphore(MAX_REQUESTS)

    def available(self, resource: str) -> bool:
        if resource == "proxmox":
            return self.state.manifest.get("system_type") == "server_proxmox"
        if resource == "agents":
            return self.state.features().prompts
        return resource in {"host", "web", "audit", "storage", "services"}

    def read(self, resource: str) -> dict[str, Any]:
        state = self.state
        if resource == "host":
            records = state.system_overview()
            reading = state.status_cache.peek("host")
            data = host_data(records)
            return snapshot(resource, data, reading.observed_at if reading else None, interval=30, stale_after=90,
                            unavailable=not data["metrics"], incomplete=bool(reading and reading.failed) or any(
                                row["value"] == "Unavailable" for row in data["metrics"]))
        if resource == "web":
            reading = state.web_services()
            observations = [reading.observed_at, *[row["probe_observed_at"] for row in reading.value or []
                                                  if "probe_observed_at" in row]]
            observed_at = min(observations) if all(value is not None for value in observations) else None
            return snapshot(resource, web_data(reading.value or []), observed_at, interval=60, stale_after=180,
                            unavailable=reading.value is None,
                            incomplete=reading.failed or bool(reading.value and any(row.get("limited") for row in reading.value)),
                            warnings=("Service discovery is limited to 64 entries and four local probes.",)
                            if reading.value and any(row.get("limited") for row in reading.value) else ())
        if resource == "services":
            # A passive read must not opt an operator into on-demand inspection.
            if state.status_cache.peek("services") is None:
                raise StatusBusy
            records = state.service_health()
            reading = state.status_cache.peek("services")
            metrics = [{"label": row["label"][:80], "value": row["value"][:40],
                        "description": row["description"][:120]} for row in records[:48]]
            return snapshot(resource, {"metrics": metrics}, reading.observed_at, interval=30, stale_after=90,
                            incomplete=reading.failed or any(row["value"] == "Unavailable" for row in metrics))
        if resource == "proxmox":
            reading = state.status_cache.read("proxmox", 60, state.proxmox_snapshot)
            value = reading.value or {}
            available = value.get("available") is True
            data = {"guests": len(value["guests"]) if available and value.get("inventory") else None,
                    "running": sum(row["status"] == "running" and not row["template"] for row in value["guests"])
                    if available and value.get("inventory") else None}
            return snapshot(resource, data, value.get("generated_at"), interval=60, stale_after=180,
                            unavailable=not available, incomplete=reading.failed or not value.get("inventory_complete")
                            or bool(value.get("issues")))
        if resource == "audit":
            reading = state.status_cache.read("audit", 300, state.audit_snapshot)
            value = reading.value or {}
            applicable = value.get("status") != "not_applicable"
            available = value.get("status") in {"ok", "degraded"}
            data = {"applicable": applicable, "events": len(value.get("events", [])) if available else None}
            return snapshot(resource, data, value.get("generated_at"), interval=300, stale_after=900,
                            unavailable=applicable and not available,
                            incomplete=reading.failed or value.get("status") == "degraded" or bool(value.get("issues")),
                            warnings=("Kernel audit is not applicable on this machine.",) if not applicable else ())
        if resource == "storage":
            # Operation metadata is already in memory. Read it directly so a
            # just-started scan cannot inherit a cached idle polling interval.
            observed_at = time.time()
            value = state.storage_diagnostics.status_summary()
            return snapshot(resource, value, observed_at, interval=5 if value["state"] == "running" else 60,
                            stale_after=180)
        if resource == "agents":
            reading = state.status_cache.read("agents", 5, lambda: agent_summary(state.agent_tasks))
            value = reading.value
            return snapshot(resource, value or {}, reading.observed_at, interval=5 if value and value["running"] else 30,
                            stale_after=90, unavailable=value is None, incomplete=reading.failed)
        raise ValueError("Unknown status resource")
