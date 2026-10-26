"""Controller-side typed SSH operations for one explicitly trusted target.

The build client's private transport extension points keep credential pinning,
bounded pipe supervision and process cleanup identical for both protocols.
"""

from __future__ import annotations

import os
import time
from typing import Any

from lib.build_admission import MAX_OPERATIONS, _source, _timestamp
from lib.build_client import (
    BuildClientError,
    BuildHostClient,
    BuildHostProtocolError,
    BuildHostRemoteError,
    _Reply,
    _digest,
)
from lib.build_protocol import decode_document, encode_document, request_digest
from lib.build_target_policy import (
    MAX_COMPONENTS_PER_APPLICATION,
    _platform,
    _route,
    _target_limits,
)
from lib.build_target_service import (
    MAX_ARTIFACT_BUNDLE_BYTES,
    MAX_HEADER_LINE_BYTES,
    MAX_RESPONSE_LINE_BYTES,
    _parse_header,
    _safe_id,
    _safe_slug,
)


REMOTE_COMMAND = "basaltwater-target-v1"
_ADMISSION_FIELDS = frozenset({
    "operation_id", "application", "project", "generation", "input",
    "bindings_sha256", "source", "created_at", "expires_at", "state",
    "effective_status", "superseded", "expired",
})


class TargetClientError(BuildClientError):
    """A target client failure with no remote diagnostic data."""


class TargetProtocolError(TargetClientError):
    """The target response violated the typed public protocol."""


class TargetTransportError(TargetClientError):
    """The finite SSH transport did not complete."""


class TargetRemoteError(TargetClientError):
    """The target returned a fixed public rejection code."""

    def __init__(self, code: str) -> None:
        super().__init__(f"Target returned {code}", transport_started=True)
        self.code = code


class TargetOperationUnconfirmed(TargetClientError):
    """A mutating call may already have changed durable target state."""

    def __init__(self, action: str, fields: dict[str, Any], *, code: str | None) -> None:
        self.action = action
        self.operation_id = fields.get("operation_id")
        self.idempotency_key = fields.get("idempotency_key")
        self.error_code = code
        if action == "reserve":
            guidance = (
                f"repeat reserve with the same idempotency key {self.idempotency_key} "
                "and exact inputs to recover the operation ID; do not choose a new key"
            )
        else:
            guidance = (
                f"query status for the same operation ID {self.operation_id} and "
                "reconcile its state and exact inputs before retrying"
            )
        super().__init__(f"Target {action} is unconfirmed; {guidance}", transport_started=True)


def _record(value: object, keys: set[str] | frozenset[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError("Invalid target response fields")
    return dict(value)


def _preflight(value: object, application: str) -> dict[str, Any]:
    result = _record(value, {"application", "project", "routes", "platform", "limits"})
    if _safe_slug(result["application"], "application") != application:
        raise ValueError("Target returned a different application")
    _safe_slug(result["project"], "project")
    result["platform"] = _platform(result["platform"], "target platform")
    result["limits"] = _target_limits(result["limits"], "target limits")
    routes = result["routes"]
    if not isinstance(routes, list) or not 1 <= len(routes) <= MAX_COMPONENTS_PER_APPLICATION:
        raise ValueError("Invalid target route list")
    names: set[str] = set()
    destinations: set[tuple[str, str]] = set()
    checked = []
    for value in routes:
        route = _record(value, {"name", "type", "domain", "path"})
        name = _safe_slug(route.pop("name"), "component")
        normalized = _route(route, "target route")
        destination = (normalized["domain"], normalized["path"])
        if route != normalized or name in names or destination in destinations:
            raise ValueError("Invalid target route identity")
        names.add(name)
        destinations.add(destination)
        checked.append({"name": name, **normalized})
    result["routes"] = checked
    return result


def _admission(value: object, action: str, fields: dict[str, Any]) -> dict[str, Any]:
    result = _record(value, _ADMISSION_FIELDS)
    operation_id = _safe_id(result["operation_id"], "operation ID")
    _safe_slug(result["application"], "application")
    _safe_slug(result["project"], "project")
    generation = result["generation"]
    if type(generation) is not int or not 1 <= generation <= MAX_OPERATIONS:
        raise ValueError("Invalid target generation")
    identity = _record(result["input"], {"request_id", "request_sha256"})
    if _safe_id(identity["request_id"], "request ID") == operation_id:
        raise ValueError("Target operation and build request IDs must differ")
    _digest(identity["request_sha256"], "Request digest")
    _digest(result["bindings_sha256"], "Bindings digest")
    result["input"] = identity
    result["source"] = _source(result["source"])
    created = _timestamp(result["created_at"], "Admission creation")
    expires = _timestamp(result["expires_at"], "Admission expiry")
    if expires <= created:
        raise ValueError("Invalid target expiry")
    state = result["state"]
    if not isinstance(state, str) or state not in {
        "reserved", "cancelled", "activating", "uncertain", "completed",
    }:
        raise ValueError("Invalid target operation state")
    if type(result["superseded"]) is not bool or type(result["expired"]) is not bool:
        raise ValueError("Invalid target operation flags")
    effective = "uncertain" if state == "activating" else state
    if state == "reserved":
        if result["superseded"]:
            effective = "superseded"
        elif result["expired"]:
            effective = "expired"
    if result["effective_status"] != effective:
        raise ValueError("Target operation state and flags disagree")
    if action == "reserve":
        request = fields["request"]
        if (result["application"] != fields["application"]
                or result["project"] != request["project"]
                or identity != {"request_id": request["request_id"],
                                "request_sha256": request_digest(request)}
                or result["source"] != request["source"]):
            raise ValueError("Target returned a different reservation identity")
    elif operation_id != fields["operation_id"]:
        raise ValueError("Target returned a different operation ID")
    if action == "cancel" and state != "cancelled":
        raise ValueError("Target did not acknowledge cancellation")
    return result


def _acknowledgement(value: object, action: str, fields: dict[str, Any]) -> dict[str, Any]:
    keys = {"operation_id", "generation", "artifact_sha256", "status"}
    keys.add("artifact_size" if action == "stage" else "application")
    result = _record(value, keys)
    if type(result["generation"]) is not int:
        raise ValueError("Invalid target acknowledgement generation")
    for key in ("operation_id", "generation", "artifact_sha256"):
        if result[key] != fields[key]:
            raise ValueError("Target acknowledged a different operation or artifact")
    if action == "stage":
        if (type(result["artifact_size"]) is not int
                or result["artifact_size"] != fields["artifact_size"]
                or result["status"] != "staged"):
            raise ValueError("Invalid target staging acknowledgement")
    elif (result["application"] != fields["deployment"]["application"]
          or result["status"] != "completed"):
        raise ValueError("Invalid target activation acknowledgement")
    return result


def _validate_reply(line: bytes, *, action: str, fields: dict[str, Any],
                    payload_limit: int,
                    expected_artifact: tuple[str, int] | None = None) -> _Reply:
    try:
        if (len(line) > MAX_RESPONSE_LINE_BYTES or not line.endswith(b"\n")
                or b"\n" in line[:-1] or b"\r" in line):
            raise ValueError("Invalid response framing")
        value = decode_document(line[:-1], maximum=MAX_RESPONSE_LINE_BYTES - 1)
        if not isinstance(value, dict) or type(value.get("schema")) is not int or value["schema"] != 1:
            raise ValueError("Invalid target response schema")
        if type(value.get("payload_bytes")) is not int or value["payload_bytes"] != 0:
            raise ValueError("Target responses cannot have a payload")
        if value.get("ok") is False:
            _record(value, {"schema", "ok", "error", "payload_bytes"})
            if not isinstance(value["error"], str) or value["error"] not in {"rejected", "unavailable"}:
                raise ValueError("Invalid target error code")
            return _Reply(None, 0, value["error"])
        _record(value, {"schema", "ok", "result", "payload_bytes"})
        if value["ok"] is not True:
            raise ValueError("Invalid target success flag")
        if action == "preflight":
            result = _preflight(value["result"], fields["application"])
        elif action in {"reserve", "status", "cancel"}:
            result = _admission(value["result"], action, fields)
        else:
            result = _acknowledgement(value["result"], action, fields)
        return _Reply(result, 0, None)
    except (ValueError, TypeError, KeyError, RecursionError, UnicodeError, OverflowError):
        raise TargetProtocolError("Target returned an invalid response", transport_started=True) from None


class _TargetTransport(BuildHostClient):
    """Private specialization; the public target API uses composition."""

    _remote_command = REMOTE_COMMAND
    _header_limit = MAX_HEADER_LINE_BYTES
    _parse_reply = staticmethod(_validate_reply)


class BuildTargetClient:
    """One explicitly trusted target, fresh SSH per call, without retries."""

    def __init__(self, *, host: str, username: str, port: int,
                 identity_file: str | os.PathLike[str],
                 known_hosts_file: str | os.PathLike[str], timeout: int | float) -> None:
        self._transport = _TargetTransport(
            host=host, username=username, port=port, identity_file=identity_file,
            known_hosts_file=known_hosts_file, timeout=timeout,
        )

    def close(self) -> None:
        """Release pinned credential descriptors; safe to repeat."""
        self._transport.close()

    def __enter__(self) -> BuildTargetClient:
        self._transport.__enter__()
        return self

    def __exit__(self, _kind: object, _value: object, _traceback: object) -> None:
        self.close()

    def preflight(self, application: str) -> dict[str, Any]:
        """Return public routes, platform and artifact ceilings for this app."""
        return self._execute("preflight", {"application": application})

    def reserve(self, *, idempotency_key: str, application: str, request: object,
                deployment: object, ttl_seconds: int) -> dict[str, Any]:
        """Reserve target intent or recover an exact-key replay."""
        return self._execute("reserve", {
            "idempotency_key": idempotency_key, "application": application,
            "request": request, "deployment": deployment, "ttl_seconds": ttl_seconds,
        })

    def status(self, operation_id: str) -> dict[str, Any]:
        """Read one public admission record; read errors are never unconfirmed."""
        return self._execute("status", {"operation_id": operation_id})

    def cancel(self, operation_id: str) -> dict[str, Any]:
        """Request durable cancellation before activation starts."""
        return self._execute("cancel", {"operation_id": operation_id})

    def stage(self, *, operation_id: str, generation: int, request: object,
              deployment: object, artifact_path: str | os.PathLike[str],
              artifact_sha256: str, artifact_size: int) -> dict[str, Any]:
        """Hash then stream a pinned regular artifact with exact size and EOF."""
        return self._execute("stage", {
            "operation_id": operation_id, "generation": generation, "request": request,
            "deployment": deployment, "artifact_sha256": artifact_sha256,
            "artifact_size": artifact_size,
        }, artifact_path=artifact_path)

    def activate(self, *, operation_id: str, generation: int, request: object,
                 deployment: object, artifact_sha256: str) -> dict[str, Any]:
        """Activate or acknowledge a cached completion of the same identity."""
        return self._execute("activate", {
            "operation_id": operation_id, "generation": generation, "request": request,
            "deployment": deployment, "artifact_sha256": artifact_sha256,
        })

    def _execute(self, action: str, fields: dict[str, Any], *,
                 artifact_path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
        deadline = time.monotonic() + self._transport.timeout
        # Reuse the endpoint's pure parser for identical field, type, projection,
        # request identity and size admission. No local path enters the document.
        header = encode_document({"schema": 1, "action": action, **fields},
                                 maximum=MAX_HEADER_LINE_BYTES - 1) + b"\n"
        _, validated = _parse_header(header)
        validated.pop("schema")
        validated.pop("action")
        source = None
        started = False

        def on_start() -> None:
            nonlocal started
            started = True

        try:
            try:
                if action == "stage":
                    source = self._transport._open_source(
                        artifact_path, validated["artifact_sha256"], deadline=deadline,
                        maximum=MAX_ARTIFACT_BUNDLE_BYTES, expected_size=validated["artifact_size"],
                    )
                result, _payload = self._transport._call(
                    action, validated, source=source, deadline=deadline, on_start=on_start,
                )
                return result
            finally:
                if source is not None:
                    os.close(source.descriptor)
        except (Exception, KeyboardInterrupt) as exc:
            code = exc.code if isinstance(exc, BuildHostRemoteError) else None
            if action in {"reserve", "cancel", "stage", "activate"} and started:
                raise TargetOperationUnconfirmed(action, validated, code=code) from None
            if not isinstance(exc, BuildClientError):
                raise
            if isinstance(exc, TargetClientError):
                raise
            if code is not None:
                raise TargetRemoteError(code) from None
            error_type = TargetProtocolError if isinstance(exc, BuildHostProtocolError) else TargetTransportError
            raise error_type("Target SSH operation did not complete",
                             transport_started=exc.transport_started) from None


__all__ = [
    "BuildTargetClient", "TargetClientError", "TargetOperationUnconfirmed",
    "TargetProtocolError", "TargetRemoteError", "TargetTransportError", "REMOTE_COMMAND",
]
