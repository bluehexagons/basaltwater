"""One-use root-only runtime secrets, separate from durable approval records."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import math
import os
from pathlib import Path
import subprocess
import time

from lib.atomic_io import read_json_file, remove_file_durable, write_json_atomic
from lib.privilege_policy import load_policy, protected_path
from lib.privilege_execution import ENVIRONMENT
from lib.samba_accounts import HEX_ID, validate_account_action, validate_reset_password

PASSWORD_DIRECTORY = "/run/basaltwater-privilege-broker/samba-passwords"
PASSWORD_TTL = 3600
MAX_STAGED = 32


@contextmanager
def _owner(*, create: bool = False):
    parent = os.path.dirname(PASSWORD_DIRECTORY)
    protected_path(parent, directory=True)
    result = subprocess.run(["/usr/bin/findmnt", "--noheadings", "--output", "FSTYPE", "--target", parent],
                            capture_output=True, text=True, timeout=2, check=False, env=ENVIRONMENT)
    if result.returncode or result.stdout.strip() != "tmpfs":
        raise ValueError("Password staging requires a memory-backed runtime directory")
    if create:
        try:
            os.mkdir(PASSWORD_DIRECTORY, 0o700)
        except FileExistsError:
            pass
    protected_path(PASSWORD_DIRECTORY, directory=True)
    if os.lstat(PASSWORD_DIRECTORY).st_mode & 0o077:
        raise ValueError("Password runtime directory must be private")
    lock_path = PASSWORD_DIRECTORY + "/.lock"
    if os.path.lexists(lock_path):
        protected_path(lock_path)
    with open(lock_path, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def _path(job_id: str) -> str:
    if not isinstance(job_id, str) or not HEX_ID.fullmatch(job_id):
        raise ValueError("Invalid password staging identity")
    return PASSWORD_DIRECTORY + "/" + job_id + ".json"


def _read(path: str) -> dict:
    protected_path(path)
    if os.lstat(path).st_mode & 0o077:
        raise ValueError("Password staging file must be private")
    value = read_json_file(path, max_bytes=4096)
    if (not isinstance(value, dict) or set(value) != {"parameters", "uid", "password", "expires", "binding"}
            or type(value["uid"]) is not int or value["uid"] < 1000
            or type(value["expires"]) not in {int, float} or not math.isfinite(value["expires"])
            or not isinstance(value["binding"], str) or len(value["binding"]) != 64):
        raise ValueError("Invalid staged password")
    validate_account_action(value["parameters"])
    if value["parameters"]["action"] != "reset-password":
        raise ValueError("Invalid staged password action")
    validate_reset_password(value["password"])
    return value


def _prune(*, clear: bool = False) -> int:
    count = 0
    for path in Path(PASSWORD_DIRECTORY).iterdir():
        if path.suffix != ".json" or not HEX_ID.fullmatch(path.stem):
            continue
        value = _read(str(path))
        if clear or value["expires"] <= time.time():
            remove_file_durable(str(path))
        else:
            count += 1
    return count


def prune_passwords(*, clear: bool = False) -> None:
    if not os.path.lexists(PASSWORD_DIRECTORY):
        return
    with _owner():
        _prune(clear=clear)


def stage_password(parameters: dict, password: str, uid: int) -> dict:
    from smb.account_management import password_binding

    parameters = validate_account_action(parameters)
    password = validate_reset_password(password)
    if parameters["action"] != "reset-password" or type(uid) is not int or uid < 1000:
        raise ValueError("Invalid password staging request")
    with _owner(create=True):
        if _prune() >= MAX_STAGED:
            raise ValueError("Password staging limit reached")
        path = _path(parameters["job_id"])
        if os.path.lexists(path):
            raise ValueError("This password staging identity was already used")
        # The broker's read-only sandbox must not open Samba's database through
        # pdbedit, which requires write access even for listing. Bind metadata
        # here; the separately approved worker checks fresh account eligibility
        # and the reviewed account revision before consuming the secret.
        binding = password_binding()
        expires = time.time() + PASSWORD_TTL
        write_json_atomic(path, {"parameters": parameters, "uid": uid, "password": password,
                                 "expires": expires, "binding": binding}, mode=0o600)
    return {"job_id": parameters["job_id"], "expires": expires}


def require_password(parameters: dict, uid: int) -> None:
    """Check acceptance without returning a secret to the broker database path."""
    with _owner():
        value = _read(_path(parameters["job_id"]))
        if value["parameters"] != parameters or value["uid"] != uid or value["expires"] <= time.time():
            raise ValueError("The staged password expired or does not match this request")


def consume_password(parameters: dict) -> str:
    from smb.account_management import password_binding

    with _owner():
        path = _path(parameters["job_id"])
        value = _read(path)
        # Remove before effects, including failed/stale attempts. Never replay.
        remove_file_durable(path)
        if (value["parameters"] != parameters or value["uid"] != load_policy()["requester_uid"]
                or value["expires"] <= time.time() or value["binding"] != password_binding()):
            raise ValueError("The password reset expired or Samba accounts changed")
        return value["password"]


def discard_password(job_id: str) -> None:
    if not os.path.lexists(PASSWORD_DIRECTORY):
        return
    with _owner():
        path = _path(job_id)
        if os.path.lexists(path):
            protected_path(path)
            remove_file_durable(path)
