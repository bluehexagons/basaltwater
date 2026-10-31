#!/usr/bin/env python3
"""Inspect local Samba accounts and apply individually approved account changes."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import signal
import subprocess
import sys
import tempfile

if __name__ == "__main__" and not __package__:
    sys.path.insert(0, "/opt/basaltwater")

from lib.machine_state import PERSISTENT_STATE_DIR as STATE_DIR, load_setup_config
from lib.operation_state import OperationStateStore
from lib.privilege_execution import ENVIRONMENT
from lib.privilege_policy import protected_path
from lib.samba_accounts import LIFECYCLE_ACTIONS, MAX_ACCOUNTS, validate_account_action, validate_account_snapshot
from lib.validators import validate_username

SMB_CONF = "/etc/samba/smb.conf"
MAX_OUTPUT = 2 * 1024 * 1024
MAX_DATABASE_BYTES = 16 * 1024 * 1024


def _inspect(argv: list[str]) -> str:
    """Bound read-only tool output on disk and in memory; never request hashes."""
    with tempfile.TemporaryFile() as output:
        result = subprocess.run(["/usr/bin/prlimit", f"--fsize={MAX_OUTPUT + 1}", "--", *argv],
                                stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.DEVNULL,
                                env=ENVIRONMENT, timeout=3, check=False)
        output.seek(0)
        raw = output.read(MAX_OUTPUT + 1)
    if result.returncode or len(raw) > MAX_OUTPUT:
        raise ValueError("Samba account inspection failed")
    return raw.decode("utf-8", errors="strict")


def _stamp(path: str) -> tuple[int, ...]:
    protected_path(path)
    info = os.stat(path, follow_symlinks=False)
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


@contextmanager
def _inspection_database(private: str):
    """Keep Samba's read/write database opens inside a private disposable copy."""
    paths = [str(Path(private) / name) for name in ("passdb.tdb", "secrets.tdb")]
    before = [_stamp(path) for path in paths]
    with tempfile.TemporaryDirectory(prefix="basaltwater-samba-inspection-") as temporary:
        for path in paths:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as source:
                raw = source.read(MAX_DATABASE_BYTES + 1)
            if len(raw) > MAX_DATABASE_BYTES:
                raise ValueError("Samba inspection database exceeds its limit")
            destination = str(Path(temporary) / Path(path).name)
            descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as target:
                target.write(raw)
        if before != [_stamp(path) for path in paths]:
            raise ValueError("Samba databases changed during inspection")
        yield temporary
        if before != [_stamp(path) for path in paths]:
            raise ValueError("Samba databases changed during inspection")


def _accounts() -> list[dict]:
    conf_stamp = _stamp(SMB_CONF)
    settings = {}
    for key in ("server role", "passdb backend", "private dir"):
        settings[key] = _inspect(["/usr/bin/testparm", "-s", "--parameter-name=" + key]).strip()
    if settings["server role"] != "standalone server" or settings["passdb backend"] != "tdbsam":
        raise ValueError("Account controls require standalone Samba with local tdbsam")
    database = str(Path(settings["private dir"]) / "passdb.tdb")
    database_stamp = _stamp(database)
    with _inspection_database(settings["private dir"]) as temporary:
        options = ["-b", "tdbsam:" + str(Path(temporary) / "passdb.tdb"),
                   "--option=private dir=" + temporary, "--option=panic action="]
        listing = _inspect(["/usr/bin/pdbedit", "-L", *options])
        details = _inspect(["/usr/bin/pdbedit", "-L", "-v", *options])
    users = {}
    for line in listing.splitlines():
        name, separator, rest = line.partition(":")
        uid, _, _full_name = rest.partition(":")
        if (not separator or not validate_username(name) or not uid.isascii() or not uid.isdigit()
                or name in users or len(users) >= MAX_ACCOUNTS):
            raise ValueError("Unsupported Samba account list")
        users[name] = int(uid)
    flags, identities = {}, {}
    current = None
    for line in details.splitlines():
        if line.startswith("Unix username:"):
            current = line.partition(":")[2].strip()
            if current not in users or current in flags:
                raise ValueError("Inconsistent Samba account details")
        elif line.startswith("Account Flags:"):
            value = line.partition(":")[2].strip()
            if current is None or current in flags or not re.fullmatch(r"\[[A-Z ]{1,32}\]", value):
                raise ValueError("Unsupported Samba account flags")
            flags[current] = value
        elif line.startswith("User SID:"):
            value = line.partition(":")[2].strip()
            if current is None or current in identities or not re.fullmatch(r"S-\d+(?:-\d+){2,15}", value):
                raise ValueError("Unsupported Samba account identity")
            identities[current] = value
    if (set(flags) != set(users) or set(identities) != set(users)
            or database_stamp != _stamp(database) or conf_stamp != _stamp(SMB_CONF)):
        raise ValueError("Samba accounts changed during inspection")
    rows = []
    for name in sorted(users):
        try:
            uid = pwd.getpwnam(name).pw_uid
        except KeyError:
            uid = None
        control = flags[name]
        manageable = (uid == users[name] and uid is not None and 1000 <= uid < 65534
                      and "U" in control and not set(control) & set("NWSTIM"))
        public = {"username": name, "uid": uid, "disabled": "D" in control,
                  "locked": "L" in control, "manageable": manageable}
        # Bind each review to its own stable account identity/flags. Other
        # accounts can change without invalidating every row in the export.
        revision = {"account": public, "flags": control, "sid": identities[name], "config": conf_stamp}
        public["revision"] = hashlib.sha256(json.dumps(revision, sort_keys=True).encode()).hexdigest()
        rows.append(public)
    return rows


def collect_account_snapshot(saved: dict) -> dict:
    if saved.get("enable_samba") is not True:
        return {"available": False}
    try:
        from smb.account_lifecycle import collect_lifecycle_snapshot

        rows = _accounts()
        return validate_account_snapshot({"available": True, "accounts": rows,
                                          "lifecycle": collect_lifecycle_snapshot(saved, rows)})
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        return {"available": False}


def password_binding() -> str:
    """Bind a staged reset to local config/passdb metadata, without reading hashes."""
    conf = _stamp(SMB_CONF)
    private = _inspect(["/usr/bin/testparm", "-s", "--parameter-name=private dir"]).strip()
    metadata = [conf, _stamp(str(Path(private) / "passdb.tdb")), _stamp("/etc/passwd")]
    if conf != _stamp(SMB_CONF):
        raise ValueError("Samba configuration changed")
    return hashlib.sha256(json.dumps(metadata).encode()).hexdigest()


def _reset_password(value: dict) -> None:
    from smb.password_staging import consume_password

    try:
        from samba.samba3 import passdb
    except ImportError as exc:
        raise RuntimeError("Samba password controls require the Samba Python bindings") from exc

    binding = password_binding()
    password = consume_password(value)
    # smbpasswd can enable a disabled account when changing its password. Use
    # the local tdbsam backend directly instead: no Unix/PAM password hooks,
    # and the original flags and new password share the same database update.
    try:
        passdb.set_smb_config(SMB_CONF)
        database = passdb.PDB("tdbsam")
        account = database.getsampwnam(value["username"])
        flags = account.acct_ctrl
        if account.username != value["username"]:
            raise ValueError("Samba password account identity changed")
        account.plaintext_passwd = password
        account.acct_ctrl = flags
        if binding != password_binding():
            raise ValueError("Samba accounts changed before password update")
        database.update_sam_account(account)
    except passdb.error as exc:
        raise RuntimeError("Samba password update failed") from exc


def change_account(value: dict) -> None:
    value = validate_account_action(value)
    if os.geteuid() != 0:
        raise PermissionError("Samba account controls require root")
    protected_path(STATE_DIR, directory=True)
    marker = STATE_DIR + "/setup-operation.json"
    for path in (marker, marker + ".lock"):
        if os.path.lexists(path):
            protected_path(path)
    with OperationStateStore(marker).ownership() as store:
        if store.load() is not None:
            raise ValueError("Setup or recovery must finish before account changes")
        saved = load_setup_config()
        if saved is None or saved.get("enable_samba") is not True:
            raise ValueError("Samba is not enabled in saved setup")
        rows = _accounts()
        if value["action"] in LIFECYCLE_ACTIONS:
            from smb.account_lifecycle import change_lifecycle

            change_lifecycle(value, store, saved, rows)
            return
        row = next((row for row in rows if row["username"] == value["username"]), None)
        if (row is None or not row["manageable"] or row["revision"] != value["revision"]
                or value["action"] != "reset-password" and row["disabled"] == (value["action"] == "disable")):
            raise ValueError("Samba account changed or cannot be managed")
        if value["action"] == "reset-password":
            _reset_password(value)
        else:
            subprocess.run(["/usr/bin/smbpasswd", "-e" if value["action"] == "enable" else "-d", value["username"]],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           env=ENVIRONMENT, timeout=15, check=True)
        after = next((entry for entry in _accounts() if entry["username"] == value["username"]), None)
        if (after is None or after["uid"] != row["uid"]
                or (after["revision"] != row["revision"] if value["action"] == "reset-password"
                    else after["disabled"] != (value["action"] == "disable"))):
            raise ValueError("Samba account change could not be verified")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ("action", "username", "revision", "job_id"):
        parser.add_argument(field)
    args = parser.parse_args()
    def interrupted(_signum, _frame):
        raise SystemExit(124)
    signal.signal(signal.SIGTERM, interrupted)
    try:
        change_account(vars(args))
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        return 125


if __name__ == "__main__":
    raise SystemExit(main())
