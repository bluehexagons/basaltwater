"""Independent approval credentials; never use the coding account's password."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

from lib.validators import validate_username


def password_record(username: str, password: str) -> dict:
    if not validate_username(username):
        raise ValueError("Invalid approval username")
    if not 16 <= len(password) <= 256 or ":" in username:
        raise ValueError("Use a separate approval password of 16 to 256 characters")
    salt = secrets.token_hex(16)
    hashed = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    return {"username": username, "salt": salt, "hash": hashed}


def validate_auth(record: object) -> dict:
    if not isinstance(record, dict) or set(record) != {"username", "salt", "hash"}:
        raise ValueError("Invalid approval credentials")
    if not isinstance(record["username"], str) or not validate_username(record["username"]):
        raise ValueError("Invalid approval username")
    for field, size in (("salt", 16), ("hash", 64)):
        if not isinstance(record[field], str) or len(record[field]) != size * 2 or len(bytes.fromhex(record[field])) != size:
            raise ValueError("Invalid approval password hash")
    return record


def authenticate(header: str, record: dict) -> bool:
    if not header.startswith("Basic ") or len(header) > 2048:
        return False
    try:
        decoded = base64.b64decode(header[6:], validate=True).decode("utf-8")
        username, password = decoded.split(":", 1)
    except (ValueError, UnicodeError):
        return False
    if len(password) > 256:
        return False
    actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(record["salt"]), n=16384, r=8, p=1).hex()
    return hmac.compare_digest(actual, record["hash"]) and hmac.compare_digest(username.encode(), record["username"].encode())


def auth_from_args(args, username: str) -> str | None:
    """Hash on the controller; only the hash travels in private setup arguments."""
    import getpass
    import json

    password = getattr(args, "privilege_broker_password", None)
    if isinstance(password, str):
        if password == "":
            if getattr(args, "dry_run", False):
                return None
            password = getpass.getpass("Separate privilege approval password: ")
        return json.dumps(password_record(username, password))
    record = getattr(args, "privilege_broker_auth", None)
    if isinstance(record, str):
        validate_auth(json.loads(record))
        return record
    return None
