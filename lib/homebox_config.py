"""Validated configuration for the native HomeBox service."""

from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING

from lib.validation import validate_filesystem_path, validate_network_ip_or_cidr, validate_ssl_email
from lib.validators import validate_host

if TYPE_CHECKING:
    from lib.config import SetupConfig

DEFAULT_VERSION = "v0.26.2"
DEFAULT_PORT = 7745
DEFAULT_DATA = "/var/lib/homebox"


def parse_homebox_spec(spec: str) -> tuple[str, int]:
    """Parse a DNS HTTPS frontend or an explicit loopback :PORT."""
    if not isinstance(spec, str) or not spec or spec != spec.strip():
        raise ValueError("HomeBox requires DOMAIN[:PORT] or :PORT")
    domain, sep, raw_port = spec.partition(":")
    if sep and not re.fullmatch(r"[0-9]{1,5}", raw_port):
        raise ValueError("HomeBox port must be an integer")
    port = int(raw_port) if sep else 443
    if not 1024 <= port <= 65535 and port != 443:
        raise ValueError("HomeBox port must be 443 or between 1024 and 65535")
    if domain:
        if not validate_host(domain) or not re.fullmatch(r"[A-Za-z0-9.-]+", domain):
            raise ValueError("Invalid HomeBox DNS hostname")
        if "." not in domain or domain.replace(".", "").isdigit():
            raise ValueError("HomeBox HTTPS requires a DNS hostname")
    elif not sep or port == 443:
        raise ValueError("Loopback HomeBox requires :PORT with an unprivileged port")
    return domain.lower().rstrip("."), port


def validate_homebox_version(version: str) -> str:
    if not isinstance(version, str) or not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", version):
        raise ValueError("HomeBox version must be a stable tag such as v0.26.2")
    if tuple(map(int, version[1:].split("."))) < (0, 26, 2):
        raise ValueError("HomeBox versions before v0.26.2 are not supported")
    return version


def validate_homebox_path(path: str) -> str:
    validate_filesystem_path(path)
    if not re.fullmatch(r"/(?:srv|mnt|data|var/lib)/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*", path):
        raise ValueError("HomeBox data must be below /srv, /mnt, /data, or /var/lib with simple path components")
    if os.path.normpath(path) != path or any(p in {".", ".."} for p in path.split("/")):
        raise ValueError("HomeBox data path must be normalized")
    return path


def paths_overlap(left: str, right: str) -> bool:
    left, right = os.path.normpath(left), os.path.normpath(right)
    return left == right or left.startswith(right + "/") or right.startswith(left + "/")


def homebox_settings(config: SetupConfig) -> dict:
    """Return non-secret desired settings after validation."""
    domain, public_port = parse_homebox_spec(config.homebox[0])
    return {
        "domain": domain,
        "public_port": public_port,
        "port": (config.homebox_port or DEFAULT_PORT) if domain else public_port,
        "data_path": config.homebox[1] if len(config.homebox) == 2 else DEFAULT_DATA,
        "email": config.homebox_admin or "admin@homebox.local",
        "sources": config.effective_access_sources(),
    }


def validate_homebox_settings(config: SetupConfig) -> None:
    """Validate full setup intent before controller or target mutations."""
    if config.homebox is None:
        if config.homebox_version or config.homebox_admin or config.homebox_port:
            raise ValueError("HomeBox options require --homebox (or a saved HomeBox setup)")
        return
    if config.system_type not in {"server_lite", "server_web", "server_dev", "agent_vm", "control_plane"}:
        raise ValueError("HomeBox requires a server setup profile")
    if config.machine_type == "oci":
        raise ValueError("HomeBox requires persistent systemd services")
    if config.custom_steps:
        raise ValueError("HomeBox requires the complete server setup pipeline, not --steps")
    if config.homebox == []:
        return
    if not isinstance(config.homebox, list) or len(config.homebox) not in {1, 2} or not all(isinstance(v, str) for v in config.homebox):
        raise ValueError("--homebox requires DOMAIN[:PORT] and optional DATA_PATH")
    desired = homebox_settings(config)
    data = validate_homebox_path(desired["data_path"])
    if any(len(mount) > 1 and mount[1] == data for mount in config.storage_mounts or []):
        raise ValueError("Use a HomeBox data subdirectory beneath the declared storage mount")
    if config.homebox_version:
        validate_homebox_version(config.homebox_version)
    validate_ssl_email(desired["email"])
    if config.enable_cloudflare:
        raise ValueError("HomeBox Cloudflare ingress is not supported; use --ssl or loopback")
    if desired["domain"] and not config.enable_ssl:
        raise ValueError("Hostname-based HomeBox requires --ssl")
    if config.homebox_port is not None and (type(config.homebox_port) is not int or not 1024 <= config.homebox_port <= 65535):
        raise ValueError("--homebox-port must be between 1024 and 65535")
    if not desired["domain"] and config.homebox_port is not None:
        raise ValueError("Use :PORT to choose a loopback HomeBox port")
    port = desired["port"]
    reserved = {80, 443, 8443}
    for enabled, value in ((config.web_interfaces, config.web_interface_port),
                           (config.device_pairing_providers, config.device_pairing_port),
                           (config.web_panel_port is not None, config.web_panel_port)):
        if enabled:
            reserved.add(value)
    for spec, default in ((config.antistatic_server, 8080), (config.antistatic_db, 8081)):
        if spec:
            reserved.add(int(spec.rsplit(":", 1)[1]) if ":" in spec else default)
    if config.gogs:
        from web.gogs_steps import parse_gogs_spec, _gogs_backend_port
        _, gogs_port = parse_gogs_spec(config.gogs[0], strict=True)
        reserved.update((gogs_port, _gogs_backend_port(config, gogs_port)))
    if port in reserved or (desired["domain"] and port == desired["public_port"]):
        raise ValueError("HomeBox backend port conflicts with a managed service; choose --homebox-port")
    if desired["domain"] and desired["public_port"] in reserved - {443}:
        raise ValueError("HomeBox HTTPS port conflicts with another managed service")
    for source in desired["sources"]:
        validate_network_ip_or_cidr(source, "HomeBox access source")
    others = ["/var/lib/basaltwater", "/var/lib/homebox-backups", "/var/lib/gogs"]
    if config.gogs and len(config.gogs) == 2:
        others.append(config.gogs[1])
    others.extend(share[2] for share in config.samba_shares or [] if len(share) >= 3)
    others.extend(mount[0] for mount in config.smb_mounts or [] if mount)
    if config.samba_metadata_cache:
        others.append(config.samba_metadata_cache)
    if config.enable_syncthing:
        others.append(config.syncthing_root or "/srv/syncthing")
    if any(paths_overlap(data, other) for other in others):
        raise ValueError("HomeBox data overlaps another service, backup root, or file share")
    if desired["domain"]:
        specs = [config.gogs[0]] if config.gogs else []
        specs += [v for v in (config.antistatic_server, config.antistatic_db) if v]
        specs += [part for spec, _ in config.deploy_specs or [] for part in spec.split(",")]
        if any(spec.split(":", 1)[0].lower().rstrip(".") == desired["domain"] for spec in specs):
            raise ValueError("HomeBox hostname is already assigned to another application")
