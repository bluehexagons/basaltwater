"""KDE login startup for the package-managed T3 Code desktop application."""

from __future__ import annotations

import os
from pathlib import Path
import re
import stat

from common.cachyos_steps import _MARKER, _write_managed
from lib.validation import validate_filesystem_path


AUTOSTART_NAME = "basaltwater-cachyos-t3.desktop"
AUTOSTART_ENTRY = (
    f"{_MARKER}\n[Desktop Entry]\nType=Application\nName=T3 Code\n"
    "Comment=Start T3 Code at KDE login\n"
    "Exec=/usr/bin/t3code\nTryExec=/usr/bin/t3code\n"
    "Icon=t3code\nTerminal=false\nOnlyShowIn=KDE;\n"
)


def autostart_path(home: Path) -> Path:
    """Inspect the managed destination without creating it or following links."""
    configured = os.environ.get("XDG_CONFIG_HOME", "")
    root = Path(configured) if configured and Path(configured).is_absolute() else home / ".config"
    path = root / "autostart" / AUTOSTART_NAME
    validate_filesystem_path(str(path))
    for parent in (path.parent, *path.parent.parents):
        if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
            raise ValueError("Unsafe T3 desktop autostart directory")
    if path.exists() or path.is_symlink():
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_size > 16384 or info.st_mode & 0o022):
            raise ValueError("T3 desktop autostart must be an owned regular file without group/other write access")
        if not path.read_text().startswith(_MARKER + "\n"):
            raise ValueError("Refusing unmanaged T3 desktop autostart file; inspect KDE's Autostart settings")
    return path


def enable_autostart(home: Path) -> None:
    """Enable the desktop at the next KDE login without launching another app."""
    _write_managed(autostart_path(home), AUTOSTART_ENTRY)


def disable_autostart(home: Path) -> None:
    """Remove only our entry after a successful switch to managed web mode."""
    autostart_path(home).unlink(missing_ok=True)


def collect_autostart_health(home: Path) -> tuple[str, str]:
    """Observe configuration only; never launch Electron or expose file contents."""
    try:
        path = autostart_path(home)
        if not path.exists():
            return "failed", "T3 desktop login startup missing; rerun setup with --t3code-desktop."
        content = path.read_text()
    except (OSError, ValueError):
        return "failed", "T3 desktop autostart unsafe, unreadable, or unmanaged; inspect KDE's Autostart settings locally."
    if re.search(r"^Hidden\s*=\s*true\s*$", content, re.M):
        return "deferred", "T3 desktop login startup disabled in KDE; desktop setup or refresh re-enables it."
    if content != AUTOSTART_ENTRY:
        return "failed", "Managed T3 desktop autostart modified; inspect KDE's Autostart settings or rerun desktop setup."
    return "available", "T3 desktop configured to start at KDE login; app launch, provider threads, and terminals remain unverified."
