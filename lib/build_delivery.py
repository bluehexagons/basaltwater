"""Bounded internal controller composition of prepared builds and deployments."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import math
import os
import stat
import time
from typing import Any

from lib.build_artifacts import unpack_artifact
from lib.build_client import (
    BuildHostClient,
    _absolute_path,
    _file_identity,
    _open_private_directory,
    _open_trusted_file,
    _validate_job_record,
)
from lib.build_manifest import DEBIAN_PLATFORM
from lib.build_protocol import MAX_DESCRIPTOR_BYTES, encode_document, request_digest
from lib.build_source import MAX_SNAPSHOT_BYTES
from lib.build_target_client import BuildTargetClient, _acknowledgement, _admission
from lib.build_target_service import MAX_HEADER_LINE_BYTES, _parse_header
from lib.operation_state import OperationStateError, OperationStateStore


MAX_PLANS = 16
MAX_CONTEXT_BYTES = 64 * 1024
MAX_DELIVERY_SECONDS = 24 * 3600


@dataclass(frozen=True)
class DeliveryPlan:
    """Caller-retained pinned inputs; keys and request IDs are never generated."""

    request: Mapping[str, Any]
    source_path: str | os.PathLike[str]
    deployment: Mapping[str, Any]
    idempotency_key: str
    required_tools: tuple[str, ...]
    ttl_seconds: int = 3600


@dataclass(frozen=True)
class ApplicationDeliveryReceipt:
    """Evidence of one acknowledged activation, without deployment authority."""

    application: str
    request_id: str
    operation_id: str
    generation: int
    artifact_sha256: str
    artifact_size: int
    status: str = "completed"


@dataclass(frozen=True)
class DeliveryReceipt:
    """Ordered completed activations and their private durable evidence file."""

    applications: tuple[ApplicationDeliveryReceipt, ...]
    receipt_path: str


class DeliveryRecoveryRequired(RuntimeError):
    """Inspect durable identities before any explicit recovery or new work."""

    def __init__(self, journal_path: str) -> None:
        self.journal_path = journal_path
        super().__init__(
            f"Delivery stopped; preserve {journal_path} and all workspace files. "
            "Inspect builder.status(request_id) and target.status(operation_id) using "
            "the recorded IDs. For a lost reserve reply, explicitly recover with "
            "target.reserve using the SAME recorded idempotency key and exact "
            "caller-retained inputs. Reconcile staged bytes and actual activation "
            "state before any replay; never generate replacement IDs or cancel "
            "automatically. Completed application receipts remain evidence."
        )


def _positive(value: object, maximum: float, label: str) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= maximum:
        raise ValueError(f"{label} must be finite, positive and at most {maximum}")
    return float(value)


def _hash_private(path: str, maximum: int, tick: Callable[[], None]) -> tuple[str, int]:
    parent = _open_private_directory(os.path.dirname(path))
    os.close(parent)
    descriptor, identity = _open_trusted_file(path, "Delivery input", private=True)
    try:
        size = os.fstat(descriptor).st_size
        if not 1 <= size <= maximum:
            raise ValueError("Delivery input exceeds its byte bound")
        digest = hashlib.sha256()
        remaining = size
        while remaining:
            tick()
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                raise ValueError("Delivery input was truncated")
            digest.update(chunk)
            remaining -= len(chunk)
        if (os.read(descriptor, 1) or _file_identity(os.fstat(descriptor)) != identity
                or _file_identity(os.stat(path, follow_symlinks=False)) != identity):
            raise ValueError("Delivery input changed")
        return digest.hexdigest(), size
    finally:
        os.close(descriptor)


def _validated(plan: DeliveryPlan, tick: Callable[[], None]) -> DeliveryPlan:
    if (not isinstance(plan, DeliveryPlan) or not isinstance(plan.request, Mapping)
            or not isinstance(plan.deployment, Mapping)):
        raise ValueError("Expected a DeliveryPlan")
    # The same pure parser bounds the actual reservation wire document and
    # checks the full request/projection binding, explicit key and TTL.
    _, fields = _parse_header(encode_document({
        "schema": 1, "action": "reserve", "application": plan.deployment.get("application"),
        "idempotency_key": plan.idempotency_key, "request": plan.request,
        "deployment": plan.deployment, "ttl_seconds": plan.ttl_seconds,
    }, maximum=MAX_HEADER_LINE_BYTES - 1) + b"\n")
    request, deployment = fields["request"], fields["deployment"]
    if request["platform"] != DEBIAN_PLATFORM:
        raise ValueError("Delivery supports only Debian 13 x86_64")
    tools = plan.required_tools
    if (not isinstance(tools, tuple) or len(tools) > 3
            or any(not isinstance(tool, str) or tool not in {"go", "node", "python"} for tool in tools)
            or len(set(tools)) != len(tools)):
        raise ValueError("Required tools must be distinct explicit supported tool names")
    if any(item["commands"] for item in request["components"]) and not tools:
        raise ValueError("Command-bearing requests require explicit tools")
    if any(item["type"] == "service" for item in deployment["components"]) and "go" not in tools:
        raise ValueError("First-slice services require the explicit Go tool declaration")
    # Ensure later stage headers fit too, before any target reservation.
    _parse_header(encode_document({
        "schema": 1, "action": "stage", "operation_id": "0" * 32,
        "generation": 4096, "request": request, "deployment": deployment,
        "artifact_sha256": "0" * 64,
        "artifact_size": request["limits"]["artifact_bytes"] + MAX_DESCRIPTOR_BYTES + 1024,
    }, maximum=MAX_HEADER_LINE_BYTES - 1) + b"\n")
    source_path = _absolute_path(plan.source_path, "Source snapshot")
    digest, _ = _hash_private(source_path, MAX_SNAPSHOT_BYTES, tick)
    if digest != request["source"]["snapshot_sha256"]:
        raise ValueError("Pinned snapshot does not match the request")
    return DeliveryPlan(request, source_path, deployment, plan.idempotency_key, tools, plan.ttl_seconds)


class BuildDeliveryController:
    """Compose exclusively borrowed trusted clients; never close or replay them.

    Calls temporarily lower the existing transport timeout to the remaining
    overall budget. Clients must not be shared concurrently during delivery.
    """

    def __init__(self, *, builder: BuildHostClient, target: BuildTargetClient,
                 workspace: str | os.PathLike[str], timeout_seconds: float = 3600,
                 poll_interval: float = 1, clock: Callable[[], float] = time.monotonic,
                 sleeper: Callable[[float], None] = time.sleep) -> None:
        self.builder = builder
        self.target = target
        self.workspace = _absolute_path(workspace, "Delivery workspace")
        self.timeout_seconds = _positive(timeout_seconds, MAX_DELIVERY_SECONDS, "Delivery timeout")
        self.poll_interval = _positive(poll_interval, 60, "Poll interval")
        self.clock, self.sleeper = clock, sleeper
        self.journal_path = os.path.join(self.workspace, "delivery.json")

    def deliver(self, plans: Sequence[DeliveryPlan]) -> DeliveryReceipt:
        """Reserve all, build/verify/stage all, then activate sequentially.

        Any failure after intent persistence keeps the unfinished guard. There
        is deliberately no resume, cancellation, fallback or recovery API.
        """
        deadline = self.clock() + self.timeout_seconds

        def remaining() -> float:
            value = deadline - self.clock()
            if not math.isfinite(value) or value <= 0:
                raise TimeoutError("Delivery deadline reached; remote work may still be running")
            return value

        def tick() -> None:
            remaining()

        def invoke(client: BuildHostClient | BuildTargetClient, method: str,
                   *args: Any, **kwargs: Any) -> Any:
            # Both concrete clients use this finite per-call transport bound.
            transport = client._transport if isinstance(client, BuildTargetClient) else client
            original = transport.timeout
            transport.timeout = min(original, remaining())
            try:
                return getattr(client, method)(*args, **kwargs)
            finally:
                transport.timeout = original

        root = _open_private_directory(self.workspace)
        try:
            self._check_state_files()
            with OperationStateStore(self.journal_path).ownership() as store:
                if store.load() is not None:
                    raise DeliveryRecoveryRequired(self.journal_path)
                # A workspace belongs to exactly one delivery. Bounded retained
                # evidence is never pruned, reused or overwritten by a new run.
                with os.scandir(root) as entries:
                    if any(item.name != "delivery.json.lock" for item in entries):
                        raise FileExistsError("Delivery workspace contains retained evidence; preserve it")
                if (not isinstance(plans, Sequence) or isinstance(plans, (str, bytes))
                        or not 1 <= len(plans) <= MAX_PLANS):
                    raise ValueError(f"Delivery requires 1 to {MAX_PLANS} plans")
                selected = [_validated(plan, tick) for plan in plans]
                for values in (
                    [plan.deployment["application"] for plan in selected],
                    [plan.request["request_id"] for plan in selected],
                    [plan.idempotency_key for plan in selected],
                ):
                    if len(set(values)) != len(values):
                        raise ValueError("Applications, request IDs and keys must each be unique")
                receipt_name = "receipt-" + selected[0].idempotency_key + ".json"
                # Every plan is fully locally validated before the first probe;
                # every grant/tool check finishes before the first mutation.
                capabilities = invoke(self.builder, "capabilities")
                for plan in selected:
                    self._preflight(plan, capabilities,
                                    invoke(self.target, "preflight", plan.deployment["application"]))
                context: dict[str, Any] = {"schema": 1, "applications": [
                    {"application": plan.deployment["application"],
                     "project": plan.request["project"], "request_id": plan.request["request_id"],
                     "idempotency_key": plan.idempotency_key,
                     "request_sha256": request_digest(plan.request),
                     "source": dict(plan.request["source"]),
                     "deployment_sha256": hashlib.sha256(encode_document(plan.deployment)).hexdigest(),
                     "status": "prepared"}
                    for plan in selected
                ]}
                encode_document(context, maximum=MAX_CONTEXT_BYTES)
                operation = store.begin("build_delivery", "controller-workspace", "prepared", context=context)

                def save(entry: dict[str, Any], phase: str) -> None:
                    entry["status"] = phase
                    encode_document(context, maximum=MAX_CONTEXT_BYTES)
                    store.transition(operation.operation_id, phase, context=context)

                try:
                    for plan, entry in zip(selected, context["applications"]):
                        save(entry, "reserving")
                        admission = invoke(self.target, "reserve", **self._reservation(plan))
                        # Save the returned recovery IDs before any further call.
                        entry.update(operation_id=admission["operation_id"], generation=admission["generation"],
                                     bindings_sha256=admission["bindings_sha256"])
                        save(entry, "reserved")
                        self._current(plan, entry, admission)
                    for plan, entry in zip(selected, context["applications"]):
                        save(entry, "submitting")
                        job = invoke(self.builder, "submit", plan.request, plan.source_path)
                        while True:
                            job = _validate_job_record(job, entry["request_id"], dict(plan.request))
                            entry["build_state"] = job["state"]
                            save(entry, "building")
                            tick()
                            if job["state"] == "succeeded":
                                break
                            if job["state"] not in {"queued", "running"} or job["cancel_requested"]:
                                raise ValueError("Build did not succeed")
                            self.sleeper(min(self.poll_interval, remaining()))
                            job = invoke(self.builder, "status", entry["request_id"])
                        artifact = job["artifact"]
                        if artifact["request_sha256"] != entry["request_sha256"]:
                            raise ValueError("Artifact belongs to different build inputs")
                        entry.update(artifact_sha256=artifact["sha256"], artifact_size=artifact["size"])
                        path = os.path.join(self.workspace, "artifact-" + entry["request_id"] + ".bundle")
                        save(entry, "downloading")
                        invoke(self.builder, "download_artifact", entry["request_id"], path,
                               artifact["sha256"], artifact["size"])
                        tick()
                        digest, size = _hash_private(path, plan.request["limits"]["artifact_bytes"]
                                                     + MAX_DESCRIPTOR_BYTES + 1024, tick)
                        if (digest, size) != (artifact["sha256"], artifact["size"]):
                            raise ValueError("Downloaded artifact identity differs")
                        save(entry, "verifying")
                        descriptor = unpack_artifact(
                            path, plan.request, os.path.join(self.workspace, "verified-" + entry["request_id"]),
                            expected_sha256=artifact["sha256"],
                        )
                        descriptor_digest = hashlib.sha256(
                            encode_document(descriptor, maximum=MAX_DESCRIPTOR_BYTES),
                        ).hexdigest()
                        if descriptor_digest != artifact["descriptor_sha256"]:
                            raise ValueError("Downloaded descriptor identity differs")
                        save(entry, "staging")
                        acknowledgement = invoke(self.target, "stage", **self._mutation(plan, entry),
                                                 artifact_path=path, artifact_size=size)
                        entry["stage_receipt"] = _acknowledgement(
                            acknowledgement, "stage", self._mutation(plan, entry) | {"artifact_size": size},
                        )
                        save(entry, "staged")
                    # Refresh ALL admissions after the last stage and immediately
                    # before the activation loop. The target still checks under lock.
                    for plan, entry in zip(selected, context["applications"]):
                        admission = invoke(self.target, "status", entry["operation_id"])
                        self._current(plan, entry, admission)
                    for plan, entry in zip(selected, context["applications"]):
                        save(entry, "activating")
                        acknowledgement = invoke(self.target, "activate", **self._mutation(plan, entry))
                        entry["activation_receipt"] = _acknowledgement(
                            acknowledgement, "activate", self._mutation(plan, entry),
                        )
                        save(entry, "completed")
                    tick()
                    # Exclusive receipt publication preserves every successful
                    # delivery, independently of the store's rotating last result.
                    payload = encode_document(context, maximum=MAX_CONTEXT_BYTES)
                    descriptor_fd = os.open(receipt_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                            0o600, dir_fd=root)
                    with os.fdopen(descriptor_fd, "wb") as stream:
                        stream.write(payload)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.fsync(root)
                    store.complete(operation.operation_id)
                    return DeliveryReceipt(tuple(ApplicationDeliveryReceipt(**{
                        key: entry[key] for key in (
                            "application", "request_id", "operation_id", "generation",
                            "artifact_sha256", "artifact_size",
                        )
                    }) for entry in context["applications"]), os.path.join(self.workspace, receipt_name))
                except (Exception, KeyboardInterrupt):
                    # Never mask uncertainty with a second remote mutation. If
                    # even this update fails, the preceding durable phase guards it.
                    try:
                        store.transition(operation.operation_id, "recovery_required",
                                         status="recovery_required", context=context)
                    except (Exception, KeyboardInterrupt):
                        pass
                    raise DeliveryRecoveryRequired(self.journal_path) from None
        finally:
            os.close(root)

    def _check_state_files(self) -> None:
        for suffix in ("", ".lock", ".last.json"):
            path = self.journal_path + suffix
            if os.path.lexists(path):
                descriptor, _ = _open_trusted_file(path, "Delivery state", private=True)
                try:
                    info = os.fstat(descriptor)
                    if stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 1024 * 1024:
                        raise OperationStateError("Unsafe or oversized delivery state")
                finally:
                    os.close(descriptor)

    @staticmethod
    def _reservation(plan: DeliveryPlan) -> dict[str, Any]:
        return {"idempotency_key": plan.idempotency_key, "application": plan.deployment["application"],
                "request": plan.request, "deployment": plan.deployment, "ttl_seconds": plan.ttl_seconds}

    @staticmethod
    def _mutation(plan: DeliveryPlan, entry: dict[str, Any]) -> dict[str, Any]:
        return {"operation_id": entry["operation_id"], "generation": entry["generation"],
                "request": plan.request, "deployment": plan.deployment,
                "artifact_sha256": entry["artifact_sha256"]}

    @staticmethod
    def _preflight(plan: DeliveryPlan, capabilities: dict[str, Any], grant: dict[str, Any]) -> None:
        request, deployment = plan.request, plan.deployment
        builder = capabilities["projects"].get(request["project"])
        routes = [{key: item[key] for key in ("name", "type", "domain", "path")}
                  for item in deployment["components"]]
        if (grant["application"] != deployment["application"] or grant["project"] != request["project"]
                or grant["platform"] != request["platform"]
                or sorted(grant["routes"], key=lambda item: item["name"]) != routes
                or any(request["limits"][key] > grant["limits"][key] for key in grant["limits"])):
            raise ValueError("Target preflight does not grant this delivery")
        if (builder is None or builder["platform"] != request["platform"]
                or any(request["limits"][key] > builder["limits"][key] for key in request["limits"])
                or any(tool not in builder["runtimes"] or not capabilities["tool_versions"].get(tool)
                       for tool in plan.required_tools)):
            raise ValueError("Builder preflight lacks the requested project, platform, limits or tools")

    @staticmethod
    def _current(plan: DeliveryPlan, entry: dict[str, Any], record: dict[str, Any]) -> None:
        checked = _admission(record, "reserve", BuildDeliveryController._reservation(plan))
        if (checked["operation_id"] != entry["operation_id"] or checked["generation"] != entry["generation"]
                or checked["bindings_sha256"] != entry["bindings_sha256"]
                or checked["state"] != "reserved" or checked["effective_status"] != "reserved"
                or checked["expired"] or checked["superseded"]):
            raise ValueError("Target admission is no longer current and reserved")
