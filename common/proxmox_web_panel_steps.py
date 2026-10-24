"""Publish Proxmox inventory and isolate the optional panel from host secrets."""

from __future__ import annotations

import os

from lib.atomic_io import write_text_atomic
from lib.proxmox_panel import SNAPSHOT, SNAPSHOT_DIR
from lib.remote_utils import is_service_active, run
from lib.setup_directories import check_directory_path, directory_fd
from lib.unit_transaction import remove_units, replace_units, snapshot_unit_file


PROXMOX_PANEL_NGINX_DROPIN = "/etc/systemd/system/nginx.service.d/50-basaltwater-web-panel.conf"
PROXMOX_PANEL_INACCESSIBLE_PATHS = "InaccessiblePaths=-/etc/pve -/var/lib/pve-cluster"
_MARKER = "# Managed by basaltwater Proxmox web panel"
_CONTENT = f"{_MARKER}\n[Service]\n{PROXMOX_PANEL_INACCESSIBLE_PATHS}\n"
SNAPSHOT_SERVICE = "basaltwater-web-panel-proxmox.service"
SNAPSHOT_TIMER = "basaltwater-web-panel-proxmox.timer"
_SNAPSHOT_SCRIPT = "/opt/basaltwater/common/service_tools/web_panel_proxmox.py"


def proxmox_snapshot_present() -> bool:
    """Keep panel removal independent of Proxmox when it was never enabled."""
    return os.path.lexists(SNAPSHOT_DIR) or any(
        snapshot_unit_file("/etc/systemd/system/" + unit) is not None for unit in (SNAPSHOT_TIMER, SNAPSHOT_SERVICE)
    )


def preflight_proxmox_snapshot() -> None:
    """Reject operator-owned units and unsafe snapshot paths before mutations."""
    check_directory_path(SNAPSHOT_DIR)
    for unit in (SNAPSHOT_TIMER, SNAPSHOT_SERVICE):
        previous = snapshot_unit_file("/etc/systemd/system/" + unit)
        if previous is not None and not previous["content"].startswith(_MARKER + "\n"):
            raise ValueError(f"Refusing unmanaged Proxmox snapshot unit: {unit}")
    if os.path.lexists(SNAPSHOT) and (os.path.islink(SNAPSHOT) or not os.path.isfile(SNAPSHOT)):
        raise ValueError(f"Refusing unsafe Proxmox snapshot: {SNAPSHOT}")


def configure_proxmox_snapshot(service_gid: int) -> None:
    """Publish cached inventory through a root collector, never the web worker."""
    if type(service_gid) is not int or service_gid <= 0:
        raise ValueError("Proxmox snapshots require an unprivileged panel group")
    preflight_proxmox_snapshot()
    with directory_fd(SNAPSHOT_DIR, create=True) as descriptor:
        os.fchown(descriptor, 0, service_gid)
        os.fchmod(descriptor, 0o2750)
    service = f"""{_MARKER}
[Unit]
Description=Proxmox inventory snapshot for the Basaltwater panel
After=pve-cluster.service pvestatd.service

[Service]
Type=oneshot
User=root
Group={service_gid}
UMask=0027
ExecStart=/usr/bin/python3 -I {_SNAPSHOT_SCRIPT} --export
TimeoutStartSec=30
TimeoutStopSec=5
KillMode=control-group
LimitFSIZE=3M
LimitCORE=0
NoNewPrivileges=true
PrivateTmp=true
PrivateDevices=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths={SNAPSHOT_DIR}
ProtectControlGroups=true
ProtectKernelModules=true
ProtectKernelTunables=true
RestrictSUIDSGID=true
RestrictAddressFamilies=AF_UNIX
StandardOutput=null
StandardError=journal
"""
    timer = f"""{_MARKER}
[Unit]
Description=Refresh Proxmox inventory for the Basaltwater panel

[Timer]
OnBootSec=30s
OnUnitInactiveSec=60s
AccuracySec=5s

[Install]
WantedBy=timers.target
"""
    replace_units({SNAPSHOT_SERVICE: service, SNAPSHOT_TIMER: timer}, activate=(SNAPSHOT_TIMER,))
    run(["systemctl", "start", SNAPSHOT_SERVICE], check=True, timeout=40)


def remove_proxmox_snapshot() -> None:
    if not proxmox_snapshot_present():
        return
    preflight_proxmox_snapshot()
    if any(snapshot_unit_file("/etc/systemd/system/" + unit) is not None for unit in (SNAPSHOT_TIMER, SNAPSHOT_SERVICE)):
        remove_units((SNAPSHOT_TIMER, SNAPSHOT_SERVICE))
    try:
        os.unlink(SNAPSHOT)
    except FileNotFoundError:
        pass
    try:
        os.rmdir(SNAPSHOT_DIR)
    except FileNotFoundError:
        pass


def preflight_proxmox_panel_nginx() -> dict | None:
    """Refuse an unsafe or operator-owned drop-in before changing the panel."""
    check_directory_path(os.path.dirname(PROXMOX_PANEL_NGINX_DROPIN))
    previous = snapshot_unit_file(PROXMOX_PANEL_NGINX_DROPIN)
    if previous is not None and not previous["content"].startswith(_MARKER + "\n"):
        raise RuntimeError(f"Refusing unmanaged Nginx drop-in: {PROXMOX_PANEL_NGINX_DROPIN}")
    return previous


def _restart_nginx() -> None:
    run("systemctl daemon-reload", check=True)
    run("systemctl restart nginx", check=True)
    if not is_service_active("nginx"):
        raise RuntimeError("Nginx did not start with the Proxmox panel isolation")


def _change_dropin(content: str | None) -> None:
    previous = preflight_proxmox_panel_nginx()
    if previous is None and content is None:
        return
    if previous is not None and previous["content"] == content:
        # A prior setup may have stopped after publishing this file. Disk
        # contents do not prove the running master has the mount restrictions.
        _restart_nginx()
        return
    with directory_fd(os.path.dirname(PROXMOX_PANEL_NGINX_DROPIN), create=True):
        if content is None:
            os.unlink(PROXMOX_PANEL_NGINX_DROPIN)
        else:
            write_text_atomic(PROXMOX_PANEL_NGINX_DROPIN, content, mode=0o644)
    try:
        # Reloading cannot apply systemd mount namespace restrictions.
        _restart_nginx()
    except Exception:
        if previous is None:
            os.unlink(PROXMOX_PANEL_NGINX_DROPIN)
        else:
            write_text_atomic(PROXMOX_PANEL_NGINX_DROPIN, **previous)
        _restart_nginx()
        raise


def configure_proxmox_panel_nginx() -> None:
    """Hide pmxcfs and its backing database from Nginx's www-data workers."""
    _change_dropin(_CONTENT)


def remove_proxmox_panel_nginx() -> None:
    """Remove the owned isolation after removing the panel listener."""
    _change_dropin(None)
