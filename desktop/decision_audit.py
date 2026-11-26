"""Check a controller decision against saved desktop receipts and pinned frames."""

from __future__ import annotations

import codecs
import hashlib

from desktop import learning_records as contract, record_bundles
from lib.atomic_io import parse_json

_DELIVERY = {
    "not-sent": ("refused", "not-sent"),
    "completed": ("completed", "sent"),
    "partial": ("attempted", "unknown"),
    "unknown": ("attempted", "unknown"),
}


def _load_receipt(path):
    raw = record_bundles._read(path, contract.MAX_BYTES)
    encoding = "utf-16" if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)) else "utf-8-sig"
    receipt = parse_json(raw.decode(encoding))
    if not isinstance(receipt, dict):
        raise ValueError("Expected a desktop receipt JSON object")
    return receipt, hashlib.sha256(raw).hexdigest()


def _object(value):
    return value if isinstance(value, dict) else {}


def _input(receipt):
    if any(key in receipt for key in ("input_id", "input_delivery", "dispatch_stage")):
        return receipt
    return _object(receipt.get("result"))


def _capture(receipt):
    if receipt.get("error") is not None or receipt.get("ok") is False:
        return {}
    result = _object(receipt.get("result", receipt))
    if result.get("error") is not None or result.get("ok") is False:
        return {}
    capture = _object(result.get("observation", result))
    return capture if capture.get("error") is None and capture.get("ok") is not False else {}


def _token(value):
    if value is not None:
        contract.text(value)
    return value


def _identity_check(actual, expected, field):
    if actual is None or expected is None:
        return "unverified", f"{field}-unavailable"
    if actual != expected:
        return "mismatch", f"{field}-differs"
    return "matched", None


def _dispatch_check(claim, actual, digest):
    stage = _token(actual.get("dispatch_stage"))
    delivery = _token(actual.get("input_delivery"))
    status, reason = _identity_check(_token(actual.get("input_id")), claim["id"], "input-id")
    if status == "matched":
        if delivery not in _DELIVERY or stage is None:
            status, reason = "unverified", "explicit-dispatch-unavailable"
        elif _DELIVERY[delivery] != (claim["stage"], claim["delivery"]):
            status, reason = "mismatch", "dispatch-claim-differs"
    return {"status": status, "reason": reason, "receipt_sha256": digest,
            "dispatch_stage": stage, "input_delivery": delivery}


def _frame_check(capture, frames):
    artifact = capture.get("artifact_sha256")
    if artifact is None:
        return ("unverified", "frame-digest-unavailable"), []
    contract.digest(artifact)
    if not frames:
        return ("unverified", "frame-not-pinned"), []
    candidates = [frame for frame in frames if frame["sha256"] == artifact]
    if not candidates:
        return ("mismatch", "frame-digest-differs"), []
    metadata = {}
    for key in ("geometry", "image_geometry", "image_origin"):
        value = capture.get(key)
        if value is None:
            continue
        if not isinstance(value, list) or len(value) != 2:
            raise ValueError(f"Capture {key} must be a coordinate pair")
        for number in value:
            contract.integer(number, 0 if key == "image_origin" else 1, 16384)
        metadata[key] = value
    matches = []
    for frame in candidates:
        expected = {"geometry": frame["geometry"], "image_geometry": [frame["width"], frame["height"]],
                    "image_origin": frame["origin"]}
        if all(expected[key] == value for key, value in metadata.items()):
            matches.append(frame["id"])
    if not matches:
        return ("mismatch", "frame-geometry-differs"), []
    return ("matched", None), matches


def _capture_check(saved, digest, packet, actual, *, after=False):
    capture = _capture(saved)
    last = _object(capture.get("last_input"))
    observation_id = _token(capture.get("observation_id"))
    checks = [
        _identity_check(_token(capture.get("generation")), _token(actual.get("generation")), "generation"),
    ]
    if after:
        checks.append(_identity_check(_token(last.get("input_id")), _token(actual.get("input_id")), "last-input-id"))
        if observation_id is None:
            checks.append(("unverified", "observation-id-unavailable"))
        elif observation_id == actual.get("observation_id"):
            checks.append(("mismatch", "reused-input-observation"))
        for key in ("generation", "input_delivery", "observation_id"):
            if last.get(key) is not None and actual.get(key) is not None:
                checks.append(_identity_check(_token(last[key]), _token(actual[key]), f"last-input-{key.replace('_', '-')}"))
    else:
        checks.append(_identity_check(observation_id, _token(actual.get("observation_id")), "observation-id"))

    frame_check, matches = _frame_check(capture, packet["frames"])
    checks.append(frame_check)
    priority = ("mismatch", "unverified", "matched")
    status, reason = min(checks, key=lambda check: priority.index(check[0]))
    changed = capture.get("changed")
    return {"status": status, "reason": reason, "receipt_sha256": digest, "packet_id": packet["id"],
            "frame_ids": matches, "changed": changed if status == "matched" and type(changed) is bool else None}


def audit(artifact, decision_id, input_receipt, *, before=None, after=None):
    """Compare one decision with explicit receipts while preserving unknowns."""
    _, manifest, document = record_bundles.load_verified(artifact)
    contract.text(decision_id)
    records = {row["id"]: row for row in document["records"]}
    decision = records.get(decision_id)
    if decision is None or decision["kind"] != "decision":
        raise ValueError("Expected a decision ID in the verified bundle")
    packet = records[decision["packet_id"]]
    saved_input, input_digest = _load_receipt(input_receipt)
    actual = _input(saved_input)
    dispatch = _dispatch_check(decision["dispatch"], actual, input_digest)
    before_check, after_check = None, None
    if before is not None:
        before_check = _capture_check(*_load_receipt(before), packet, actual)
    if after is not None:
        packet_id, capture_receipt = after
        contract.text(packet_id)
        followup = records.get(packet_id)
        if followup is None or followup["kind"] != "observation" or packet_id == packet["id"]:
            raise ValueError("Expected a distinct follow-up observation packet")
        if any(followup[key] != packet[key] for key in ("run_id", "match_id", "clock_scope", "conditions")):
            raise ValueError("Follow-up packet must share the original run, match, clock scope and conditions")
        if packet["monotonic_ns"] is not None and followup["monotonic_ns"] is not None:
            predates = followup["monotonic_ns"] < packet["monotonic_ns"]
        else:
            predates = contract.utc(followup["captured_at"]) < contract.utc(packet["captured_at"])
        if predates:
            raise ValueError("Follow-up packet predates the decision observation")
        saved_after = (saved_input, input_digest) if capture_receipt == input_receipt else _load_receipt(capture_receipt)
        after_check = _capture_check(*saved_after, followup, actual, after=True)

    statuses = {check["status"] for check in (dispatch, before_check, after_check) if check is not None}
    status = "mismatch" if "mismatch" in statuses else "unverified" if "unverified" in statuses else "matched"
    return {"schema_version": 1, "ok": status != "mismatch", "status": status, "decision_id": decision_id,
            "records_sha256": manifest["source"]["sha256"], "dispatch": dispatch, "before": before_check,
            "after": after_check, "effect_declared": decision["observed_effect"] is not None,
            "effect_assessment": "not-evaluated", "input_authority": False}
