"""Fixed Headscale portal boundary and one-use, process-local registration input."""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import stat
import time

from lib.atomic_io import read_json_file
from lib.validation import validate_no_control_characters

BINARY = "/usr/bin/headscale"
UNIT = "/lib/systemd/system/headscale.service"
SOCKET = "/run/headscale/headscale.sock"
TTL = 300
FIELDS = {"users_create": {"name"}, "users_delete": {"id"}, "register": {"user", "token"},
          "node_expire": {"id"}, "node_delete": {"id"}}


def managed_installed() -> bool:
    """Root installer observation; never widen access to the private manifest."""
    from lib.headscale_server import ServerPaths, load_manifest

    try:
        return (os.geteuid() == 0 and load_manifest(ServerPaths()) is not None and os.path.isfile(BINARY)
                and any(os.path.isfile(path) for path in (UNIT, "/usr/lib/systemd/system/headscale.service",
                                                         "/etc/systemd/system/headscale.service")))
    except (OSError, ValueError, RuntimeError):
        return False


def require_installed() -> None:
    from lib.privilege_policy import protected_path

    protected_path("/etc/basaltwater/web-panel/config.json")
    protected_path(BINARY)
    panel = read_json_file("/etc/basaltwater/web-panel/config.json", max_bytes=256 * 1024)
    if (panel.get("system_type") == "server_proxmox"
            or panel.get("features", {}).get("managed_headscale") is not True
            or not managed_installed() or os.path.islink(SOCKET)
            or not stat.S_ISSOCK(os.stat(SOCKET).st_mode)):
        raise PermissionError("Managed Headscale administration is unavailable")


def validate_action(parameters: object) -> dict:
    if (not isinstance(parameters, dict) or not isinstance(parameters.get("action"), str)
            or parameters["action"] not in FIELDS):
        raise ValueError("Invalid Headscale action")
    action = parameters["action"]
    if set(parameters) != {"action", *FIELDS[action]}:
        raise ValueError("Invalid Headscale fields")
    for key, value in parameters.items():
        if not isinstance(value, str) or not 1 <= len(value) <= 128:
            raise ValueError("Invalid Headscale value")
        validate_no_control_characters(value, "Headscale value")
        pattern = r"[a-f0-9]{32}" if key == "token" else r"[0-9]{1,20}" if key in {"id", "user"} else r"[A-Za-z0-9][A-Za-z0-9_.@+-]{0,127}"
        if not re.fullmatch(pattern, value):
            raise ValueError("Invalid Headscale value")
    return dict(parameters)


def native(action: str, parameters: dict) -> dict:
    """Lazy integration contract; no subprocess argv or native error text escapes."""
    try:
        from lib.headscale_admin import execute_admin_action

        result = execute_admin_action(action, parameters)
        if not isinstance(result, dict):
            raise ValueError("Invalid native response")
        return result
    except Exception:
        raise RuntimeError("Headscale operation was not confirmed; inspect native state") from None


def snapshot() -> dict:
    """Allowlist typed native output; never export URLs or credentials."""
    result = {}
    for collection, action in (("users", "users_list"), ("nodes", "nodes_list")):
        records = native(action, {}).get(collection)
        if not isinstance(records, list) or len(records) > 200:
            raise ValueError("Headscale inventory exceeds portal limit")
        result[collection] = []
        for record in records:
            if not isinstance(record, dict):
                raise ValueError("Invalid Headscale inventory")
            safe = {}
            for key in (("id", "name") if collection == "users" else
                        ("id", "node_id", "hostname", "addresses", "tags", "user_id", "online", "expiry")):
                value = record.get(key)
                if key in {"addresses", "tags"}:
                    if not isinstance(value, list) or len(value) > 32 or any(not isinstance(v, str) or len(v) > 128 for v in value):
                        raise ValueError("Invalid Headscale inventory")
                elif key == "online":
                    if type(value) is not bool:
                        raise ValueError("Invalid Headscale inventory")
                elif value is not None and (not isinstance(value, str) or len(value) > 256):
                    raise ValueError("Invalid Headscale inventory")
                safe[key] = value
            result[collection].append(safe)
    return result


class TransientRegistrations:
    """Secrets cannot survive restart or be rebound to another durable review."""

    def __init__(self) -> None:
        self.entries: dict[str, tuple] = {}
        self.seen: set[bytes] = set()

    def capture(self, uid: int, user: str, auth_id: object) -> str:
        if not isinstance(auth_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,256}", auth_id):
            raise ValueError("Invalid native Auth ID")
        fingerprint = hashlib.sha256(auth_id.encode()).digest()
        if fingerprint in self.seen or len(self.seen) >= 4096:
            raise ValueError("Registration input already submitted or capacity reached; verify a fresh native request")
        self.seen.add(fingerprint)
        token = secrets.token_hex(16)
        self.entries[token] = (uid, user, auth_id, time.monotonic() + TTL, None)
        return token

    def bind(self, token: str, request: dict) -> None:
        uid, user, secret, expires, _ = self.entries[token]
        self.entries[token] = (uid, user, secret, expires, (request["id"], request["digest"]))

    def discard(self, token: str) -> None:
        self.entries.pop(token, None)

    def consume(self, request: dict) -> str:
        parameters = request["plan"]["parameters"]
        entry = self.entries.pop(parameters["token"], None)
        if (entry is None or entry[0] != request["uid"] or entry[1] != parameters["user"]
                or entry[3] <= time.monotonic() or entry[4] != (request["id"], request["digest"])):
            raise ValueError("Registration expired or unavailable; verify a fresh native request")
        return entry[2]
