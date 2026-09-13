"""Fail closed on invalid persisted state while preserving recovery evidence."""

from __future__ import annotations

from lib.atomic_io import read_json_file


class StateReadError(ValueError):
    def __init__(self, path: str, reason: str):
        super().__init__(
            f"Invalid state at {path}: {reason}. File retained; restore a verified "
            "backup or explicitly quarantine it after reviewing recovery needs."
        )


def read_state_object(path: str, *, versioned: bool = True) -> dict | None:
    try:
        value = read_json_file(path)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise StateReadError(path, "unreadable, unsafe, or malformed JSON") from exc
    if not isinstance(value, dict):
        raise StateReadError(path, "expected an object")
    if versioned and (type(value.get("version", 1)) is not int or value.get("version", 1) != 1):
        raise StateReadError(path, "unsupported schema version")
    return value
