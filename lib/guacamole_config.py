"""Fixed, non-secret configuration for the optional Guacamole desktop."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lib.validators import validate_username
from lib.machine_state import can_manage_system_services

if TYPE_CHECKING:
    from lib.config import SetupConfig

VERSION = "1.6.0"
BUILD_VERSION = "1.6.0-bw1"
PACKAGE_VERSION = "1.6.0-1+bw1"
PACKAGE_NAME = "basaltwater-guacamole"
BUILD_REPOSITORY = "bluehexagons/basaltwater-guacamole"
# SHA-256 of reviewed release assets; never trust a remotely supplied checksum.
BINARY_HASHES = {
    "amd64": "ec32c33d98a8430b133efcb5fd21fa475455f74c484bda1686e5d4eaa6d67810",
    "arm64": "7ea7332da16ba4448246dff9cdf46fc24f7582fdaf7d0331a9461dc5bd2fc5d2",
}
RELEASE_ROOT = "/opt/basaltwater-guacamole"
RELEASE = f"{RELEASE_ROOT}/{BUILD_VERSION}"
CONFIG = "/etc/basaltwater/guacamole.json"
HTTP_SOCKET = "/run/basaltwater-guacamole/http.sock"
RDP_SOCKET = "/run/basaltwater-guacamole-rdp/rdp.sock"
SERVICE = "basaltwater-guacamole"
RDP_SERVICE = "basaltwater-guacamole-rdp"
UNIT_MARKER = "# Managed by basaltwater Guacamole"


def validate_guacamole_settings(config: SetupConfig, *, complete: bool = True) -> None:
    """Validate a complete plan, allowing partial configuration before patch merge."""
    if config.guacamole is not None and type(config.guacamole) is not bool:
        raise ValueError("guacamole must be boolean")
    if not config.guacamole:
        return
    if (config.username == "root" or not validate_username(config.username)
            or config.system_type in {"server_proxmox", "server_wsl", "agent_cachyos", "custom_steps"}):
        raise ValueError("--guacamole requires a non-root Debian workstation/server account")
    if config.harden_user:
        raise ValueError("--guacamole requires Unix password login and cannot be combined with --harden-user")
    if complete and (config.web_panel_port is None or not config.enable_ssl or config.disable_web_panel):
        raise ValueError("--guacamole requires --web-panel and --ssl")
    if complete and config.enable_rdp and config.rdp_bind_address not in {"0.0.0.0", "127.0.0.1"}:
        raise ValueError("--guacamole requires XRDP to accept local IPv4 connections")
    if complete and config.machine_type != "auto" and not can_manage_system_services(config.machine_type):
        raise ValueError("--guacamole requires a machine that can manage system services")
