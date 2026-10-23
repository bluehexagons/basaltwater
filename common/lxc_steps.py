"""Scoped compatibility settings for systemd in unprivileged LXC guests."""

from __future__ import annotations

from pathlib import Path

from lib.atomic_io import write_text_atomic
from lib.config import SetupConfig
from lib.machine_state import can_manage_system_services, is_unprivileged
from lib.remote_utils import is_dry_run, read_os_release, run


SYSTEMD_OVERRIDE_DIRECTORY = Path("/etc/systemd/system")
REBOOT_MARKER = Path("/run/reboot-required")
_MOUNT_UNITS = ("tmp.mount", "run-lock.mount", "dev-mqueue.mount")
_MOUNT_COMPAT = """# Managed by Basaltwater: keep filesystem-specific LXC mount confinement.
[Mount]
Environment=LIBMOUNT_FORCE_MOUNT2=always
"""


def configure_lxc_mount_compatibility(config: SetupConfig) -> None:
    """Use classic mounts for Debian 13's three affected systemd mount units.

    util-linux's detached mount API reaches AppArmor as a mount move without
    a filesystem type. Stock PVE confinement rejects it. Keep the adjustment
    local to these units and activate it at reboot, never over a populated /tmp.
    """
    if not can_manage_system_services() or not is_unprivileged():
        return
    if config.dry_run or is_dry_run():
        print("  [DRY-RUN] Would check Debian LXC mount compatibility")
        return
    release = read_os_release()
    if release.get("ID") != "debian" or release.get("VERSION_ID") != "13":
        return
    detection = run(
        ["systemd-detect-virt", "--container"],
        capture_output=True, check=False, timeout=10,
    )
    if detection.returncode != 0 or detection.stdout.strip() != "lxc":
        return

    changed = False
    for unit in _MOUNT_UNITS:
        state = run(
            ["systemctl", "show", unit, "--property=LoadState", "--value"],
            capture_output=True, timeout=30,
        )
        if state.stdout.strip() != "loaded":
            continue
        path = SYSTEMD_OVERRIDE_DIRECTORY / f"{unit}.d" / "50-basaltwater-mount-compat.conf"
        if path.exists() and path.read_text(encoding="utf-8") == _MOUNT_COMPAT:
            continue
        write_text_atomic(str(path), _MOUNT_COMPAT, mode=0o644)
        changed = True

    if changed:
        REBOOT_MARKER.touch(exist_ok=True)
        run(["systemctl", "daemon-reload"], timeout=30)
        print("  ✓ Configured LXC mount compatibility; restart the container to apply")
