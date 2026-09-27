"""Reviewed AUR installation shared by CachyOS desktop software and T3."""

from __future__ import annotations

import os
from pathlib import Path
import re
import shlex
import shutil

from common.cachyos_steps import _directory, _tool_path
from lib.remote_utils import CommandExecutionError, CommandTimeoutError, run
from lib.validation import validate_arch_package_name, validate_filesystem_path


def package_version(package: str) -> str | None:
    """Query package metadata without launching applications or self-updaters."""
    package = validate_arch_package_name(package)
    result = run(["pacman", "-Q", "--", package], capture_output=True, check=False, timeout=15)
    if result.returncode == 1:
        return None
    parts = (result.stdout or "").split()
    if result.returncode or len(parts) != 2 or parts[0] != package or not re.fullmatch(r"[0-9A-Za-z.+_:~-]+", parts[1]):
        raise RuntimeError(f"Cannot determine {package} package state; inspect pacman before retrying")
    return parts[1]


def install_command(home: Path, package: str) -> list[str]:
    """Prefer current CachyOS's Shelly CLI, preserving its review policy."""
    package = validate_arch_package_name(package)
    executable = shutil.which("shelly", path=_tool_path(home))
    if executable:
        return [executable, "install", "aur", package]
    for name in ("paru", "yay"):
        executable = shutil.which(name, path=_tool_path(home))
        if executable:
            return [executable, "-S", "--aur", "--needed", "--", package]
    raise RuntimeError("Installing AUR packages requires Shelly, paru, or yay; "
                       "restore CachyOS's default helper with sudo pacman -S --needed shelly "
                       "and rerun your setup selection")


def prepare_cache(home: Path, *, create: bool = False) -> None:
    """Prepare user-owned caches before Shelly elevates and runs Git as us.

    Shelly 3.1.6 creates its cache as root, then drops privileges for Git.
    Check both XDG and default locations because elevation may drop XDG.
    Existing caches are never deleted, chowned, or recursively repaired.
    """
    roots = [home / ".cache"]
    configured = os.environ.get("XDG_CACHE_HOME", "")
    if configured and Path(configured).is_absolute() and Path(configured) not in roots:
        roots.append(Path(configured))
    for root in roots:
        cache = root / "Shelly"
        validate_filesystem_path(str(cache))
        existing = Path(cache.anchor)
        for path in reversed((cache, *cache.parents)):
            if path.is_symlink() or (path.exists() and not path.is_dir()):
                raise ValueError(f"Unsafe Shelly AUR cache directory: {path}")
            if path.exists():
                existing = path
                if not os.access(path, os.X_OK):
                    break  # Report this parent before inspecting its children.
        # The nearest existing parent must let this account create missing
        # directories. Checking this in preflight avoids a late opaque failure.
        if not os.access(existing, os.W_OK | os.X_OK) or (
            existing == cache and existing.stat().st_uid != os.getuid()
        ):
            raise RuntimeError(
                f"Shelly AUR cache is not writable/owned by this user: {existing}. "
                f"Inspect with: ls -ld -- {shlex.quote(str(existing))}. "
                "Repair that directory's ownership/permissions before retrying; "
                "a root-owned Shelly cache can cause its generic download error."
            )
        if create:
            _directory(cache)


def install_package(command: list[str], home: Path) -> None:
    shelly = Path(command[0]).name == "shelly"
    if shelly:
        prepare_cache(home, create=True)
    try:
        # Keep stdin/stdout/stderr attached for review, build, and sudo prompts.
        run(command, interactive=True)
    except (CommandExecutionError, CommandTimeoutError) as exc:
        details = (
            "\nShelly may omit Git's underlying clone/pull error. Check cache "
            "ownership and the configured source with `shelly config get AurUrl`; "
            "see docs/CACHYOS.md (AUR download failures) for a Git-only probe. "
            "Shelly logs: /var/log/shelly.log or "
            "${XDG_STATE_HOME:-$HOME/.local/state}/shelly/shelly.log."
        ) if shelly else ""
        raise RuntimeError(
            f"AUR installation failed: {exc}\n"
            f"Retry as your desktop user: {shlex.join(command)}{details}"
        ) from exc
