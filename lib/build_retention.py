"""Explicit root-local terminal settlement, pruning and successful bundle release."""

from __future__ import annotations

from contextlib import ExitStack
import copy
import hashlib
import os
import re
from typing import Any, BinaryIO

from lib.build_artifacts import MAGIC
from lib.build_inputs import (
    MAX_INTENT_BYTES,
    MAX_PRUNING_INTENT_BYTES,
    MAX_RETAINED_METADATA_BYTES,
    BuildInputStore,
    _check_regular,
    _identifier,
    _private_root,
    _prune_revision,
    _READ,
    _SHA256,
)
from lib.build_jobs import MAX_STORE_BYTES, BuildJobStore, WorkerLease
from lib.build_payloads import payload_lease
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
_PATHS = {"source": ("inputs", ".source"), "log": ("logs", ".log"),
          "bundle": ("artifacts", ".bundle")}


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

    Inspect and apply take worker (nonblocking), input, payload (nonblocking),
    then short job-state locks. No payload I/O runs under the job-state lock.
    Roots are never created or recursively traversed. Only selected fixed
    payload entries may be unlinked. Plans contain no commands or log bytes.
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

    def prune_terminal(self, request_id: str, *, expected_revision: str,
                       kinds: tuple[str, ...] = ("source", "log")) -> dict[str, Any]:
        """Durably record selected deletion before unlink, then credit once.

        Settlement must have completed first. Pending operations require this
        exact expected revision and kind set; admission never resumes them.
        Bundle release is a separate explicit action. All replay/job identities
        remain retained within the existing finite history limits.
        """
        _require_root()
        revision = _identifier(expected_revision, "Expected revision", _SHA256)
        if (not isinstance(kinds, (tuple, list)) or not kinds
                or any(not isinstance(kind, str) or kind not in {"source", "log"} for kind in kinds)
                or len(set(kinds)) != len(kinds)):
            raise ValueError("Pruning kinds must select source and/or log exactly once")
        selected = [kind for kind in _PATHS if kind in kinds]
        return self._operate(request_id, expected_revision=revision, prune_kinds=selected)

    def release_bundle(self, request_id: str, *, expected_revision: str) -> dict[str, Any]:
        """Relinquish builder retrieval of one exact settled successful bundle.

        Only this root-local action can start or resume bundle deletion. Reads
        never release a hold; job results and replay identities remain intact.
        """
        _require_root()
        revision = _identifier(expected_revision, "Expected revision", _SHA256)
        return self._operate(request_id, expected_revision=revision, prune_kinds=["bundle"])

    def _plan(self, request_id: str, record: dict[str, Any], job: dict[str, Any],
              directories: dict[str, int], known_ids: set[str]) -> dict[str, Any]:
        request = job["request"]
        if job["state"] not in _TERMINAL:
            raise ValueError("Build retention requires a certain terminal job")
        if (job["request_id"] != request_id or request["request_id"] != request_id
                or job["owner"] != record["owner"] or job["request_sha256"] != record["request_sha256"]
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
        bundle = _payload(directories["artifacts"], request_id + ".bundle",
                          maximum=limits["artifact_bytes"] + MAX_DESCRIPTOR_BYTES + 1024,
                          mode=0o600, request=request)
        log = _payload(directories["logs"], request_id + ".log",
                       maximum=limits["log_bytes"], mode=0o600)
        observed = {"source": source, "bundle": bundle, "log": log}
        original_payloads = observed
        if record["schema"] in {3, 4}:
            pruning = record["pruning"]
            if hashlib.sha256(encode_document(job, maximum=MAX_STORE_BYTES)).hexdigest() != pruning["job_sha256"]:
                raise ValueError("Build retention terminal job identity changed")
            original_payloads = pruning["payloads"]
            for kind, original in original_payloads.items():
                operations = [op for op in pruning["operations"] if kind in op["kinds"]]
                current = observed[kind]
                if operations and operations[0]["state"] == "completed":
                    if current is not None:
                        raise ValueError("Build retention pruned payload reappeared")
                elif current != original and not (current is None and operations):
                    raise ValueError("Build retention pruning payload evidence changed")
        # Validate immutable success and settlement against the original bytes,
        # while unlink/recheck and availability always use observed payloads.
        source, bundle, log = (original_payloads[kind] for kind in ("source", "bundle", "log"))
        if (source is None or source["size"] != record["source_size"]
                or source["sha256"] != record["source_sha256"]):
            raise ValueError("Build retention source does not match immutable input identity")
        if record["schema"] == 4 and job["state"] != "succeeded":
            raise ValueError("Build retention bundle release requires a succeeded job")
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
        charge = metadata_charge + sum(max(FILE_CHARGE_FLOOR, item["size"])
                                       for item in original_payloads.values() if item is not None)
        if charge > record["reservation_bytes"]:
            raise ValueError("Build retention charge exceeds the original reservation")
        evidence = {
            "schema": 1, "request_id": request_id, "state": job["state"],
            "input": {key: value for key, value in record.items()
                      if key not in {"schema", "charge_bytes", "settlement_revision", "pruning"}},
            "job_sha256": hashlib.sha256(encode_document(job, maximum=MAX_STORE_BYTES)).hexdigest(),
            "roots": {key: {"path": self.roots[key],
                            "identity": [os.fstat(fd).st_dev, os.fstat(fd).st_ino]}
                      for key, fd in directories.items()},
            "payloads": original_payloads, "metadata_charge_bytes": metadata_charge,
            "charge_bytes": charge,
        }
        revision = hashlib.sha256(encode_document(evidence, maximum=MAX_PLAN_BYTES)).hexdigest()
        original_charge = (record["pruning"]["operations"][0]["previous_charge_bytes"]
                           if record["schema"] in {3, 4} else record.get("charge_bytes"))
        if record["schema"] in {2, 3, 4} and (record["settlement_revision"] != revision
                                          or original_charge != charge):
            raise ValueError("Build retention settled evidence changed; root reconciliation required")
        plan = {"schema": 1, "request_id": request_id, "state": job["state"],
                "revision": revision, "reservation_bytes": record["reservation_bytes"],
                "current_charge_bytes": record.get("charge_bytes", record["reservation_bytes"]),
                "charge_bytes": charge, "metadata_charge_bytes": metadata_charge,
                "payloads": observed, "settled": record["schema"] in {2, 3, 4},
                "pruning": [], "availability": {
                    kind: "present" if item is not None else "absent" for kind, item in observed.items()}}
        encode_document(plan, maximum=MAX_PLAN_BYTES)
        return plan

    @staticmethod
    def _view(plan: dict[str, Any], record: dict[str, Any], *, count: int | None = None) -> dict[str, Any]:
        """Project current intent or reconstruct an exact completed retry receipt."""
        if record["schema"] not in {3, 4}:
            return plan
        result = copy.deepcopy(plan)
        pruning = record["pruning"]
        operations = pruning["operations"][:count]
        result["payloads"] = copy.deepcopy(pruning["payloads"])
        result["pruning"] = copy.deepcopy(operations)
        result["revision"] = record["settlement_revision"]
        for operation in operations:
            completed = operation["state"] == "completed"
            if completed:
                result["revision"] = _prune_revision(result["revision"], operation["kinds"])
                for kind in operation["kinds"]:
                    result["payloads"][kind] = None
            result["current_charge_bytes"] = operation[
                "charge_bytes" if completed else "previous_charge_bytes"]
            result["charge_bytes"] = operation["charge_bytes"]
        result["availability"] = {
            kind: "present" if item is not None else "absent"
            for kind, item in result["payloads"].items()}
        for operation in operations:
            for kind in operation["kinds"]:
                result["availability"][kind] = (
                    "pruned" if operation["state"] == "completed" else
                    "pending_present" if plan["payloads"][kind] is not None else "pending_absent")
        encode_document(result, maximum=MAX_PLAN_BYTES)
        return result

    def _check_save(self, record: dict[str, Any], *, metadata_bytes: int, encoded_size: int) -> None:
        self.inputs._validate_intent(record, request_id=record["request_id"])
        maximum = MAX_PRUNING_INTENT_BYTES if record["schema"] in {3, 4} else MAX_INTENT_BYTES
        encoded = encode_document(record, maximum=maximum)
        if metadata_bytes - encoded_size + len(encoded) > min(
            MAX_RETAINED_METADATA_BYTES, self.inputs.max_records * MAX_PRUNING_INTENT_BYTES,
        ):
            raise ValueError("Build retention exceeds the retained metadata bound")

    def _save(self, root_fd: int, record: dict[str, Any], *, metadata_bytes: int, encoded_size: int) -> None:
        self._check_save(record, metadata_bytes=metadata_bytes, encoded_size=encoded_size)
        self.inputs._save_settlement(root_fd, record)

    def _operate(self, request_id: str, *, expected_revision: str | None,
                 prune_kinds: list[str] | None = None) -> dict[str, Any]:
        _require_root()
        request_id = _identifier(request_id, "Request ID")
        with self.jobs.worker() as lease, ExitStack() as stack:
            directories = {key: stack.enter_context(_private_root(path))
                           for key, path in self.roots.items()}
            root_fd = directories["inputs"]
            with self.inputs._locked(root_fd):
                intents, _charged, metadata_bytes = self.inputs._scan_intents(
                    root_fd, maintenance_temporary=(request_id + ".settlement.tmp"
                                                    if expected_revision is not None else None),
                )
                if request_id not in intents:
                    raise ValueError("Build retention input was not found")
                record, encoded_size, source, partial = intents[request_id]
                if (source is None and record["schema"] not in {3, 4}) or partial is not None:
                    raise ValueError("Build retention requires a complete published input")
                stack.enter_context(payload_lease(self.jobs, request_id, exclusive=True))
                job = self.jobs.read_for_retention(request_id, lease=lease)
                plan = self._plan(request_id, record, job, directories, set(intents))
                self._recheck(request_id, plan, job, directories, lease)
                view = self._view(plan, record)
                if prune_kinds is not None:
                    return self._prune(request_id, record, plan, view, job, directories, lease,
                                       expected_revision=expected_revision, kinds=prune_kinds,
                                       metadata_bytes=metadata_bytes, encoded_size=encoded_size)
                if expected_revision is not None and expected_revision != view["revision"]:
                    raise ValueError("Build retention plan is stale")
                if expected_revision is not None and any(
                    op["state"] == "pending" for op in view["pruning"]
                ):
                    raise ValueError("Build retention pruning needs explicit completion")
                if expected_revision is not None and not plan["settled"]:
                    updated = {**record, "schema": 2, "charge_bytes": plan["charge_bytes"],
                               "settlement_revision": plan["revision"]}
                    self._check_save(updated, metadata_bytes=metadata_bytes, encoded_size=encoded_size)
                    self._discard_staged(root_fd, updated)
                    self._save(root_fd, updated, metadata_bytes=metadata_bytes, encoded_size=encoded_size)
                    plan["settled"] = True
                    plan["current_charge_bytes"] = plan["charge_bytes"]
                elif expected_revision is not None:
                    _absent(root_fd, request_id + ".settlement.tmp")
                return view

    def _prune(self, request_id: str, record: dict[str, Any], plan: dict[str, Any],
               view: dict[str, Any], job: dict[str, Any], directories: dict[str, int],
               lease: WorkerLease, *, expected_revision: str | None, kinds: list[str],
               metadata_bytes: int, encoded_size: int) -> dict[str, Any]:
        if not plan["settled"]:
            raise ValueError("Build retention requires settlement before pruning")
        release = kinds == ["bundle"]
        if release and job["state"] != "succeeded":
            raise ValueError("Build retention bundle release requires a succeeded job")
        updated = copy.deepcopy(record)
        if record["schema"] == 2:
            updated.update(schema=3, pruning={
                "payloads": plan["payloads"], "operations": [],
                "job_sha256": hashlib.sha256(encode_document(job, maximum=MAX_STORE_BYTES)).hexdigest(),
            })
        if release:
            updated["schema"] = 4
        operations = updated["pruning"]["operations"]
        existing = next((index for index, op in enumerate(operations)
                         if op["expected_revision"] == expected_revision), None)
        if existing is not None:
            operation = operations[existing]
            if operation["kinds"] != kinds:
                raise ValueError("Build retention pruning retry changed selected kinds")
            if operation["state"] == "completed":
                _absent(directories["inputs"], request_id + ".settlement.tmp")
                self._sync_deleted(directories, kinds)
                return self._view(plan, record, count=existing + 1)
        else:
            if any(op["state"] == "pending" for op in operations):
                raise ValueError("Build retention pruning needs its original revision and kinds")
            if expected_revision != view["revision"]:
                raise ValueError("Build retention plan is stale")
            if any(kind in op["kinds"] for op in operations for kind in kinds):
                raise ValueError("Build retention payload kind was already pruned")
            credit = sum(max(FILE_CHARGE_FLOOR, plan["payloads"][kind]["size"])
                         for kind in kinds if plan["payloads"][kind] is not None)
            if not credit:
                raise ValueError("Build retention selected no retained payloads")
            operation = {"expected_revision": expected_revision, "kinds": kinds, "state": "pending",
                         "previous_charge_bytes": record["charge_bytes"],
                         "charge_bytes": record["charge_bytes"] - credit}
            operations.append(operation)
        completed = copy.deepcopy(updated)
        completed["pruning"]["operations"][-1]["state"] = "completed"
        completed["charge_bytes"] = operation["charge_bytes"]
        self._check_save(updated, metadata_bytes=metadata_bytes, encoded_size=encoded_size)
        self._check_save(completed, metadata_bytes=metadata_bytes, encoded_size=encoded_size)
        self._view(plan, updated)
        self._view(plan, completed)
        self._discard_staged(directories["inputs"], updated)
        if existing is None:
            # Bounded intent and prior charge are durable BEFORE the first unlink.
            self._save(directories["inputs"], updated, metadata_bytes=metadata_bytes, encoded_size=encoded_size)
        for kind in kinds:
            root, suffix = _PATHS[kind]
            item = plan["payloads"][kind]
            if item is not None:
                named = os.stat(request_id + suffix, dir_fd=directories[root], follow_symlinks=False)
                if _identity(named) != item["identity"]:
                    raise ValueError("Build retention payload replaced before unlink")
                os.unlink(request_id + suffix, dir_fd=directories[root])
        # Sync even already-absent selected entries on explicit resume. Until
        # this and final replacement succeed, pending keeps the entire old charge.
        self._sync_deleted(directories, kinds)
        verified = self._plan(request_id, updated, job, directories, set(self.inputs._scan_intents(
            directories["inputs"])[0]))
        self._recheck(request_id, verified, job, directories, lease)
        operation["state"] = "completed"
        updated["charge_bytes"] = operation["charge_bytes"]
        self._save(directories["inputs"], updated, metadata_bytes=metadata_bytes, encoded_size=encoded_size)
        return self._view(verified, updated)

    def _discard_staged(self, directory: int, pending: dict[str, Any]) -> None:
        """Discard only an exact, complete staged settlement/prune transition.

        Never adopt a temporary's lower charge. Reverify persisted evidence and
        redo directory syncs before saving completion. Partial or unrelated
        metadata remains ambiguous and is retained for root reconciliation.
        """
        name = pending["request_id"] + ".settlement.tmp"
        maximum = MAX_INTENT_BYTES if pending["schema"] == 2 else MAX_PRUNING_INTENT_BYTES
        try:
            descriptor = os.open(name, _READ, dir_fd=directory)
        except FileNotFoundError:
            return
        try:
            before = os.fstat(descriptor)
            _check_regular(before, "staged retention intent", modes=frozenset({0o600}),
                           max_size=maximum)
            raw = os.read(descriptor, maximum + 1)
            staged = self.inputs._validate_intent(
                decode_document(raw, maximum=maximum), request_id=pending["request_id"],
            )
            candidates = [pending]
            if pending["schema"] in {3, 4}:
                completed = copy.deepcopy(pending)
                operation = completed["pruning"]["operations"][-1]
                operation["state"] = "completed"
                completed["charge_bytes"] = operation["charge_bytes"]
                candidates.append(completed)
            if (len(raw) != before.st_size or os.read(descriptor, 1)
                    or staged not in candidates
                    or encode_document(staged, maximum=maximum) != raw
                    or _identity(os.fstat(descriptor)) != _identity(before)
                    or _identity(os.stat(name, dir_fd=directory, follow_symlinks=False)) != _identity(before)):
                raise ValueError("Build retention staged intent is ambiguous")
            os.unlink(name, dir_fd=directory)
            os.fsync(directory)
        finally:
            os.close(descriptor)

    @staticmethod
    def _sync_deleted(directories: dict[str, int], kinds: list[str]) -> None:
        for root in dict.fromkeys(_PATHS[kind][0] for kind in kinds):
            os.fsync(directories[root])

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
