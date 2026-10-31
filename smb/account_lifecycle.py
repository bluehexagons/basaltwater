"""Approved Samba identity creation and Samba-only removal under setup ownership."""

from __future__ import annotations

import hashlib
import json
import os
import pwd
import subprocess

from lib.atomic_io import write_json_atomic
from lib.machine_state import PERSISTENT_SETUP_CONFIG_FILE as SETUP_CONFIG_FILE, load_setup_config
from lib.privilege_execution import ENVIRONMENT
from lib.samba_accounts import MAX_ACCOUNTS, validate_lifecycle_snapshot
from lib.validators import validate_username
from smb.account_management import SMB_CONF, _accounts, _stamp, password_binding

PASSWD_FILE = "/etc/passwd"
NORMAL_ACCOUNT = 0x10  # Samba ACB_NORMAL; newly created records start disabled.


def _local_accounts() -> list[dict]:
    """Read only local non-system identities; never export GECOS or shadow data."""
    before = _stamp(PASSWD_FILE)
    with open(PASSWD_FILE, "rb") as source:
        raw = source.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024 or _stamp(PASSWD_FILE) != before:
        raise ValueError("Local Unix account inventory changed or exceeds its limit")
    rows, names = [], set()
    for line in raw.decode("utf-8", errors="strict").splitlines():
        fields = line.split(":")
        if (len(fields) != 7 or fields[0] in names or not fields[2].isascii() or not fields[2].isdigit()
                or not fields[3].isascii() or not fields[3].isdigit()):
            raise ValueError("Unsupported local Unix account inventory")
        name, uid, gid = fields[0], int(fields[2]), int(fields[3])
        names.add(name)
        if not 1000 <= uid < 65534:
            continue
        if not validate_username(name) or len(rows) >= MAX_ACCOUNTS:
            raise ValueError("Unsupported local Unix account inventory")
        identity = pwd.getpwnam(name)
        if identity.pw_name != name or identity.pw_uid != uid or identity.pw_gid != gid:
            raise ValueError("Local Unix identity differs from name service lookup")
        rows.append({"username": name, "uid": uid})
    return sorted(rows, key=lambda row: row["username"])


def _creation_revision() -> str:
    return hashlib.sha256(json.dumps([password_binding(), _stamp(SETUP_CONFIG_FILE)]).encode()).hexdigest()


def _unreferenced(saved: dict, rows: list[dict]) -> list[str]:
    from smb.share_management import _rows

    referenced = {name for share in _rows(saved) for name in share["users"]}
    return [row["username"] for row in rows if row["manageable"] and row["username"] not in referenced]


def collect_lifecycle_snapshot(saved: dict, rows: list[dict]) -> dict:
    """Export identity choices independently of ordinary account inspection."""
    from smb.share_management import _owner

    try:
        with _owner(observe=True) as (_store, fresh, _identity):
            if fresh != saved:
                raise ValueError("Saved Samba configuration changed")
            revision = _creation_revision()
            names = {row["username"] for row in rows}
            unix_accounts = [row for row in _local_accounts() if row["username"] not in names]
            removable = _unreferenced(saved, rows)
            if revision != _creation_revision() or rows != _accounts():
                raise ValueError("Samba lifecycle inventory changed")
            return validate_lifecycle_snapshot({"available": True, "revision": revision,
                "unix_accounts": unix_accounts, "removable": removable}, names)
    except (OSError, ValueError, RuntimeError, KeyError, subprocess.SubprocessError):
        return {"available": False}


def _identity(username: str):
    try:
        return pwd.getpwnam(username)
    except KeyError:
        return None


def _bindings():
    try:
        from samba.samba3 import passdb
    except ImportError as exc:
        raise RuntimeError("Samba identity controls require the Samba Python bindings") from exc
    return passdb


def _credentials_without(saved: dict, username: str) -> dict:
    credentials = saved.get("share_credentials")
    if credentials is None:
        return saved
    if (not isinstance(credentials, list) or any(not isinstance(row, list) or len(row) != 2
            or not validate_username(row[0]) or not isinstance(row[1], str) for row in credentials)):
        raise ValueError("Unsupported saved share credentials")
    retained = [row for row in credentials if row[0] != username]
    return {**saved, "share_credentials": retained or None} if retained != credentials else saved


def change_lifecycle(value: dict, store, saved: dict, rows: list[dict]) -> None:
    """Caller owns setup; all expected failures precede secret consumption/effects."""
    from smb.password_staging import consume_password

    action, username = value["action"], value["username"]
    setup_stamp = _stamp(SETUP_CONFIG_FILE)
    if saved != load_setup_config() or setup_stamp != _stamp(SETUP_CONFIG_FILE):
        raise ValueError("Saved Samba configuration changed before execution")
    row = next((row for row in rows if row["username"] == username), None)
    identity = _identity(username)
    local_accounts = _local_accounts()
    local = next((row for row in local_accounts if row["username"] == username), None)
    updated = saved
    if action == "remove":
        if (row is None or not row["manageable"] or row["revision"] != value["revision"]
                or username not in _unreferenced(saved, rows) or identity is None or local is None
                or local["uid"] != row["uid"]):
            raise ValueError("Detach the unchanged account from every managed share before removing its Samba login")
        updated = _credentials_without(saved, username)
    else:
        if row is not None or len(rows) >= MAX_ACCOUNTS or value["revision"] != _creation_revision():
            raise ValueError("The reviewed Samba or Unix account inventory changed")
        if (action == "create" and (identity is not None or len(local_accounts) >= MAX_ACCOUNTS)
                or action == "add-login" and (identity is None or local is None)):
            raise ValueError("The selected new or existing Unix identity does not match")
    passdb = _bindings()
    # A secret is consumed while its original /etc/passwd binding still holds.
    # useradd intentionally changes that file afterwards, under the setup guard.
    password = consume_password(value) if action != "remove" else None
    if setup_stamp != _stamp(SETUP_CONFIG_FILE) or rows != _accounts():
        raise ValueError("Samba configuration or accounts changed before execution")
    record = store.begin("samba_account", "localhost", "creating_unix" if action == "create" else "changing_samba",
                         context={"action": action, "username": username, "job_id": value["job_id"],
                                  "existing_uid": identity.pw_uid if identity is not None else None})
    try:
        if action == "create":
            subprocess.run(["/usr/sbin/useradd", "--no-create-home", "--shell", "/usr/sbin/nologin", "--user-group",
                "--password", "!", "--key", "UID_MIN=1000", "--key", "UID_MAX=65533",
                "--key", "CREATE_MAIL_SPOOL=no", "--", username],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                env=ENVIRONMENT, timeout=15, check=True)
            identity = _identity(username)
            if (identity is None or not 1000 <= identity.pw_uid < 65534 or identity.pw_shell != "/usr/sbin/nologin"
                    or identity.pw_passwd != "x"):
                raise ValueError("New Unix identity could not be verified")
            record = store.transition(record.operation_id, "changing_samba",
                                      context={**record.context, "created_uid": identity.pw_uid})
        if identity != _identity(username) or setup_stamp != _stamp(SETUP_CONFIG_FILE):
            raise ValueError("Unix identity or saved configuration changed")
        passdb.set_smb_config(SMB_CONF)
        database = passdb.PDB("tdbsam")
        if action == "remove":
            account = database.getsampwnam(username)
            if account.username != username:
                raise ValueError("Samba identity changed")
            # delete_user can invoke a Unix deletion script; this lower-level
            # method deletes only the passdb record, never the Unix account.
            database.delete_sam_account(account)
        else:
            database.create_user(username, NORMAL_ACCOUNT)
            account = database.getsampwnam(username)
            if account.username != username or account.acct_ctrl & ~1 != NORMAL_ACCOUNT:
                raise ValueError("New Samba identity could not be verified")
            account.plaintext_passwd = password
            account.acct_ctrl = NORMAL_ACCOUNT
            database.update_sam_account(account)
        after = next((row for row in _accounts() if row["username"] == username), None)
        if (identity != _identity(username) or action == "remove" and after is not None
                or action != "remove" and (after is None or after["uid"] != identity.pw_uid
                    or not after["manageable"] or after["disabled"] or after["locked"])):
            raise ValueError("Samba account change could not be verified")
        if updated != saved:
            store.transition(record.operation_id, "saving")
            if setup_stamp != _stamp(SETUP_CONFIG_FILE):
                raise ValueError("Saved configuration changed during Samba removal")
            write_json_atomic(SETUP_CONFIG_FILE, updated, mode=0o600, uid=0, gid=0)
        store.complete(record.operation_id)
    except BaseException as exc:
        try:
            current = store.load()
            if current is not None:
                store.transition(record.operation_id, current.phase, status="recovery_required",
                                 context={**current.context, "error_type": type(exc).__name__})
        except (OSError, ValueError):
            pass
        if isinstance(exc, passdb.error):
            raise RuntimeError("Samba account lifecycle change failed") from None
        raise
