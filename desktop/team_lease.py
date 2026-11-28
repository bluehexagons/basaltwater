"""Opt-in ownership of helper actions on one physical desktop resource."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import re
import secrets
import stat

from desktop.qa_artifacts import clock, stamp
from desktop.team_records import (
    TeamBusy, _record, identifier, load_team, locked, message, object_file, private_directory, require_worker,
)
from lib.atomic_io import remove_file_durable, write_json_atomic

CAPABILITY = "team-lease-v1"
LEASE_ENV = "BASALTW_DESKTOP_LEASE_FILE"
PASSTHROUGH = {"status", "stop", "revoke", "control", "handoff", "browser-take", "browser-release",
               "browser-refresh", "browser-logout", "maintenance-logout", "launch-status"}


class LeaseRejected(PermissionError):
    """The request was refused before any desktop or gamepad dispatch."""


def _digest(token):
    if not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{64}", token):
        return ""
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def _credential(path):
    folder = private_directory(Path(path).absolute().parent)
    candidate = folder / Path(path).name
    info = candidate.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise LeaseRejected("Expected a private desktop lease file")
    data = object_file(candidate, max_bytes=2048)
    if not _digest(data.get("token")):
        raise LeaseRejected("Invalid desktop lease file")
    return data["token"]


def wire_request(payload):
    path = os.environ.get(LEASE_ENV)
    if path and payload.get("action") not in PASSTHROUGH:
        return {**payload, "team_lease_token": _credential(path)}
    return payload


def _read(folder):
    try:
        pointer = object_file(folder / "team-resource.json", max_bytes=8192)
    except FileNotFoundError:
        return None, None
    root, team = load_team(pointer["team"])
    resource = identifier(pointer["resource"], "Resource")
    if resource not in team["resources"]:
        raise LeaseRejected("Active team resource is absent from the assignment")
    statefile = root / "leases" / f"{resource}.json"
    return statefile, object_file(statefile, max_bytes=2 * 1024 * 1024)


def _save(path, state):
    write_json_atomic(str(path), state)


def _public(path, state):
    events = state["events"]
    return {"artifact": str(path), "team": state["team"], "resource": state["resource"],
            "backend": state["backend"], "owner": state["owner"], "state": state["state"], "epoch": state["epoch"],
            "generation": state["generation"], "pending_input": state["pending_input"],
            "last_event": events[-1], "event_count": len(events)}


def _valid(token, expected):
    return bool(expected) and secrets.compare_digest(_digest(token), expected)


def resource_directory(native):
    from desktop import native_session, session_runtime

    if native:
        return native_session.runtime_directory(create=True)
    return session_runtime.runtime_directory()


@contextmanager
def admission(folder, payload, *, channel, generation):
    """Keep the resource locked through dispatch, release, and receipt writing."""
    token = payload.pop("team_lease_token", None)
    payload.pop("_team_lease_context", None)
    action = payload.get("action")
    if (channel == "helper" and action in PASSTHROUGH) or (channel == "pad" and action in {"status", "stop"}):
        yield None
        return
    with locked(folder, "team-resource.lock", nonblocking=True):
        statefile, state = _read(folder)
        if state is None or state["state"] == "closed":
            if token is not None:
                raise LeaseRejected("Team lease is closed or inactive; inspect ownership")
            yield None
            return
        if not _valid(token, state["owner_digest"]):
            raise LeaseRejected("This desktop is assigned to another worker; inspect team lease status")
        if state["generation"] != generation:
            raise LeaseRejected("Desktop helper changed; coordinator recovery and fresh observations are required")
        if state["pending_input"] and action not in {"screenshot", "inspect", "windows"}:
            raise LeaseRejected("Prior input delivery or release is uncertain; inspect and request coordinator recovery")
        if channel == "pad" and action == "press":
            source = payload.get("observation")
            identity = source.get("observation_id") if isinstance(source, dict) else None
        elif action == "input":
            identity = payload.get("observation_id")
        else:
            identity = None
        if action in {"input", "press"} and (
                not isinstance(identity, str) or state["observation"] != identity):
            raise LeaseRejected("Capture and inspect a fresh frame in this lease epoch before input")
        if action == "element" and payload.get("ref") not in state["references"]:
            raise LeaseRejected("Inspect the element again under the current lease")
        if action in {"input", "press", "element", "exec", "window", "development", "logout", "screenshot"}:
            state.update(observation=None, references=[])
            _save(statefile, state)
        yield (statefile, state)


def dispatch(folder, payload, generation, call, *, channel="helper", rejection=()):
    with admission(folder, payload, channel=channel, generation=generation) as lease:
        if lease is None:
            return call(payload)
        statefile, state = lease
        action = payload.get("action")
        context = {"resource": state["resource"], "epoch": state["epoch"], "owner": state["owner"]}
        payload["_team_lease_context"] = context
        input_operation = action in {"input", "press", "element"}
        if input_operation:
            state["pending_input"] = {"channel": channel, "owner": state["owner"], "epoch": state["epoch"],
                                      "started": stamp()}
            _save(statefile, state)
        try:
            response = call(payload)
        except Exception as exc:
            receipt = getattr(exc, "receipt", {})
            if input_operation and (isinstance(exc, rejection) or receipt.get("input_delivery") == "not-sent"):
                state.update(pending_input=None, last_input=stamp())
                _save(statefile, state)
            raise
        if action == "screenshot":
            state["observation"] = response.get("observation_id")
            _save(statefile, state)
        elif action == "inspect":
            state["references"] = [row["ref"] for row in response.get("elements", []) if isinstance(row.get("ref"), str)]
            _save(statefile, state)
        elif input_operation:
            if action == "element":
                complete = "requested" in response and "error" not in response
            elif channel == "helper":
                complete = response.get("input_delivery") in {"completed", "not-sent"}
            else:
                complete = response.get("delivery") in {"completed", "not-sent"} and response.get("neutralized") is True
            if complete:
                state.update(pending_input=None, last_input=stamp())
                _save(statefile, state)
        if input_operation or action in {"screenshot", "inspect"}:
            response = {**response, "team_lease": context}
        return response


def dispatch_helper(folder, payload, generation, call):
    from desktop.receipts import input_failure

    try:
        return dispatch(folder, payload, generation, call)
    except (LeaseRejected, TeamBusy) as exc:
        if payload.get("action") != "input":
            raise
        return {**input_failure(exc, payload), "reason": "team-lease-refused"}


def _issue(root, resource, worker, epoch, purpose):
    token = secrets.token_hex(32)
    folder = private_directory(root / "workers" / worker)
    path = folder / f"{resource}-{epoch}-{purpose}-{secrets.token_hex(8)}.lease.json"
    _save(path, {"token": token})
    return str(path), _digest(token)


def _live_generation(backend, native):
    from desktop.gamepad_qa import backend_name, call

    current = backend.status()
    generation = current.get("generation")
    if (current.get("state") != "running" or not isinstance(generation, str)
            or CAPABILITY not in current.get("capabilities", [])):
        raise LeaseRejected("A running desktop helper with team-lease-v1 is required")
    # The pad socket is shared across backends. A mismatched running pad refuses
    # this status request, so do not activate while its input route is unknown.
    pad = call({"action": "status", "backend": backend_name(native)})
    if pad.get("error") or pad.get("running") and pad.get("team_lease") != CAPABILITY:
        raise LeaseRejected("Confirm the running gamepad supports team-lease-v1, or stop it normally before activation")
    return generation


def _journal(state, action, *, actor, evidence=None, delivery=None, note=None):
    if len(state["events"]) >= 256 and action != "release":
        raise ValueError("Lease event limit reached; close it and prepare a new assignment")
    state["events"].append({"event": action, "actor": actor, "owner": state["owner"], "epoch": state["epoch"],
                            "generation": state["generation"], "at": stamp(), "clock": clock(), "evidence": evidence,
                            "delivery": delivery, "note": note})


def claim(path, resource, worker, *, native, backend):
    root, team = load_team(path)
    assignment = require_worker(team, worker)
    identifier(resource, "Resource")
    from desktop.gamepad_qa import backend_name

    folder, name = resource_directory(native), backend_name(native)
    if team["resources"].get(resource) != name or (worker != "coordinator" and assignment["resource"] != resource):
        raise ValueError("Worker or resource does not match the selected desktop backend")
    with locked(folder, "team-resource.lock", nonblocking=True):
        active_file, active = _read(folder)
        if active and active["state"] != "closed":
            raise LeaseRejected(f"Desktop already assigned to {active['owner']}; inspect {active_file}")
        statefile = root / "leases" / f"{resource}.json"
        if statefile.exists():
            raise ValueError("Resource already has a lease history; use a new assignment")
        generation = _live_generation(backend, native)
        epoch = 1
        credential, owner_digest = _issue(root, resource, worker, epoch, "owner")
        recovery, recovery_digest = _issue(root, resource, "coordinator", epoch, "recovery")
        state = {"schema_version": 1, "kind": "desktop-team-lease", "state": "active", "team": str(root),
                 "resource": resource, "backend": name, "epoch": epoch, "generation": generation, "owner": worker,
                 "owner_digest": owner_digest, "recovery_digest": recovery_digest, "lease_file": credential,
                 "observation": None, "references": [], "pending_input": None, "last_input": None, "events": []}
        _journal(state, "claim", actor="coordinator")
        _save(statefile, state)
        _save(folder / "team-resource.json", {"team": str(root), "resource": resource})
        return {**_public(statefile, state), "lease_file": credential, "recovery_file": recovery,
                "lease_environment": LEASE_ENV}


def status(*, native):
    from desktop.gamepad_qa import backend_name

    folder, name = resource_directory(native), backend_name(native)
    statefile, state = _read(folder)
    return _public(statefile, state) if state else {"backend": name, "state": "inactive"}


def receipt(*, native, epoch, credential, result):
    """Recover the reply for the current completed transfer without changing ownership."""
    folder = resource_directory(native)
    with locked(folder, "team-resource.lock", nonblocking=True):
        statefile, state = _read(folder)
        if (not state or state["state"] != "active" or state["epoch"] != epoch
                or state["events"][-1]["event"] not in ("handoff", "recover")
                or state["events"][-1]["evidence"] != result):
            raise LeaseRejected("No matching current lease transfer; inspect lease status")
        if not _valid(_credential(credential), state["recovery_digest"]):
            raise LeaseRejected("Reading a transfer receipt requires the coordinator recovery credential")
        if state["pending_input"]:
            raise LeaseRejected("Input delivery is uncertain; reconcile it before resuming the transfer")
        lease_file = state.get("lease_file")
        if not lease_file:
            raise LeaseRejected("No saved transfer credential; use coordinator recovery")
        try:
            valid = _valid(_credential(lease_file), state["owner_digest"])
        except FileNotFoundError:
            valid = False
        if not valid:
            raise LeaseRejected("The current credential is unavailable; use coordinator recovery")
        return {**_public(statefile, state), "lease_file": lease_file, "lease_environment": LEASE_ENV}


def transition(action, *, native, backend, epoch, worker, credential, result, note):
    folder = resource_directory(native)
    with locked(folder, "team-resource.lock", nonblocking=True):
        statefile, state = _read(folder)
        if state is None or state["state"] != "active" or state["epoch"] != epoch:
            raise LeaseRejected("Active lease epoch changed; inspect ownership")
        root, team = load_team(state["team"])
        actor = "coordinator" if action == "recover" else state["owner"]
        digest = state["recovery_digest"] if action == "recover" else state["owner_digest"]
        if not _valid(_credential(credential), digest):
            raise LeaseRejected("Lease credential is stale or does not authorize this transition")
        if state["pending_input"] and action != "recover":
            raise LeaseRejected("Coordinator recovery is required before transferring or releasing this desktop")
        row = _record(root / "results", result)
        previous = state["events"][-1]
        last_input = state["last_input"] or previous["at"]
        if state["pending_input"]:
            last_input = state["pending_input"]["started"]
        if (row["worker"] != actor or not row["clock"]["boot_id"]
                or row["clock"] != previous["clock"]
                or row["created"]["monotonic_ns"] < last_input["monotonic_ns"]):
            raise ValueError("The transition requires a new result from its actor after the last input in this ownership epoch")
        explanation = message(note, "Transition note")
        if row["delivery"] in {"unknown", "partial"}:
            raise LeaseRejected("Input delivery is uncertain; coordinator recovery requires a reconciled result")
        if action == "handoff":
            current = backend.status()
            if current.get("generation") != state["generation"] or current.get("state") != "running":
                raise LeaseRejected("Desktop helper changed; use coordinator recovery")
        state.update(observation=None, references=[], pending_input=None, last_input=None, epoch=epoch + 1)
        lease_file = None
        if action == "release":
            state.update(state="closed", owner=None, owner_digest=None, recovery_digest=None, lease_file=None)
        else:
            selected = require_worker(team, worker)
            if worker != "coordinator" and selected["resource"] != state["resource"]:
                raise ValueError("Next worker is not assigned to this resource")
            if action == "recover":
                state["generation"] = _live_generation(backend, native)
            lease_file, state["owner_digest"] = _issue(root, state["resource"], worker, state["epoch"], "owner")
            state.update(owner=worker, lease_file=lease_file)
        _journal(state, action, actor=actor, evidence=result, delivery=row["delivery"], note=explanation)
        _save(statefile, state)
        if action == "release":
            remove_file_durable(folder / "team-resource.json")
        return {**_public(statefile, state), **({"lease_file": lease_file, "lease_environment": LEASE_ENV} if lease_file else {})}
