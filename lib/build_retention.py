"""Explicit root-local terminal reservation settlement; never removes payloads."""

from __future__ import annotations

from contextlib import ExitStack
import hashlib
import os
import re
from typing import Any, BinaryIO

from lib.build_artifacts import MAGIC
from lib.build_inputs import (
    MAX_INTENT_BYTES,
    MAX_RETAINED_METADATA_BYTES,
    BuildInputStore,
    _check_regular,
    _identifier,
    _private_root,
    _READ,
    _SHA256,
)
from lib.build_jobs import MAX_STORE_BYTES, BuildJobStore, WorkerLease
from lib.build_protocol import (
    MAX_DESCRIPTOR_BYTES,
    decode_document,
    encode_document,
    validate_artifact_for_request,
)


MAX_PLAN_BYTES = 16 * 1024
FILE_CHARGE_FLOOR = 4096
_CHUNK = 64 * 1024
_TERMINAL = frozenset({"succeeded", "failed", "cancelled", "expired"})


def _require_root() -> None:
    if os.geteuid() != 0:
        raise PermissionError("Build retention requires root")


def _identity(info: os.stat_result) -> list[int]:
    return [info.st_dev, info.st_ino, info.st_uid, info.st_nlink, info.st_mode,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def _absent(directory: int, name: str) -> None:
    try:
        os.stat(name, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return
    raise ValueError("Build retention has partial or scratch evidence; root reconciliation required")


def _bundle(stream: BinaryIO, request: dict[str, Any]) -> dict[str, Any]:
    """Verify the embedded descriptor and every inventory byte without extraction."""
    def read(size: int) -> bytes:
        content = stream.read(size)
        if len(content) != size:
            raise ValueError("Build retention artifact is truncated")
        return content

    if read(len(MAGIC)) != MAGIC:
        raise ValueError("Build retention artifact format is unsupported")
    length = int.from_bytes(read(8), "big")
    if not 1 <= length <= MAX_DESCRIPTOR_BYTES:
        raise ValueError("Build retention descriptor exceeds its bound")
    raw = read(length)
    descriptor = validate_artifact_for_request(
        decode_document(raw, maximum=MAX_DESCRIPTOR_BYTES), request,
    )
    if encode_document(descriptor, maximum=MAX_DESCRIPTOR_BYTES) != raw:
        raise ValueError("Build retention descriptor is not canonical")
    for entry in descriptor["entries"]:
        if entry["kind"] != "file":
            continue
        digest = hashlib.sha256()
        remaining = entry["size"]
        while remaining:
            chunk = read(min(_CHUNK, remaining))
            digest.update(chunk)
            remaining -= len(chunk)
        if digest.hexdigest() != entry["sha256"]:
            raise ValueError("Build retention artifact inventory digest changed")
    if stream.read(1):
        raise ValueError("Build retention artifact has trailing bytes")
    return {"descriptor_sha256": hashlib.sha256(raw).hexdigest(), "descriptor_bytes": length}


def _payload(directory: int, name: str, *, maximum: int, mode: int,
             request: dict[str, Any] | None = None) -> dict[str, Any] | None:
    try:
        named = os.stat(name, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return None
    _check_regular(named, "retained payload", modes=frozenset({mode}), max_size=maximum)
    descriptor = os.open(name, _READ, dir_fd=directory)
    try:
        before = os.fstat(descriptor)
        if _identity(before) != _identity(named):
            raise ValueError("Build retention payload changed while opening")
        digest = hashlib.sha256()
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(_CHUNK, remaining))
            if not chunk:
                raise ValueError("Build retention payload was truncated")
            digest.update(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise ValueError("Build retention payload grew while reading")
        extra = {}
        if request is not None:
            os.lseek(descriptor, 0, os.SEEK_SET)
            with os.fdopen(os.dup(descriptor), "rb") as stream:
                extra = _bundle(stream, request)
        if (_identity(os.fstat(descriptor)) != _identity(before)
                or _identity(os.stat(name, dir_fd=directory, follow_symlinks=False)) != _identity(before)):
            raise ValueError("Build retention payload changed while verifying")
        return {"identity": _identity(before), "size": before.st_size,
                "sha256": digest.hexdigest(), **extra}
    finally:
        os.close(descriptor)


class BuildRetentionStore:
    """Compose trusted builder stores and protected roots for explicit maintenance.

    Inspect and apply both take worker (nonblocking), input, then short job-state
    locks. No payload I/O runs under the job-state lock. Roots are never created
    or modified. Plans contain bounded identities, never commands or log bytes.
    """

    def __init__(self, *, inputs: BuildInputStore, jobs: BuildJobStore,
                 scratch_root: str, artifacts_root: str, logs_root: str) -> None:
        _require_root()
        self.inputs = inputs
        self.jobs = jobs
        self.roots = {"inputs": inputs.root, "state": jobs.parent, "scratch": scratch_root,
                      "artifacts": artifacts_root, "logs": logs_root}
        for path in self.roots.values():
            with _private_root(path):
                pass
        paths = sorted(self.roots.values())
        if any(a == b or b.startswith(a + "/") for index, a in enumerate(paths)
               for b in paths[index + 1:]):
            raise ValueError("Build retention roots must be distinct and non-overlapping")

    def inspect_retention(self, request_id: str) -> dict[str, Any]:
        """Return a verified bounded plan and revision, without changing charge."""
        return self._operate(request_id, expected_revision=None)

    def settle_terminal(self, request_id: str, *, expected_revision: str) -> dict[str, Any]:
        """Reverify the inspected revision and atomically persist its charge."""
        _require_root()
        revision = _identifier(expected_revision, "Expected revision", _SHA256)
        return self._operate(request_id, expected_revision=revision)

    def _plan(self, request_id: str, record: dict[str, Any], job: dict[str, Any],
              directories: dict[str, int], known_ids: set[str]) -> dict[str, Any]:
        request = job["request"]
        if job["state"] not in _TERMINAL:
            raise ValueError("Build retention requires a certain terminal job")
        if (job["owner"] != record["owner"] or job["request_sha256"] != record["request_sha256"]
                or request["source"]["snapshot_sha256"] != record["source_sha256"]
                or self.inputs._reservation(record["source_size"], request) != record["reservation_bytes"]):
            raise ValueError("Build retention input and job identities do not match")
        _absent(directories["inputs"], request_id + ".part")
        _absent(directories["scratch"], request_id)
        # Temporary publication trees cannot be attributed safely. Fail closed
        # without walking any tree or trying to remove/repair evidence.
        for root, suffix in (("artifacts", "bundle"), ("logs", "log")):
            for name in os.listdir(directories[root]):
                match = re.fullmatch(r"([0-9a-f]{32})\." + suffix, name)
                if match is None or match.group(1) not in known_ids:
                    raise ValueError("Build retention root contains ambiguous evidence")
        limits = request["limits"]
        source = _payload(directories["inputs"], request_id + ".source",
                          maximum=record["source_size"], mode=0o400)
        if (source is None or source["size"] != record["source_size"]
                or source["sha256"] != record["source_sha256"]):
            raise ValueError("Build retention source does not match immutable input identity")
        bundle = _payload(directories["artifacts"], request_id + ".bundle",
                          maximum=limits["artifact_bytes"] + MAX_DESCRIPTOR_BYTES + 1024,
                          mode=0o600, request=request)
        log = _payload(directories["logs"], request_id + ".log",
                       maximum=limits["log_bytes"], mode=0o600)
        if job["state"] == "succeeded":
            artifact = job["artifact"]
            if (bundle is None or log is None or bundle["sha256"] != artifact["sha256"]
                    or bundle["size"] != artifact["size"]
                    or bundle["descriptor_sha256"] != artifact["descriptor_sha256"]):
                raise ValueError("Build retention succeeded artifact identity does not match")
        # Charge the intent and all four shared lock/witness files per request,
        # plus its job metadata and a conservative ledger-envelope allowance.
        # Shared overhead is deliberately overcounted, never amortized away.
        metadata_charge = 5 * FILE_CHARGE_FLOOR + max(
            FILE_CHARGE_FLOOR, len(encode_document(job, maximum=MAX_STORE_BYTES)) + 1024,
        )
        payloads = {"source": source, "bundle": bundle, "log": log}
        charge = metadata_charge + sum(max(FILE_CHARGE_FLOOR, item["size"])
                                       for item in payloads.values() if item is not None)
        if charge > record["reservation_bytes"]:
            raise ValueError("Build retention charge exceeds the original reservation")
        evidence = {
            "schema": 1, "request_id": request_id, "state": job["state"],
            "input": {key: value for key, value in record.items()
                      if key not in {"schema", "charge_bytes", "settlement_revision"}},
            "job_sha256": hashlib.sha256(encode_document(job, maximum=MAX_STORE_BYTES)).hexdigest(),
            "roots": {key: {"path": self.roots[key],
                            "identity": [os.fstat(fd).st_dev, os.fstat(fd).st_ino]}
                      for key, fd in directories.items()},
            "payloads": payloads, "metadata_charge_bytes": metadata_charge,
            "charge_bytes": charge,
        }
        revision = hashlib.sha256(encode_document(evidence, maximum=MAX_PLAN_BYTES)).hexdigest()
        if record["schema"] == 2 and (record["settlement_revision"] != revision
                                      or record["charge_bytes"] != charge):
            raise ValueError("Build retention settled evidence changed; root reconciliation required")
        plan = {"schema": 1, "request_id": request_id, "state": job["state"],
                "revision": revision, "reservation_bytes": record["reservation_bytes"],
                "current_charge_bytes": record.get("charge_bytes", record["reservation_bytes"]),
                "charge_bytes": charge, "metadata_charge_bytes": metadata_charge,
                "payloads": payloads, "settled": record["schema"] == 2}
        encode_document(plan, maximum=MAX_PLAN_BYTES)
        return plan

    def _operate(self, request_id: str, *, expected_revision: str | None) -> dict[str, Any]:
        _require_root()
        request_id = _identifier(request_id, "Request ID")
        with self.jobs.worker() as lease, ExitStack() as stack:
            directories = {key: stack.enter_context(_private_root(path))
                           for key, path in self.roots.items()}
            root_fd = directories["inputs"]
            with self.inputs._locked(root_fd):
                intents, _charged, metadata_bytes = self.inputs._scan_intents(root_fd)
                if request_id not in intents:
                    raise ValueError("Build retention input was not found")
                record, encoded_size, source, partial = intents[request_id]
                if source is None or partial is not None:
                    raise ValueError("Build retention requires a complete published input")
                job = self.jobs.read_for_retention(request_id, lease=lease)
                plan = self._plan(request_id, record, job, directories, set(intents))
                if expected_revision is not None and expected_revision != plan["revision"]:
                    raise ValueError("Build retention plan is stale")
                self._recheck(request_id, plan, job, directories, lease)
                if expected_revision is not None and not plan["settled"]:
                    updated = {**record, "schema": 2, "charge_bytes": plan["charge_bytes"],
                               "settlement_revision": plan["revision"]}
                    encoded = encode_document(updated, maximum=MAX_INTENT_BYTES)
                    if metadata_bytes - encoded_size + len(encoded) > min(
                        MAX_RETAINED_METADATA_BYTES, self.inputs.max_records * MAX_INTENT_BYTES,
                    ):
                        raise ValueError("Build retention exceeds the retained metadata bound")
                    self.inputs._save_settlement(root_fd, updated)
                    plan["settled"] = True
                    plan["current_charge_bytes"] = plan["charge_bytes"]
                return plan

    def _recheck(self, request_id: str, plan: dict[str, Any], job: dict[str, Any],
                 directories: dict[str, int], lease: WorkerLease) -> None:
        if self.jobs.read_for_retention(request_id, lease=lease) != job:
            raise ValueError("Build retention job identity changed")
        for key, directory in directories.items():
            with _private_root(self.roots[key]) as current:
                if ((os.fstat(current).st_dev, os.fstat(current).st_ino)
                        != (os.fstat(directory).st_dev, os.fstat(directory).st_ino)):
                    raise ValueError("Build retention root identity changed")
        _absent(directories["inputs"], request_id + ".part")
        _absent(directories["scratch"], request_id)
        for key, root, suffix in (("source", "inputs", ".source"),
                                  ("bundle", "artifacts", ".bundle"), ("log", "logs", ".log")):
            item = plan["payloads"][key]
            if item is None:
                _absent(directories[root], request_id + suffix)
            else:
                named = os.stat(request_id + suffix, dir_fd=directories[root], follow_symlinks=False)
                if _identity(named) != item["identity"]:
                    raise ValueError("Build retention payload identity changed")
