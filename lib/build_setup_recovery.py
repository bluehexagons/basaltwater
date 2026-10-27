"""Bounded offline projection of one unfinished controller build setup.

Use ``python3 -B -m lib.build_setup_recovery --journal /absolute/path/setup.json``.
The startup flag is required to prevent package imports from writing bytecode.
Ordinary imports retain Python's normal cache behavior. No state accessor,
transport, source snapshot or artifact is opened by this inspector.
"""

from __future__ import annotations

from contextlib import ExitStack
from datetime import datetime, timedelta
import fcntl
import hashlib
import os
import re
import stat
import sys
from typing import Any
import uuid

from lib.build_admission import MAX_OPERATIONS, MAX_TTL_SECONDS
from lib.build_protocol import (
    MAX_DESCRIPTOR_BYTES, decode_document, encode_document, parse_build_request, request_digest,
)
from lib.build_target_policy import parse_target_deployment
from lib.operation_state import _record_from_dict
from lib.validation import validate_filesystem_path
from lib.validators import validate_host


MAX_REPORT_BYTES = 64 * 1024
MAX_PREPARED_BYTES = 32 * 1024**2
MAX_STATE_BYTES = 1024 * 1024
MAX_APPLICATIONS = 16
_READ = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
_DIRECTORY = _READ | os.O_DIRECTORY
_SETUP_PHASES = {"bootstrapping", "delivering", "profile_applying", "recovery_required"}
_APP_PHASES = (
    "prepared", "reserving", "reserved", "submitting", "building", "downloading",
    "verifying", "staging", "staged", "activating", "completed",
)
_BASE_ENTRY = {"application", "project", "request_id", "idempotency_key", "request_sha256",
               "source", "deployment_sha256", "status"}
_ADMISSION = {"operation_id", "generation", "bindings_sha256"}
_ARTIFACT = {"artifact_sha256", "artifact_size"}


class BuildSetupInspectionError(ValueError):
    """Fixed, safe refusal; no evidence values or filesystem paths in errors."""

    def __init__(self, code: str = "invalid_evidence") -> None:
        self.code = code
        super().__init__("Build setup inspection refused: " + code)


def _require(condition: object) -> None:
    if not condition:
        raise BuildSetupInspectionError()


def _fields(value: object, required: set[str], optional: set[str] | None = None) -> dict[str, Any]:
    _require(isinstance(value, dict) and required <= set(value) <= required | (optional or set()))
    return value


def _hex(value: object, length: int = 64) -> str:
    _require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{%d}" % length, value) is not None)
    return value


def _path(value: object) -> str:
    _require(isinstance(value, str) and 1 <= len(value) <= 4096)
    validate_filesystem_path(value)
    _require(value.startswith("/") and not value.startswith("//") and os.path.normpath(value) == value
             and len(value.split("/")) <= 128)
    return value


def _timestamp(value: object) -> None:
    _require(isinstance(value, str) and len(value) <= 64)
    parsed = datetime.fromisoformat(value)
    _require(parsed.utcoffset() == timedelta(0) and parsed.isoformat() == value)


def _identity(info: os.stat_result) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


class _Evidence:
    """Small fixed descriptor graph; retain ancestors and recheck every edge."""

    def __init__(self, stack: ExitStack) -> None:
        self.stack = stack
        self.opened: list[tuple[int, int | None, str, tuple[int, ...]]] = []
        self.absent: list[tuple[int, str]] = []

    def open(self, parent: int | None, name: str, *, directory: bool = False,
             optional: bool = False, private: bool = True, maximum: int = MAX_STATE_BYTES) -> int | None:
        try:
            descriptor = os.open(name, _DIRECTORY if directory else _READ, dir_fd=parent)
        except FileNotFoundError:
            if not optional:
                raise
            _require(parent is not None)
            self.absent.append((parent, name))
            return None
        self.stack.callback(os.close, descriptor)
        info = os.fstat(descriptor)
        if directory:
            _require(stat.S_ISDIR(info.st_mode) and info.st_uid in {0, os.geteuid()})
            if private:
                _require(info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o700)
        else:
            _require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
                     and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1
                     and 0 <= info.st_size <= maximum)
        self.opened.append((descriptor, parent, name, _identity(info)))
        return descriptor

    def root(self, path: str) -> int:
        descriptor = self.open(None, "/", directory=True, private=False)
        for part in path.split("/")[1:]:
            previous = os.fstat(descriptor)
            # Trusted sticky parents protect owned children (e.g. /tmp).
            _require(not previous.st_mode & 0o022 or previous.st_mode & stat.S_ISVTX)
            descriptor = self.open(descriptor, part, directory=True, private=False)
        info = os.fstat(descriptor)
        _require(info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o700)
        return descriptor

    def lock(self, parent: int, name: str) -> None:
        descriptor = self.open(parent, name, maximum=0)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BuildSetupInspectionError("busy") from None

    def json(self, parent: int, name: str, *, maximum: int = MAX_STATE_BYTES,
             optional: bool = False) -> Any:
        descriptor = self.open(parent, name, maximum=maximum, optional=optional)
        if descriptor is None:
            return None
        size = os.fstat(descriptor).st_size
        _require(size > 0)
        chunks = []
        remaining = size
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            _require(chunk)
            chunks.append(chunk)
            remaining -= len(chunk)
        _require(not os.read(descriptor, 1))
        value = decode_document(b"".join(chunks), maximum=maximum)
        _require(isinstance(value, dict))
        return value

    def verify(self) -> None:
        for descriptor, parent, name, identity in self.opened:
            _require(_identity(os.fstat(descriptor)) == identity
                     and _identity(os.stat(name, dir_fd=parent, follow_symlinks=False)) == identity)
        for parent, name in self.absent:
            try:
                os.stat(name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                continue
            raise BuildSetupInspectionError()


def _operation(value: object, kind: str, resource: str | None, phases: set[str]) -> Any:
    _fields(value, {"schema_version", "operation_id", "operation_type", "resource", "phase", "status",
                    "started_at", "updated_at", "context", "phases"})
    record = _record_from_dict(value, "local evidence")
    _timestamp(record.started_at)
    _timestamp(record.updated_at)
    _require(str(uuid.UUID(record.operation_id)) == record.operation_id
             and record.operation_type == kind and (resource is None or record.resource == resource)
             and record.phase in phases and record.phases and record.phases[-1] == record.phase
             and all(phase in phases for phase in record.phases)
             and (record.status == "recovery_required") == (record.phase == "recovery_required"))
    return record


def _plans(prepared: object) -> list[dict[str, Any]]:
    document = _fields(prepared, {"schema", "plans"})
    _require(type(document["schema"]) is int and document["schema"] == 1
             and isinstance(document["plans"], list) and 1 <= len(document["plans"]) <= MAX_APPLICATIONS)
    plans = []
    for raw in document["plans"]:
        plan = _fields(raw, {"request", "source_path", "deployment", "idempotency_key", "required_tools", "ttl_seconds"})
        request = parse_build_request(plan["request"])
        deployment = parse_target_deployment(plan["deployment"], request=request)
        _require(request == plan["request"] and deployment == plan["deployment"])
        _path(plan["source_path"])  # Syntax only; never open, stat or resolve payload paths.
        _hex(plan["idempotency_key"], 32)
        tools = plan["required_tools"]
        _require(isinstance(tools, list) and len(tools) <= 3
                 and all(isinstance(tool, str) and tool in {"go", "node", "python"} for tool in tools)
                 and len(set(tools)) == len(tools)
                 and (tools or not any(item["commands"] for item in request["components"]))
                 and ("go" in tools or not any(item["type"] == "service" for item in deployment["components"]))
                 and type(plan["ttl_seconds"]) is int and 1 <= plan["ttl_seconds"] <= MAX_TTL_SECONDS)
        plans.append({"application": deployment["application"], "project": request["project"],
                      "request_id": request["request_id"], "idempotency_key": plan["idempotency_key"],
                      "request_sha256": request_digest(request), "source": request["source"],
                      "deployment_sha256": hashlib.sha256(encode_document(deployment)).hexdigest(),
                      "artifact_limit": request["limits"]["artifact_bytes"] + MAX_DESCRIPTOR_BYTES + 1024})
    for key in ("application", "request_id", "idempotency_key"):
        _require(len({plan[key] for plan in plans}) == len(plans))
    return plans


def _applications(value: object, plans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    context = _fields(value, {"schema", "applications"})
    _require(type(context["schema"]) is int and context["schema"] == 1)
    encode_document(context, maximum=MAX_REPORT_BYTES)
    entries = context["applications"]
    _require(isinstance(entries, list) and len(entries) == len(plans))
    operations = set()
    for entry, plan in zip(entries, plans):
        _fields(entry, _BASE_ENTRY, _ADMISSION | _ARTIFACT | {"build_state", "stage_receipt", "activation_receipt"})
        _require(all(entry[key] == plan[key] for key in _BASE_ENTRY - {"status"}))
        phase = entry["status"]
        _require(isinstance(phase, str) and phase in _APP_PHASES)
        index = _APP_PHASES.index(phase)
        # Producer saves returned IDs before progressing beyond reserving.
        _require(bool(_ADMISSION & set(entry)) == bool(_ADMISSION <= set(entry)))
        _require((_ADMISSION <= set(entry)) == (index >= 2))
        if index >= 2:
            operation = _hex(entry["operation_id"], 32)
            _require(operation != plan["request_id"] and operation not in operations)
            operations.add(operation)
            _require(type(entry["generation"]) is int and 1 <= entry["generation"] <= MAX_OPERATIONS)
            _hex(entry["bindings_sha256"])
        _require(("build_state" in entry) == (index >= 4))
        if "build_state" in entry:
            _require(isinstance(entry["build_state"], str) and entry["build_state"] in {
                "queued", "running", "succeeded", "failed", "expired", "cancelled", "uncertain",
            } and (index == 4 or entry["build_state"] == "succeeded"))
        _require(bool(_ARTIFACT & set(entry)) == bool(_ARTIFACT <= set(entry)))
        _require((_ARTIFACT <= set(entry)) == (index >= 5))
        if index >= 5:
            _hex(entry["artifact_sha256"])
            _require(type(entry["artifact_size"]) is int and 1 <= entry["artifact_size"] <= plan["artifact_limit"])
        for key, threshold, status, extra in (
            ("stage_receipt", 8, "staged", "artifact_size"),
            ("activation_receipt", 10, "completed", "application"),
        ):
            _require((key in entry) == (index >= threshold))
            if key in entry:
                ack = _fields(entry[key], {"operation_id", "generation", "artifact_sha256", "status", extra})
                _require(type(ack["generation"]) is int and ack["status"] == status
                         and all(ack[field] == entry[field] for field in {"operation_id", "generation", "artifact_sha256", extra})
                         and (extra != "artifact_size" or type(ack[extra]) is int))
    # All reservations precede building; all stages precede activation. Earlier
    # applications finish each sequential loop before later applications enter it.
    ranks = [_APP_PHASES.index(entry["status"]) for entry in entries]
    _require(ranks == sorted(ranks, reverse=True))
    _require(sum(rank not in {0, 2, 8, 10} for rank in ranks) <= 1)
    _require(not any(rank >= 3 for rank in ranks) or all(rank >= 2 for rank in ranks))
    _require(not any(rank >= 9 for rank in ranks) or all(rank >= 8 for rank in ranks))
    return entries


def _inspect(evidence: _Evidence, journal_path: object) -> dict[str, Any]:
    journal = _path(os.fspath(journal_path))
    _require(os.path.basename(journal) == "setup.json")
    host_path = os.path.dirname(journal)
    host = evidence.root(host_path)
    evidence.lock(host, "setup.json.lock")
    setup = _operation(evidence.json(host, "setup.json"), "build_setup", None, _SETUP_PHASES)
    history = setup.phases[:-1] if setup.phase == "recovery_required" else setup.phases
    _require(history in {("bootstrapping",), ("bootstrapping", "delivering"),
                         ("bootstrapping", "delivering", "profile_applying")})
    context = _fields(setup.context, {"schema", "attempt", "host", "builder", "profile_sha256", "prepared_sha256"},
                      {"delivery_receipt", "expected_releases"})
    _require(type(context["schema"]) is int and context["schema"] == 1
             and isinstance(context["host"], str) and len(context["host"]) <= 253 and validate_host(context["host"])
             and isinstance(context["builder"], str) and len(context["builder"]) <= 253 and validate_host(context["builder"])
             and setup.resource == context["host"]
             and os.path.basename(host_path) == hashlib.sha256(context["host"].lower().rstrip(".").encode()).hexdigest())
    _hex(context["profile_sha256"])
    _hex(context["prepared_sha256"])
    attempt_path = _path(context["attempt"])
    _require(os.path.dirname(attempt_path) == host_path)
    attempt_name = _hex(os.path.basename(attempt_path), 32)
    attempt = evidence.open(host, attempt_name, directory=True)
    prepared = evidence.json(attempt, "prepared.json", maximum=MAX_PREPARED_BYTES)
    _require(hashlib.sha256(encode_document(prepared, maximum=MAX_PREPARED_BYTES)).hexdigest() == context["prepared_sha256"])
    plans = _plans(prepared)
    expected = "delivery_receipt" in context
    _require(expected == ("expected_releases" in context)
             and ("profile_applying" not in setup.phases or expected))
    bootstrap_only = all(phase in {"bootstrapping", "recovery_required"} for phase in setup.phases)
    _require(not bootstrap_only or not expected)
    delivery = evidence.open(attempt, "delivery", directory=True, optional=bootstrap_only)
    entries = None
    marker = result = receipt = None
    record = None
    if delivery is not None:
        _require(not bootstrap_only)
        # A delivery directory may precede lock creation during bootstrap's
        # handoff, but once it exists the stable lock is required for inspection.
        evidence.lock(delivery, "delivery.json.lock")
        marker = evidence.json(delivery, "delivery.json", optional=True)
        result = evidence.json(delivery, "delivery.json.last.json", optional=True)
        receipt_name = "receipt-" + plans[0]["idempotency_key"] + ".json"
        receipt = evidence.json(delivery, receipt_name, maximum=MAX_REPORT_BYTES, optional=True)
        if marker is not None:
            record = _operation(marker, "build_delivery", "controller-workspace", set(_APP_PHASES) | {"recovery_required"})
            entries = _applications(record.context, plans)
            ranks = [_APP_PHASES.index(entry["status"]) for entry in entries]
            active = [rank for rank in ranks if rank not in {0, 2, 8, 10}]
            _require(record.phase == "recovery_required" or record.phase == _APP_PHASES[active[0] if active else max(ranks)])
        if result is not None:
            _fields(result, {"version", "outcome", "completed_at", "operation"})
            _require(type(result["version"]) is int and result["version"] == 1 and result["outcome"] == "succeeded"
                     and isinstance(result["completed_at"], str) and len(result["completed_at"]) <= 64)
            _timestamp(result["completed_at"])
            completed = _operation(result["operation"], "build_delivery", "controller-workspace", set(_APP_PHASES))
            completed_entries = _applications(completed.context, plans)
            _require(completed.phase == "completed" and all(entry["status"] == "completed" for entry in completed_entries))
            if record is not None:
                _require(record.operation_id == completed.operation_id and record.started_at == completed.started_at
                         and record.context == completed.context)
            else:
                record, entries = completed, completed_entries
        if receipt is not None:
            receipt_entries = _applications(receipt, plans)
            _require(all(entry["status"] == "completed" for entry in receipt_entries)
                     and entries == receipt_entries)
        _require(entries is not None)
        _require(result is None or receipt is not None)
        _require(marker is not None or (result is not None and receipt is not None))
    _require(not expected or (entries is not None and receipt is not None and result is not None))
    if expected:
        _require(context["delivery_receipt"] == attempt_path + "/delivery/" + receipt_name)
        releases = [{key: entry[key] for key in ("application", "operation_id", "generation", "artifact_sha256")}
                    for entry in entries]
        _require(context["expected_releases"] == releases)
        for release in context["expected_releases"]:
            _require(type(release["generation"]) is int)
    apps = []
    for index, plan in enumerate(plans):
        entry = entries[index] if entries is not None else {}
        phase = entry.get("status", "unknown")
        apps.append({**{key: plan[key] for key in _BASE_ENTRY - {"status"}},
                     "local_status": phase,
                     "operation_id": entry.get("operation_id"), "generation": entry.get("generation"),
                     "bindings_sha256": entry.get("bindings_sha256"), "build_state": entry.get("build_state"),
                     "artifact_sha256": entry.get("artifact_sha256"), "artifact_size": entry.get("artifact_size"),
                     "stage_acknowledgement": entry.get("stage_receipt"),
                     "activation_acknowledgement": entry.get("activation_receipt"),
                     "unresolved_intent": {"reserving": "reserve", "submitting": "submit", "building": "build",
                                           "downloading": "download", "verifying": "verify", "staging": "stage",
                                           "activating": "activate"}.get(phase)})
    return {"schema_version": 1, "remote_state": "unobserved", "payload_contents": "unverified",
            "setup": {"operation_id": setup.operation_id, "phase": setup.phase, "status": setup.status,
                      "unfinished_guard": True, "prepared_sha256": context["prepared_sha256"],
                      "profile_sha256": context["profile_sha256"]},
            "delivery": {"operation_id": record.operation_id if record else None,
                         "phase": record.phase if record else "unknown", "status": record.status if record else "unknown",
                         "unfinished_guard": marker is not None, "successful_last_result": result is not None,
                         "completed_receipt": receipt is not None},
            "applications": apps}


def inspect_build_setup_recovery(journal_path: str | os.PathLike[str]) -> dict[str, Any]:
    """Inspect fixed local evidence under existing read-only, nonblocking locks.

    Return a schema-1 projection bounded to 64 KiB and 16 applications. Unknown
    remote IDs are None. Refuse unsafe, busy, missing, inconsistent or unsupported
    evidence with BuildSetupInspectionError. This never establishes remote state
    or permits replay. Evidence bytes and metadata (except access time) are not
    changed; Python imports may write bytecode unless disabled at startup.
    """
    try:
        with ExitStack() as stack:
            evidence = _Evidence(stack)
            report = _inspect(evidence, journal_path)
            encode_document(report, maximum=MAX_REPORT_BYTES - 1)
            evidence.verify()
            return report
    except BuildSetupInspectionError:
        raise
    except OSError:
        raise BuildSetupInspectionError("unavailable") from None
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
        raise BuildSetupInspectionError() from None


def main() -> int:
    """Print one bounded JSON report or a fixed JSON refusal (exit 2)."""
    try:
        if len(sys.argv) != 3 or sys.argv[1] != "--journal":
            raise BuildSetupInspectionError("invalid_arguments")
        if not sys.dont_write_bytecode:
            raise BuildSetupInspectionError("bytecode_enabled_use_python_B")
        report = inspect_build_setup_recovery(sys.argv[2])
    except BuildSetupInspectionError as error:
        print(encode_document({"schema_version": 1, "error": error.code}).decode(), file=sys.stderr)
        return 2
    print(encode_document(report, maximum=MAX_REPORT_BYTES - 1).decode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
