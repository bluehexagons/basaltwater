"""Bounded public contracts for independently approved share changes."""

from __future__ import annotations

import os

from lib.samba_accounts import HEX_ID, HEX_REVISION
from lib.validation import validate_filesystem_path, validate_samba_share_name
from lib.validators import validate_username

SHARE_HELPER = "/opt/basaltwater/smb/share_job.py"
SHARE_STATE = "/var/lib/basaltwater-privilege-broker/samba-shares.json"
SHARE_UNIT = "basaltwater-samba-shares.service"
SHARE_TIMEOUT = 3600


def validate_share(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != {"name", "access", "path", "users"}:
        raise ValueError("Invalid share fields")
    if not isinstance(value["name"], str):
        raise ValueError("Invalid share name")
    validate_samba_share_name(value["name"])
    if value["access"] not in ("read", "write"):
        raise ValueError("Choose read-only or read/write access")
    path = value["path"]
    if (not isinstance(path, str) or not path or len(path) > 512 or "," in path
            or any(ord(character) < 32 or ord(character) == 127 for character in path)
            or not os.path.isabs(path) or path.startswith("//") or os.path.normpath(path) != path or path == "/"):
        raise ValueError("Share directory must be an absolute normalized path of up to 512 characters")
    validate_filesystem_path(path)
    users = value["users"]
    if (not isinstance(users, list) or not 1 <= len(users) <= 64
            or any(not validate_username(user) for user in users) or len(set(users)) != len(users)):
        raise ValueError("Choose between one and 64 distinct existing Samba users")
    return {**value, "users": list(users)}


def validate_share_action(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != {"before", "after", "revision", "job_id"}:
        raise ValueError("Invalid share review fields")
    result = dict(value)
    for key in ("before", "after"):
        if value[key] is not None:
            result[key] = validate_share(value[key])
    before, after = result["before"], result["after"]
    if before == after or before is not None and after is not None and before["name"] != after["name"]:
        raise ValueError("Choose a share change; renaming requires adding and removing shares separately")
    for key, pattern in (("revision", HEX_REVISION), ("job_id", HEX_ID)):
        if not isinstance(value[key], str) or not pattern.fullmatch(value[key]):
            raise ValueError("Invalid share review identity")
    return result


def share_label(value: dict) -> str:
    return "Remove share" if value["after"] is None else "Create share" if value["before"] is None else "Update share"


def share_name(value: dict) -> str:
    return (value["after"] or value["before"])["name"]


def validate_share_snapshot(value: object) -> dict:
    if not isinstance(value, dict) or value.get("available") is not True:
        return {"available": False}
    if (set(value) != {"available", "revision"} or not isinstance(value["revision"], str)
            or not HEX_REVISION.fullmatch(value["revision"])):
        raise ValueError("Invalid share management snapshot")
    return value


def share_dispatch_argv(value: dict) -> list[str]:
    value = validate_share_action(value)
    # Paths and usernames travel in a consumed private payload, never through
    # systemd's variable/specifier expansion boundary.
    return ["/usr/bin/systemd-run", "--no-ask-password", "--quiet", "--collect", "--unit=" + SHARE_UNIT,
            "--property=Type=exec", "--property=Restart=no", f"--property=RuntimeMaxSec={SHARE_TIMEOUT}",
            "--property=TimeoutStopSec=30", "--property=KillMode=control-group", "--property=UMask=0077",
            "--property=StandardInput=null", "--property=StandardOutput=null", "--property=StandardError=null",
            "--working-directory=/", "--uid=root", "--", "/usr/bin/python3", "-I", SHARE_HELPER, value["job_id"]]
