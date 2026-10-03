"""Install owned skill bundles without replacing personal supporting files."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
from typing import Iterable

from lib.atomic_io import read_json_file, write_json_atomic
from lib.validation import validate_filesystem_path


SKILL_AGENT_TOOLS = frozenset({"codex", "opencode", "claude"})
_MARKER = b"managed-by: basaltwater"
_INVENTORY = ".basaltwater-files.json"
_RESOURCE_DIRS = {"references", "scripts", "assets", "agents"}
_MAX_RESOURCE_BYTES = 16 * 1024 * 1024


def skill_catalogs(agent_tools: Iterable[str]) -> tuple[str, ...]:
    """Return the distinct personal catalogs consumed by selected providers."""
    tools = set(agent_tools)
    return tuple(
        directory
        for directory, providers in (
            (".agents", {"codex", "opencode"}),
            (".claude", {"claude"}),
        )
        if tools.intersection(providers)
    )


def check_skill_path(path: Path, uid: int) -> None:
    """Reject symlink ancestors and existing entries owned by another user."""
    validate_filesystem_path(str(path))
    for parent in (*reversed(path.parents), path):
        if parent.is_symlink():
            raise RuntimeError(f"Refusing unsafe agent skill path: {parent}")
    if path.exists() and path.stat().st_uid != uid:
        raise RuntimeError(f"Refusing agent skill path owned by another user: {path}")


def ensure_skill_directory(path: Path, uid: int, gid: int) -> bool:
    check_skill_path(path, uid)
    if path.exists():
        if not path.is_dir():
            raise RuntimeError(f"Refusing unsafe agent skill directory: {path}")
        return False
    path.mkdir(mode=0o755)
    os.chown(path, uid, gid)
    return True


def _resource_name(name: str) -> bool:
    parts = PurePosixPath(name).parts
    return bool(
        len(parts) >= 2 and parts[0] in _RESOURCE_DIRS
        and str(PurePosixPath(name)) == name
        and all(part not in {".", ".."} for part in parts)
        and "\\" not in name
    )


def _read(path: Path, uid: int | None = None) -> bytes | None:
    if uid is not None:
        check_skill_path(path, uid)
    if not os.path.lexists(path):
        return None
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_RESOURCE_BYTES
            or (uid is not None and info.st_uid != uid)
        ):
            raise RuntimeError(f"Refusing unsafe managed agent skill file: {path}")
        content = stream.read(_MAX_RESOURCE_BYTES + 1)
    if len(content) > _MAX_RESOURCE_BYTES:
        raise RuntimeError(f"Managed agent skill file is too large: {path}")
    return content


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def skill_resources_ready(directory: Path, uid: int) -> bool:
    """Check recorded bundle resources while accepting older entrypoint-only skills."""
    try:
        for name, digest in _inventory(directory, uid).items():
            path = directory / name
            content = _read(path, uid)
            if content is None or _digest(content) != digest or path.stat().st_mode & 0o022:
                return False
        return True
    except (OSError, ValueError, RuntimeError):
        return False


def _inventory(directory: Path, uid: int) -> dict[str, str]:
    path = directory / _INVENTORY
    check_skill_path(path, uid)
    if not os.path.lexists(path):
        return {}
    try:
        value = read_json_file(str(path))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"Invalid managed agent skill inventory: {path}") from exc
    if not isinstance(value, dict) or value.get("version") != 1:
        raise RuntimeError(f"Invalid managed agent skill inventory: {path}")
    files = value.get("files")
    if not isinstance(files, dict) or any(
        not isinstance(name, str) or not _resource_name(name)
        or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
        for name, digest in files.items()
    ):
        raise RuntimeError(f"Invalid managed agent skill inventory: {path}")
    return files


def _source_bundle(source: Path) -> dict[str, tuple[bytes, int]]:
    validate_filesystem_path(str(source), must_exist=True)
    if source.is_symlink() or not source.is_dir():
        raise RuntimeError(f"Managed agent skill source is unsafe: {source}")
    files: dict[str, tuple[bytes, int]] = {}

    def visit(directory: Path) -> None:
        for path in sorted(directory.iterdir()):
            name = path.relative_to(source).as_posix()
            if path.is_symlink():
                raise RuntimeError(f"Refusing symlinked managed agent skill source: {path}")
            if path.is_dir():
                if PurePosixPath(name).parts[0] not in _RESOURCE_DIRS:
                    raise RuntimeError(f"Unsupported managed agent skill resource: {path}")
                visit(path)
            else:
                if name != "SKILL.md" and not _resource_name(name):
                    raise RuntimeError(f"Unsupported managed agent skill resource: {path}")
                content = _read(path)
                if content is None:
                    raise RuntimeError(f"Managed agent skill source disappeared: {path}")
                files[name] = (content, 0o755 if path.stat().st_mode & 0o111 else 0o644)

    visit(source)
    entrypoint = files.get("SKILL.md", (b"", 0))[0]
    if _MARKER not in entrypoint or len(entrypoint) > 256 * 1024:
        raise RuntimeError(f"Managed agent skill entrypoint is missing or invalid: {source}")
    files["SKILL.md"] = (entrypoint, 0o644)
    return files


def _write(path: Path, content: bytes, mode: int, uid: int, gid: int) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=".basaltwater-skill-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.chown(temporary, uid, gid)
        check_skill_path(path, uid)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _remove_resources(directory: Path, recorded: dict[str, str], uid: int) -> bool:
    """Remove only unchanged, recorded resources and their empty directories."""
    changed = False
    parents: set[Path] = set()
    for name, digest in recorded.items():
        path = directory / name
        content = _read(path, uid)
        if content is not None and _digest(content) == digest:
            path.unlink()
            changed = True
        parents.update(parent for parent in path.parents if directory in parent.parents)
    for parent in sorted(parents, key=lambda path: len(path.parts), reverse=True):
        check_skill_path(parent, uid)
        if parent.exists() and parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
    return changed


def install_skill_bundle(source: Path, directory: Path, uid: int, gid: int) -> bool:
    """Refresh a bundle, preflighting collisions before replacing any files."""
    files = _source_bundle(source)
    check_skill_path(directory, uid)
    if directory.exists() and not directory.is_dir():
        raise RuntimeError(f"Refusing unsafe agent skill directory: {directory}")
    recorded = _inventory(directory, uid)
    previous: dict[str, bytes | None] = {}
    for name, (content, _mode) in files.items():
        path = directory / name
        for parent in path.parents:
            if parent == directory.parent:
                break
            check_skill_path(parent, uid)
            if parent.exists() and not parent.is_dir():
                raise RuntimeError(f"Refusing unsafe agent skill directory: {parent}")
        previous[name] = _read(path, uid)
        if previous[name] is None:
            continue
        if name == "SKILL.md":
            if _MARKER not in previous[name]:
                raise RuntimeError(f"Refusing to replace unmanaged agent skill: {path}")
        elif name not in recorded or _digest(previous[name]) not in {recorded[name], _digest(content)}:
            raise RuntimeError(f"Refusing to replace personal or modified agent skill resource: {path}")
    stale = {name: digest for name, digest in recorded.items() if name not in files}
    # Inspect obsolete paths before writing too: a symlink must never turn a
    # catalog refresh into a deletion outside the skill directory.
    for name in stale:
        _read(directory / name, uid)
    changed = ensure_skill_directory(directory, uid, gid)
    for name, (content, mode) in files.items():
        path = directory / name
        for parent in reversed(path.parents):
            if directory in parent.parents:
                changed = ensure_skill_directory(parent, uid, gid) or changed
        if previous[name] != content:
            _write(path, content, mode, uid, gid)
            changed = True
        else:
            if stat.S_IMODE(path.stat().st_mode) != mode:
                os.chmod(path, mode)
                changed = True
            os.chown(path, uid, gid)
    changed = _remove_resources(directory, stale, uid) or changed
    inventory = {name: _digest(content) for name, (content, _mode) in files.items() if name != "SKILL.md"}
    if inventory != recorded or (inventory and not (directory / _INVENTORY).exists()):
        write_json_atomic(str(directory / _INVENTORY), {"version": 1, "files": inventory}, uid=uid, gid=gid)
        changed = True
    inventory_path = directory / _INVENTORY
    if inventory_path.exists() and stat.S_IMODE(inventory_path.stat().st_mode) != 0o600:
        inventory_path.chmod(0o600)
        changed = True
    return changed


def remove_skill_bundle(directory: Path, uid: int) -> bool:
    """Retire managed guidance while preserving personal and edited resources."""
    check_skill_path(directory, uid)
    entrypoint = directory / "SKILL.md"
    content = _read(entrypoint, uid)
    if content is None or _MARKER not in content:
        return False
    recorded = _inventory(directory, uid)
    for name in recorded:
        _read(directory / name, uid)
    _remove_resources(directory, recorded, uid)
    entrypoint.unlink()
    inventory = directory / _INVENTORY
    if inventory.exists():
        inventory.unlink()
    if not any(directory.iterdir()):
        directory.rmdir()
    return True
