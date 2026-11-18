"""Ownership checks for source that privileged Basaltwater processes execute."""

from __future__ import annotations

import os
from pathlib import Path
import stat

from lib.validation import validate_filesystem_path


RUNTIME_DATA_NAMES = frozenset({
    "state", "deployments", "worktrees", "cachyos-t3",
    "agent_payload", "device_pairing_payload", "web_panel_payload",
    "github_runner_payload", ".remote_setup_args.json",
})
OFFICIAL_SOURCE_URL = "https://github.com/bluehexagons/basaltwater.git"
SYSTEM_INSTALL_MARKER = ".basaltwater/system-install"
SOURCE_RESERVED_NAMES = RUNTIME_DATA_NAMES | {
    ".basaltwater-installation.json", ".basaltwater-refresh.json",
}


def require_source_parent(path: Path, *, owner_uid: int | None = None) -> None:
    """Require protected ancestors of a runtime or staging path."""
    validate_filesystem_path(str(path), must_exist=True)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Runtime parent must be absolute and normalized: {path}")
    owner = os.geteuid() if owner_uid is None else owner_uid
    for candidate in (path, *path.parents):
        info = candidate.lstat()
        # A root-owned sticky ancestor cannot displace its protected child.
        sticky_ancestor = (
            candidate != path and info.st_uid == 0 and info.st_mode & stat.S_ISVTX
        )
        if not stat.S_ISDIR(info.st_mode) or (
            not sticky_ancestor and (info.st_uid not in {0, owner} or info.st_mode & 0o022)
        ):
            raise ValueError(f"Unsafe privileged source directory: {candidate}")


def ensure_source_directory(path: Path, mode: int) -> None:
    """Create missing directories only below an already protected ancestor."""
    if not os.path.lexists(path):
        ensure_source_directory(path.parent, 0o755)
        path.mkdir(mode=mode, exist_ok=True)
    require_source_parent(path)


def require_runtime_source(path: Path, *, owner_uid: int | None = None) -> None:
    """Reject writable code, symlinks and special files throughout a runtime."""
    owner = os.geteuid() if owner_uid is None else owner_uid
    require_source_parent(path, owner_uid=owner)
    if path.lstat().st_uid != owner:
        raise ValueError(f"Unsafe privileged source owner: {path}")
    git_dir = path / ".git"
    if (os.path.lexists(git_dir) and not git_dir.is_dir()
            or any(os.path.lexists(git_dir / name) for name in ("commondir", "objects/info/alternates"))):
        raise ValueError(f"Privileged source requires a standalone Git checkout: {git_dir}")

    def raise_walk_error(error: OSError) -> None:
        raise error

    for directory, dirs, files in os.walk(path, onerror=raise_walk_error):
        if directory == str(path):
            dirs[:] = [name for name in dirs if name not in RUNTIME_DATA_NAMES]
            files = [name for name in files if name not in RUNTIME_DATA_NAMES]
        for name in (*dirs, *files):
            candidate = Path(directory, name)
            info = candidate.lstat()
            if (
                info.st_uid != owner or info.st_mode & 0o022
                or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))
            ):
                raise ValueError(f"Unsafe privileged source entry: {candidate}")


def trusted_git_command(*arguments: str) -> list[str]:
    """Build a Git command for the official HTTPS source."""
    return [
        "env", "-i", f"PATH={os.environ['PATH']}", "LC_ALL=C",
        "GIT_CONFIG_GLOBAL=/dev/null", "GIT_CONFIG_NOSYSTEM=1", "GIT_TERMINAL_PROMPT=0",
        "sh", "-c", 'umask 022; exec "$@"', "sh", "git",
        "--no-replace-objects", "-c", "core.hooksPath=/dev/null", "-c", "protocol.allow=never",
        "-c", "protocol.https.allow=always", "-c", "http.followRedirects=false",
        "-c", "http.sslVerify=true", *arguments,
    ]
