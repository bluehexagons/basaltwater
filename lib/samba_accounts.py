"""Public, credential-free contracts for approved Samba account controls."""

from __future__ import annotations

import re

from lib.validators import validate_username
from lib.validation import validate_no_control_characters

SAMBA_ACCOUNT_HELPER = "/opt/basaltwater/smb/account_management.py"
MAX_ACCOUNTS = 512
HEX_ID = re.compile(r"[a-f0-9]{32}\Z")
HEX_REVISION = re.compile(r"[a-f0-9]{64}\Z")
ACCOUNT_ACTIONS = {
    "enable": "Enable Samba login",
    "disable": "Disable Samba login",
    "reset-password": "Reset Samba password",
}


def validate_account_action(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != {"action", "username", "revision", "job_id"}:
        raise ValueError("Invalid Samba account action fields")
    if not isinstance(value["action"], str) or value["action"] not in ACCOUNT_ACTIONS:
        raise ValueError("Choose an available Samba account action")
    if not validate_username(value["username"]):
        raise ValueError("Invalid Samba username")
    for key, pattern in (("revision", HEX_REVISION), ("job_id", HEX_ID)):
        if not isinstance(value[key], str) or not pattern.fullmatch(value[key]):
            raise ValueError("Invalid Samba account review identity")
    return dict(value)


def validate_reset_password(value: object) -> str:
    if not isinstance(value, str) or not 12 <= len(value) <= 128 or not value.strip():
        raise ValueError("Use a Samba password of 12 to 128 characters")
    validate_no_control_characters(value, "Samba password")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise ValueError("Invalid Samba password encoding") from exc
    return value


def account_dispatch_argv(value: dict) -> list[str]:
    value = validate_account_action(value)
    # Every variable argument is a validated simple token. No password or path
    # crosses systemd's argument expansion boundary.
    return ["/usr/bin/systemd-run", "--no-ask-password", "--quiet", "--wait", "--collect",
            "--unit=basaltwater-samba-account-" + value["job_id"], "--property=Type=exec",
            "--property=Restart=no", "--property=RuntimeMaxSec=45", "--property=TimeoutStopSec=5",
            "--property=KillMode=control-group", "--property=UMask=0077",
            "--property=StandardInput=null", "--property=StandardOutput=null", "--property=StandardError=null",
            "--working-directory=/", "--uid=root", "--", "/usr/bin/python3", "-I", SAMBA_ACCOUNT_HELPER,
            value["action"], value["username"], value["revision"], value["job_id"]]


def validate_account_snapshot(value: object) -> dict:
    if not isinstance(value, dict) or value.get("available") is not True:
        return {"available": False}
    if (set(value) != {"available", "accounts"} or not isinstance(value["accounts"], list)
            or len(value["accounts"]) > MAX_ACCOUNTS):
        raise ValueError("Invalid Samba account inventory")
    names = set()
    for row in value["accounts"]:
        if (not isinstance(row, dict) or set(row) != {"username", "uid", "disabled", "locked", "manageable", "revision"}
                or not validate_username(row["username"]) or row["username"] in names
                or row["uid"] is not None and (type(row["uid"]) is not int or row["uid"] < 0)
                or any(type(row[key]) is not bool for key in ("disabled", "locked", "manageable"))
                or row["manageable"] and (row["uid"] is None or not 1000 <= row["uid"] < 65534)
                or not isinstance(row["revision"], str) or not HEX_REVISION.fullmatch(row["revision"])):
            raise ValueError("Invalid Samba account record")
        names.add(row["username"])
    return value
