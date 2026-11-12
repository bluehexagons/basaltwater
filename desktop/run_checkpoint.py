"""Controller-written run clocks and checkpoints; no scheduler or desktop input."""

from __future__ import annotations

from datetime import datetime, timedelta
import os
import json
from pathlib import Path
import stat
from statistics import median

from desktop.qa_artifacts import clock, new_bundle, stamp
from lib.atomic_io import read_json_file, write_json_atomic
from lib.operation_state import OperationStateStore
from lib.validation import validate_filesystem_path, validate_no_control_characters

EVENT_LIMIT = 1000
MAX_BYTES = 4 * 1024 * 1024
EVENTS = ("checkpoint", "suspend", "resume", "context", "policy", "finish")


def _label(value: str, label: str, maximum: int = 128) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= maximum:
        raise ValueError(f"{label} requires 1–{maximum} characters")
    validate_no_control_characters(value, label)
    return value


def _seconds(value: int | None) -> None:
    if value is not None and (type(value) is not int or not 1 <= value <= 30 * 86400):
        raise ValueError("Timing limits require integer seconds from 1 to 2592000")


def _path(artifact: str) -> Path:
    validate_filesystem_path(artifact, must_exist=True)
    path = Path(artifact).absolute()
    if path != path.resolve() or path.name != "run.json":
        raise ValueError("Expected a canonical private run.json path")
    for item in (path, path.parent):
        info = item.lstat()
        if info.st_uid != os.getuid() or info.st_mode & 0o077 or not (
                stat.S_ISREG(info.st_mode) if item == path else stat.S_ISDIR(info.st_mode)):
            raise ValueError("Run state requires private owned regular files/directories")
    if any((parent / ".git").exists() for parent in path.parents):
        raise ValueError("Keep run state outside Git")
    return path


def _next_review(row: dict, due: int | None, interval: int | None) -> tuple[int | None, int | None]:
    if row.get("review_seconds") is not None:
        interval = row["review_seconds"]
        due = row["monotonic_ns"] + interval * 10**9
    elif row["event"] == "checkpoint" and due is not None:
        due = row["monotonic_ns"] + interval * 10**9
    return due, interval


def _load(path: Path) -> dict:
    data = read_json_file(str(path), max_bytes=MAX_BYTES)
    if (not isinstance(data, dict) or type(data.get("schema_version")) is not int or data["schema_version"] not in (1, 2)
            or data.get("kind") != "desktop-run" or not isinstance(data.get("clock"), dict)
            or not isinstance(data.get("events"), list) or not 1 <= len(data["events"]) <= EVENT_LIMIT):
        raise ValueError("Invalid/unsupported run record; preserve it for inspection")
    _label(data.get("run_id"), "Run ID")
    _label(data.get("match_id"), "Match ID")
    previous, suspended, finished = -1, False, False
    review_due, review_interval = None, None
    for index, row in enumerate(data["events"]):
        if (not isinstance(row, dict) or row.get("event") not in ("start", *EVENTS)
                or type(row.get("monotonic_ns")) is not int or row["monotonic_ns"] < max(0, previous)):
            raise ValueError("Invalid run event clocks; preserve the record")
        expected = {
            "previous_gap_seconds": (row["monotonic_ns"] - previous) / 10**9 if index else None,
            "checkpoint_late_seconds": max(0, (row["monotonic_ns"] - review_due) / 10**9)
                if review_due is not None else None,
        }
        for key, value in expected.items():
            # Missing legacy metadata stays unknown; present metadata must agree
            # with the original event clocks and policy, before this event resets it.
            if key not in row:
                continue
            matches = row[key] is None if value is None else type(row[key]) in (int, float) and row[key] == value
            if not matches:
                raise ValueError(f"Inconsistent saved {key}; preserve the record")
        previous = row["monotonic_ns"]
        _label(row.get("at"), "Event UTC")
        try:
            utc = datetime.fromisoformat(row["at"])
        except ValueError as exc:
            raise ValueError("Invalid run event UTC; preserve the record") from exc
        if utc.utcoffset() != timedelta(0):
            raise ValueError("Run event requires an explicit UTC timestamp; preserve the record")
        _label(row.get("phase"), "Phase")
        _seconds(row.get("review_seconds"))
        _seconds(row.get("hard_seconds"))
        for key, label, maximum in (("note", "Note", 1024), ("context", "Context ID", 128),
                                     ("game_clock_observed", "Game clock", 128)):
            if row.get(key) is not None:
                _label(row[key], label, maximum)
        references = row.get("artifacts", [])
        if not isinstance(references, list) or len(references) > 16:
            raise ValueError("Invalid run artifact references; preserve the record")
        for reference in references:
            validate_filesystem_path(reference)
            if not Path(reference).is_absolute():
                raise ValueError("Run artifact references require absolute paths")
        event = row["event"]
        clear = row.get("clear_hard_limit", False)
        if (type(clear) is not bool or clear and (data["schema_version"] != 2
                or event != "policy" or row.get("hard_seconds") is not None)):
            raise ValueError("Invalid hard-limit clearing policy; preserve the record")
        if (finished or (event == "start") != (index == 0)
                or event == "suspend" and (suspended or not row.get("note"))
                or event == "resume" and not suspended
                or event == "context" and not row.get("context")
                or event == "policy" and (not row.get("note") or not row["note"].strip() or
                    row.get("review_seconds") is None and row.get("hard_seconds") is None and not clear)
                or event not in ("start", "policy") and
                    (row.get("review_seconds") is not None or row.get("hard_seconds") is not None)):
            raise ValueError("Invalid run event history; preserve the record")
        if event in ("suspend", "resume"):
            suspended = event == "suspend"
        finished = event == "finish"
        review_due, review_interval = _next_review(row, review_due, review_interval)
    return data


def _save(path: Path, data: dict) -> None:
    if len((json.dumps(data, indent=2) + "\n").encode("utf-8")) > MAX_BYTES:
        raise ValueError("Run reached byte capacity; preserve its original clocks/history")
    write_json_atomic(str(path), data, mode=0o600)


def _interval_summary(rows: list[dict], end: int) -> dict:
    """Attribute elapsed intervals to the phase/state established by their left event."""
    phases: dict[str, dict] = {}
    gaps = []
    suspended = False
    for index, row in enumerate(rows):
        if row["event"] in ("suspend", "resume"):
            suspended = row["event"] == "suspend"
        right = rows[index + 1]["monotonic_ns"] if index + 1 < len(rows) else end
        seconds = (right - row["monotonic_ns"]) / 10**9
        phase = phases.setdefault(row["phase"], {"wall_seconds": 0.0, "owner_wait_seconds": 0.0,
                                               "declared_active_wall_seconds": 0.0})
        phase["wall_seconds"] += seconds
        phase["owner_wait_seconds"] += seconds if suspended else 0
        phase["declared_active_wall_seconds"] += 0 if suspended else seconds
        if index + 1 < len(rows):
            gaps.append({"from_index": index, "to_index": index + 1, "phase": row["phase"],
                         "seconds": seconds, "owner_wait": suspended})
    values = [gap["seconds"] for gap in gaps]
    return {"phase_times": phases,
            "event_gaps": {"count": len(gaps), "median_seconds": median(values) if values else None,
                           "max_seconds": max(values) if values else None,
                           "longest": sorted(gaps, key=lambda gap: gap["seconds"], reverse=True)[:3],
                           "scope": "Completed ledger-event intervals, including recorded owner waits; not action latency or model time."},
            "context_changes": sum(row["event"] == "context" for row in rows)}


def _summary(data: dict, observed: dict) -> dict:
    rows = data["events"]
    comparable = bool(data["clock"].get("boot_id")) and data["clock"] == clock()
    if not comparable:
        return {"run_id": data["run_id"], "match_id": data["match_id"], "timing": "unsupported-clock",
                "detail": "No comparable boot clock; original history preserved, no alignment or clock reset"}
    end = rows[-1]["monotonic_ns"] if rows[-1]["event"] == "finish" else observed["monotonic_ns"]
    if end < rows[-1]["monotonic_ns"]:
        raise ValueError("Run clock moved backwards")
    suspended_at, suspension = None, 0
    review_due, review_interval, hard_seconds = None, None, None
    checkpoint_lateness = []
    for row in rows:
        if row["event"] == "suspend":
            suspended_at = row["monotonic_ns"]
        elif row["event"] in ("resume", "finish") and suspended_at is not None:
            suspension += row["monotonic_ns"] - suspended_at
            suspended_at = None
        if row["event"] == "checkpoint" and review_due is not None:
            checkpoint_lateness.append(max(0, (row["monotonic_ns"] - review_due) / 10**9))
        review_due, review_interval = _next_review(row, review_due, review_interval)
        if row.get("clear_hard_limit"):
            hard_seconds = None
        elif row.get("hard_seconds") is not None:
            hard_seconds = row["hard_seconds"]
    if suspended_at is not None:
        suspension += end - suspended_at
    wall = (end - rows[0]["monotonic_ns"]) / 10**9
    return {"run_id": data["run_id"], "match_id": data["match_id"], "phase": rows[-1]["phase"],
            "started": {key: rows[0][key] for key in ("at", "monotonic_ns")}, "observed": observed,
            "events": len(rows), "remaining_events": EVENT_LIMIT - len(rows), "timing": "comparable-boot",
            "finished": rows[-1]["event"] == "finish", "owner_wait_suspended": suspended_at is not None,
            "wall_seconds": wall, "owner_wait_seconds": suspension / 10**9,
            "declared_active_wall_seconds": wall - suspension / 10**9,
            "since_last_event_seconds": (end - rows[-1]["monotonic_ns"]) / 10**9,
            "checkpoint_due_monotonic_ns": review_due,
            "checkpoint_remaining_seconds": max(0, (review_due - end) / 10**9) if review_due is not None else None,
            "checkpoint_late_seconds": max(0, (end - review_due) / 10**9) if review_due is not None else None,
            "checkpoint_history": {"reviewed": len(checkpoint_lateness),
                                   "late": sum(value > 0 for value in checkpoint_lateness),
                                   "max_late_seconds": max(checkpoint_lateness, default=None),
                                   "scope": "Completed checkpoints with a preceding review deadline; recomputed from saved clocks/policy, not proof of authorship."},
            "hard_limit_reached": wall >= hard_seconds if hard_seconds is not None else None,
            "hard_limit_seconds": hard_seconds, "observed_active_work_seconds": None,
            "model_reasoning_seconds": None, "tokens": None, "cost": None,
            **_interval_summary(rows, end),
            "limit": "Declared active wall includes game/tool/coordination gaps. Controller enforces limits; no scheduler or input authority."}


def start(run_id: str, match_id: str, phase: str, *, output: str | None = None,
          review_seconds: int | None = None, hard_seconds: int | None = None) -> dict:
    for value, label in ((run_id, "Run ID"), (match_id, "Match ID"), (phase, "Phase")):
        _label(value, label)
    _seconds(review_seconds)
    _seconds(hard_seconds)
    domain, observed = clock(), stamp()
    if domain.get("boot_id") is None:
        raise ValueError("No comparable boot clock; run timing is unsupported on this platform")
    path = new_bundle(output) / "run.json"
    data = {"schema_version": 1, "kind": "desktop-run", "run_id": run_id, "match_id": match_id, "clock": domain,
            "events": [{"event": "start", "phase": phase, **observed,
                        "review_seconds": review_seconds, "hard_seconds": hard_seconds}]}
    with OperationStateStore(str(path)).ownership():
        _save(path, data)
    return {"artifact": str(path), **_summary(data, observed)}


def update(artifact: str, event: str = "show", *, phase: str | None = None, note: str | None = None,
           context: str | None = None, review_seconds: int | None = None, hard_seconds: int | None = None,
           artifacts: list[str] | None = None, game_clock: str | None = None, clear_hard_limit: bool = False) -> dict:
    path = _path(artifact)
    if event not in ("show", *EVENTS):
        raise ValueError("Unknown run event")
    for value, label, maximum in ((phase, "Phase", 128), (note, "Note", 1024),
                                   (context, "Context ID", 128), (game_clock, "Game clock", 128)):
        if value is not None:
            _label(value, label, maximum)
    _seconds(review_seconds)
    _seconds(hard_seconds)
    if type(clear_hard_limit) is not bool or clear_hard_limit and hard_seconds is not None:
        raise ValueError("Use either clear_hard_limit=True or positive hard_seconds, never both")
    if event == "policy" and (note is None or not note.strip() or
            review_seconds is None and hard_seconds is None and not clear_hard_limit):
        raise ValueError("Policy changes require a note with authorization/source and a timing change")
    if event == "context" and context is None:
        raise ValueError("Context changes require a context ID")
    if event == "suspend" and note is None:
        raise ValueError("Owner-wait suspension requires an explicit reason")
    if event != "policy" and (review_seconds is not None or hard_seconds is not None or clear_hard_limit):
        raise ValueError("Timing changes require an explicit policy event")
    references = artifacts or []
    if len(references) > 16:
        raise ValueError("Attach at most 16 artifact references per checkpoint")
    for reference in references:
        validate_filesystem_path(reference)
        if not Path(reference).is_absolute():
            raise ValueError("Artifact references require absolute paths")
    store = OperationStateStore(str(path))
    with store.observation() if event == "show" else store.ownership():
        data = _load(path)
        observed = stamp()
        previous = _summary(data, observed)
        if event == "show":
            return {"artifact": str(path), **previous, "latest": data["events"][-1]}
        if previous["timing"] != "comparable-boot":
            raise ValueError("No comparable boot clock; cannot continue or reset this run")
        if previous["finished"] or len(data["events"]) >= EVENT_LIMIT:
            raise ValueError("Run is finished or at event capacity; preserve its original clocks/history")
        if (event == "suspend" and previous["owner_wait_suspended"] or
                event == "resume" and not previous["owner_wait_suspended"]):
            raise ValueError("Owner-wait transition contradicts the recorded state")
        row = {"event": event, **observed, "phase": phase or data["events"][-1]["phase"],
               "previous_gap_seconds": previous["since_last_event_seconds"],
               "checkpoint_late_seconds": previous["checkpoint_late_seconds"],
               "note": note, "context": context, "artifacts": references, "game_clock_observed": game_clock,
               "review_seconds": review_seconds, "hard_seconds": hard_seconds,
               **({"clear_hard_limit": True} if clear_hard_limit else {})}
        data["events"].append(row)
        if clear_hard_limit:
            # Old readers must refuse rather than silently retain the old cap.
            data["schema_version"] = 2
        _save(path, data)
        return {"artifact": str(path), **_summary(data, observed), "latest": row}
