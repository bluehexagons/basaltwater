"""Save desktop observations without returning their full payload to the caller."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import secrets
import shutil
import tempfile
import time

DEFAULT_CAPTURE_MAX_AGE_SECONDS = 60
MAX_CAPTURE_MAX_AGE_SECONDS = 600


def validate_capture_max_age(value: int) -> int:
    """Bound an explicitly selected observation lifetime, independently of I/O."""
    if type(value) is not int or not 1 <= value <= MAX_CAPTURE_MAX_AGE_SECONDS:
        raise ValueError("Capture max age must be an integer from 1 to 600 seconds")
    return value


def capture_freshness(captured: float | None = None, max_age: int = DEFAULT_CAPTURE_MAX_AGE_SECONDS) -> dict:
    """Report monotonic age without renewing an observation."""
    age = max(0, time.monotonic() - captured) if captured is not None else None
    return {"age_seconds": age, "max_age_seconds": max_age,
            "remaining_seconds": max(0, max_age - age) if age is not None else 0,
            "fresh": age is not None and age <= max_age}


def validate_observation_option(payload: dict) -> None:
    observation_id = payload.get("observation_id")
    if observation_id is not None and (payload.get("action") != "input"
            or not isinstance(observation_id, str) or not re.fullmatch(r"[0-9a-f]{32}", observation_id)):
        raise ValueError("Observation input requires a returned 32-character observation ID")


class CaptureObservation:
    """One helper-owned capture authority; callers cannot supply its clock or age."""

    def __init__(self, started: float, max_age: int, geometry: list[int], *, window: dict | None = None,
                 region: list[int] | None = None):
        self.id = secrets.token_hex(16)
        self.started = started
        self.max_age = validate_capture_max_age(max_age)
        self.geometry = list(geometry)
        self.window = window
        self.region = list(region) if region is not None else [0, 0, *geometry]

    def validate(self, payload: dict) -> None:
        validate_observation_option(payload)
        if payload.get("observation_id") != self.id:
            raise ValueError("Observation is consumed or superseded; capture and inspect again")
        geometry = payload.get("geometry")
        if not isinstance(geometry, list) or any(type(n) is not int for n in geometry) or geometry != self.geometry:
            raise ValueError("Observation geometry changed; capture and inspect again")
        if not self.freshness()["fresh"]:
            raise ValueError("Observation expired; capture and inspect again")
        if payload.get("kind") in ("click", "move", "drag"):
            left, top, width, height = self.region
            endpoints = [("x", "y"), ("to_x", "to_y")] if payload["kind"] == "drag" else [("x", "y")]
            for x_key, y_key in endpoints:
                x, y = payload.get(x_key), payload.get(y_key)
                if (type(x) is not int or type(y) is not int
                        or not left <= x < left + width or not top <= y < top + height):
                    raise ValueError("Pointer target lies outside the observed image")

    def freshness(self) -> dict:
        return capture_freshness(self.started, self.max_age)


def compact_capture(result: dict) -> dict:
    """Keep capture evidence and errors while omitting the control envelope."""
    return {key: result[key] for key in (
        "output", "generation", "control_revision", "geometry", "image_geometry", "image_origin", "captured_at", "changed",
        "observation_id", "coordinate_space", "window",
        "pixel_sha256", "artifact_sha256", "frame_received_at", "frame_received_monotonic_ns", "frame_pts_ns",
        "initial_frame_skipped", "capture_freshness", "last_input", "error", "evidence_error") if key in result}


def save_observation(result: dict) -> dict:
    """Retain the exact JSON result privately and return a bounded preview.

    Output shortening is independent of scan completeness. In particular,
    a truncated accessibility scan stays truncated in the summary.
    """
    directory = Path(tempfile.mkdtemp(prefix="basaltwater-desktop-"))
    try:
        os.chmod(directory, 0o700)
        path = directory / "observation.json"
        with path.open("x", encoding="utf-8") as output:
            os.fchmod(output.fileno(), 0o600)
            json.dump(result, output, ensure_ascii=True)
            output.write("\n")
        summary = {
            "artifact": str(path),
            "bytes": path.stat().st_size,
            "counts": {},
            "preview": {},
        }
        for key in ("generation", "pid", "root", "truncated", "coverage", "condition", "state", "error"):
            value = result.get(key)
            if key in result and isinstance(value, (str, int, float, bool, type(None))):
                summary[key] = value[:512] if isinstance(value, str) else value
        for key in ("elements", "windows"):
            items = result.get(key)
            if not isinstance(items, list):
                continue
            summary["counts"][key] = len(items)
            summary["preview"][key] = [
                {field: value[:160] if isinstance(value, str) else value
                 for field in ("ref", "id", "pid", "name", "role", "title")
                 if isinstance((value := item.get(field)), (str, int))}
                for item in items[:3] if isinstance(item, dict)
            ]
        return summary
    except BaseException:
        shutil.rmtree(directory)
        raise
