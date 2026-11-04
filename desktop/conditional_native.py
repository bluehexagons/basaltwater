"""Explicit CachyOS backend adapter for the controller-owned executor."""

from __future__ import annotations

import copy
import hashlib
import json
import secrets

from desktop import client, learning_records as records, perception
from desktop.observations import compact_capture
from desktop.qa_artifacts import clock
from desktop.record_bundles import _read


class NativeController:
    """No startup, consent, permission transition or automatic helper refresh."""

    def __init__(self, backend, profile, templates, *, run_id, match_id, phase):
        if backend is client.runtime:
            raise ValueError("Conditional native adapter requires the CachyOS backend")
        perception.validate_templates(profile, templates)
        for value in (run_id, match_id, phase):
            records.text(value)
        self.backend = backend
        self.profile, self.templates = copy.deepcopy(profile), dict(templates)
        self.run_id, self.match_id, self.phase = run_id, match_id, phase

    def status(self, deadline):
        return self.backend.status(deadline=deadline)

    def observe(self, path, deadline, max_bytes):
        # Conservative RGBA PNG bound, including row filters/zlib/chunk overhead.
        # Refuse before capture if its output cannot fit the declared reservation.
        if 4 * self.profile["image_geometry"][0] * self.profile["image_geometry"][1] + 65536 > max_bytes:
            raise ValueError("Frame budget cannot hold this profile's worst-case PNG")
        current = self.status(deadline)
        require_control(current)
        capture = client.send_action({"action": "screenshot", "generation": current["generation"],
            "control_revision": current["control_revision"], "output": str(path), "next_frame": True,
            "crop": self.profile["image_origin"] + self.profile["image_geometry"]}, deadline=deadline, backend=self.backend)
        if "error" in capture or capture.get("initial_frame_skipped") is not True:
            raise ValueError(capture.get("error", "Helper did not confirm a subsequent frame"))
        if any(capture.get(key) != current[key] for key in ("generation", "control_revision")):
            raise ValueError("Control changed during capture")
        content = _read(path, max_bytes, private=True)
        digest = hashlib.sha256(content).hexdigest()
        if capture.get("artifact_sha256") != digest:
            raise ValueError("Capture artifact hash is unavailable or changed")
        domain = clock()
        identifier = secrets.token_hex(16)
        geometry = capture.get("image_geometry")
        if not isinstance(geometry, list) or len(geometry) != 2:
            raise ValueError("Capture image geometry is unavailable")
        packet = {"kind": "observation", "id": "packet-" + identifier,
            "run_id": self.run_id, "match_id": self.match_id, "phase": self.phase,
            "round_id": None, "state_revision": None, "captured_at": capture.get("captured_at"),
            "monotonic_ns": capture.get("frame_received_monotonic_ns") if domain["boot_id"] else None,
            "clock_scope": "linux-boot:" + (domain["boot_id"] or "unknown"),
            "conditions": {key: self.profile["conditions"].get(key) for key in records.CONDITIONS},
            "frames": [{"id": "frame-" + identifier, "sha256": digest, "bytes": len(content),
                        "width": geometry[0], "height": geometry[1], "origin": capture.get("image_origin"),
                        "geometry": capture.get("geometry")}], "fields": []}
        frame_id = packet["frames"][0]["id"]
        result, _ = perception.recognize(packet, frame_id, content, self.profile, self.templates)
        packet["fields"] = [{key: field[key] for key in ("name", "status", "value", "sources")} for field in result["fields"]]
        # A revision tracks recognized evidence/regions and profile, not image motion.
        state = {"fields": [{key: field[key] for key in ("name", "status", "value")} | {
            "regions": [source["rectangle"] for source in field["sources"]]} for field in packet["fields"]],
            "profile": self.profile}
        packet["state_revision"] = hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()
        result["state_revision"] = packet["state_revision"]
        return {"packet": packet, "capture": compact_capture(capture), "recognition": result,
                "frame_id": frame_id, "frame": content}

    def dispatch(self, payload, deadline):
        return client.send_action(payload, deadline=deadline, backend=self.backend)


def require_control(current):
    if not isinstance(current, dict):
        raise ValueError("Malformed desktop status")
    if current.get("state") != "running" or current.get("agent_control") != "enabled":
        raise ValueError("Desktop is not running under enabled agent control")
    for key in ("generation", "control_revision"):
        records.text(current.get(key))
    if "observation-input" not in current.get("input_capabilities", []):
        raise ValueError("Helper does not support observation-bound input; refresh separately at a safe checkpoint")
