"""Isolate Nginx from Proxmox configuration when serving the optional panel."""

from __future__ import annotations

import os

from lib.atomic_io import write_text_atomic
from lib.remote_utils import is_service_active, run
from lib.setup_directories import check_directory_path, directory_fd
from lib.unit_transaction import snapshot_unit_file


PROXMOX_PANEL_NGINX_DROPIN = "/etc/systemd/system/nginx.service.d/50-basaltwater-web-panel.conf"
PROXMOX_PANEL_INACCESSIBLE_PATHS = "InaccessiblePaths=-/etc/pve -/var/lib/pve-cluster"
_MARKER = "# Managed by basaltwater Proxmox web panel"
_CONTENT = f"{_MARKER}\n[Service]\n{PROXMOX_PANEL_INACCESSIBLE_PATHS}\n"


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
    if (previous is None and content is None
            or previous is not None and previous["content"] == content):
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
