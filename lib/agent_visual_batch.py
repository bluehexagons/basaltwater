"""Validate project-owned batches and build private, portable inspection galleries."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import uuid

from lib.agent_visuals import _directory, _image
from lib.atomic_io import read_json_file, write_json_atomic
from lib.validation import validate_filesystem_path, validate_no_control_characters
from lib.visual_compare_viewer import render_batch_viewer


MAX_BATCH_BYTES = 128 * 1024 * 1024
CATEGORIES = ("silhouette", "underside", "junction", "detail")


def _label(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise ValueError(f"{name} must be a nonempty string of at most 200 characters")
    validate_no_control_characters(value, name)
    return value


def _keys(value: object) -> list[str]:
    if not isinstance(value, list) or not 1 <= len(value) <= 32:
        raise ValueError("compare_keys must contain 1–32 top-level observed settings keys")
    keys = [_label(key, "Comparison key") for key in value]
    if len(set(keys)) != len(keys):
        raise ValueError("compare_keys must be unique")
    return keys


def _compatibility(before: dict, after: dict, keys: list[str]) -> list[str]:
    issues = []
    for key in keys:
        if key not in before or key not in after or before[key] is None or after[key] is None:
            issues.append(f"Missing observed setting: {key}")
        elif json.dumps(before[key], sort_keys=True) != json.dumps(after[key], sort_keys=True):
            issues.append(f"Different observed setting: {key}")
    if (before["width"], before["height"]) != (after["width"], after["height"]):
        issues.append("Different image dimensions")
    return issues


def review_batch(args: argparse.Namespace) -> dict:
    """Reject incomplete/incompatible inputs before writing any review artifacts."""
    path = Path(os.path.abspath(os.path.expanduser(args.manifest)))
    validate_filesystem_path(str(path), must_exist=True)
    manifest = read_json_file(str(path), max_bytes=1024 * 1024)
    if not isinstance(manifest, dict) or type(manifest.get("version")) is not int or manifest["version"] != 1:
        raise ValueError("Batch manifest requires version: 1")
    json.dumps(manifest, allow_nan=False)
    keys = _keys(manifest.get("compare_keys"))
    sequence = manifest.get("sequence")
    if sequence is not None:
        if (not isinstance(sequence, dict) or set(sequence) != {"fps"}
                or type(sequence["fps"]) not in (int, float) or not math.isfinite(sequence["fps"])
                or not 0 < sequence["fps"] <= 60):
            raise ValueError("sequence requires only fps, greater than zero and at most 60")
    entries = manifest.get("pairs")
    if not isinstance(entries, list) or not 1 <= len(entries) <= 256:
        raise ValueError("Batch manifest requires 1–256 pairs")
    pairs, ids, total_bytes = [], set(), 0
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Each pair must be an object")
        identity = _label(entry.get("id"), "Pair id")
        if identity in ids:
            raise ValueError(f"Duplicate pair id: {identity}")
        ids.add(identity)
        pair = {"id": identity, "label": _label(entry.get("label", identity), "Pair label"),
                "category": _label(entry.get("category", "other"), "View category")}
        for side in ("before", "after"):
            capture = entry.get(side)
            if not isinstance(capture, dict) or not isinstance(capture.get("settings"), dict):
                raise ValueError(f"{identity}: {side} requires image and observed settings object")
            image_path = capture.get("image")
            if not isinstance(image_path, str) or not image_path:
                raise ValueError(f"{identity}: {side} image path is required")
            validate_filesystem_path(image_path)
            pair[side] = _image(str(path.parent / os.path.expanduser(image_path)), capture["settings"])
            total_bytes += len(pair[side]["image"])
            if total_bytes > MAX_BATCH_BYTES:
                raise ValueError("Batch embedded images exceed 128 MiB; split the manifest")
        pair["issues"] = _compatibility(pair["before"]["metadata"], pair["after"]["metadata"], keys)
        if pair["issues"] and not args.allow_incompatible:
            raise ValueError(f"{identity}: {'; '.join(pair['issues'])}; repair metadata/captures or explicitly use --allow-incompatible")
        crops = entry.get("crops", [])
        if not isinstance(crops, list) or len(crops) > 32:
            raise ValueError("Each pair accepts at most 32 named crops")
        names = {"Full image"}
        pair["crops"] = []
        for crop in crops:
            if not isinstance(crop, dict):
                raise ValueError("Each crop requires name and rect")
            name = _label(crop.get("name"), "Crop name")
            rect = crop.get("rect")
            if name in names or not isinstance(rect, list) or len(rect) != 4 or any(type(n) is not int for n in rect):
                raise ValueError("Crops require unique names and integer [x, y, width, height]")
            x, y, width, height = rect
            if min(x, y) < 0 or min(width, height) <= 0 or any(
                x + width > pair[side]["metadata"]["width"] or y + height > pair[side]["metadata"]["height"]
                for side in ("before", "after")
            ):
                raise ValueError(f"{identity}: crop {name} lies outside a capture")
            names.add(name)
            pair["crops"].append({"name": name, "rect": rect})
        pairs.append(pair)
    # Identity binds downloaded review records to the exact images, metadata and crops.
    records = [{**pair, "before": pair["before"]["metadata"], "after": pair["after"]["metadata"]} for pair in pairs]
    identity = hashlib.sha256(json.dumps({"pairs": records, "sequence": sequence}, sort_keys=True, allow_nan=False).encode()).hexdigest()
    result = {"schema_version": 1, "batch_id": identity, "compare_keys": keys, "pairs": records,
              "missing_categories": sorted(set(CATEGORIES) - {pair["category"] for pair in pairs}),
              "visual_correctness": "unverified"}
    if sequence is not None:
        result["sequence"] = sequence
    title = _label(manifest.get("title", "Batch visual review"), "Batch title")
    directory = _directory(args.output, "batch-" + uuid.uuid4().hex[:12])
    viewer = os.path.join(directory, "index.html")
    with os.fdopen(os.open(viewer, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as file:
        file.write(render_batch_viewer({**result, "title": title, "pairs": pairs}))
    result.update(viewer=viewer, directory=directory)
    write_json_atomic(os.path.join(directory, "batch.json"), result, mode=0o600)
    return result
