"""Join private QA observations by a shared boot clock without inventing offsets."""

from __future__ import annotations

from datetime import datetime
import re
from desktop.qa_artifacts import new_bundle, snapshot, stamp
from lib.atomic_io import read_json_file, write_json_atomic


def timeline(artifacts: list[str], output: str | None = None) -> dict:
    if not 1 <= len(artifacts) <= 16:
        raise ValueError("Timeline requires 1–16 recording manifests")
    folder = new_bundle(output)
    events, sources, boot_id = [], [], None
    for index, path in enumerate(artifacts):
        saved = folder / f"source-{index}.json"
        provenance = snapshot(path, saved)
        data = read_json_file(str(saved), max_bytes=8 * 1024 * 1024)
        if not isinstance(data, dict) or type(data.get("schema_version")) is not int or data["schema_version"] != 1:
            raise ValueError("Unsupported timeline source")
        clock = data.get("clock")
        if (not isinstance(clock, dict) or clock.get("monotonic_scope") != "linux-boot"
                or not isinstance(clock.get("boot_id"), str)
                or not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", clock["boot_id"])):
            raise ValueError("Source has no comparable boot clock; old evidence remains readable but cannot be aligned")
        if boot_id is not None and boot_id != clock["boot_id"]:
            raise ValueError("Cannot align evidence from different Linux boots")
        boot_id = clock["boot_id"]
        kind = data.get("kind", "desktop-actions")
        if kind not in ("desktop-actions", "application-audio", "accessibility-recording"):
            raise ValueError("Expected action, application-audio or accessibility recording evidence")
        rows = data.get("events", [])
        if not isinstance(rows, list) or len(rows) > 5000:
            raise ValueError("Timeline source exceeds event budget")
        for row in rows:
            if not isinstance(row, dict) or type(row.get("monotonic_ns")) is not int or row["monotonic_ns"] <= 0:
                raise ValueError("Every timeline event requires an observed monotonic timestamp")
            if not isinstance(row.get("at"), str) or len(row["at"]) > 64 or datetime.fromisoformat(row["at"]).tzinfo is None:
                raise ValueError("Timeline events require timezone-aware timestamps")
            events.append({**row, "source_index": index, "kind": kind})
        if kind == "application-audio":
            for phase in ("started", "link_ready", "finished"):
                row = data.get(phase)
                if isinstance(row, dict) and type(row.get("monotonic_ns")) is int:
                    events.append({"source_index": index, "kind": kind, "phase": phase, **row})
        sources.append({**provenance, "kind": kind, "qualified": data.get("qualified"),
                        "truncated": data.get("events_truncated", data.get("truncated")),
                        "error": data.get("error"), "errors": data.get("errors"), "status": data.get("status"),
                        "truncation_reasons": data.get("truncation_reasons"), "audio_origin": data.get("audio_origin")})
    if len(events) > 10000:
        raise ValueError("Combined timeline exceeds 10,000 events")
    events.sort(key=lambda row: row["monotonic_ns"])
    origin = events[0]["monotonic_ns"] if events else None
    for row in events:
        row["timeline_seconds"] = (row["monotonic_ns"] - origin) / 1e9
    data = {"schema_version": 1, "kind": "qa-timeline", "created": stamp(),
            "clock": {"boot_id": boot_id, "monotonic_scope": "linux-boot"}, "sources": sources, "events": events,
            "limitations": ["Audio start/link-ready/finish are observation bounds; sample-zero alignment is unknown.",
                            "AT-SPI announcements are application events, not proof of spoken output.",
                            "Truncated or failed sources remain explicitly marked; this join cannot restore missing events."]}
    write_json_atomic(str(folder / "manifest.json"), data, mode=0o600)
    return {"artifact": str(folder / "manifest.json"), "events": len(events), "sources": sources}
