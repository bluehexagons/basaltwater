"""Explicit controller SSH authority and one-attempt tagged device enrollment."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import os
import re
import uuid

from lib.concurrency import resource_lock
from lib.headscale_admin import VERSION, approved_tags, identifier
from lib.headscale_client import _identity
from lib.headscale_config import validate_headscale_url
from lib.headscale_private import read_private, write_private
from lib.headscale_registry import resolve_server
from lib.headscale_transport import SSHBinding, exchange, parse_ssh_binding
from lib.workspace import normalize_workspace_dir


@dataclass(frozen=True)
class ManagementBinding:
    server_id: str
    endpoint: str
    ssh: SSHBinding

    def to_dict(self) -> dict:
        return {"server_id": self.server_id, "endpoint": self.endpoint, "ssh": self.ssh.to_dict()}


def _path(workspace: str | None, name: str) -> str:
    return os.path.join(normalize_workspace_dir(workspace), name)


def _bindings(workspace: str | None) -> dict[str, ManagementBinding]:
    state = read_private(_path(workspace, "headscale-management.json"))
    if state is None:
        return {}
    if (set(state) != {"schema", "bindings"} or type(state["schema"]) is not int
            or state["schema"] != 1 or type(state["bindings"]) is not dict):
        raise ValueError("Invalid private management bindings; preserve for recovery")
    result = {}
    for server_id, value in state["bindings"].items():
        if (not isinstance(server_id, str) or str(uuid.UUID(server_id)) != server_id
                or type(value) is not dict or set(value) != {"server_id", "endpoint", "ssh"}
                or value["server_id"] != server_id):
            raise ValueError("Invalid private management identity")
        endpoint = validate_headscale_url(value["endpoint"])
        if endpoint != value["endpoint"]:
            raise ValueError("Noncanonical management endpoint")
        result[server_id] = ManagementBinding(server_id, endpoint, parse_ssh_binding(value["ssh"]))
    return result


def _save_bindings(workspace: str | None, bindings: dict[str, ManagementBinding]) -> None:
    write_private(_path(workspace, "headscale-management.json"), {
        "schema": 1, "bindings": {key: value.to_dict() for key, value in bindings.items()},
    })


def resolve_management(reference: str, *, workspace: str | None = None) -> ManagementBinding:
    selected = resolve_server(reference, workspace)
    binding = _bindings(workspace).get(selected.id)
    if binding is None or binding.endpoint != selected.url:
        raise ValueError("Explicit SSH management binding is missing or its endpoint changed")
    return binding


def _control(binding: ManagementBinding, action: str, parameters: dict) -> dict:
    return exchange(binding.ssh, "management", {"endpoint": binding.endpoint, "action": action, "parameters": parameters})


def bind_management(reference: str, ssh: SSHBinding, *, workspace: str | None = None,
                    dry_run: bool = False) -> ManagementBinding:
    selected = resolve_server(reference, workspace)
    candidate = ManagementBinding(selected.id, selected.url, ssh)
    if dry_run:
        current = _bindings(workspace).get(selected.id)
        if current is not None and current != candidate:
            raise ValueError("Remove the existing management binding explicitly before replacing it")
        return candidate
    with resource_lock("headscale-management", normalize_workspace_dir(workspace)):
        current = _bindings(workspace)
        if selected.id in current and current[selected.id] != candidate:
            raise ValueError("Remove the existing management binding explicitly before replacing it")
        if _control(candidate, "identity", {}) != {"endpoint": selected.url, "version": VERSION}:
            raise ValueError("Observed managed server differs from explicit binding")
        # Recheck discovery after network I/O before publishing controller state.
        if resolve_server(selected.id, workspace) != selected:
            raise ValueError("Server reference changed during management binding")
        current[selected.id] = candidate
        _save_bindings(workspace, current)
    return candidate


def _journal(workspace: str | None) -> dict | None:
    value = read_private(_path(workspace, "headscale-enrollment.json"))
    if value is None:
        return None
    if (set(value) != {"schema", "attempt", "server_id", "endpoint", "control", "target", "tags", "phase", "key_id", "node_id"}
            or type(value["schema"]) is not int or value["schema"] != 1
            or not isinstance(value["phase"], str)
            or value["phase"] not in {"issuing", "issued", "delivering", "verifying", "complete", "reconciled"}):
        raise ValueError("Invalid private enrollment journal; preserve for recovery")
    for field in ("attempt", "server_id"):
        if not isinstance(value[field], str) or str(uuid.UUID(value[field])) != value[field]:
            raise ValueError("Invalid enrollment identity")
    validate_headscale_url(value["endpoint"])
    parse_ssh_binding(value["control"])
    parse_ssh_binding(value["target"])
    approved_tags(value["tags"])
    for field in ("key_id", "node_id"):
        if value[field] != "":
            identifier(value[field])
    return value


def inspect_enrollment(*, workspace: str | None = None) -> dict | None:
    """Read-only metadata; no locks, network calls, key values or native Auth IDs."""
    return _journal(workspace)


def inspect_management(reference: str, *, workspace: str | None = None) -> dict:
    return _control(resolve_management(reference, workspace=workspace), "inspect", {})


def remove_management(reference: str, *, workspace: str | None = None, dry_run: bool = False) -> None:
    selected = resolve_server(reference, workspace)
    def remove():
        records = _bindings(workspace)
        attempt = _journal(workspace)
        if attempt and attempt["server_id"] == selected.id and attempt["phase"] not in {"complete", "reconciled"}:
            raise ValueError("Reconcile the retained enrollment before removing management authority")
        if selected.id not in records:
            raise ValueError("No management binding exists")
        del records[selected.id]
        if not dry_run:
            _save_bindings(workspace, records)
    if dry_run:
        remove()
    else:
        with resource_lock("headscale-management", normalize_workspace_dir(workspace)):
            remove()


def _observed_identity(value: object, endpoint: str, tags: list[str]) -> dict:
    fields = {"node_id", "addresses", "login_server", "tags", "online", "hostname"}
    if type(value) is not dict or set(value) != fields or value["login_server"] != endpoint:
        raise ValueError("Invalid observed target identity")
    identity = _identity({"BackendState": "Running", "Self": {
        "ID": value["node_id"], "TailscaleIPs": value["addresses"], "Tags": value["tags"],
        "Online": value["online"], "HostName": value["hostname"],
    }}, {"ControlURL": endpoint}, endpoint, True)
    identifier(identity.node_id)
    if sorted(identity.tags) != tags:
        raise ValueError("Observed target service tags differ from authorization")
    return asdict(identity)


def _verify_node(node: object, identity: dict) -> dict:
    if (type(node) is not dict or node.get("node_id") != identity["node_id"]
            or node.get("id") != identity["node_id"] or node.get("user_id") is not None
            or node.get("online") is not True or type(node.get("addresses")) is not list
            or set(node["addresses"]) != set(identity["addresses"])
            or type(node.get("tags")) is not list or sorted(node["tags"]) != sorted(identity["tags"])):
        raise ValueError("Server and target enrollment observations differ")
    return node


def enroll_machine(reference: str, target: SSHBinding, tags: list[str], *,
                   workspace: str | None = None, dry_run: bool = False) -> dict:
    tags = approved_tags(tags)
    binding = resolve_management(reference, workspace=workspace)
    old = _journal(workspace)
    if old and old["phase"] not in {"complete", "reconciled"}:
        raise ValueError("Reconcile the previous enrollment before issuing another key")
    if dry_run:
        return {"dry_run": True, "endpoint": binding.endpoint, "tags": tags}
    with resource_lock("headscale-management", normalize_workspace_dir(workspace)), \
            resource_lock("headscale-enrollment", normalize_workspace_dir(workspace)):
        if resolve_management(binding.server_id, workspace=workspace) != binding or _journal(workspace) != old:
            raise ValueError("Enrollment selection changed; inspect before retry")
        if _control(binding, "identity", {}) != {"endpoint": binding.endpoint, "version": VERSION}:
            raise ValueError("Managed server identity differs")
        observed = exchange(target, "client", {"action": "inspect", "url": binding.endpoint, "tags": tags})
        if observed.get("phase") == "enrolled":
            identity = _observed_identity(observed.get("identity"), binding.endpoint, tags)
            nodes = _control(binding, "inspect", {}).get("nodes", [])
            matches = [node for node in nodes if node.get("node_id") == identity["node_id"]]
            if len(matches) != 1:
                raise ValueError("Existing target is unavailable on managed server")
            return {"node": _verify_node(matches[0], identity), "already_enrolled": True}
        if observed != {"phase": "prepared", "identity": None}:
            raise ValueError("Target is not prepared for explicit enrollment")
        journal = {"schema": 1, "attempt": str(uuid.uuid4()), "server_id": binding.server_id,
                   "endpoint": binding.endpoint, "control": binding.ssh.to_dict(), "target": target.to_dict(),
                   "tags": tags, "phase": "issuing", "key_id": "", "node_id": ""}
        path = _path(workspace, "headscale-enrollment.json")
        write_private(path, journal)  # BEFORE the remote issuance side effect.
        issued = _control(binding, "issue", {"tags": tags})
        if (set(issued) != {"id", "expiry", "tags", "reusable", "ephemeral"}
                or issued["reusable"] is not False or issued["ephemeral"] is not False or issued["tags"] != tags):
            raise ValueError("Issued key attributes differ; inspect retained intent")
        identifier(issued["id"])
        expiry = datetime.fromisoformat(issued["expiry"].replace("Z", "+00:00"))
        if not 0 < (expiry - datetime.now(timezone.utc)).total_seconds() <= 3660:
            raise ValueError("Issued key expiry differs; inspect retained intent")
        journal.update(phase="issued", key_id=issued["id"])
        write_private(path, journal)
        journal["phase"] = "delivering"
        write_private(path, journal)  # An uncertain take can never be replayed.
        material = _control(binding, "take", {"id": issued["id"]})
        if (set(material) != {"id", "key", "tags", "endpoint"} or material["id"] != issued["id"]
                or material["endpoint"] != binding.endpoint or material["tags"] != tags
                or not isinstance(material["key"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{32,4096}", material["key"])):
            raise ValueError("Private delivery metadata differs; reconcile")
        try:
            enrolled = exchange(target, "client", {"action": "enroll", "url": binding.endpoint,
                                                   "tags": tags, "key": material["key"]})
        finally:
            material.clear()
        identity = _observed_identity(enrolled.get("identity"), binding.endpoint, tags)
        journal.update(phase="verifying", node_id=identity["node_id"])
        write_private(path, journal)
        result = _control(binding, "complete", {"id": issued["id"], "node_id": identity["node_id"], "tags": tags})
        if set(result) != {"node", "key_id"} or result["key_id"] != issued["id"]:
            raise ValueError("Native completion unconfirmed; reconcile")
        _verify_node(result["node"], identity)
        journal["phase"] = "complete"
        write_private(path, journal)
        return result


def reconcile_enrollment(reference: str, expected_key_ids: list[str], expected_node_ids: list[str], *,
                         workspace: str | None = None, dry_run: bool = False) -> dict:
    for values in (expected_key_ids, expected_node_ids):
        if type(values) is not list or len(values) > 10000 or len(set(values)) != len(values):
            raise ValueError("Exact reviewed native IDs required")
        for value in values:
            identifier(value)
    binding = resolve_management(reference, workspace=workspace)
    journal = _journal(workspace)
    if (journal is None or journal["server_id"] != binding.server_id or journal["endpoint"] != binding.endpoint
            or journal["control"] != binding.ssh.to_dict()):
        raise ValueError("Exact retained enrollment binding required")
    if dry_run:
        return {"dry_run": True, "action": "reconcile"}
    with resource_lock("headscale-management", normalize_workspace_dir(workspace)), \
            resource_lock("headscale-enrollment", normalize_workspace_dir(workspace)):
        if _journal(workspace) != journal or resolve_management(binding.server_id, workspace=workspace) != binding:
            raise ValueError("Enrollment journal changed")
        observed = _control(binding, "inspect", {})
        if observed.get("reconciliation_required") is True:
            result = _control(binding, "reconcile", {"expected_key_ids": expected_key_ids, "expected_node_ids": expected_node_ids})
        else:
            # Completion can succeed remotely while its acknowledgement is lost.
            # Explicitly review the complete current inventory in this case.
            if ({key["id"] for key in observed["keys"]} != set(expected_key_ids)
                    or {node["id"] for node in observed["nodes"]} != set(expected_node_ids)):
                raise ValueError("Native inventory changed; review reconciliation again")
            result = {"reconciled": expected_key_ids, "nodes": expected_node_ids}
        journal["phase"] = "reconciled"
        write_private(_path(workspace, "headscale-enrollment.json"), journal)
        return result
