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
