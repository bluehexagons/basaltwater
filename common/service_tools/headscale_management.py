#!/usr/bin/env python3
"""Private root SSH adapter; key delivery is an explicit one-use exchange."""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone

if __name__ == "__main__" and not __package__:
    sys.path.insert(0, "/opt/basaltwater")

from lib import headscale_admin as admin
from lib.atomic_io import parse_json, remove_file_durable
from lib.concurrency import resource_lock
from lib.headscale_config import validate_headscale_url
from lib.headscale_private import read_private, write_private


def _intent(adapter, key_id: str) -> dict:
    intent = read_private(str(adapter.paths.private / "key-intent.json"))
    if (intent is None or set(intent) != {"schema", "endpoint", "tags", "expiry", "before", "id", "delivery"}
            or type(intent["schema"]) is not int or intent["schema"] != 1
            or intent["endpoint"] != adapter.endpoint or intent["id"] != admin.identifier(key_id)
            or type(intent["expiry"]) is not int or intent["expiry"] < 1
            or type(intent["before"]) is not list
            or not isinstance(intent["delivery"], str) or not re.fullmatch(r"[0-9a-f]{32}", intent["delivery"])):
        raise ValueError("Exact issuance intent unavailable")
    admin.approved_tags(intent["tags"])
    for ident in intent["before"]:
        admin.identifier(ident)
    return intent


def take_key(adapter, key_id: str) -> dict:
    """Persist the claim BEFORE emitting a key; lost output cannot be replayed."""
    with resource_lock("headscale-key-issue", str(adapter.paths.private)):
        intent = _intent(adapter, key_id)
        claim_path = str(adapter.paths.private / "key-delivery.json")
        if read_private(claim_path) is not None:
            raise ValueError("Private key delivery already claimed; reconcile")
        keys = [key for key in adapter.rpc("ListPreAuthKeys").pre_auth_keys if str(key.id) == key_id]
        now = datetime.now(timezone.utc).timestamp()
        if (len(keys) != 1 or keys[0].used or keys[0].reusable or keys[0].ephemeral
                or keys[0].HasField("user") or sorted(keys[0].acl_tags) != intent["tags"]
                or keys[0].expiration.seconds != intent["expiry"] or keys[0].expiration.seconds <= now):
            raise ValueError("Issued key no longer eligible for delivery")
        # Reading this file privately is separate from metadata inspection.
        key_path = adapter.paths.private / "keys" / intent["delivery"]
        info = key_path.lstat()
        if info.st_uid != 0 or info.st_mode & 0o077:
            raise ValueError("Unsafe private key delivery")
        material = admin.regular_bytes(key_path).decode()
        # v0.29.7 returns the secret once at creation. Subsequent lists return
        # only hskey-auth-{12-character-prefix}-***, never the full secret.
        parsed = re.fullmatch(r"hskey-auth-([A-Za-z0-9_-]{12})-[A-Za-z0-9_-]{64}", material)
        if parsed is None or keys[0].key != "hskey-auth-" + parsed.group(1) + "-***":
            raise ValueError("Private key delivery differs from native issuance")
        write_private(claim_path, {"schema": 1, "id": key_id, "delivery": intent["delivery"]})
        remove_file_durable(str(key_path))
        return {"id": key_id, "key": material, "tags": intent["tags"], "endpoint": adapter.endpoint}


def complete_delivery(adapter, key_id: str, node_id: str, tags: list[str]) -> dict:
    """Verify native key-to-node linkage before releasing the issuance guard."""
    key_id, node_id, tags = admin.identifier(key_id), admin.identifier(node_id), admin.approved_tags(tags)
    with resource_lock("headscale-key-issue", str(adapter.paths.private)):
        intent = _intent(adapter, key_id)
        claim_path = str(adapter.paths.private / "key-delivery.json")
        if read_private(claim_path) != {"schema": 1, "id": key_id, "delivery": intent["delivery"]}:
            raise ValueError("Private delivery claim unavailable")
        nodes = [node for node in adapter.rpc("ListNodes").nodes if str(node.id) == node_id]
        keys = [key for key in adapter.rpc("ListPreAuthKeys").pre_auth_keys if str(key.id) == key_id]
        if (tags != intent["tags"] or len(nodes) != 1 or len(keys) != 1 or not keys[0].used
                or keys[0].reusable or keys[0].ephemeral or keys[0].HasField("user") or sorted(keys[0].acl_tags) != tags
                or not nodes[0].HasField("pre_auth_key") or str(nodes[0].pre_auth_key.id) != key_id
                or sorted(nodes[0].tags) != tags or not nodes[0].online):
            raise ValueError("Native enrollment association unconfirmed; reconcile")
        projected = admin.node_projection(nodes[0])
        adapter.rpc("ExpirePreAuthKey", id=int(key_id))
        remaining = [key for key in adapter.rpc("ListPreAuthKeys").pre_auth_keys if str(key.id) == key_id]
        if (len(remaining) != 1 or not remaining[0].HasField("expiration")
                or remaining[0].expiration.seconds > datetime.now(timezone.utc).timestamp()):
            raise ValueError("Consumed bootstrap key expiry unconfirmed")
        remove_file_durable(str(adapter.paths.private / "keys" / intent["delivery"]))
        # Clearing the claim first is safe: the key is already used and expired.
        # A crash here leaves the intent blocking a fresh issuance.
        remove_file_durable(claim_path)
        remove_file_durable(str(adapter.paths.private / "key-intent.json"))
        return {"node": projected, "key_id": key_id}


def execute_request(payload: bytes) -> dict:
    try:
        if os.geteuid() != 0 or len(payload) > 16384:
            raise ValueError()
        request = parse_json(payload.decode())
        if type(request) is not dict or set(request) != {"endpoint", "action", "parameters"} or type(request["parameters"]) is not dict:
            raise ValueError()
        adapter = admin.NativeAdmin()
        adapter.verify()
        if adapter.endpoint != validate_headscale_url(request["endpoint"]):
            raise ValueError("Explicit management endpoint mismatch")
        action, parameters = request["action"], request["parameters"]
        if action == "identity" and not parameters:
            result = {"endpoint": adapter.endpoint, "version": admin.VERSION}
        elif action == "inspect" and not parameters:
            result = admin.inspect_machine_keys()
            intent = read_private(str(adapter.paths.private / "key-intent.json"))
            before = set(intent["before"]) if intent is not None else set()
            result["new_key_ids"] = [key["id"] for key in result["keys"] if key["id"] not in before] if intent else []
        elif action == "issue" and set(parameters) == {"tags"}:
            result = admin.issue_machine_key(parameters["tags"])
        elif action == "take" and set(parameters) == {"id"}:
            result = take_key(adapter, admin.identifier(parameters["id"]))
        elif action == "complete" and set(parameters) == {"id", "node_id", "tags"}:
            result = complete_delivery(adapter, parameters["id"], parameters["node_id"], parameters["tags"])
        elif action == "reconcile" and set(parameters) == {"expected_key_ids", "expected_node_ids"}:
            result = admin.reconcile_machine_key(**parameters)
        else:
            raise ValueError()
        return {"ok": True, "result": result}
    except Exception:
        return {"ok": False, "error": "Management operation unconfirmed; inspect keys and nodes before retry"}


def main() -> int:
    result = execute_request(sys.stdin.buffer.read(16385))
    print(json.dumps(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
