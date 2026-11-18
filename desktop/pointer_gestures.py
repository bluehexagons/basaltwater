"""Bounded portal pointer/touch plans, validated completely before dispatch."""

from __future__ import annotations

import hashlib
import json
import time

CAPABILITIES = {"touch": "touchscreen", "relative": "relative-pointer", "scroll": "discrete-scroll"}
MAX_FRAMES = 128
MAX_CONTACTS = 10
MAX_PLAN_BYTES = 49152


def validate(payload: dict) -> None:
    kind = payload.get("kind")
    if kind != "touch" and payload.get("frames") is not None:
        raise ValueError("Touch frames apply only to touch input")
    if kind not in ("relative", "scroll") and any(payload.get(k) is not None for k in ("dx", "dy")):
        raise ValueError("Deltas apply only to relative or scroll input")
    if kind not in CAPABILITIES:
        return
    if payload.get("action") != "input" or not isinstance(payload.get("observation_id"), str) or not payload["observation_id"]:
        raise ValueError("Pointer gestures require a fresh single-use screenshot observation")
    if any(payload.get(k) is not None for k in
           ("x", "y", "to_x", "to_y", "duration_ms", "hold_ms", "key", "text", "codes")):
        raise ValueError("Gesture input cannot include click, drag or keyboard arguments")
    if kind in ("relative", "scroll"):
        limit = 4096 if kind == "relative" else 100
        values = [payload.get(k, 0) for k in ("dx", "dy")]
        if any(type(n) is not int or not -limit <= n <= limit for n in values) or not any(values):
            raise ValueError(f"{kind} requires nonzero integer dx/dy within -{limit}..{limit}")
        return
    geometry = payload.get("geometry")
    if (not isinstance(geometry, list) or len(geometry) != 2
            or any(type(n) is not int or not 1 <= n <= 16384 for n in geometry)):
        raise ValueError("Touch requires observed full-monitor geometry")
    frames = payload.get("frames")
    if not isinstance(frames, list) or not 2 <= len(frames) <= MAX_FRAMES:
        raise ValueError(f"Touch requires 2–{MAX_FRAMES} complete contact frames")
    previous = -1
    seen_contact = False
    for frame in frames:
        if not isinstance(frame, dict) or set(frame) != {"at_ms", "contacts"}:
            raise ValueError("Each touch frame requires only at_ms and contacts")
        at, contacts = frame["at_ms"], frame["contacts"]
        if type(at) is not int or not previous < at <= 5000 or (previous == -1 and at != 0):
            raise ValueError("Touch times must start at zero, strictly increase and end within 5000 ms")
        previous = at
        if not isinstance(contacts, list) or len(contacts) > MAX_CONTACTS:
            raise ValueError(f"Touch supports at most {MAX_CONTACTS} simultaneous contacts")
        identities = set()
        for contact in contacts:
            if not isinstance(contact, dict) or set(contact) != {"id", "x", "y"}:
                raise ValueError("Each touch contact requires only id, x and y")
            slot, x, y = contact["id"], contact["x"], contact["y"]
            if type(slot) is not int or not 0 <= slot < MAX_CONTACTS or slot in identities:
                raise ValueError("Touch IDs must be distinct integers from 0 through 9 in each frame")
            if type(x) is not int or type(y) is not int or not (0 <= x < geometry[0] and 0 <= y < geometry[1]):
                raise ValueError("Touch coordinates must lie inside the selected full monitor")
            identities.add(slot)
            seen_contact = True
    if frames[-1]["contacts"] or not seen_contact:
        raise ValueError("Touch must include a contact and finish with all contacts lifted")
    if len(_plan_bytes(frames)) > MAX_PLAN_BYTES:
        raise ValueError(f"Touch plan exceeds {MAX_PLAN_BYTES} canonical JSON bytes")


def _plan_bytes(frames: list) -> bytes:
    return json.dumps(frames, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def plan_summary(payload: dict) -> dict:
    """Pin a validated plan without copying contact coordinates into every event."""
    validate(payload)
    encoded = _plan_bytes(payload["frames"])
    return {"sha256": hashlib.sha256(encoded).hexdigest(), "bytes": len(encoded),
            "frames": len(payload["frames"])}


def execute_touch(session, payload: dict) -> None:
    """Release every attempted contact even after uncertain dispatch or takeover."""
    frames = payload["frames"]
    receipt = payload.setdefault("_input_receipt", {})
    receipt.update(touch_frames_completed=0, touch_contacts_released=False,
                   touch_frame_completed_monotonic_ns=[])
    active = session.injected_touches
    positions = {}
    started = time.monotonic()
    try:
        for frame in frames:
            session.hold(max(0, (started + frame["at_ms"] / 1000 - time.monotonic()) * 1000), payload)
            contacts = {c["id"]: (c["x"], c["y"]) for c in frame["contacts"]}
            for slot in sorted(active - contacts.keys()):
                session.guard(payload)
                session.portal.notify("NotifyTouchUp", "u", slot, timeout_ms=250)
                active.remove(slot)
            for slot, (x, y) in contacts.items():
                session.guard(payload)
                down = slot not in active
                if not down and positions.get(slot) == (x, y):
                    continue
                # Track BEFORE sending: a timed-out down may still have arrived.
                active.add(slot)
                session.mark_input_dispatch(payload)
                session.portal.notify("NotifyTouchDown" if down else "NotifyTouchMotion", "uudd",
                    session.node, slot, x * session.logical_size[0] / session.geometry[0],
                    y * session.logical_size[1] / session.geometry[1], timeout_ms=250)
            positions = contacts
            receipt["touch_frames_completed"] += 1
            receipt["touch_frame_completed_monotonic_ns"].append(time.monotonic_ns())
    finally:
        release_error = None
        for slot in sorted(active):
            try:
                session.portal.notify("NotifyTouchUp", "u", slot, timeout_ms=250)
                active.remove(slot)
            except Exception as exc:
                release_error = exc
        receipt["touch_contacts_released"] = not active
        if release_error is not None:
            session.fail("Touch release failed; native control stopped")
            session.close()
            raise RuntimeError("Touch release failed; native control stopped") from release_error
