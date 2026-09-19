"""Bounded automatic-restart holds for long-running agent work."""

from __future__ import annotations

import argparse
import json
import math
import os
import stat
import time
from datetime import datetime, timezone
from typing import Any, Optional

from lib.atomic_io import remove_file_durable, write_json_atomic
from lib.types import JSONDict
from lib.validation import validate_filesystem_path


DEFAULT_HOLD_HOURS = 8
MAX_HOLD_HOURS = 72
_MAX_HOLD_BYTES = 4096
_CLOCK_SKEW_SECONDS = 5 * 60
_HOLD_STATE_RELATIVE = os.path.join(
    ".local",
    "state",
    "basaltwater",
    "agent-maintenance-hold.json",
)


def agent_maintenance_path(home: str) -> str:
    """Return the fixed per-user maintenance-hold path."""
    user_home = os.path.abspath(os.path.expanduser(home))
    path = os.path.join(user_home, _HOLD_STATE_RELATIVE)
    validate_filesystem_path(path, must_exist=False)
    return path


def _within(path: str, parent: str) -> bool:
    try:
        resolved_parent = os.path.realpath(parent)
        return (
            os.path.commonpath((os.path.realpath(path), resolved_parent))
            == resolved_parent
        )
    except ValueError:
        return False


def _state_parent(home: str, *, create: bool) -> str:
    path = agent_maintenance_path(home)
    parent = os.path.dirname(path)
    if not _within(parent, home):
        raise RuntimeError("Agent maintenance state must remain below the user's home")
    if create:
        os.makedirs(parent, mode=0o700, exist_ok=True)
    if not os.path.isdir(parent) or not _within(parent, home):
        raise RuntimeError("Agent maintenance state must remain below the user's home")
    if os.path.islink(parent):
        raise RuntimeError("Agent maintenance state directory must not be a symbolic link")
    return parent


def _timestamp(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) else None


def _iso_timestamp(value: Optional[float]) -> Optional[str]:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(value, timezone.utc).isoformat().replace(
            "+00:00", "Z"
        )
    except (OSError, OverflowError, ValueError):
        return None


def _result(
    status: str,
    *,
    created_at: Optional[float] = None,
    expires_at: Optional[float] = None,
    now: Optional[float] = None,
) -> JSONDict:
    active = status == "active"
    remaining = (
        max(0, int(expires_at - now))
        if active and expires_at is not None and now is not None
        else 0
    )
    return {
        "status": status,
        "active": active,
        "created_at": _iso_timestamp(created_at),
        "expires_at": _iso_timestamp(expires_at),
        "remaining_seconds": remaining,
    }


def inspect_agent_maintenance(
    home: Optional[str] = None,
    *,
    now: Optional[float] = None,
) -> JSONDict:
    """Inspect a hold without returning arbitrary file contents or paths."""
    user_home = os.path.abspath(home or os.path.expanduser("~"))
    try:
        current_time = time.time() if now is None else float(now)
    except (TypeError, ValueError, OverflowError):
        return _result("invalid")
    if not math.isfinite(current_time):
        return _result("invalid")
    path = agent_maintenance_path(user_home)
    if not _within(path, user_home):
        return _result("invalid")
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
        )
    except FileNotFoundError:
        return _result("inactive")
    except OSError:
        return _result("invalid")

    try:
        home_owner = os.stat(user_home).st_uid
        metadata = os.fstat(descriptor)
        resolved_descriptor = os.path.realpath(f"/proc/self/fd/{descriptor}")
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != home_owner
            or stat.S_IMODE(metadata.st_mode) & 0o077
            or metadata.st_size > _MAX_HOLD_BYTES
            or not _within(resolved_descriptor, user_home)
        ):
            return _result("invalid")
        with os.fdopen(descriptor, encoding="utf-8") as file_obj:
            descriptor = -1
            payload = file_obj.read(_MAX_HOLD_BYTES + 1)
        if len(payload.encode("utf-8")) > _MAX_HOLD_BYTES:
            return _result("invalid")
        state = json.loads(payload)
    except (OSError, json.JSONDecodeError, UnicodeError):
        return _result("invalid")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not isinstance(state, dict) or state.get("schema_version") != 1:
        return _result("invalid")

    created_at = _timestamp(state.get("created_at"))
    expires_at = _timestamp(state.get("expires_at"))
    if (
        created_at is None
        or expires_at is None
        or expires_at <= created_at
        or expires_at - created_at > MAX_HOLD_HOURS * 60 * 60
        or created_at > current_time + _CLOCK_SKEW_SECONDS
    ):
        return _result("invalid")
    status = "active" if expires_at > current_time else "expired"
    return _result(
        status,
        created_at=created_at,
        expires_at=expires_at,
        now=current_time,
    )


def hold_agent_maintenance(
    hours: int = DEFAULT_HOLD_HOURS,
    *,
    home: Optional[str] = None,
    now: Optional[float] = None,
) -> JSONDict:
    """Create or renew a private, bounded automatic-restart hold."""
    if (
        isinstance(hours, bool)
        or not isinstance(hours, int)
        or not 1 <= hours <= MAX_HOLD_HOURS
    ):
        raise ValueError(
            f"Maintenance hold hours must be between 1 and {MAX_HOLD_HOURS}"
        )
    user_home = os.path.abspath(home or os.path.expanduser("~"))
    validate_filesystem_path(user_home, must_exist=True)
    try:
        owner_uid = os.stat(user_home).st_uid
    except OSError as exc:
        raise RuntimeError(f"Could not inspect agent home: {user_home}") from exc
    if owner_uid != os.geteuid():
        raise RuntimeError("Agent maintenance holds must be managed by the home owner")

    current_time = time.time() if now is None else float(now)
    if not math.isfinite(current_time):
        raise ValueError("Maintenance hold time must be finite")
    parent = _state_parent(user_home, create=True)
    os.chmod(parent, 0o700)
    path = agent_maintenance_path(user_home)
    expires_at = current_time + hours * 60 * 60
    write_json_atomic(
        path,
        {
            "schema_version": 1,
            "created_at": current_time,
            "expires_at": expires_at,
        },
        mode=0o600,
        sort_keys=True,
    )
    result = inspect_agent_maintenance(user_home, now=current_time)
    if result["status"] != "active":
        raise RuntimeError("Could not verify the agent maintenance hold")
    return result


def release_agent_maintenance(*, home: Optional[str] = None) -> JSONDict:
    """Remove a maintenance hold idempotently."""
    user_home = os.path.abspath(home or os.path.expanduser("~"))
    validate_filesystem_path(user_home, must_exist=True)
    try:
        owner_uid = os.stat(user_home).st_uid
    except OSError as exc:
        raise RuntimeError(f"Could not inspect agent home: {user_home}") from exc
    if owner_uid != os.geteuid():
        raise RuntimeError("Agent maintenance holds must be managed by the home owner")
    path = agent_maintenance_path(user_home)
    if not os.path.lexists(path):
        return {
            **_result("inactive"),
            "released": False,
        }
    _state_parent(user_home, create=False)
    removed = remove_file_durable(path)
    return {
        **_result("inactive"),
        "released": removed,
    }


def _print_result(result: JSONDict, *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(result, indent=2))
        return
    status = str(result["status"])
    if status == "active":
        print(f"Agent maintenance hold: active until {result['expires_at']}")
    elif status == "expired":
        print(f"Agent maintenance hold: expired at {result['expires_at']}")
    elif status == "invalid":
        print("Agent maintenance hold: invalid; release and recreate it")
    elif result.get("released"):
        print("Agent maintenance hold: released")
    else:
        print("Agent maintenance hold: inactive")


def run_agent_maintenance_command(args: argparse.Namespace) -> int:
    """Run a parsed local maintenance-hold operation."""
    try:
        if args.agent_maintenance_command == "hold":
            result = hold_agent_maintenance(args.hours)
        elif args.agent_maintenance_command == "status":
            result = inspect_agent_maintenance()
        elif args.agent_maintenance_command == "release":
            result = release_agent_maintenance()
        else:
            print(
                "Error: agent maintenance command required "
                "(hold, status, or release)"
            )
            return 1
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Error: {exc}")
        return 1
    _print_result(result, json_output=bool(args.json))
    return 1 if result["status"] == "invalid" else 0
