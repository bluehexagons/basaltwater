"""Track managed context preferences without recording provider configuration."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Iterator

from lib.atomic_io import parse_json, write_json_atomic
from lib.types import JSONDict


CONTEXT_ENV_KEYS = frozenset({
    "CLAUDE_CODE_AUTO_COMPACT_WINDOW", "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE",
    "DISABLE_COMPACT", "DISABLE_AUTO_COMPACT",
})
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


def context_projection(value: JSONDict, tool: str) -> JSONDict:
    """Select context settings only; hashes never include credentials or hooks."""
    if tool == "codex":
        return {key: value[key] for key in (
            "model_auto_compact_token_limit", "model_auto_compact_token_limit_scope",
        ) if key in value}
    if tool == "claude":
        result = {key: value[key] for key in ("autoCompactWindow",) if key in value}
        for key in ("env", "modelSettings"):
            if key in value and not isinstance(value[key], dict):
                raise ValueError(f"Claude configuration '{key}' must contain an object")
        for model, settings in value.get("modelSettings", {}).items():
            if not isinstance(settings, dict):
                raise ValueError("Claude per-model settings must contain objects")
            if "autoCompactWindow" in settings:
                result.setdefault("modelSettings", {})[model] = {"autoCompactWindow": settings["autoCompactWindow"]}
        environment = {key: item for key, item in value.get("env", {}).items() if key in CONTEXT_ENV_KEYS}
        if environment:
            result["env"] = environment
        return result
    raise ValueError("Unsupported managed context tool")


def context_digest(value: JSONDict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def context_paths(value: JSONDict, prefix: tuple[str, ...] = ()) -> list[list[str]]:
    """Locate only managed scalar context preferences."""
    result = []
    for key, item in value.items():
        path = (*prefix, key)
        if isinstance(item, dict):
            result.extend(context_paths(item, path))
        else:
            result.append(list(path))
    return result


def remove_context_paths(value: JSONDict, paths: list[list[str]]) -> None:
    for path in paths:
        current = value
        parents = []
        for key in path[:-1]:
            parents.append((current, key))
            current = current[key]
        del current[path[-1]]
        for parent, key in reversed(parents):
            if parent[key]:
                break
            del parent[key]


def read_context_state(content: str | None, tool: str) -> JSONDict | None:
    if content is None:
        return None
    state = parse_json(content)
    if not isinstance(state, dict) or type(state.get("version")) is not int or state["version"] != 1 or state.get("tool") != tool:
        raise ValueError("Invalid managed context state")
    if type(state.get("overridden")) is not bool:
        raise ValueError("Invalid managed context override state")
    if "active" not in state or "pending" not in state:
        raise ValueError("Incomplete managed context state")
    for name in ("active", "pending"):
        entry = state.get(name)
        if entry is None:
            continue
        if not isinstance(entry, dict) or not isinstance(entry.get("digest"), str) or not _DIGEST.fullmatch(entry["digest"]):
            raise ValueError("Invalid managed context digest")
        paths = entry.get("paths")
        if not isinstance(paths, list) or len(paths) > 2000:
            raise ValueError("Invalid managed context paths")
        # Validate paths by constructing a projection. State cannot nominate
        # credentials, arbitrary settings, or ancestor objects for removal.
        sample: JSONDict = {}
        for path in paths:
            if (not isinstance(path, list) or not 1 <= len(path) <= 5
                    or any(not isinstance(key, str) or not key or len(key) > 200 or any(ord(c) < 32 for c in key) for key in path)):
                raise ValueError("Invalid managed context path")
            current = sample
            for key in path[:-1]:
                current = current.setdefault(key, {})
                if not isinstance(current, dict):
                    raise ValueError("Overlapping managed context paths")
            if path[-1] in current:
                raise ValueError("Duplicate managed context paths")
            current[path[-1]] = 1
        if sorted(paths) != sorted(context_paths(context_projection(sample, tool))):
            raise ValueError("State contains unmanaged context paths")
    return state


def save_context_state(path: Path, state: JSONDict, uid: int, gid: int) -> None:
    write_json_atomic(str(path), state, uid=uid, gid=gid)


@contextmanager
def context_lock(path: Path, uid: int, gid: int) -> Iterator[None]:
    created = False
    try:
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        descriptor = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
    else:
        created = True
    try:
        if created:
            os.fchown(descriptor, uid, gid)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600:
            raise ValueError(f"Unsafe managed context lock: {path}")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Another context setup is running; retry after it finishes") from exc
        yield
    finally:
        os.close(descriptor)
