"""Bounded typed dispatcher for externally built target deployments.

The caller supplies a principal authenticated by a trusted transport. This
internal service accepts one strict JSON header per connection, with a binary
body only for ``stage``. It does not authenticate, listen on a socket, invoke a
shell, build, or enable any setup or command-line path.
"""

from __future__ import annotations

from collections.abc import Callable
import math
import re
from typing import Any, BinaryIO

from lib.build_admission import (
    MAX_TTL_SECONDS,
    DeploymentAdmissionStore,
)
from lib.build_protocol import (
    MAX_ARTIFACT_BYTES,
    MAX_DESCRIPTOR_BYTES,
    MAX_REQUEST_BYTES,
    decode_document,
    encode_document,
    parse_build_request,
    request_digest,
)
from lib.build_target_policy import (
    authorize_target_deployment,
    capabilities_for_principal,
    parse_target_deployment,
)


MAX_TARGET_PROJECTION_BYTES = MAX_REQUEST_BYTES
MAX_HEADER_LINE_BYTES = MAX_REQUEST_BYTES + MAX_TARGET_PROJECTION_BYTES + 4096
MAX_RESPONSE_LINE_BYTES = 64 * 1024
MAX_ARTIFACT_BUNDLE_BYTES = MAX_ARTIFACT_BYTES + MAX_DESCRIPTOR_BYTES + 1024
_MAX_GENERATION = 2**63 - 1
_ID = re.compile(r"[0-9a-f]{32}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
_PRINCIPAL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}")
_REVISION = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
_OPERATION_STATES = frozenset({"reserved", "cancelled", "activating", "uncertain", "completed"})
_EFFECTIVE_STATES = frozenset({
    "reserved", "cancelled", "superseded", "expired", "uncertain", "completed",
})


def _fixed_response(*, error: str | None = None, result: object = None) -> bytes:
    if error is None:
        value = {"schema": 1, "ok": True, "result": result, "payload_bytes": 0}
    else:
        value = {"schema": 1, "ok": False, "error": error, "payload_bytes": 0}
    encoded = encode_document(value, maximum=MAX_RESPONSE_LINE_BYTES - 1) + b"\n"
    if len(encoded) > MAX_RESPONSE_LINE_BYTES:
        raise ValueError("Target response exceeds its byte limit")
    return encoded


def _error_code(error: Exception) -> str:
    if isinstance(error, PermissionError):
        return "unavailable" if error.errno is not None else "rejected"
    if isinstance(error, OSError):
        return "unavailable"
    if isinstance(error, (ValueError, RuntimeError)):
        return "rejected"
    return "unavailable"


def _write_all(stream: BinaryIO, value: bytes | memoryview) -> None:
    remaining = memoryview(value)
    while remaining:
        written = stream.write(remaining)
        if type(written) is not int or written <= 0:
            raise OSError("Target response write did not complete")
        remaining = remaining[written:]


def _require_eof(stream: BinaryIO) -> None:
    trailing = stream.read(1)
    if not isinstance(trailing, bytes) or trailing:
        raise ValueError("Unexpected trailing target protocol payload")


def _safe_id(value: object, label: str) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise ValueError(f"Invalid {label}")
    return value


def _safe_slug(value: object, label: str) -> str:
    if not isinstance(value, str) or _SLUG.fullmatch(value) is None:
        raise ValueError(f"Invalid {label}")
    return value


def _safe_principal(value: object) -> str:
    if not isinstance(value, str) or _PRINCIPAL.fullmatch(value) is None:
        raise ValueError("Invalid authenticated principal")
    return value


def _parse_header(header: bytes) -> tuple[str, dict[str, Any]]:
    if (not isinstance(header, bytes) or not header
            or len(header) > MAX_HEADER_LINE_BYTES or not header.endswith(b"\n")
            or b"\n" in header[:-1] or b"\r" in header):
        raise ValueError("Malformed target protocol header")
    document = decode_document(header[:-1], maximum=MAX_HEADER_LINE_BYTES - 1)
    if (not isinstance(document, dict) or type(document.get("schema")) is not int
            or document.get("schema") != 1):
        raise ValueError("Unsupported target protocol schema")

    fields_by_action = {
        "preflight": {"schema", "action", "application"},
        "reserve": {"schema", "action", "idempotency_key", "application",
                    "request", "deployment", "ttl_seconds"},
        "status": {"schema", "action", "operation_id"},
        "cancel": {"schema", "action", "operation_id"},
        "stage": {"schema", "action", "operation_id", "generation", "request",
                  "deployment", "artifact_sha256", "artifact_size"},
        "activate": {"schema", "action", "operation_id", "generation", "request",
                     "deployment", "artifact_sha256"},
    }
    action = document.get("action")
    if not isinstance(action, str) or action not in fields_by_action:
        raise ValueError("Unsupported target protocol action")
    if set(document) != fields_by_action[action]:
        raise ValueError("Target protocol header has missing or unsupported fields")

    fields = dict(document)
    if action == "preflight":
        fields["application"] = _safe_slug(fields["application"], "application")
    elif action == "reserve":
        fields["idempotency_key"] = _safe_id(fields["idempotency_key"], "idempotency key")
        fields["application"] = _safe_slug(fields["application"], "application")
        fields["request"] = parse_build_request(fields["request"])
        if type(fields["ttl_seconds"]) is not int or not 1 <= fields["ttl_seconds"] <= MAX_TTL_SECONDS:
            raise ValueError("Admission TTL is outside supported limits")
        encode_document(fields["deployment"], maximum=MAX_TARGET_PROJECTION_BYTES)
        fields["deployment"] = parse_target_deployment(
            fields["deployment"], request=fields["request"],
        )
        if fields["deployment"]["application"] != fields["application"]:
            raise ValueError("Target projection application does not match the reservation")
    elif action in {"status", "cancel"}:
        fields["operation_id"] = _safe_id(fields["operation_id"], "operation ID")
    else:
        fields["operation_id"] = _safe_id(fields["operation_id"], "operation ID")
        generation = fields["generation"]
        if type(generation) is not int or not 1 <= generation <= _MAX_GENERATION:
            raise ValueError("Target generation is outside supported limits")
        fields["request"] = parse_build_request(fields["request"])
        encode_document(fields["deployment"], maximum=MAX_TARGET_PROJECTION_BYTES)
        fields["deployment"] = parse_target_deployment(
            fields["deployment"], request=fields["request"],
        )
        if (not isinstance(fields["artifact_sha256"], str)
                or _SHA256.fullmatch(fields["artifact_sha256"]) is None):
            raise ValueError("Invalid artifact digest")
        if action == "stage":
            size = fields["artifact_size"]
            if type(size) is not int or not 1 <= size <= MAX_ARTIFACT_BUNDLE_BYTES:
                raise ValueError("Artifact body size is outside supported limits")
            bundle_limit = (fields["request"]["limits"]["artifact_bytes"]
                            + MAX_DESCRIPTOR_BYTES + 1024)
            if size > bundle_limit:
                raise ValueError("Artifact body exceeds the requested artifact ceiling")
    return action, fields


def _public_admission(record: object) -> dict[str, Any]:
    """Project only explicitly approved operation metadata to the caller."""
    required = {
        "operation_id", "application", "generation", "project", "request_id",
        "request_sha256", "source", "bindings_sha256", "created_at", "expires_at",
        "state", "effective_status", "superseded", "expired",
    }
    if not isinstance(record, dict) or not required <= set(record):
        raise ValueError("Admission store returned invalid operation metadata")
    operation_id = _safe_id(record["operation_id"], "operation ID")
    application = _safe_slug(record["application"], "application")
    project = _safe_slug(record["project"], "project")
    request_id = _safe_id(record["request_id"], "request ID")
    request_sha256 = record["request_sha256"]
    bindings_sha256 = record["bindings_sha256"]
    if (not isinstance(request_sha256, str) or _SHA256.fullmatch(request_sha256) is None
            or not isinstance(bindings_sha256, str) or _SHA256.fullmatch(bindings_sha256) is None):
        raise ValueError("Admission store returned invalid operation digests")
    source = record["source"]
    if (not isinstance(source, dict)
            or set(source) != {"revision", "snapshot_sha256", "manifest_sha256"}
            or not isinstance(source["revision"], str)
            or _REVISION.fullmatch(source["revision"]) is None
            or not isinstance(source["snapshot_sha256"], str)
            or _SHA256.fullmatch(source["snapshot_sha256"]) is None
            or not isinstance(source["manifest_sha256"], str)
            or _SHA256.fullmatch(source["manifest_sha256"]) is None):
        raise ValueError("Admission store returned invalid source identity")
    generation = record["generation"]
    if type(generation) is not int or not 1 <= generation <= _MAX_GENERATION:
        raise ValueError("Admission store returned an invalid generation")
    for key in ("created_at", "expires_at"):
        value = record[key]
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError("Admission store returned invalid expiry metadata")
    state = record["state"]
    effective_status = record["effective_status"]
    if state not in _OPERATION_STATES or effective_status not in _EFFECTIVE_STATES:
        raise ValueError("Admission store returned invalid operation status")
    if type(record["superseded"]) is not bool or type(record["expired"]) is not bool:
        raise ValueError("Admission store returned invalid operation flags")
    return {
        "operation_id": operation_id,
        "application": application,
        "project": project,
        "generation": generation,
        "input": {"request_id": request_id, "request_sha256": request_sha256},
        "bindings_sha256": bindings_sha256,
        "source": dict(source),
        "created_at": record["created_at"],
        "expires_at": record["expires_at"],
        "state": state,
        "effective_status": effective_status,
        "superseded": record["superseded"],
        "expired": record["expired"],
    }


class BuildTargetService:
    """Dispatch one target-deployment operation for an authenticated principal.

    ``stage_artifact`` owns exact body framing, durable binding/upload/hash and
    descriptor verification, and a final admission recheck. Its caller must
    supervise blocking I/O with a deadline. ``activate_release`` runs under
    the admission generation lock and must verify the staged identity and
    runtime before it changes or publishes a release.
    """

    def __init__(
        self,
        *,
        admissions: DeploymentAdmissionStore,
        load_policy: Callable[[], object],
        stage_artifact: Callable[..., object],
        activate_release: Callable[..., object],
    ) -> None:
        if not isinstance(admissions, DeploymentAdmissionStore):
            raise ValueError("A deployment admission store is required")
        if any(not callable(callback) for callback in
               (load_policy, stage_artifact, activate_release)):
            raise ValueError("Target service callbacks must be callable")
        self.admissions = admissions
        self.load_policy = load_policy
        self.stage_artifact = stage_artifact
        self.activate_release = activate_release

    def serve_once(
        self,
        *,
        principal: str,
        input_stream: BinaryIO,
        output_stream: BinaryIO,
    ) -> None:
        """Read one bounded header line and dispatch a single request."""
        try:
            header = input_stream.readline(MAX_HEADER_LINE_BYTES + 1)
            if not isinstance(header, bytes):
                raise ValueError("Target protocol input must be binary")
        except (ValueError, OSError) as error:
            _write_all(output_stream, _fixed_response(error=_error_code(error)))
            output_stream.flush()
            return
        self.handle(header, principal=principal, input_stream=input_stream,
                    output_stream=output_stream)

    def handle(
        self,
        header: bytes,
        *,
        principal: str,
        input_stream: BinaryIO,
        output_stream: BinaryIO,
    ) -> None:
        """Dispatch a raw header and stream input only to the typed stage callback."""
        try:
            action, fields = _parse_header(header)
            owner = _safe_principal(principal)
            if action != "stage":
                _require_eof(input_stream)
            result = self._dispatch(action, fields, owner=owner, input_stream=input_stream)
            response = _fixed_response(result=result)
        except Exception as error:
            response = _fixed_response(error=_error_code(error))
        _write_all(output_stream, response)
        output_stream.flush()

    def _dispatch(
        self,
        action: str,
        fields: dict[str, Any],
        *,
        owner: str,
        input_stream: BinaryIO,
    ) -> dict[str, Any]:
        if action == "preflight":
            capabilities = capabilities_for_principal(self.load_policy(), principal=owner)
            application = capabilities["applications"].get(fields["application"])
            if application is None:
                raise PermissionError("Target application grant is unavailable")
            return {
                "application": fields["application"],
                "project": application["project"],
                "routes": application["components"],
                "platform": application["platform"],
                "limits": application["limits"],
            }

        if action == "reserve":
            authorized = authorize_target_deployment(
                self.load_policy(), principal=owner, application=fields["application"],
                request=fields["request"], deployment=fields["deployment"],
            )
            record = self.admissions.reserve(
                fields["application"], fields["request"], owner=owner,
                idempotency_key=fields["idempotency_key"],
                bindings_sha256=authorized["bindings_sha256"],
                ttl_seconds=fields["ttl_seconds"],
            )
            return _public_admission(record)

        if action in {"status", "cancel"}:
            record = self.admissions.status(fields["operation_id"], owner=owner)
            self._authorize_current_grant(owner, record)
            if action == "cancel":
                record = self.admissions.cancel(fields["operation_id"], owner=owner)
            return _public_admission(record)

        if action == "stage":
            record, authorized = self._current_operation_authorization(
                fields, owner=owner,
            )
            if (record.get("state") != "reserved"
                    or record.get("effective_status") != "reserved"):
                raise ValueError("Only the current reserved operation can stage an artifact")
            self.stage_artifact(
                fields["operation_id"], fields["generation"], fields["request"],
                owner=owner,
                bindings_sha256=authorized["bindings_sha256"],
                artifact_sha256=fields["artifact_sha256"],
                artifact_size=fields["artifact_size"],
                source=input_stream,
                admissions=self.admissions,
            )
            return {
                "operation_id": record["operation_id"],
                "generation": record["generation"],
                "artifact_sha256": fields["artifact_sha256"],
                "artifact_size": fields["artifact_size"],
                "status": "staged",
            }

        if action == "activate":
            record, authorized = self._current_operation_authorization(
                fields, owner=owner,
            )

            def activate_under_lock() -> None:
                current = authorize_target_deployment(
                    self.load_policy(), principal=owner,
                    application=record["application"], request=fields["request"],
                    deployment=fields["deployment"],
                )
                self._check_admission_identity(
                    record, fields, owner=owner, authorized=current,
                )
                # The callback's private result is deliberately discarded. The
                # admission ledger and public transport receive no callback data.
                self.activate_release(
                    bindings=current["bindings"],
                    request=fields["request"],
                    operation_id=fields["operation_id"],
                    generation=fields["generation"],
                    artifact_sha256=fields["artifact_sha256"],
                    principal=owner,
                )
                return None

            self.admissions.activate(
                fields["operation_id"], fields["generation"], fields["request"],
                fields["artifact_sha256"], owner=owner,
                bindings_sha256=authorized["bindings_sha256"],
                action=activate_under_lock,
            )
            return {
                "application": record["application"],
                "operation_id": record["operation_id"],
                "generation": record["generation"],
                "artifact_sha256": fields["artifact_sha256"],
                "status": "completed",
            }

        raise ValueError("Unsupported target protocol action")

    def _authorize_current_grant(self, owner: str, record: object) -> None:
        if not isinstance(record, dict):
            raise ValueError("Admission store returned invalid operation metadata")
        application = _safe_slug(record.get("application"), "application")
        project = _safe_slug(record.get("project"), "project")
        capabilities = capabilities_for_principal(self.load_policy(), principal=owner)
        grant = capabilities["applications"].get(application)
        if grant is None or grant["project"] != project:
            raise PermissionError("Current target application grant is unavailable")

    def _current_operation_authorization(
        self,
        fields: dict[str, Any],
        *,
        owner: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        record = self.admissions.status(fields["operation_id"], owner=owner)
        authorization = authorize_target_deployment(
            self.load_policy(), principal=owner,
            application=record.get("application"), request=fields["request"],
            deployment=fields["deployment"],
        )
        self._check_admission_identity(record, fields, owner=owner, authorized=authorization)
        return record, authorization

    @staticmethod
    def _check_admission_identity(
        record: object,
        fields: dict[str, Any],
        *,
        owner: str,
        authorized: dict[str, Any],
    ) -> None:
        request = fields["request"]
        deployment = fields["deployment"]
        if not isinstance(record, dict):
            raise ValueError("Admission store returned invalid operation metadata")
        if (record.get("owner") != owner
                or record.get("operation_id") != fields["operation_id"]
                or type(record.get("generation")) is not int
                or record["generation"] != fields["generation"]
                or record.get("application") != deployment["application"]
                or record.get("project") != request["project"]
                or record.get("request_id") != request["request_id"]
                or record.get("request_sha256") != request_digest(request)
                or record.get("source") != request["source"]
                or record.get("bindings_sha256") != authorized["bindings_sha256"]):
            raise ValueError("Target operation does not match its admitted identity")


__all__ = [
    "BuildTargetService",
    "MAX_ARTIFACT_BUNDLE_BYTES",
    "MAX_HEADER_LINE_BYTES",
    "MAX_RESPONSE_LINE_BYTES",
    "MAX_TARGET_PROJECTION_BYTES",
]
