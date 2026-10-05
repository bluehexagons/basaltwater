"""Private, revocable portal restore tokens; never include tokens in receipts."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import stat

from lib.atomic_io import read_json_file, remove_file_durable, write_json_atomic
from lib.validation import validate_filesystem_path, validate_no_control_characters


def directory(*, create=False):
    path = Path.home()
    for component in (".local", "state", "basaltwater", "native-desktop"):
        path /= component
        validate_filesystem_path(str(path))
        if create:
            path.mkdir(mode=0o700, exist_ok=True)
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise RuntimeError("Unsafe native grant directory")
        if component == "native-desktop" and info.st_mode & 0o077:
            raise RuntimeError("Native grant directory must have mode 0700")
    return path


def validate_token(token):
    if not isinstance(token, str) or not 1 <= len(token) <= 4096:
        raise ValueError("Invalid portal restore token")
    validate_no_control_characters(token, "portal restore token")


def load():
    path = directory() / "grant.json"
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RuntimeError("Unsafe native grant file")
    data = read_json_file(str(path), max_bytes=8192)
    if (not isinstance(data, dict) or set(data) != {"version", "token", "consumed", "paused"}
            or type(data["version"]) is not int or data["version"] != 1 or type(data["paused"]) is not bool):
        raise ValueError("Invalid native grant record")
    for token in (data["token"], data["consumed"]):
        if token is not None:
            validate_token(token)
    return data


def metadata():
    data = load()
    return {"remember_requested": data is not None, "grant_saved": bool(data and data["token"]),
            "paused": bool(data and data["paused"])}


@contextmanager
def locked():
    folder = directory(create=True)
    fd = os.open(folder / "grant.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise RuntimeError("Unsafe native grant lock")
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield folder / "grant.json"
    finally:
        os.close(fd)


def enable():
    with locked() as path:
        if load() is None:
            write_json_atomic(str(path), {"version": 1, "token": None, "consumed": None, "paused": False})


def consume():
    """Clear a single-use token before sending it; crashes require fresh approval."""
    with locked() as path:
        data = load()
        if data is None:
            return None
        token = data["token"]
        if token is not None:
            write_json_atomic(str(path), {**data, "token": None, "consumed": token})
        return token


def save_token(token):
    validate_token(token)
    with locked() as path:
        data = load()
        if data is None:
            raise RuntimeError("Native grant was revoked; refusing to save a token")
        write_json_atomic(str(path), {**data, "token": token, "consumed": None})


def set_paused(paused):
    if type(paused) is not bool:
        raise ValueError("Native pause state must be boolean")
    with locked() as path:
        data = load()
        if data is not None:
            write_json_atomic(str(path), {**data, "paused": paused})


def forget():
    with locked() as path:
        load()  # Reject unsafe state before deletion.
        remove_file_durable(str(path))
