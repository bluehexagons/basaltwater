"""Durable human choices for agent input, independent of desktop processes."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import math
import os
from pathlib import Path
import secrets
import stat
import time

from lib.atomic_io import read_json_file, write_json_atomic
from lib.validation import validate_filesystem_path

DEFAULT_OVERRIDE_SECONDS = 900
MODES = ("enabled", "human-override", "disabled")


def _private(info):
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RuntimeError("Unsafe desktop control state; expected private owner-only storage")


@contextmanager
def _locked():
    folder = Path.home()
    directories = []
    for component in (".local", "state", "basaltwater", "desktop-control"):
        folder /= component
        validate_filesystem_path(str(folder))
        folder.mkdir(mode=0o700, exist_ok=True)
        info = folder.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & (0o077 if component == "desktop-control" else 0o022)):
            raise RuntimeError("Unsafe desktop control state directory")
        directories.append(folder)
    descriptor = os.open(folder / "state.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        _private(os.fstat(descriptor))
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        if not (folder / "state.json").exists():
            # Persist the entire first-time hierarchy before acknowledging a
            # human choice. Retry this after interrupted directory creation.
            for directory in directories:
                parent = os.open(directory.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    os.fsync(parent)
                finally:
                    os.close(parent)
        yield folder / "state.json"
    finally:
        os.close(descriptor)


def _record(mode, until=None):
    return {"version": 1, "agent_control": mode, "override_until": until,
            "control_revision": secrets.token_hex(16)}


def _read(path):
    try:
        _private(path.lstat())
    except FileNotFoundError:
        # Preserve the old remembered human pause on the first upgraded start.
        from desktop import native_grants
        grant = native_grants.load()
        data = _record("human-override" if grant and grant["paused"] else "enabled")
        write_json_atomic(str(path), data)
        return data
    data = read_json_file(str(path), max_bytes=4096)
    if (not isinstance(data, dict)
            or set(data) != {"version", "agent_control", "override_until", "control_revision"}
            or type(data["version"]) is not int or data["version"] != 1
            or data["agent_control"] not in MODES
            or not isinstance(data["control_revision"], str) or len(data["control_revision"]) != 32
            or any(char not in "0123456789abcdef" for char in data["control_revision"])):
        raise ValueError("Invalid desktop control state")
    until = data["override_until"]
    if until is not None and (data["agent_control"] != "human-override"
                             or type(until) not in (int, float) or not math.isfinite(until) or until <= 0):
        raise ValueError("Invalid human override deadline")
    if until is not None and time.time() >= until:
        # The human approved this return when choosing a timed override.
        data = _record("enabled")
        write_json_atomic(str(path), data)
    return data


def _snapshot(data):
    until = data["override_until"]
    return {key: value for key, value in data.items() if key != "version"} | {
        "paused": data["agent_control"] != "enabled",
        "override_remaining": max(0, math.ceil(until - time.time())) if until is not None else None,
    }


def status():
    with _locked() as path:
        return _snapshot(_read(path))


def change(mode, *, seconds=DEFAULT_OVERRIDE_SECONDS, human_approved=False):
    """UI actions or explicitly approved CLI requests are the only writers."""
    if human_approved is not True:
        raise PermissionError("Changing agent control requires explicit human approval or the human control UI")
    if not isinstance(mode, str) or mode not in MODES:
        raise ValueError("Agent control must be enabled, human-override, or disabled")
    if seconds is not None and (type(seconds) is not int or not 60 <= seconds <= 86400):
        raise ValueError("Human override duration must be 60–86400 seconds, or indefinite")
    until = time.time() + seconds if mode == "human-override" and seconds is not None else None
    with _locked() as path:
        _read(path)  # Validate existing state before replacing it.
        data = _record(mode, until)
        write_json_atomic(str(path), data)
        return _snapshot(data)


def require_enabled(payload):
    current = status()
    if current["agent_control"] != "enabled":
        raise RuntimeError("Agent desktop control is " + current["agent_control"]
                           + "; wait for its timer or obtain explicit human approval to change it")
    if payload.get("control_revision") != current["control_revision"]:
        raise ValueError("Desktop control changed; observe again before sending input")
    return current


def describe(current):
    mode = current.get("agent_control", "enabled")
    if mode == "disabled":
        return "Agents disabled"
    if mode == "human-override":
        remaining = current.get("override_remaining")
        return ("Human override — until you enable agents" if remaining is None
                else f"Human override — {math.ceil(remaining / 60)} min remaining")
    return "Agents enabled"
