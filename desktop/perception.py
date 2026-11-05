"""Optional read-only fixed-region recognizer; scores are not probabilities."""

from __future__ import annotations

import copy
import hashlib
import time

from desktop import learning_records as contract, png_regions
from desktop.record_bundles import _frame, _read
from lib.atomic_io import parse_json

DETECTOR = "region-mae-v1"


def validate_profile(profile):
    contract.bounded(profile)
    contract.keys(profile, "schema_version kind id version detector conditions geometry image_origin image_geometry fields")
    if type(profile["schema_version"]) is not int or profile["schema_version"] != 1 or profile["kind"] != "region-profile" or profile["detector"] != DETECTOR:
        raise ValueError("Unsupported perception profile/version/detector")
    for key in ("id", "version"):
        contract.text(profile[key])
    contract.keys(profile["conditions"], "game build profile recognizer")
    for value in profile["conditions"].values():
        contract.text(value)
    for key in ("geometry", "image_origin", "image_geometry"):
        value = profile[key]
        if not isinstance(value, list) or len(value) != 2:
            raise ValueError("Profile requires geometry/origin pairs")
        for number in value:
            contract.integer(number, 0 if key == "image_origin" else 1, 16384)
    if any(profile["image_origin"][i] + profile["image_geometry"][i] > profile["geometry"][i] for i in (0, 1)):
        raise ValueError("Profile image outside geometry")
    if profile["image_geometry"][0] * profile["image_geometry"][1] > png_regions.MAX_PIXELS:
        raise ValueError("Profile exceeds decoder pixel limit")
    fields, templates, pixels = set(), {}, 0
    for field in contract.sequence(profile["fields"], 4):
        contract.keys(field, "name rectangle max_error min_margin action_risk templates")
        contract.text(field["name"])
        contract.text(field["action_risk"])
        if field["name"] in fields:
            raise ValueError("Duplicate perception field")
        fields.add(field["name"])
        rectangle = field["rectangle"]
        if not isinstance(rectangle, list) or len(rectangle) != 4:
            raise ValueError("Expected profile rectangle")
        for index, value in enumerate(rectangle):
            contract.integer(value, 0 if index < 2 else 1, 16384)
        if rectangle[0] + rectangle[2] > profile["image_geometry"][0] or rectangle[1] + rectangle[3] > profile["image_geometry"][1]:
            raise ValueError("Profile rectangle outside image")
        pixels += rectangle[2] * rectangle[3]
        for key in ("max_error", "min_margin"):
            if field[key] is None:
                raise ValueError("Profile thresholds must be explicit")
            contract.nullable_number(field[key], 1)
        if field["min_margin"] == 0:
            raise ValueError("A positive ambiguity margin is required")
        labels = set()
        for template in contract.sequence(field["templates"], 4):
            contract.keys(template, "id label sha256 bytes")
            for key in ("id", "label"):
                contract.text(template[key])
            contract.digest(template["sha256"])
            contract.integer(template["bytes"], 1, 1024 * 1024)
            if template["id"] in templates or template["label"] in labels:
                raise ValueError("Duplicate template ID/field label")
            labels.add(template["label"])
            templates[template["id"]] = (template, rectangle[2:])
        if not labels:
            raise ValueError("Field requires templates")
    if not fields or pixels > 65536:
        raise ValueError("Require 1–4 fields with at most 65536 combined region pixels")
    return templates


def load_profile(source, paths):
    content = _read(source, 65536)
    profile = parse_json(content)
    declared = validate_profile(profile)
    if set(paths) != set(declared):
        raise ValueError("Supply exactly the declared template paths")
    templates = {key: _read(paths[key], row[0]["bytes"]) for key, row in declared.items()}
    validate_templates(profile, templates)
    return content, profile, templates


def validate_templates(profile, templates):
    declarations = validate_profile(profile)
    if set(templates) != set(declarations):
        raise ValueError("Template set differs from profile")
    decoded = {}
    for key, (declaration, geometry) in declarations.items():
        content = templates[key]
        if len(content) != declaration["bytes"] or hashlib.sha256(content).hexdigest() != declaration["sha256"]:
            raise ValueError("Template hash/size mismatch")
        image = png_regions.decode(content)
        if list(image[:2]) != geometry:
            raise ValueError("Template geometry differs from declared region")
        decoded[key] = image[2]
    return decoded


def recognize(packet, frame_id, content, profile, templates):
    """Consume canonical pinned packet/frame bytes; emit fields, never authority."""
    started = time.perf_counter()
    contract.validate({"schema_version": 1, "records": [packet]})
    decoded_templates = validate_templates(profile, templates)
    frames = {frame["id"]: frame for frame in packet["frames"]}
    if frame_id not in frames:
        raise ValueError("Frame does not belong to observation packet")
    frame = frames[frame_id]
    _frame(content, frame)
    drift = (profile["geometry"] != frame["geometry"] or profile["image_origin"] != frame["origin"]
             or profile["image_geometry"] != [frame["width"], frame["height"]]
             or any(packet["conditions"][key] != value for key, value in profile["conditions"].items()))
    image = None if drift else png_regions.decode(content)
    fields, crops = [], {}
    for field in profile["fields"]:
        scores, label, reason = [], None, "profile-drift" if drift else "no-match"
        if image is not None:
            pixels = png_regions.region(image, field["rectangle"])
            crops[field["name"]] = png_regions.encode(*field["rectangle"][2:], pixels)
            for template in field["templates"]:
                error = sum(abs(left - right) for left, right in zip(pixels, decoded_templates[template["id"]])) / (len(pixels) * 255)
                scores.append({"label": template["label"], "error": error})
            scores.sort(key=lambda row: (row["error"], row["label"]))
            if scores[0]["error"] <= field["max_error"]:
                if len(scores) > 1 and scores[1]["error"] - scores[0]["error"] < field["min_margin"]:
                    reason = "ambiguous"
                else:
                    label, reason = scores[0]["label"], "matched"
        fields.append({"name": field["name"], "status": "observed" if label is not None else "unknown",
                       "value": label, "sources": [] if drift else [{"frame_id": frame_id, "rectangle": list(field["rectangle"])}],
                       "reason": reason, "scores": scores})
    return {"schema_version": 1, "kind": "region-recognition", "packet_id": packet["id"],
            "state_revision": packet["state_revision"], "frame_id": frame_id, "source_sha256": frame["sha256"],
            "detector": DETECTOR, "profile_id": profile["id"], "profile_version": profile["version"],
            "settings": copy.deepcopy(profile), "elapsed_seconds": time.perf_counter() - started,
            "score_semantics": "mean absolute RGB channel error / 255; lower is closer, not probability",
            "drift": drift, "fields": fields, "input_authority": False}, crops


def field_delta(previous, current):
    """Compare field evidence without treating pixel motion as semantic progress."""
    for result in (previous, current):
        if not isinstance(result, dict) or result.get("kind") != "region-recognition":
            raise ValueError("Delta requires recognition results")
        contract.bounded(result)
        if type(result.get("schema_version")) is not int or result["schema_version"] != 1 or type(result.get("drift")) is not bool:
            raise ValueError("Delta requires version-one results with explicit drift")
        profile = result.get("settings")
        validate_profile(profile)
        expected = {field["name"] for field in profile["fields"]}
        names = set()
        for field in contract.sequence(result.get("fields"), 4):
            if not isinstance(field, dict) or not {"name", "status", "value", "sources"} <= field.keys():
                raise ValueError("Delta requires canonical field evidence")
            contract.text(field["name"])
            if field["name"] in names:
                raise ValueError("Duplicate delta field")
            names.add(field["name"])
            if field["status"] not in ("observed", "unknown") or (field["value"] is None) != (field["status"] == "unknown"):
                raise ValueError("Invalid recognition field status/value")
            contract.sequence(field["sources"], 8)
        if names != expected:
            raise ValueError("Delta field set differs from profile")
    comparable = (not previous.get("drift") and not current.get("drift")
                  and previous.get("detector") == current.get("detector") == DETECTOR
                  and previous.get("settings") == current.get("settings"))
    if not comparable:
        return {"comparable": False, "changes": [], "reason": "profile-or-geometry-drift", "input_authority": False}
    before = {field["name"]: field for field in previous["fields"]}
    changes = []
    for field in current["fields"]:
        prior = before[field["name"]]
        if (prior["status"], prior["value"]) != (field["status"], field["value"]):
            changes.append({"name": field["name"], "before": prior["value"],
                            "after": field["value"], "status": field["status"], "sources": copy.deepcopy(field["sources"])})
    return {"comparable": True, "changes": changes, "semantic_progress": "not-asserted", "input_authority": False}
