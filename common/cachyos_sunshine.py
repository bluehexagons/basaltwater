"""Enable the package-owned Sunshine service in the existing KDE session."""

from __future__ import annotations

import os
from pathlib import Path
import re
import stat
import time

from common.cachyos_steps import _directory, _home
from lib.atomic_io import write_text_atomic
from lib.config import SetupConfig
from lib.machine_state import can_manage_system_services
from lib.remote_utils import is_dry_run, run
from lib.validation import validate_filesystem_path


SERVICE = "app-dev.lizardbyte.app.Sunshine.service"
UNIT = "/usr/lib/systemd/user/" + SERVICE


def _config_path(home: Path) -> Path:
    path = home / ".config/sunshine/sunshine.conf"
    validate_filesystem_path(str(path))
    for parent in (path.parent, *path.parent.parents):
        if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
            raise ValueError("Unsafe Sunshine configuration directory")
        if parent.is_relative_to(home) and parent.exists() and parent.stat().st_uid != os.getuid():
            raise ValueError("Sunshine configuration directories must belong to the desktop user")
    if path.exists() or path.is_symlink():
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size > 65536:
            raise ValueError("Sunshine configuration must be an owned regular file of at most 64 KiB")
    return path


def preflight(config: SetupConfig) -> None:
    if not can_manage_system_services(config.machine_type):
        raise ValueError("This machine cannot manage Sunshine's user service")
    _config_path(_home(config))
    result = run(["systemctl", "--user", "is-active", "graphical-session.target"],
                 check=False, capture_output=True, timeout=15)
    if result.returncode or result.stdout.strip() != "active":
        raise ValueError("Sunshine setup requires an active KDE graphical session")


def _default_intel_encoder(path: Path) -> None:
    """Prefer verified VA-API on Intel when no encoder was explicitly selected."""
    previous = path.read_text() if path.exists() else ""
    if re.search(r"^\s*encoder\s*=", previous, re.M):
        return
    try:
        vendors = {node.read_text().strip().lower() for node in
                   Path("/sys/class/drm").glob("renderD[0-9]*/device/vendor")}
    except OSError:
        return
    if vendors != {"0x8086"}:
        return
    from lib.cachyos_doctor import _probe
    from lib.cachyos_health import collect_sunshine_health

    observations = collect_sunshine_health(_probe, os.getuid(), bus_ready=False)
    if not any(name == "graphics.sunshine-vaapi" and state == "available" for name, state, _ in observations):
        print("  Intel VA-API encoding unverified; retain automatic encoder selection and inspect vainfo")
        return
    _directory(path.parent)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    write_text_atomic(str(path), previous + ("\n" if previous and not previous.endswith("\n") else "") +
                      "# Basaltwater default: verified Intel VA-API avoids Vulkan encoder probing\nencoder = vaapi\n", mode=mode)
    print("  Sunshine: selected verified Intel VA-API; other settings retained")


def configure(config: SetupConfig) -> None:
    if config.dry_run or is_dry_run():
        print("  Would enable Sunshine at KDE login and start its user service; normal quit stays stopped")
        return
    preflight(config)
    path = _config_path(_home(config))
    result = run(["systemctl", "--user", "show", SERVICE, "--property=LoadState",
                  "--property=FragmentPath", "--property=DropInPaths", "--property=Restart",
                  "--property=ActiveState"], capture_output=True, timeout=15)
    values = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    if (set(values) != {"LoadState", "FragmentPath", "DropInPaths", "Restart", "ActiveState"}
            or values["LoadState"] != "loaded" or values["FragmentPath"] != UNIT
            or values["DropInPaths"] or values["Restart"] not in {"no", "on-failure"}):
        raise RuntimeError("Resolve masked, overridden, or custom Sunshine units before setup; existing units retained")
    for target in (UNIT, "/usr/bin/sunshine"):
        owner = run(["pacman", "-Qqo", "--", target], capture_output=True, timeout=15)
        if owner.stdout.strip() != "sunshine":
            raise RuntimeError("Sunshine unit and executable must belong to the native sunshine package")
    if values["ActiveState"] != "active":
        _default_intel_encoder(path)
    run(["systemctl", "--user", "reset-failed", SERVICE], timeout=15)
    run(["systemctl", "--user", "enable", "--now", SERVICE], timeout=30)
    stable = 0
    for attempt in range(12):
        result = run(["systemctl", "--user", "is-active", SERVICE],
                     capture_output=True, check=False, timeout=15)
        stable = stable + 1 if result.returncode == 0 and result.stdout.strip() == "active" else 0
        if stable >= 3:
            break
        if result.stdout.strip() == "failed" or attempt == 11:
            raise RuntimeError("Sunshine did not start; inspect its user journal and encoder/capture settings, then rerun setup")
        time.sleep(1)
    enabled = run(["systemctl", "--user", "is-enabled", SERVICE],
                  capture_output=True, timeout=15)
    if enabled.stdout.strip() != "enabled":
        raise RuntimeError("Sunshine login startup could not be verified")
    print("  Sunshine active and enabled at KDE login; quit the tray app or stop its user service to pause")
    print("  Pair Moonlight through local Sunshine administration and verify video, audio, and input")
