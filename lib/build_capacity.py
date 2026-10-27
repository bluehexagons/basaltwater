"""Root-local, noninitializing observations of builder admission capacity."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import ExitStack
import hashlib
import os
from typing import Any

from lib.build_inputs import (
    MAX_PRUNING_INTENT_BYTES,
    MAX_RETAINED_METADATA_BYTES,
    BuildInputStore,
    _private_root,
)
from lib.build_jobs import MAX_OPERATIONS, MAX_STORE_BYTES, BuildJobStore
from lib.build_protocol import encode_document


MAX_REPORT_BYTES = 64 * 1024
_ROOTS = ("inputs", "state", "scratch", "artifacts", "logs")
_CERTAIN_TERMINAL = frozenset({"succeeded", "failed", "cancelled", "expired"})


def _require_root() -> None:
    if os.geteuid() != 0:
        raise PermissionError("Build capacity inspection requires root")


def _filesystem_observations(directories: dict[str, int]) -> list[dict[str, Any]]:
    groups: dict[int, dict[str, Any]] = {}
    for name, descriptor in directories.items():
        device = os.fstat(descriptor).st_dev
        group = groups.setdefault(device, {"roots": [], "available_bytes": None,
                                            "observations": []})
        group["roots"].append(name)
        try:
            observed = os.fstatvfs(descriptor)
            if (type(observed.f_bavail) is not int or type(observed.f_frsize) is not int
                    or observed.f_bavail < 0 or observed.f_frsize <= 0
                    or observed.f_bavail * observed.f_frsize > 2**127 - 1):
                raise ValueError("Invalid filesystem observation")
            available = observed.f_bavail * observed.f_frsize
        except (OSError, ValueError):
            group["observations"].append("available_bytes_unavailable")
            continue
        previous = group["available_bytes"]
        if previous is not None and previous != available:
            group["observations"].append("available_bytes_changed_between_roots")
        group["available_bytes"] = available if previous is None else min(previous, available)
    for group in groups.values():
        group["observations"] = sorted(set(group["observations"]))
        # One failed root observation makes the shared filesystem observation
        # incomplete; do not present another root's value as a complete result.
        if "available_bytes_unavailable" in group["observations"]:
            group["available_bytes"] = None
    return list(groups.values())


def inspect_build_capacity(config: Mapping[str, Any]) -> dict[str, Any]:
    """Return a detached bounded report from trusted ``load_config()`` output.

    Only ``roots``, ``max_records`` and ``max_bytes`` are consumed; the mapping
    is never changed. Root authority and protected existing roots are required.
    Existing input then short job locks are acquired nonblocking. All metadata
    reads bypass initialization, expiry, settlement durability repair and saves.
    Invalid/missing/ambiguous evidence raises a fixed safe error. Filesystem
    observation failure is reported separately. No payload is opened or hashed.
    This is an observation of persisted charges, never an admission decision,
    physical reservation, payload verification or durability acknowledgement.
    """
    _require_root()
    try:
        return _inspect(config)
    except RuntimeError:
        raise RuntimeError("Build capacity accounting is busy") from None
    except (OSError, ValueError, KeyError, TypeError):
        raise ValueError("Build capacity evidence is unavailable or inconsistent") from None


def _inspect(config: Mapping[str, Any]) -> dict[str, Any]:
    roots = config["roots"]
    if not isinstance(roots, Mapping) or set(roots) != set(_ROOTS):
        raise ValueError("Invalid capacity roots")
    if any(not isinstance(path, str) for path in roots.values()):
        raise ValueError("Invalid capacity root path")
    paths = sorted(roots.values())
    if any(a == b or b.startswith(a + "/") for index, a in enumerate(paths)
           for b in paths[index + 1:]):
        raise ValueError("Overlapping capacity roots")
    with ExitStack() as stack:
        directories = {name: stack.enter_context(_private_root(roots[name])) for name in _ROOTS}
        identities = {name: (os.fstat(fd).st_dev, os.fstat(fd).st_ino)
                      for name, fd in directories.items()}
        if len(set(identities.values())) != len(_ROOTS):
            raise ValueError("Aliased capacity roots")
        inputs = BuildInputStore(roots["inputs"], max_records=config["max_records"],
                                 max_bytes=config["max_bytes"])
        jobs = BuildJobStore(os.path.join(roots["state"], "jobs.json"))
        with inputs.capacity_snapshot() as (intents, charged, metadata_bytes):
            state, job_bytes = jobs.capacity_snapshot()
            records = state["jobs"]
            if set(records) - set(intents):
                raise ValueError("Build jobs lack retained input evidence")
            counts = {"unsettled": 0, "settled": 0, "pruned": 0, "pending_pruning": 0,
                      "inputs_without_jobs": 0, "incomplete_inputs": 0}
            states = {name: 0 for name in ("queued", "running", "succeeded", "failed",
                                          "cancelled", "expired", "uncertain")}
            original = 0
            for identifier, (record, _size, source, partial) in intents.items():
                original += record["reservation_bytes"]
                schema = record["schema"]
                category = "unsettled" if schema == 1 else "settled"
                if schema in {3, 4}:
                    category = ("pending_pruning" if any(op["state"] == "pending"
                                for op in record["pruning"]["operations"]) else "pruned")
                counts[category] += 1
                if schema not in {3, 4} and (source is None or partial is not None):
                    counts["incomplete_inputs"] += 1
                job = records.get(identifier)
                if job is None:
                    if schema != 1:
                        raise ValueError("Settled inputs lack established job evidence")
                    counts["inputs_without_jobs"] += 1
                    continue
                request = job["request"]
                if (job["owner"] != record["owner"]
                        or job["request_sha256"] != record["request_sha256"]
                        or request["source"]["snapshot_sha256"] != record["source_sha256"]
                        or inputs._reservation(record["source_size"], request) != record["reservation_bytes"]):
                    raise ValueError("Build input and job identities differ")
                if schema not in {3, 4} and (source is None or partial is not None):
                    raise ValueError("Submitted input lacks established source evidence")
                if schema != 1 and job["state"] not in _CERTAIN_TERMINAL:
                    raise ValueError("Settled input has inconsistent job evidence")
                if schema == 4 and job["state"] != "succeeded":
                    raise ValueError("Bundle release lacks successful job evidence")
                if schema in {3, 4}:
                    digest = hashlib.sha256(encode_document(job, maximum=MAX_STORE_BYTES)).hexdigest()
                    if digest != record["pruning"]["job_sha256"]:
                        raise ValueError("Pruning job identity differs")
                states[job["state"]] += 1
            metadata_ceiling = min(MAX_RETAINED_METADATA_BYTES,
                                   inputs.max_records * MAX_PRUNING_INTENT_BYTES)
            report = {
                "schema": 1,
                "inputs": {
                    "original_reservation_bytes": original, "current_charge_bytes": charged,
                    "byte_ceiling": inputs.max_bytes, "byte_headroom": inputs.max_bytes - charged,
                    "retained_slots": len(intents), "slot_ceiling": inputs.max_records,
                    "slot_headroom": inputs.max_records - len(intents),
                    "metadata_bytes": metadata_bytes, "metadata_byte_ceiling": metadata_ceiling,
                    "metadata_byte_headroom": metadata_ceiling - metadata_bytes, **counts,
                },
                "jobs": {
                    "replay_records": len(records), "record_ceiling": MAX_OPERATIONS,
                    "record_headroom": MAX_OPERATIONS - len(records),
                    "metadata_bytes": job_bytes, "metadata_byte_ceiling": MAX_STORE_BYTES,
                    "metadata_byte_headroom": MAX_STORE_BYTES - job_bytes,
                    "draining": state["draining"], "states": states,
                },
                "filesystems": _filesystem_observations(directories),
                "observations": [
                    "persisted_charges_only_no_durability_reconciliation",
                    "payload_contents_and_packaging_temporary_trees_unverified",
                    "filesystem_availability_is_separate_from_logical_headroom",
                    "packaging_peak_includes_export_snapshot_and_bundle",
                    "report_is_not_a_reservation_or_readiness_decision",
                ],
            }
            # Re-open protected paths to detect replacement of any configured
            # root, including those independently opened by the store readers.
            for name in _ROOTS:
                with _private_root(roots[name]) as fd:
                    if (os.fstat(fd).st_dev, os.fstat(fd).st_ino) != identities[name]:
                        raise ValueError("Build root changed during capacity inspection")
            encode_document(report, maximum=MAX_REPORT_BYTES)
            return report
