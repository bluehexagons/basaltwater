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
    "create": "Create Samba and Unix account",
    "add-login": "Add Samba login to Unix account",
    "remove": "Remove Samba login",
}
PASSWORD_ACTIONS = frozenset({"reset-password", "create", "add-login"})
LIFECYCLE_ACTIONS = frozenset({"create", "add-login", "remove"})


def account_effect(action: str) -> str:
    """Describe identity and credential effects identically at approval boundaries."""
    if action == "create":
        return ("Create a new local Unix account with a locked Unix password and a nologin shell, without creating a home directory, "
                "then create an enabled Samba login with the separately staged one-use password. "
                "Reject an existing Unix or Samba identity. No share access is granted. "
                "Interrupted creation can leave a Unix identity or disabled Samba record and a setup recovery guard. ")
    if action == "add-login":
        return ("Create an enabled Samba login for the reviewed existing local Unix account using the separately staged "
                "one-use password. Preserve its Unix password, shell, home and groups. "
                "Existing group permissions can allow share access immediately; managed membership is unchanged. "
                "Reject a changed identity or an existing Samba login. Interrupted creation retains a setup recovery guard. ")
    if action == "remove":
        return ("Delete only the Samba login and its saved setup credential, retaining the Unix identity, Unix password, "
                "groups and files. Refuse users referenced by any saved managed share; detach them first. "
                "Manual share configuration is outside this check. Interrupted removal retains a setup recovery guard. ")
    if action == "reset-password":
        return ("Change only the Samba password using the separately staged one-use password. "
                "Preserve login flags, Unix password and share membership. Reject changed passdb metadata. ")
    if action in {"enable", "disable"}:
        return "This changes only the Samba disabled flag and preserves the password, Unix login and share membership. "
    raise ValueError("Choose an available Samba account action")


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
    if (set(value) not in ({"available", "accounts"}, {"available", "accounts", "lifecycle"}) or not isinstance(value["accounts"], list)
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
    if "lifecycle" in value:
        validate_lifecycle_snapshot(value["lifecycle"], names)
    return value


def validate_lifecycle_snapshot(value: object, accounts: set[str]) -> dict:
    if isinstance(value, dict) and set(value) == {"available"} and value["available"] is False:
        return value
    if (not isinstance(value, dict) or set(value) != {"available", "revision", "unix_accounts", "removable"}
            or value["available"] is not True or not isinstance(value["revision"], str)
            or not HEX_REVISION.fullmatch(value["revision"]) or not isinstance(value["unix_accounts"], list)
            or len(value["unix_accounts"]) > MAX_ACCOUNTS or not isinstance(value["removable"], list)
            or len(value["removable"]) > MAX_ACCOUNTS):
        raise ValueError("Invalid Samba lifecycle inventory")
    names = set()
    for row in value["unix_accounts"]:
        if (not isinstance(row, dict) or set(row) != {"username", "uid"} or not validate_username(row["username"])
                or row["username"] in names or row["username"] in accounts
                or type(row["uid"]) is not int or not 1000 <= row["uid"] < 65534):
            raise ValueError("Invalid local Unix identity")
        names.add(row["username"])
    removable = value["removable"]
    if (any(not isinstance(name, str) or name not in accounts for name in removable)
            or len(set(removable)) != len(removable)):
        raise ValueError("Invalid removable Samba identity")
    return value
