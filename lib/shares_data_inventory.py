"""Non-secret, bounded configuration inventory for Shares & Data."""

from __future__ import annotations

import os

from lib.task_utils import validate_frequency, validate_sync_paths
from lib.validation import validate_filesystem_path, validate_samba_share_name
from lib.validators import validate_username

MAX_SHARES = 64
MAX_USERS = 64
MAX_ROUTINES = 128


def _text(value: object, maximum: int = 1024) -> str:
    if (not isinstance(value, str) or not value or len(value) > maximum
            or any(ord(character) < 32 or ord(character) == 127 for character in value)):
        raise ValueError("Invalid Shares & Data text")
    return value


def _path(value: object) -> str:
    path = _text(value)
    validate_filesystem_path(path)
    if not os.path.isabs(path):
        raise ValueError("Shares & Data paths must be absolute")
    return path


def _count(value: object) -> None:
    if type(value) is not int or value < 0:
        raise ValueError("Invalid Shares & Data count")


def _specs(saved: dict, key: str) -> list:
    value = saved.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("Invalid Shares & Data configuration")
    return value


def configuration_inventory(saved: dict) -> dict:
    """Project only known public fields; never copy credentials or mount specs.

    Saved and legacy share declarations can contain inline passwords. Split
    identities here rather than passing those declarations to the HTTP process.
    This is a saved-configuration inventory, not a probe of Samba's live state.
    """
    try:
        enabled = saved.get("enable_samba", False)
        if type(enabled) is not bool:
            raise ValueError("Invalid Samba selection")
        specs = _specs(saved, "samba_shares")
        shares = []
        for spec in specs[:MAX_SHARES]:
            if not isinstance(spec, list) or len(spec) != 4 or not isinstance(spec[3], str):
                raise ValueError("Invalid share declaration")
            access, name, path, raw_users = spec
            users = list(dict.fromkeys(user.split(":", 1)[0].strip() for user in raw_users.split(",")))
            shares.append({"name": name, "access": access, "path": path,
                           "users": users[:MAX_USERS], "omitted_users": max(0, len(users) - MAX_USERS)})
        routines = []
        total = 0
        for key, kind in (("backup_specs", "backup"), ("sync_specs", "sync")):
            specs_for_kind = _specs(saved, key)
            total += len(specs_for_kind)
            for spec in specs_for_kind[:max(0, MAX_ROUTINES - len(routines))]:
                if not isinstance(spec, list) or len(spec) != 3:
                    raise ValueError("Invalid mirror declaration")
                routines.append(dict(zip(("source", "destination", "interval"), spec), kind=kind))
        result = {"available": True, "version": 1, "samba_enabled": enabled,
                  "shares": shares, "omitted_shares": max(0, len(specs) - MAX_SHARES),
                  "routines": routines, "omitted_routines": max(0, total - MAX_ROUTINES)}
        return validate_inventory(result)
    except (TypeError, ValueError):
        return {"available": False}


def validate_inventory(value: object) -> dict:
    """Reject malformed exports independently of the integrity health report."""
    if (not isinstance(value, dict) or set(value) != {
        "available", "version", "samba_enabled", "shares", "omitted_shares", "routines", "omitted_routines",
    } or value["available"] is not True or type(value["version"]) is not int or value["version"] != 1
            or type(value["samba_enabled"]) is not bool):
        raise ValueError("Shares & Data inventory unavailable")
    for field, maximum in (("shares", MAX_SHARES), ("routines", MAX_ROUTINES)):
        if not isinstance(value[field], list) or len(value[field]) > maximum:
            raise ValueError("Shares & Data inventory exceeds its limit")
        _count(value["omitted_" + field])
    for share in value["shares"]:
        if not isinstance(share, dict) or set(share) != {"name", "access", "path", "users", "omitted_users"}:
            raise ValueError("Invalid share inventory")
        validate_samba_share_name(_text(share["name"], 32))
        if share["access"] not in ("read", "write") or _path(share["path"]) == "/":
            raise ValueError("Invalid share access or path")
        users = share["users"]
        if not isinstance(users, list) or not 1 <= len(users) <= MAX_USERS:
            raise ValueError("Invalid share users")
        if any(not validate_username(_text(user, 64)) for user in users):
            raise ValueError("Invalid share username")
        _count(share["omitted_users"])
    for routine in value["routines"]:
        if not isinstance(routine, dict) or set(routine) != {"kind", "source", "destination", "interval"}:
            raise ValueError("Invalid mirror inventory")
        if routine["kind"] not in ("backup", "sync"):
            raise ValueError("Invalid mirror kind")
        validate_sync_paths(_path(routine["source"]), _path(routine["destination"]), resolve=False)
        validate_frequency(_text(routine["interval"], 32))
    return value
