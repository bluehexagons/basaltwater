"""Strict, administrator-owned policy for the agent privilege broker."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
from urllib.parse import urlsplit

from lib.validation import validate_filesystem_path, validate_no_control_characters
from lib.validators import validate_host

CONFIG_DIR = "/etc/infra-tools/privilege-broker"
POLICY_PATH = CONFIG_DIR + "/policy.json"
REQUEST_SOCKET = "/run/infra-tools-privilege-broker/request.sock"
APPROVAL_SOCKET = "/run/infra-tools-privilege-broker/approval.sock"
DATABASE_PATH = "/var/lib/infra-tools-privilege-broker/requests.sqlite3"
WEB_USER = "infra-approval"
MAX_MESSAGE = 16384
UNIT_PATTERN = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,180}\.service\Z")
ID_PATTERN = re.compile(r"[a-f0-9]{32}\Z")


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def protected_path(path: str, *, directory: bool = False) -> None:
    """Reject symlinks and non-root-writable-only paths, including ancestors."""
    validate_filesystem_path(path, must_exist=True)
    if not os.path.isabs(path) or os.path.normpath(path) != path:
        raise ValueError("Protected paths must be absolute and normalized")
    for candidate in (Path(path), *Path(path).parents):
        info = candidate.lstat()
        expected_dir = directory or candidate != Path(path)
        if (
            info.st_uid != 0 or info.st_mode & 0o022
            or (expected_dir and not stat.S_ISDIR(info.st_mode))
            or (not expected_dir and not stat.S_ISREG(info.st_mode))
        ):
            raise ValueError(f"Unsafe privileged path: {candidate}")


def validate_policy(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != {
        "version", "machine", "origin", "requester_uid", "ttl_seconds", "services", "reboot"
    }:
        raise ValueError("Invalid broker policy fields")
    if type(value["version"]) is not int or value["version"] != 1:
        raise ValueError("Unsupported broker policy version")
    if not isinstance(value["machine"], str) or not ID_PATTERN.fullmatch(value["machine"]):
        raise ValueError("machine must be a unique 32-character hexadecimal installation ID")
    if type(value["requester_uid"]) is not int or value["requester_uid"] < 1000:
        raise ValueError("requester_uid must identify a non-system account")
    if type(value["ttl_seconds"]) is not int or not 30 <= value["ttl_seconds"] <= 900:
        raise ValueError("ttl_seconds must be between 30 and 900")
    origin = value["origin"]
    if not isinstance(origin, str):
        raise ValueError("origin must be HTTPS")
    validate_no_control_characters(origin, "origin")
    parsed = urlsplit(origin)
    if (parsed.scheme != "https" or not parsed.hostname or not validate_host(parsed.hostname)
        or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment
        or parsed.port is None or not 1024 <= parsed.port <= 65535
        or not re.fullmatch(r"https://[A-Za-z0-9.\[\]:-]+", origin)):
        raise ValueError("origin must be a dedicated HTTPS origin with an explicit unprivileged port")
    services = value["services"]
    if not isinstance(services, dict) or len(services) > 32:
        raise ValueError("services must be a mapping of up to 32 exact unit names")
    for unit, decision in services.items():
        if not UNIT_PATTERN.fullmatch(unit) or decision not in ("approve", "allow"):
            raise ValueError("Service rules require exact .service names and approve/allow decisions")
    if value["reboot"] not in ("deny", "approve"):
        raise ValueError("Reboot may only be denied or individually approved")
    return value


def load_policy(path: str = POLICY_PATH) -> dict:
    protected_path(path)
    with open(path, "rb") as source:
        data = source.read(MAX_MESSAGE + 1)
    if len(data) > MAX_MESSAGE:
        raise ValueError("Broker policy exceeds size limit")
    return validate_policy(json.loads(data))


def operation_plan(policy: dict, uid: int, operation: str, parameters: object) -> dict:
    if uid != policy["requester_uid"]:
        raise PermissionError("Account is not authorized to request privileges")
    if not isinstance(parameters, dict):
        raise ValueError("parameters must be an object")
    if operation == "service.restart" and set(parameters) == {"unit"}:
        unit = parameters["unit"]
        if not isinstance(unit, str) or not UNIT_PATTERN.fullmatch(unit):
            raise ValueError("Invalid service name")
        mode = policy["services"].get(unit, "deny")
        argv = ["/usr/bin/systemctl", "--no-ask-password", "restart", "--", unit]
        effect = f"Restart {unit}; active work and dependent services may be interrupted."
    elif operation == "system.reboot" and not parameters:
        mode = policy["reboot"]
        argv = ["/usr/bin/systemctl", "--no-ask-password", "reboot"]
        effect = "Reboot this VM; all sessions and running work will be interrupted."
    else:
        raise ValueError("Unknown operation or parameters")
    if mode == "deny":
        raise PermissionError("Operation is not registered in administrator policy")
    return {"machine": policy["machine"], "uid": uid, "operation": operation,
            "parameters": parameters, "argv": argv, "effect": effect,
            "policy_digest": digest(policy), "mode": mode}
