"""Explicit pinned wait-ring dispositions, never a blanket missing-file waiver."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat

from desktop import conditional_contract, learning_records as records
from desktop.record_bundles import _read
from lib.atomic_io import parse_json
from lib.validation import validate_filesystem_path

RECEIPT_BYTES = 256 * 1024
IDENTITY_KEYS = ("generation", "control_revision", "observation_id")


def _identity(entry):
    value = entry.get("capture_identity")
    records.keys(value, " ".join(IDENTITY_KEYS))
    for key in IDENTITY_KEYS:
        records.text(value[key])
    return tuple(value[key] for key in IDENTITY_KEYS)


def load(bindings):
    """Read at most 16 explicitly hash-pinned receipts; reject invalid bindings.

    Pins are operator-supplied provenance, not independent authenticity attestation.
    Old receipts lacking capture identities cannot justify historical eviction.
    """
    if not isinstance(bindings, list) or len(bindings) > 16:
        raise ValueError("Supply at most 16 retention receipt/hash pairs")
    evictions, pins, seen = {}, [], set()
    for binding in bindings:
        if not isinstance(binding, (list, tuple)) or len(binding) != 2:
            raise ValueError("Retention requires RECEIPT SHA256 pairs")
        name, digest = binding
        records.text(name, 4096)
        records.digest(digest)
        validate_filesystem_path(name, must_exist=True)
        path = Path(name)
        if not path.is_absolute() or path.name != "receipt.json":
            raise ValueError("Retention requires an absolute wait receipt.json path")
        parent = path.parent.lstat()
        if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.getuid() or parent.st_mode & 0o077:
            raise ValueError("Retention bundle must be private and owned")
        content = _read(path, RECEIPT_BYTES, private=True)
        if hashlib.sha256(content).hexdigest() != digest:
            raise ValueError("Retention receipt hash changed")
        if str(path) in seen:
            raise ValueError("Duplicate retention receipt")
        seen.add(str(path))
        receipt = parse_json(content)
        if (not isinstance(receipt, dict) or type(receipt.get("schema_version")) is not int
                or receipt["schema_version"] != 1 or receipt.get("kind") != "conditional-receipt"
                or receipt.get("steps") != []):
            raise ValueError("Expected a capture-only wait receipt")
        plan_bytes = _read(path.parent / "plan.json", 65536, private=True)
        if hashlib.sha256(plan_bytes).hexdigest() != receipt.get("plan_sha256"):
            raise ValueError("Retention plan pin is missing or changed")
        plan = parse_json(plan_bytes)
        conditional_contract.validate(plan)
        if plan["kind"] != "event-wait" or any(receipt.get(key) != plan[source] for key, source in
                (("plan_id", "id"), ("run_id", "run_id"), ("match_id", "match_id"), ("phase", "phase"))):
            raise ValueError("Retention receipt/plan context differs")
        captures = records.sequence(receipt.get("captures"), plan["max_frames"])
        retained, evicted = [], []
        for index, entry in enumerate(captures):
            if (not isinstance(entry, dict) or type(entry.get("index")) is not int or entry["index"] != index
                    or entry.get("frame") != f"capture-{index}.png" or entry.get("metadata") != f"capture-{index}.json"):
                raise ValueError("Unsafe or conflicting wait capture reference")
            disposition = entry.get("retention")
            if disposition not in ("retained", "evicted-ring", "issue-retained", "eviction-incomplete", "pending"):
                raise ValueError("Unknown wait retention disposition")
            if disposition not in ("retained", "evicted-ring"):
                continue  # Incomplete/failed captures never justify a missing PNG.
            records.bounded(entry)
            records.digest(entry.get("sha256"))
            identity = _identity(entry)
            if disposition == "retained":
                if len(retained) >= plan["retain_frames"]:
                    raise ValueError("Wait retention exceeds its frame limit")
                image = _read(path.parent / entry["frame"], plan["limits"]["frame_bytes"], private=True)
                if hashlib.sha256(image).hexdigest() != entry["sha256"]:
                    raise ValueError("Retained wait replacement hash changed")
                retained.append(index)
            else:
                if entry.get("evicted_artifacts") != [entry["frame"], entry["metadata"]]:
                    raise ValueError("Incomplete wait eviction cannot justify a missing image")
                for key in ("frame", "metadata"):
                    if (path.parent / entry[key]).exists() or (path.parent / entry[key]).is_symlink():
                        raise ValueError("Declared wait eviction still has an artifact")
                evicted.append((index, str(path.parent / entry["frame"]), identity, entry["sha256"]))
        if not retained or len(retained) > plan["retain_frames"]:
            raise ValueError("Wait retention requires its retained replacement frames")
        for index, filename, identity, frame_hash in evicted:
            if index >= min(retained):
                raise ValueError("Wait eviction lacks a later retained replacement")
            if filename in evictions:
                raise ValueError("Conflicting or duplicate wait eviction")
            evictions[filename] = (identity, frame_hash)
        pins.append({"artifact": str(path), "sha256": digest, "expected_evictions": len(evicted)})
    return evictions, pins


def matches(evictions, result):
    """Require exact path, hash and helper capture identity in the recorder."""
    declaration = evictions.get(result.get("output"))
    return declaration is not None and declaration == (
        tuple(result.get(key) for key in IDENTITY_KEYS), result.get("artifact_sha256"))
