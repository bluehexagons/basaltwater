"""Version-one offline evidence contract. Nothing here grants input authority."""

from __future__ import annotations

from datetime import datetime, timedelta
import json
import math
import re

from lib.validation import validate_no_control_characters

MAX_BYTES = 1024 * 1024
MAX_RECORDS = 128
CONDITIONS = ("game", "build", "environment", "profile", "recognizer", "knowledge",
              "policy", "adapter", "team", "controller", "coaching")
OUTCOMES = ("win", "loss", "draw", "interrupted", "unknown")
TERMINAL_OUTCOMES = ("win", "loss", "draw")


def text(value, maximum=128):
    if not isinstance(value, str) or not 1 <= len(value) <= maximum:
        raise ValueError(f"Expected text of 1–{maximum} characters")
    validate_no_control_characters(value, "Record text")


def integer(value, low=0, high=2**63 - 1):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"Expected integer in {low}–{high}")


def keys(value, names):
    if not isinstance(value, dict) or set(value) != set(names.split()):
        raise ValueError(f"Expected exactly these fields: {names}")


def digest(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("Expected lowercase SHA-256")


def utc(value):
    text(value)
    try:
        result = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Expected ISO-8601 UTC") from exc
    if result.utcoffset() != timedelta(0):
        raise ValueError("Expected explicit UTC")
    return result


def bounded(value, depth=0):
    """Bound even free-form semantic values, settings and evidence."""
    if depth > 12:
        raise ValueError("Record nesting exceeds 12")
    if value is None or type(value) is bool:
        return
    if type(value) in (int, float):
        maximum = 2**63 - 1 if type(value) is int else 2**53 - 1
        if abs(value) > maximum or not math.isfinite(value):
            raise ValueError("Record number must be finite and within its bounded range")
    elif isinstance(value, str):
        text(value, 4096)
    elif isinstance(value, (dict, list)):
        if len(value) > 128:
            raise ValueError("Record collection exceeds 128 entries")
        if isinstance(value, dict):
            for name in value:
                text(name)
        for item in value.values() if isinstance(value, dict) else value:
            bounded(item, depth + 1)
    else:
        raise ValueError("Expected a JSON value")


def sequence(value, maximum=128):
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f"Expected list of at most {maximum} entries")
    return value


def validate(document):
    """Validate a closed record graph; return its packets' frame declarations."""
    bounded(document)
    if len(json.dumps(document, allow_nan=False).encode()) > MAX_BYTES:
        raise ValueError("Records exceed 1 MiB")
    keys(document, "schema_version records")
    if type(document["schema_version"]) is not int or document["schema_version"] != 1:
        raise ValueError("Unsupported record schema_version")
    rows = sequence(document["records"], MAX_RECORDS)
    if not rows:
        raise ValueError("At least one record is required")
    by_id, frames = {}, {}
    common = "kind id packet_id state_revision "
    shapes = {
        "observation": "kind id run_id match_id phase round_id state_revision captured_at monotonic_ns clock_scope conditions frames fields",
        "decision": common + "policy knowledge action preconditions alternative opportunity_cost expected_effect dispatch observed_effect evidence",
        "coach-request": common + "trigger allowed_digests requested_settings question deadline_at intervention_budget",
        "coach-reply": common + "request_id received_at actual_settings recommendation evidence validity",
        "coach-disposition": "kind id reply_id at current_state_revision validation disposition reason result",
    }
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Expected record object")
        if not isinstance(row.get("kind"), str) or row["kind"] not in shapes:
            raise ValueError("Unsupported record kind")
        keys(row, shapes[row["kind"]])
        text(row.get("id"))
        if row["id"] in by_id:
            raise ValueError("Duplicate record ID")
        by_id[row["id"]] = row

    def reference(identifier, kind):
        text(identifier)
        result = by_id.get(identifier)
        if result is None or result.get("kind") != kind:
            raise ValueError(f"Missing {kind} reference: {identifier}")
        return result

    for row in rows:
        kind = row.get("kind")
        if kind == "observation":
            keys(row, "kind id run_id match_id phase round_id state_revision captured_at monotonic_ns clock_scope conditions frames fields")
            for name in ("run_id", "match_id", "phase", "clock_scope"):
                text(row[name])
            for name in ("round_id", "state_revision"):
                if row[name] is not None:
                    text(row[name])
            utc(row["captured_at"])
            if row["monotonic_ns"] is not None:
                integer(row["monotonic_ns"])
            keys(row["conditions"], " ".join(CONDITIONS))
            for value in row["conditions"].values():
                if value is not None:
                    text(value)
            local = {}
            for frame in sequence(row["frames"], 8):
                keys(frame, "id sha256 bytes width height origin geometry")
                text(frame["id"])
                if frame["id"] in frames:
                    raise ValueError("Duplicate frame ID")
                digest(frame["sha256"])
                integer(frame["bytes"], 1, 16 * 1024 * 1024)
                for name in ("width", "height"):
                    integer(frame[name], 1, 16384)
                if frame["width"] * frame["height"] > 16 * 1024 * 1024:
                    raise ValueError("Frame exceeds 16 megapixels")
                for name in ("origin", "geometry"):
                    if not isinstance(frame[name], list) or len(frame[name]) != 2:
                        raise ValueError("Expected coordinate pair")
                    for number in frame[name]:
                        integer(number, 0 if name == "origin" else 1, 16384)
                if any(frame["origin"][i] + frame[name] > frame["geometry"][i]
                       for i, name in enumerate(("width", "height"))):
                    raise ValueError("Frame outside source geometry")
                frames[frame["id"]] = local[frame["id"]] = frame
            names = set()
            for field in sequence(row["fields"]):
                keys(field, "name status value sources")
                text(field["name"])
                if field["name"] in names:
                    raise ValueError("Duplicate field name")
                names.add(field["name"])
                if field["status"] not in ("observed", "inferred", "unknown"):
                    raise ValueError("Invalid field status")
                if (field["value"] is None) != (field["status"] == "unknown"):
                    raise ValueError("Only unknown fields require null values")
                sources = sequence(field["sources"], 8)
                if field["status"] == "observed" and not sources:
                    raise ValueError("Observed fields require source regions")
                for source in sources:
                    keys(source, "frame_id rectangle")
                    text(source["frame_id"])
                    frame = local.get(source["frame_id"])
                    rectangle = source["rectangle"]
                    if frame is None or not isinstance(rectangle, list) or len(rectangle) != 4:
                        raise ValueError("Source requires a region in this packet's frame")
                    for i, number in enumerate(rectangle):
                        integer(number, 0 if i < 2 else 1, 16384)
                    if rectangle[0] + rectangle[2] > frame["width"] or rectangle[1] + rectangle[3] > frame["height"]:
                        raise ValueError("Source region outside frame")
        elif kind in ("decision", "coach-request", "coach-reply"):
            packet = reference(row.get("packet_id"), "observation")
            if row.get("state_revision") != packet["state_revision"]:
                raise ValueError("Mixed packet/state revision")
            common = "kind id packet_id state_revision "
            if kind == "decision":
                keys(row, common + "policy knowledge action preconditions alternative opportunity_cost expected_effect dispatch observed_effect evidence")
                for name in ("policy", "knowledge"):
                    if row[name] is not None:
                        text(row[name])
                text(row["action"], 4096)
                sequence(row["preconditions"], 32)
                sequence(row["evidence"], 32)
                keys(row["dispatch"], "id stage delivery")
                if row["dispatch"]["id"] is not None:
                    text(row["dispatch"]["id"])
                if row["dispatch"]["stage"] not in ("not-attempted", "attempted", "completed", "refused") or row["dispatch"]["delivery"] not in ("not-sent", "sent", "unknown"):
                    raise ValueError("Invalid dispatch stage/delivery")
            elif kind == "coach-request":
                keys(row, common + "trigger allowed_digests requested_settings question deadline_at intervention_budget")
                text(row["trigger"], 4096)
                text(row["question"], 4096)
                for value in sequence(row["allowed_digests"], 32):
                    digest(value)
                if not isinstance(row["requested_settings"], dict):
                    raise ValueError("Expected requested settings object")
                if utc(row["deadline_at"]) < utc(packet["captured_at"]):
                    raise ValueError("Deadline predates packet")
                integer(row["intervention_budget"], 0, 100)
            else:
                keys(row, common + "request_id received_at actual_settings recommendation evidence validity")
                request = reference(row["request_id"], "coach-request")
                if request["packet_id"] != packet["id"]:
                    raise ValueError("Reply refers to another packet")
                if utc(row["received_at"]) < utc(packet["captured_at"]):
                    raise ValueError("Reply predates packet")
                if not isinstance(row["actual_settings"], dict):
                    raise ValueError("Expected actual settings object")
                text(row["recommendation"], 4096)
                sequence(row["evidence"], 32)
                sequence(row["validity"], 32)
        elif kind == "coach-disposition":
            keys(row, "kind id reply_id at current_state_revision validation disposition reason result")
            reply = reference(row["reply_id"], "coach-reply")
            request = reference(reply["request_id"], "coach-request")
            if utc(row["at"]) < utc(reply["received_at"]):
                raise ValueError("Disposition predates reply")
            status = advice_status(reply, request, row["current_state_revision"], row["at"])
            if row["validation"] != status:
                raise ValueError("Disposition validation contradicts state/deadline")
            if row["disposition"] not in ("accepted", "rejected", "expired") or row["disposition"] == "accepted" and status != "valid":
                raise ValueError("Invalid advice cannot be accepted")
            text(row["reason"], 4096)
        else:
            raise ValueError("Unsupported record kind")
    if len(frames) > 8 or sum(frame["bytes"] for frame in frames.values()) > 32 * 1024 * 1024:
        raise ValueError("Bundle exceeds 8 frames or 32 MiB media")
    return frames


def advice_status(reply, request, current_state_revision, at):
    """Structural invalidity raises; freshness never implies controller acceptance."""
    if current_state_revision is not None:
        text(current_state_revision)
    instant = utc(at)
    if instant < utc(reply["received_at"]):
        raise ValueError("Evaluation predates receipt")
    if instant > utc(request["deadline_at"]):
        return "late"
    if current_state_revision is None or reply["state_revision"] is None:
        return "unknown-state"
    if current_state_revision != reply["state_revision"]:
        return "stale"
    return "valid"


def validate_corpus(document):
    """Version-one corpus extension; existing record graphs remain version one."""
    bounded(document)
    if len(json.dumps(document, allow_nan=False).encode()) > MAX_BYTES:
        raise ValueError("Corpus manifest exceeds 1 MiB")
    keys(document, "schema_version kind id cutoff_at sources segments")
    if type(document["schema_version"]) is not int or document["schema_version"] != 1 or document["kind"] != "learning-corpus":
        raise ValueError("Unsupported corpus version/kind")
    text(document["id"])
    cutoff = utc(document["cutoff_at"])
    sources = {}
    for source in sequence(document["sources"], 32):
        keys(source, "id sha256 bytes availability")
        text(source["id"])
        digest(source["sha256"])
        integer(source["bytes"], 1, MAX_BYTES)
        if source["availability"] not in ("available", "missing"):
            raise ValueError("Invalid corpus source availability")
        if source["id"] in sources:
            raise ValueError("Duplicate corpus source ID")
        sources[source["id"]] = source
    if sum(source["bytes"] for source in sources.values()) > 16 * MAX_BYTES:
        raise ValueError("Corpus sources exceed 16 MiB")
    for row in sequence(document["segments"]):
        keys(row, "id source_id packet_id match_id run_id condition_id conditions included reason started_at cutoff_at window_outcome eventual_outcome eventual_at setup_failure placement rounds displayed_timer wall_seconds active_seconds usage coaching_interventions evidence_availability")
        for name in ("id", "source_id", "packet_id", "match_id", "run_id", "condition_id"):
            text(row[name])
        if row["source_id"] not in sources:
            raise ValueError(f"Undeclared source for segment {row['id']}")
        if type(row["included"]) is not bool:
            raise ValueError("Segment included must be boolean")
        text(row["reason"], 4096)
        keys(row["conditions"], " ".join(CONDITIONS))
        for value in row["conditions"].values():
            if value is not None:
                text(value)
        end = utc(row["cutoff_at"])
        if end > cutoff:
            raise ValueError("Segment cutoff exceeds corpus cutoff")
        for name in ("window_outcome", "eventual_outcome"):
            if row[name] not in OUTCOMES:
                raise ValueError("Invalid corpus outcome")
        if row["window_outcome"] in TERMINAL_OUTCOMES and row["eventual_outcome"] != row["window_outcome"]:
            raise ValueError("Terminal window outcome must agree with eventual outcome")
        if row["eventual_outcome"] in TERMINAL_OUTCOMES:
            if row["eventual_at"] is None or utc(row["eventual_at"]) > cutoff:
                raise ValueError("Terminal eventual outcome needs a corpus-cutoff-bounded timestamp")
            terminal_at = utc(row["eventual_at"])
            if row["window_outcome"] in TERMINAL_OUTCOMES and terminal_at > end:
                raise ValueError("Terminal window outcome postdates its window")
            if row["window_outcome"] not in TERMINAL_OUTCOMES and terminal_at < end:
                raise ValueError("Eventual terminal outcome predates nonterminal window")
        elif row["eventual_at"] is not None:
            raise ValueError("Nonterminal eventual outcome requires null eventual_at")
        if row["started_at"] is None:
            text(row["setup_failure"], 4096)
            if row["window_outcome"] != "unknown" or row["eventual_outcome"] != "unknown" or row["placement"] is not None:
                raise ValueError("Setup-only failure cannot have a match outcome/placement")
        elif row["setup_failure"] is not None or utc(row["started_at"]) > end:
            raise ValueError("Started match has inconsistent start/setup state")
        elif row["eventual_at"] is not None and utc(row["eventual_at"]) < utc(row["started_at"]):
            raise ValueError("Terminal outcome predates match start")
        for name, low in (("placement", 1), ("rounds", 0), ("coaching_interventions", 0)):
            if row[name] is not None:
                integer(row[name], low, 1000000)
        if row["displayed_timer"] is not None:
            text(row["displayed_timer"])
        for name in ("wall_seconds", "active_seconds"):
            nullable_number(row[name], 30 * 86400)
        if row["active_seconds"] is not None and row["wall_seconds"] is not None and row["active_seconds"] > row["wall_seconds"]:
            raise ValueError("Active seconds exceed segment wall seconds")
        keys(row["usage"], "scope tokens cost currency")
        for name in ("scope", "currency"):
            if row["usage"][name] is not None:
                text(row["usage"][name])
        if row["usage"]["tokens"] is not None:
            integer(row["usage"]["tokens"], 0, 10**12)
        nullable_number(row["usage"]["cost"], 10**9)
        if (row["usage"]["tokens"] is not None or row["usage"]["cost"] is not None) and row["usage"]["scope"] is None:
            raise ValueError("Known usage requires an explicit scope")
        if row["usage"]["cost"] is not None and row["usage"]["currency"] is None:
            raise ValueError("Known cost requires currency")
        if row["evidence_availability"] not in ("complete", "partial", "missing"):
            raise ValueError("Invalid evidence availability")
    return sources


def nullable_number(value, maximum):
    if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= maximum):
        raise ValueError(f"Expected null or finite number in 0–{maximum}")
