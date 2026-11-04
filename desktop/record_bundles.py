"""Private pinned storage and offline advice evaluation for learning records."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
import struct

from desktop import learning_records as contract
from desktop.qa_artifacts import clock, new_bundle, stamp
from lib.atomic_io import parse_json, write_bytes_atomic, write_json_atomic
from lib.validation import validate_filesystem_path


def _read(filename, limit, *, private=False):
    validate_filesystem_path(str(filename), must_exist=True)
    path = Path(filename).absolute()
    if path != path.resolve():
        raise ValueError("Record inputs must not traverse symlinks or parent components")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as source:
        before = os.fstat(source.fileno())
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= limit:
            raise ValueError(f"Expected regular input of 1–{limit} bytes")
        if private and (before.st_uid != os.getuid() or before.st_mode & 0o077):
            raise ValueError("Bundle files must be private and owned")
        content = source.read(limit + 1)
        after = os.fstat(source.fileno())
    if len(content) != before.st_size or (before.st_mtime_ns, before.st_ctime_ns) != (after.st_mtime_ns, after.st_ctime_ns):
        raise ValueError("Input changed during read")
    return content


def _pin(content, name):
    return {"artifact": name, "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}


def _frame(content, declaration):
    pin = _pin(content, "unused")
    if pin["sha256"] != declaration["sha256"] or pin["bytes"] != declaration["bytes"]:
        raise ValueError("Frame bytes do not match declared hash/size")
    if len(content) < 33 or content[:16] != b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR":
        raise ValueError("Frame requires PNG IHDR")
    if list(struct.unpack(">II", content[16:24])) != [declaration["width"], declaration["height"]]:
        raise ValueError("PNG geometry differs from frame declaration")


def create(source, media, output=None):
    """Copy exact bytes to a new bundle; commit manifest last, never edit a bundle."""
    content = _read(source, contract.MAX_BYTES)
    document = parse_json(content)
    frames = contract.validate(document)
    if set(media) != set(frames):
        raise ValueError("Supply exactly one media path for every declared frame ID")
    folder = new_bundle(output)
    try:
        write_bytes_atomic(str(folder / "records.json"), content)
        pinned = {}
        for index, (identifier, declaration) in enumerate(frames.items()):
            data = _read(media[identifier], declaration["bytes"])
            _frame(data, declaration)
            name = f"frame-{index}.png"
            write_bytes_atomic(str(folder / name), data)
            pinned[identifier] = _pin(data, name)
        manifest = {"schema_version": 1, "kind": "learning-record-bundle", "created": stamp(),
                    "clock": clock(), "source": _pin(content, "records.json"), "frames": pinned}
        write_json_atomic(str(folder / "manifest.json"), manifest)
    except (OSError, ValueError, RuntimeError) as exc:
        # Keep uncertain output for inspection; never retry into the same path.
        error = ValueError(str(exc))
        error.receipt = {"error": str(exc), "validation": "invalid", "partial": True,
                         "bundle": str(folder), "input_authority": False}
        raise error from exc
    return {"artifact": str(folder / "manifest.json"), "validation": "valid", "partial": False,
            "schema_version": 1, "records": len(document["records"]), "input_authority": False}


def validate(artifact, *, current_state_revision=None, at=None):
    """Verify pinned files and report freshness using explicitly supplied context."""
    path = Path(artifact).absolute()
    if path.name != "manifest.json":
        raise ValueError("Expected bundle manifest.json")
    parent = path.parent.stat()
    if parent.st_uid != os.getuid() or parent.st_mode & 0o077:
        raise ValueError("Bundle directory must be private and owned")
    manifest = parse_json(_read(path, 65536, private=True))
    contract.keys(manifest, "schema_version kind created clock source frames")
    if type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1 or manifest["kind"] != "learning-record-bundle":
        raise ValueError("Unsupported bundle version/kind")
    contract.keys(manifest["created"], "at monotonic_ns")
    contract.utc(manifest["created"]["at"])
    contract.integer(manifest["created"]["monotonic_ns"])
    contract.keys(manifest["clock"], "boot_id monotonic_scope")
    contract.text(manifest["clock"]["monotonic_scope"])
    if manifest["clock"]["boot_id"] is not None:
        contract.text(manifest["clock"]["boot_id"])

    def pinned(pin, expected, limit):
        contract.keys(pin, "artifact sha256 bytes")
        if pin["artifact"] != expected:
            raise ValueError("Unsafe or unexpected artifact name")
        contract.digest(pin["sha256"])
        contract.integer(pin["bytes"], 1, limit)
        content = _read(path.parent / expected, limit, private=True)
        if pin != _pin(content, expected):
            raise ValueError("Pinned artifact hash/size changed")
        return content

    document = parse_json(pinned(manifest["source"], "records.json", contract.MAX_BYTES))
    frames = contract.validate(document)
    if not isinstance(manifest["frames"], dict) or set(manifest["frames"]) != set(frames):
        raise ValueError("Manifest frame set differs from packets")
    for index, (identifier, declaration) in enumerate(frames.items()):
        _frame(pinned(manifest["frames"][identifier], f"frame-{index}.png", declaration["bytes"]), declaration)
    if (at is None) != (current_state_revision is None):
        raise ValueError("Supply --at and --current-state together; use 'unknown' for unknown state")
    replies = []
    by_id = {row["id"]: row for row in document["records"]}
    if at is not None:
        contract.utc(at)
        revision = None if current_state_revision == "unknown" else current_state_revision
        for row in document["records"]:
            if row["kind"] == "coach-reply":
                status = contract.advice_status(row, by_id[row["request_id"]], revision, at)
                replies.append({"reply_id": row["id"], "validation": status, "controller_disposition": "not-evaluated"})
    return {"artifact": str(path), "schema_version": 1, "validation": "valid", "partial": False,
            "records": len(by_id), "advice": replies, "freshness": "evaluated" if at else "not-evaluated",
            "input_authority": False}
