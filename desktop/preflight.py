"""Read-only desktop discovery and bounded, retrievable instruction ranges."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat

from desktop.qa_artifacts import new_bundle, stamp
from lib.atomic_io import write_json_atomic
from lib.validation import validate_filesystem_path


def instruction_page(path: str, *, start: int = 0, characters: int = 4000, sha256: str | None = None) -> dict:
    """Return character ranges from stable UTF-8 bytes, without claiming reading."""
    validate_filesystem_path(path, must_exist=True)
    if type(start) is not int or start < 0 or type(characters) is not int or not 0 <= characters <= 4000:
        raise ValueError("Instruction range requires start >= 0 and 0–4000 characters")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as source:
        before = os.fstat(source.fileno())
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= 1024 * 1024:
            raise ValueError("Instructions must be a regular UTF-8 file of 1 byte to 1 MiB")
        content = source.read(1024 * 1024 + 1)
        after = os.fstat(source.fileno())
    if (len(content) != before.st_size or
            (before.st_mtime_ns, before.st_ctime_ns) != (after.st_mtime_ns, after.st_ctime_ns)):
        raise ValueError("Instructions changed during read; retrieve a new manifest")
    digest = hashlib.sha256(content).hexdigest()
    if sha256 is not None and sha256 != digest:
        raise ValueError("Instruction digest changed; retrieve a new manifest and review changed instructions")
    text = content.decode("utf-8")
    if start > len(text):
        raise ValueError("Instruction start is beyond the file")
    end = min(len(text), start + characters)
    unread = [pair for pair in ([0, start], [end, len(text)]) if pair[0] < pair[1]]
    return {"path": str(Path(path).absolute()), "sha256": digest, "total_characters": len(text),
            "range": [start, end], "text": text[start:end], "unread_ranges": unread,
            "range_convention": "zero-based Unicode characters, end exclusive; ranges apply to this response only",
            "next_argv": (["basaltw", "desktop", "instructions", str(Path(path).absolute()),
                           "--start", str(end), "--sha256", digest] if end < len(text) else None),
            "model_comprehension": "unknown"}


def preflight(repository: str, *, backend, native: bool, managed_source: str | None = None,
              instructions: list[str] | None = None, output: str | None = None) -> dict:
    """Summarize existing observations; never capture, start or alter permission."""
    from lib.agent_environment import inspect_environment, scoped_environment

    paths = instructions or []
    if len(paths) > 16:
        raise ValueError("Select at most 16 relevant instruction files")
    checks, raw = {}, {"created": stamp()}
    try:
        manifest = inspect_environment(repository, installation_context=True, managed_source=managed_source)
        raw["manifest"] = scoped_environment(manifest, ["installation", "desktop", "skills"])
        checks["manifest"] = {"state": "observed"}
    except (OSError, ValueError, RuntimeError) as exc:
        manifest = {}
        raw["manifest_error"] = str(exc)
        checks["manifest"] = {"state": "unavailable", "detail": str(exc)[:300]}
    context = manifest.get("installation_context") or {}
    for name in ("invoked_managed", "managed_native_worker", "invoked_native_worker"):
        drift = (context.get("drift") or {}).get(name) if native or name == "invoked_managed" else None
        checks[name] = {"state": "mismatched" if drift is True else "observed" if drift is False else "unavailable",
                        "detail": "Clean source comparison" if drift is False else "Source differs" if drift is True else
                        "No comparable source/startup evidence; on-disk HEAD cannot establish running code"}
    skills = (manifest.get("provenance") or {}).get("delivered_skills", [])
    checks["delivered_skills"] = {"state": "mismatched" if any(s.get("drift") is True for s in skills) else
        "observed" if skills and all(s.get("drift") is False for s in skills) else "unavailable",
        "count": len(skills), "unknown_count": sum(s.get("drift") is None for s in skills)}
    try:
        current = backend.status()
        raw["desktop_status"] = current
        checks["helper"] = {"state": "observed", "value": current.get("state")}
        checks["human_control"] = {"state": "observed" if current.get("agent_control") else "unavailable",
                                   "value": current.get("agent_control")}
        checks["capture"] = {"state": "observed" if current.get("state") == "running" and current.get("observation_id") else "unavailable",
                             "freshness": current.get("capture_freshness"), "observation_id": current.get("observation_id")}
        if native:
            from lib.installation_info import _source_drift
            startup = current.get("worker_provenance") or {}
            source = (startup.get("source") if isinstance(startup, dict)
                      and type(startup.get("schema_version")) is int and startup["schema_version"] == 1
                      and startup.get("evidence_source") in ("running-git", "refresh-receipt", "setup-receipt", "unrecorded") else None)
            for name, left in (("managed_native_worker", context.get("managed")),
                               ("invoked_native_worker", context.get("invoked"))):
                drift = _source_drift(left, source)
                checks[name] = {"state": "mismatched" if drift is True else "observed" if drift is False else "unavailable",
                                "detail": "Worker startup source comparison; loaded code is not immutable attestation"}
        if native and (current.get("state") != "running" or current.get("helper_running") is not True):
            for name in ("managed_native_worker", "invoked_native_worker"):
                checks[name] = {"state": "unavailable", "detail": "Worker is not running; retained startup receipt is historical"}
    except (OSError, ValueError, RuntimeError) as exc:
        raw["desktop_error"] = str(exc)
        for name in ("helper", "human_control", "capture", "managed_native_worker", "invoked_native_worker"):
            checks[name] = {"state": "unavailable", "detail": str(exc)[:300]}
    raw["instructions"] = []
    for path in paths:
        try:
            raw["instructions"].append({"state": "observed", **instruction_page(path, characters=0)})
        except (OSError, ValueError, RuntimeError) as exc:
            raw["instructions"].append({"path": path, "state": "unavailable", "detail": str(exc)[:300]})
    for name in ("scene_and_focus", "input_effect", "model_comprehension", "session_loaded_skills"):
        checks[name] = {"state": "not-attempted", "detail": "Requires independent controller/provider evidence"}
    folder = new_bundle(output)
    write_json_atomic(str(folder / "preflight.json"), {**raw, "checks": checks}, mode=0o600)
    return {"artifact": str(folder / "preflight.json"), "checks": checks,
            "instructions": raw["instructions"], "aggregate_readiness": "not-asserted",
            "limit": "Point-in-time discovery only; no capture/input, instruction delivery or live qualification performed"}
