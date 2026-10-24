"""KDE login startup for the package-managed T3 Code desktop application."""

from __future__ import annotations

import os
from pathlib import Path
import pwd
import re
import stat
import time
from typing import TYPE_CHECKING
import uuid

from common.cachyos_steps import _MARKER, _write_managed
from lib.validation import validate_filesystem_path

if TYPE_CHECKING:
    from lib.config import SetupConfig


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


def queue_refresh(config: SetupConfig) -> None:
    """Reopen a running desktop after setup, even when it owns this agent turn."""
    from lib.machine_state import can_manage_system_services
    from lib.remote_utils import is_dry_run, run

    if config.dry_run or is_dry_run():
        print("  Would queue a graceful T3 desktop restart if running; stopped apps stay stopped")
        return
    if os.getuid() == 0 or pwd.getpwnam(config.username).pw_uid != os.getuid():
        raise ValueError("Queue T3 desktop refresh as the selected desktop user, without sudo")
    if not can_manage_system_services(config.machine_type):
        raise ValueError("This machine cannot manage the T3 desktop refresh worker")
    helper = Path(__file__).resolve().parent / "service_tools/cachyos_t3_refresh.py"
    validate_filesystem_path(str(helper), must_exist=True)
    unit = "basaltwater-t3-desktop-refresh-" + uuid.uuid4().hex
    queued_before_ns = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
    run(["/usr/bin/systemd-run", "--user", "--collect", "--unit=" + unit,
         "--on-active=5s", "--timer-property=AccuracySec=1s",
         "--property=Type=exec", "--property=UMask=0077",
         "--property=StandardOutput=null", "--property=StandardError=journal",
         "/usr/bin/python3", "-I", str(helper), str(queued_before_ns)],
        capture_output=True, timeout=15)
    print("  T3 desktop refresh queued: running app will reopen gracefully; stopped app stays stopped")
    print(f"  Refresh status: systemctl --user status {unit}.service; journalctl --user -u {unit}.service")
    print("  Saved threads resume on the next message; automatic turn continuation follows T3 settings")
    print("  Verify an authenticated provider thread and terminal after the app reopens")
