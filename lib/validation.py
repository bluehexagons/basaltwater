"""Enhanced validation framework extending existing patterns."""

from __future__ import annotations

import ipaddress
import os
import pwd
import re
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional, cast
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from lib.plugin_registry import resolve_validator

if TYPE_CHECKING:
    from lib.config import SetupConfig


def _resolve_plugin_validator(name: str) -> Callable[..., object]:
    """Resolve a plugin-owned validator or parser callable."""

    return resolve_validator(name)


def validate_no_control_characters(value: str, name: str) -> None:
    """Reject values that could add lines to generated configuration files."""

    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(f"{name} must not contain control characters")


def validate_systemd_exec_command(
    value: str,
    name: str = "systemd ExecStart command",
) -> str:
    """Reject command text that can bypass a generated unit's isolation.

    systemd treats ``+`` and ``!`` before an executable as privilege-control
    prefixes. They override ``User=``, ``Group=``, and some sandbox settings,
    so repository-controlled commands must never be allowed to use them.
    """

    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    validate_no_control_characters(value, name)
    prefix_match = re.match(r"^\s*([@:+!\-]+)", value)
    if prefix_match and ({"+", "!"} & set(prefix_match.group(1))):
        raise ValueError(
            f"{name} must not use systemd privilege-control prefixes '+' or '!'"
        )
    return value


def validate_filesystem_path(path: str, must_exist: bool = False, check_writable: bool = False) -> None:
    """Validate filesystem path with extended checks.
    
    Args:
        path: Path to validate
        must_exist: If True, path must exist
        check_writable: If True, path must be writable
        
    Raises:
        ValueError: If validation fails
    """
    if not path:
        raise ValueError("Path must be a non-empty string")

    validate_no_control_characters(path, "Path")
    
    # Basic path format validation
    try:
        Path(path)
    except (ValueError, TypeError) as e:
        raise ValueError(f"Invalid path format: {path}") from e
    
    if must_exist and not os.path.exists(path):
        raise ValueError(f"Path does not exist: {path}")
    
    if check_writable:
        if os.path.exists(path):
            if not os.access(path, os.W_OK):
                raise ValueError(f"Path is not writable: {path}")
        else:
            # Check parent directory for writability
            parent = os.path.dirname(path)
            if not os.path.exists(parent):
                raise ValueError(f"Parent directory does not exist: {parent}")
            if not os.access(parent, os.W_OK):
                raise ValueError(f"Parent directory is not writable: {parent}")


_CHANNEL_VERSION_PATTERN = re.compile(
    r"^v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:[-+][0-9A-Za-z.-]+)?$"
)
_CHANNEL_BRANCH_PATTERN = re.compile(r"^[A-Za-z0-9_](?:[A-Za-z0-9._/-]*[A-Za-z0-9_])?$")
_CHANNEL_COMMIT_PATTERN = re.compile(r"^[0-9A-Fa-f]{4,64}$")


def _validate_syncthing_root(storage_root: object) -> None:
    if storage_root is None:
        return
    if not isinstance(storage_root, str):
        raise ValueError("--syncthing-root must be a filesystem path")
    validate_filesystem_path(storage_root, must_exist=False)
    if not os.path.isabs(storage_root) or os.path.normpath(storage_root) != storage_root:
        raise ValueError("--syncthing-root must be an absolute, normalized path")
    if not _VM_MOUNT_PATH_PATTERN.fullmatch(storage_root):
        raise ValueError(
            "--syncthing-root components may contain only letters, numbers, "
            "'.', '_', or '-'"
        )
    allowed = storage_root == "/data" or storage_root.startswith(
        ("/data/", "/mnt/", "/srv/", "/var/lib/")
    )
    if not allowed:
        raise ValueError(
            "--syncthing-root must be /data or below /data, /mnt, /srv, or /var/lib"
        )
    state_root = "/var/lib/basaltwater/syncthing"
    if (
        storage_root == state_root
        or storage_root.startswith(f"{state_root}{os.sep}")
        or state_root.startswith(f"{storage_root}{os.sep}")
    ):
        raise ValueError(
            "--syncthing-root must not overlap Syncthing's managed state directory"
        )


def validate_syncthing_settings(config: Any) -> None:
    """Validate managed Syncthing service and administrator boundaries."""

    enabled = bool(getattr(config, "enable_syncthing", False))
    disabled = bool(getattr(config, "disable_syncthing", False))
    admin = getattr(config, "syncthing_admin", None)
    storage_root = getattr(config, "syncthing_root", None)
    _validate_syncthing_root(storage_root)

    if disabled:
        if enabled or admin:
            raise ValueError(
                "--no-syncthing cannot be combined with Syncthing setup options"
            )
        return
    if not enabled:
        if admin:
            raise ValueError("--syncthing-admin requires --syncthing")
        return

    username = str(getattr(config, "username", "")).strip()
    if username == "root":
        raise ValueError("--syncthing requires a non-root setup user")
    if getattr(config, "system_type", None) == "server_proxmox":
        raise ValueError("--syncthing is not supported on a Proxmox host")
    if getattr(config, "machine_type", None) == "oci":
        raise ValueError("--syncthing is not supported for OCI setup targets")
    from lib.validators import validate_username

    if not isinstance(admin, str) or not validate_username(admin):
        raise ValueError("--syncthing-admin must be a valid username")


def validate_channel(channel: str) -> str:
    """Validate and return an basaltwater release channel selector."""

    if not isinstance(channel, str) or not channel:
        raise ValueError("Channel must be a non-empty string")
    validate_no_control_characters(channel, "Channel")

    if channel in {"stable", "dev"}:
        return channel

    if channel.startswith("v"):
        if not _CHANNEL_VERSION_PATTERN.fullmatch(channel):
            raise ValueError(
                "Version channels must use a semantic version such as v1.2.3"
            )
        return channel

    if channel.startswith("branch-"):
        branch = channel.removeprefix("branch-")
        if (
            not _CHANNEL_BRANCH_PATTERN.fullmatch(branch)
            or branch.startswith(".")
            or branch.endswith(".")
            or ".." in branch
            or "//" in branch
            or "@{" in branch
        ):
            raise ValueError(f"Invalid branch channel: {channel}")
        return channel

    if channel.startswith("commit-"):
        commit = channel.removeprefix("commit-")
        if not _CHANNEL_COMMIT_PATTERN.fullmatch(commit):
            raise ValueError(
                "Commit channels must contain a hexadecimal commit hash"
            )
        return channel

    raise ValueError(
        "Unknown channel; use stable, dev, v<version>, branch-<branch>, "
        "or commit-<hash>"
    )


def validate_database_path(db_path: str) -> None:
    """Validate database path for parity file storage.
    
    The database path is a directory (e.g. .pardatabase) used to store parity
    files.  It may live inside the protected directory as a hidden subdirectory,
    which is the common usage pattern.
    
    Args:
        db_path: Database directory path to validate
        
    Raises:
        ValueError: If validation fails
    """
    # Don't require existence - database may not exist on first run
    validate_filesystem_path(db_path, must_exist=False)


def validate_service_name_uniqueness(service_name: str, existing_services: list[str]) -> bool:
    """Validate service name uniqueness and format.
    
    Args:
        service_name: Service name to validate
        existing_services: List of existing service names
        
    Returns:
        True if validation passes
        
    Raises:
        ValueError: If validation fails
    """
    if not service_name:
        raise ValueError("Service name must be a non-empty string")
    
    # Systemd unit names allow letters, digits, hyphens, underscores, and dots.
    # Max 255 chars (well within systemd limits).
    pattern = r'^[a-z_][a-z0-9_.-]{0,254}$'
    if not re.match(pattern, service_name):
        raise ValueError(f"Service name '{service_name}' must follow pattern: {pattern}")
    
    if service_name in existing_services:
        raise ValueError(f"Service name '{service_name}' already exists")
    
    # Check for systemd reserved names
    systemd_reserved = ['system', 'user', 'service', 'target', 'slice', 'scope']
    if service_name in systemd_reserved:
        raise ValueError(f"Service name '{service_name}' is reserved by systemd")
    
    return True


def validate_redundancy_percentage(redundancy: str) -> int:
    """Validate and convert redundancy percentage to integer.
    
    Args:
        redundancy: Redundancy percentage as string
        
    Returns:
        int: Validated redundancy percentage
        
    Raises:
        ValueError: If validation fails
    """
    if not redundancy:
        raise ValueError("Redundancy must be a non-empty string")
    
    # Remove % symbol if present
    redundancy_clean = redundancy.strip().rstrip('%')
    
    try:
        redundancy_int = int(redundancy_clean)
    except ValueError as e:
        raise ValueError(f"Redundancy must be a valid integer: {redundancy}") from e
    
    if not 0 <= redundancy_int <= 100:
        raise ValueError(f"Redundancy percentage must be between 0 and 100: {redundancy_int}")
    
    return redundancy_int


def validate_directory_empty(directory: str) -> None:
    """Validate that directory is empty.
    
    Args:
        directory: Directory path to check
        
    Raises:
        ValueError: If directory is not empty or doesn't exist
    """
    validate_filesystem_path(directory, must_exist=True)
    
    if not os.path.isdir(directory):
        raise ValueError(f"Path is not a directory: {directory}")
    
    try:
        entries = os.listdir(directory)
        # Skip hidden files and directories
        visible_entries = [e for e in entries if not e.startswith('.')]
        if visible_entries:
            raise ValueError(f"Directory is not empty: {directory} (contains: {', '.join(visible_entries[:5])})")
    except OSError as e:
        raise ValueError(f"Cannot read directory contents: {directory}") from e


def validate_workspace_dir(path: str) -> None:
    """Validate a workspace directory path for CLI entry points."""

    if not path:
        raise ValueError("Workspace path must be a non-empty string")

    expanded_path = os.path.abspath(os.path.expanduser(path))
    validate_filesystem_path(expanded_path, must_exist=False)

    if os.path.exists(expanded_path):
        if not os.path.isdir(expanded_path):
            raise ValueError(f"Workspace path is not a directory: {expanded_path}")
        if not os.access(expanded_path, os.W_OK):
            raise ValueError(f"Workspace path is not writable: {expanded_path}")
        return

    existing_parent = expanded_path
    while not os.path.exists(existing_parent):
        parent = os.path.dirname(existing_parent)
        if parent == existing_parent:
            break
        existing_parent = parent

    if not os.path.isdir(existing_parent):
        raise ValueError(f"Workspace parent is not a directory: {existing_parent}")
    if not os.access(existing_parent, os.W_OK):
        raise ValueError(f"Workspace parent is not writable: {existing_parent}")


def validate_network_endpoint(endpoint: str) -> None:
    """Validate network endpoint (host:port or IP:port).
    
    Args:
        endpoint: Network endpoint to validate
        
    Raises:
        ValueError: If validation fails
    """
    if not endpoint:
        raise ValueError("Endpoint must be a non-empty string")
    
    parts = endpoint.split(':')
    if len(parts) != 2:
        raise ValueError(f"Endpoint must be in format host:port: {endpoint}")
    
    host, port = parts
    
    # Validate host using existing validate_host function
    from lib.validators import validate_host
    if not validate_host(host):
        raise ValueError(f"Invalid host in endpoint: {host}")
    
    try:
        port_int = int(port)
        if not 1 <= port_int <= 65535:
            raise ValueError(f"Port must be between 1 and 65535: {port_int}")
    except ValueError as e:
        raise ValueError(f"Invalid port in endpoint: {port}") from e


def validate_deploy_targets(targets: Optional[list[str]]) -> None:
    """Validate deploy target hostnames before setup or patch execution."""

    if not targets:
        return

    from lib.validators import validate_host

    for target in targets:
        if not target or not target.strip():
            raise ValueError("Deploy target must be a non-empty hostname or IP")
        if not validate_host(target):
            raise ValueError(f"Invalid deploy target host: {target}")


def validate_gogs_settings(config: "SetupConfig") -> None:
    """Validate Gogs setup arguments before setup or patch execution."""
    gogs = config.gogs
    sources = config.gogs_sources or []
    if not gogs:
        if sources:
            raise ValueError("--gogs-source requires --gogs")
        return

    if len(gogs) not in (1, 2):
        raise ValueError("--gogs requires DOMAIN[:PORT] and optional DATA_PATH")

    spec = str(gogs[0]).strip()
    if not spec:
        raise ValueError("Gogs target spec must be a non-empty string")

    from web.gogs_steps import (
        DEFAULT_GOGS_DATA_PATH,
        effective_gogs_ipv4_sources,
        parse_gogs_spec,
    )
    from lib.validators import validate_host

    domain, port = parse_gogs_spec(spec, strict=True)
    if port == 80 and (
        domain
        or config.enable_ssl
        or config.enable_cloudflare
        or getattr(config, "include_web_server", False)
    ):
        raise ValueError(
            "Gogs port 80 is reserved for nginx HTTP ingress and TLS certificate "
            "validation; choose another --gogs port"
        )
    if domain and not validate_host(domain):
        raise ValueError(f"Invalid Gogs domain: {domain}")
    if domain:
        if sources:
            raise ValueError("--gogs-source is valid only for hostless Gogs")
        if not (config.enable_ssl or config.enable_cloudflare):
            raise ValueError(
                "Hostname-based Gogs requires --ssl or --cloudflare so credentials "
                "are not sent over plaintext HTTP"
            )
        if config.enable_ssl and config.effective_access_sources():
            raise ValueError(
                "Hostname-based Gogs with --ssl requires public port 80 for "
                "Let's Encrypt HTTP-01 validation; remove source restrictions "
                "or use --cloudflare without --ssl"
            )
        try:
            ipaddress.ip_address(domain)
        except ValueError:
            pass
        else:
            raise ValueError(
                "Hostname-based Gogs requires a DNS hostname; use --gogs :PORT "
                "for an IP-address deployment"
            )
    elif config.enable_cloudflare:
        raise ValueError("Hostless Gogs cannot use --cloudflare; supply a DNS hostname")

    normalized_sources: set[str] = set()
    for source in sources:
        normalized = validate_network_ip_or_cidr(source, "Gogs source")
        network = ipaddress.ip_network(
            normalized if "/" in normalized else f"{normalized}/32",
            strict=False,
        )
        if network.version != 4:
            raise ValueError("--gogs-source currently supports only private IPv4 sources")
        if network.is_global:
            raise ValueError(
                "--gogs-source must be private or otherwise non-global"
            )
        canonical_source = str(network)
        if canonical_source in normalized_sources:
            raise ValueError(f"Duplicate --gogs-source: {canonical_source}")
        normalized_sources.add(canonical_source)

    if not domain:
        for source in effective_gogs_ipv4_sources(config):
            network = ipaddress.ip_network(source, strict=False)
            if network.is_global:
                raise ValueError(
                    "Hostless Gogs access sources must be private or otherwise "
                    f"non-global: {source}"
                )

    data_path = DEFAULT_GOGS_DATA_PATH
    if len(gogs) == 2:
        data_path = str(gogs[1]).strip()
        if not os.path.isabs(data_path):
            raise ValueError(f"Gogs data path must be absolute: {data_path}")
        validate_filesystem_path(data_path, must_exist=False)

    normalized_data_path = os.path.normpath(data_path)
    if config.samba_metadata_cache:
        normalized_cache_path = os.path.normpath(config.samba_metadata_cache)
        common_path = os.path.commonpath(
            (normalized_data_path, normalized_cache_path)
        )
        if common_path in {normalized_data_path, normalized_cache_path}:
            raise ValueError(
                "Samba metadata cache must not overlap the live Gogs data "
                f"path {data_path}"
            )
    for share_spec in config.samba_shares or []:
        if len(share_spec) < 3:
            continue
        share_name = share_spec[1]
        normalized_share_path = os.path.normpath(share_spec[2])
        common_path = os.path.commonpath(
            (normalized_data_path, normalized_share_path)
        )
        if common_path in {normalized_data_path, normalized_share_path}:
            raise ValueError(
                f"Samba share '{share_name}' at {share_spec[2]} must not overlap "
                f"the live Gogs data path {data_path}"
            )


def validate_antistatic_settings(config: "SetupConfig") -> None:
    """Validate antistatic-server deployment and optional admin settings."""
    if not config.antistatic_server:
        if config.antistatic_admin is not None:
            raise ValueError("--antistatic-admin requires --antistatic-server")
        return

    from game.antistatic_steps import parse_antistatic_spec
    from lib.validators import validate_host

    domain, _port = parse_antistatic_spec(config.antistatic_server, strict=True)
    if domain and not validate_host(domain):
        raise ValueError(f"Invalid Antistatic server domain: {domain}")

    if not config.antistatic_admin:
        return
    if not domain:
        raise ValueError("--antistatic-admin requires a hostname-based reverse proxy deployment")
    if not (config.enable_ssl or config.enable_cloudflare):
        raise ValueError("--antistatic-admin requires --ssl or --cloudflare")

    username = config.antistatic_admin
    if username != username.strip() or ":" in username or "," in username:
        raise ValueError(f"Invalid Antistatic admin username: {username}")
    if any(ord(char) < 32 or ord(char) == 127 for char in username):
        raise ValueError("Antistatic admin username must not contain control characters")

    password = None
    for credential in config.share_credentials or []:
        if len(credential) == 2 and credential[0] == username:
            password = credential[1]
            break
    if password is None:
        raise ValueError(f"Missing credential for Antistatic admin: {username}")
    if any(ord(char) < 32 or ord(char) == 127 for char in password):
        raise ValueError("Antistatic admin password must not contain control characters")


def validate_deploy_specs(deploy_specs: Optional[list[list[str]]]) -> None:
    """Validate deploy specs before setup or patch execution."""

    if not deploy_specs:
        return

    from lib.deploy_utils import parse_deploy_spec
    from lib.validators import validate_host

    for deploy_spec_entry in deploy_specs:
        if len(deploy_spec_entry) != 2:
            raise ValueError("--deploy requires DOMAIN_OR_PATH and GIT_URL")

        deploy_specs_str, git_url = deploy_spec_entry
        if not deploy_specs_str or not str(deploy_specs_str).strip():
            raise ValueError("Deploy target spec must be a non-empty string")
        if not git_url or not str(git_url).strip():
            raise ValueError("Deploy git URL must be a non-empty string")

        for raw_deploy_spec in str(deploy_specs_str).split(","):
            deploy_spec = raw_deploy_spec.strip()
            if not deploy_spec:
                raise ValueError("Deploy target spec list must not contain empty entries")

            if deploy_spec.startswith("/"):
                validate_filesystem_path(deploy_spec, must_exist=False)
                continue

            domain, _path = parse_deploy_spec(deploy_spec)
            if not domain or not validate_host(domain):
                raise ValueError(f"Invalid deploy domain: {domain or deploy_spec}")


def validate_sync_specs(sync_specs: Optional[list[list[str]]]) -> None:
    """Validate sync specs before setup or patch execution."""

    if not sync_specs:
        return

    parse_sync_spec = cast(
        Callable[[list[str]], dict[str, Any]],
        _resolve_plugin_validator("parse_sync_spec"),
    )

    for sync_spec in sync_specs:
        sync_config = parse_sync_spec(sync_spec)
        validate_filesystem_path(sync_config["source"], must_exist=False)
        validate_filesystem_path(sync_config["destination"], must_exist=False)


def validate_backup_specs(backup_specs: Optional[list[list[str]]]) -> None:
    """Validate semantic backup specs using the existing rsync spec parser."""

    validate_sync_specs(backup_specs)


def validate_web_interface_settings(config: Any) -> None:
    """Validate explicit headless web-interface exposure settings."""

    validate_access_source_settings(config)
    validate_web_port_settings(config)

    interfaces = getattr(config, "web_interfaces", None) or []
    host = getattr(config, "web_interface_host", None)
    service_sources = getattr(config, "web_interface_sources", None) or []
    effective_sources = (
        config.effective_web_interface_sources()
        if hasattr(config, "effective_web_interface_sources")
        else service_sources
    )
    port = getattr(config, "web_interface_port", 3773)
    pairing_providers = getattr(config, "device_pairing_providers", None) or []
    pairing_port = getattr(config, "device_pairing_port", 3774)
    pairing_auth_file = getattr(config, "device_pairing_auth_file", None)
    pairing_auth_username = getattr(config, "device_pairing_auth_username", None)
    pairing_auth_password = getattr(config, "device_pairing_auth_password", None)
    pairing_payload = bool(getattr(config, "device_pairing_payload", False))
    if not interfaces:
        if host is not None or service_sources:
            raise ValueError(
                "--web-interface-host and --web-interface-source require --web-interface"
            )
        if port != 3773:
            raise ValueError(
                "--web-interface-port requires --web-interface"
            )
        if (
            pairing_providers
            or pairing_auth_file
            or pairing_auth_username
            or pairing_auth_password
            or pairing_payload
        ):
            raise ValueError("--device-pairing requires --web-interface")
        if pairing_port != 3774:
            raise ValueError("--device-pairing-port requires --device-pairing")
        return

    from lib.config import WEB_INTERFACES

    for interface in interfaces:
        if interface not in WEB_INTERFACES:
            raise ValueError(f"Unsupported web interface: {interface}")
    from lib.config import DEVICE_PAIRING_PROVIDERS

    for provider in pairing_providers:
        if provider not in DEVICE_PAIRING_PROVIDERS:
            raise ValueError(f"Unsupported device pairing provider: {provider}")
        if provider not in interfaces:
            raise ValueError(
                f"--device-pairing {provider} requires --web-interface {provider}"
            )
    if not pairing_providers:
        if pairing_port != 3774:
            raise ValueError("--device-pairing-port requires --device-pairing")
        if pairing_auth_file or pairing_auth_username or pairing_auth_password or pairing_payload:
            raise ValueError("Device-pairing authentication requires --device-pairing")
    else:
        if not isinstance(pairing_port, int) or isinstance(pairing_port, bool):
            raise ValueError("--device-pairing-port must be an integer")
        if not 1 <= pairing_port <= 65535:
            raise ValueError("--device-pairing-port must be between 1 and 65535")
        if pairing_port == port:
            raise ValueError(
                "--device-pairing-port must differ from --web-interface-port"
            )
        if pairing_auth_file and (
            pairing_auth_username is not None or pairing_auth_password is not None
        ):
            raise ValueError(
                "--device-pairing-auth-file cannot be combined with interactive credentials"
            )
        if pairing_auth_username is not None or pairing_auth_password is not None:
            if not pairing_auth_username or not pairing_auth_password:
                raise ValueError(
                    "Device-pairing Basic Auth requires a non-empty username and password"
                )
        if bool(pairing_auth_username) != bool(pairing_auth_password):
            raise ValueError(
                "Device-pairing Basic Auth requires both a username and password"
            )
        if pairing_auth_username:
            from lib.validators import validate_username

            if not validate_username(pairing_auth_username):
                raise ValueError(
                    f"Invalid device-pairing username: {pairing_auth_username}"
                )
            validate_no_control_characters(
                pairing_auth_password, "Device-pairing password"
            )
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("--web-interface-port must be between 1 and 65535")
    if not isinstance(host, str) or not host:
        raise ValueError("--web-interface-host must be a non-empty IP address")
    normalized_host = host.lower()
    if normalized_host != "localhost":
        normalized_host = validate_network_ip_or_cidr(host, "web interface bind address")
    loopback = normalized_host in {"127.0.0.1", "::1", "localhost"}
    if loopback and service_sources:
        raise ValueError(
            "--web-interface-source requires a non-loopback web interface bind"
        )
    if not loopback and not effective_sources:
        raise ValueError(
            "A non-loopback web interface bind requires --access-source or "
            "--web-interface-source"
        )

    seen_sources: set[str] = set()
    for source in effective_sources:
        normalized = validate_network_ip_or_cidr(source, "web interface source")
        network = ipaddress.ip_network(
            normalized if "/" in normalized else f"{normalized}/32",
            strict=False,
        )
        if network.is_global:
            raise ValueError(
                "--web-interface-source must be a private or otherwise non-global network"
            )
        canonical = str(network)
        if canonical in seen_sources:
            raise ValueError(f"Duplicate --web-interface-source: {canonical}")
        seen_sources.add(canonical)


def validate_access_source_settings(config: Any) -> None:
    """Validate the generic inbound-service source policy."""

    sources = getattr(config, "access_sources", None)
    if sources is None:
        sources = []
    if not isinstance(sources, list):
        raise ValueError("--access-source must contain one or more IPs or CIDRs")
    if not isinstance(getattr(config, "lan_access", False), bool):
        raise ValueError("lan_access must be a boolean")

    normalized_sources: set[str] = set()
    for source in sources:
        if not isinstance(source, str):
            raise ValueError("--access-source requires an IP address or CIDR")
        normalized = validate_network_ip_or_cidr(source, "access source")
        if normalized in normalized_sources:
            raise ValueError(f"Duplicate --access-source: {normalized}")
        normalized_sources.add(normalized)


def validate_web_port_settings(config: Any) -> None:
    """Validate managed TCP web ports and their default policy."""

    ports = getattr(config, "web_ports", None)
    default_ports = getattr(config, "default_web_ports", True)
    if not isinstance(default_ports, bool):
        raise ValueError("default_web_ports must be a boolean")
    if ports is None:
        return
    if not isinstance(ports, list):
        raise ValueError("--web-port must be a repeatable integer option")
    for port in ports:
        if not isinstance(port, int) or isinstance(port, bool):
            raise ValueError("--web-port must be an integer")
        if not 1 <= port <= 65535:
            raise ValueError("--web-port must be between 1 and 65535")


def validate_web_panel_settings(config: Any) -> None:
    """Validate the optional authenticated web panel listener and secret."""

    port = getattr(config, "web_panel_port", None)
    password = getattr(config, "web_panel_auth_password", None)
    payload = bool(getattr(config, "web_panel_payload", False))
    notification_ingest = getattr(config, "web_panel_notification_ingest", None)
    disabled = bool(getattr(config, "disable_web_panel", False))
    if notification_ingest is not None and not isinstance(
        notification_ingest, bool
    ):
        raise ValueError("web_panel_notification_ingest must be a boolean")
    if port is None:
        if password is not None or payload:
            raise ValueError("--web-panel-password requires --web-panel")
        if notification_ingest is True:
            raise ValueError("--web-panel-notification-ingest requires --web-panel")
        return
    if disabled:
        raise ValueError("--web-panel cannot be combined with --no-web-panel")
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise ValueError("--web-panel PORT must be between 1 and 65535")
    if port == 8443:
        raise ValueError(
            "--web-panel PORT 8443 is reserved for the shared internal HTTPS host"
        )
    if getattr(config, "system_type", None) == "server_proxmox":
        raise ValueError("--web-panel is not supported on a Proxmox host")
    if notification_ingest is True and getattr(config, "enable_ssl", None) is not True:
        raise ValueError("--web-panel-notification-ingest requires --ssl")
    if password is not None:
        if not isinstance(password, str) or not password:
            raise ValueError("--web-panel-password must not be empty")
        validate_no_control_characters(password, "Web panel password")
    if getattr(config, "web_interfaces", None) and port == getattr(
        config, "web_interface_port", 3773
    ):
        raise ValueError(
            "--web-panel PORT must differ from --web-interface-port"
        )
    if getattr(config, "device_pairing_providers", None) and port == getattr(
        config, "device_pairing_port", 3774
    ):
        raise ValueError(
            "--web-panel PORT must differ from --device-pairing-port"
        )


def validate_scrub_specs(scrub_specs: Optional[list[list[str]]]) -> None:
    """Validate scrub specs before setup or patch execution."""

    if not scrub_specs:
        return

    parse_scrub_spec = cast(
        Callable[[list[str]], dict[str, Any]],
        _resolve_plugin_validator("parse_scrub_spec"),
    )

    for scrub_spec in scrub_specs:
        scrub_config = parse_scrub_spec(scrub_spec)
        validate_filesystem_path(scrub_config["directory"], must_exist=False)
        validate_database_path(scrub_config["database_path"])
        validate_redundancy_percentage(scrub_config["redundancy"])


def validate_smb_mount_specs(smb_mounts: Optional[list[list[str]]]) -> None:
    """Validate SMB mount specs before setup or patch execution."""

    if not smb_mounts:
        return

    from lib.validators import validate_host
    parse_smb_mount_spec = cast(
        Callable[[Optional[list[str]]], dict[str, Any]],
        _resolve_plugin_validator("parse_smb_mount_spec"),
    )

    mountpoints: set[str] = set()
    for mount_spec in smb_mounts:
        mount_config = parse_smb_mount_spec(mount_spec)
        mountpoint = mount_config["mountpoint"]
        validate_filesystem_path(mountpoint, must_exist=False)
        normalized_mountpoint = os.path.normpath(mountpoint)
        if normalized_mountpoint != mountpoint or not normalized_mountpoint.startswith("/mnt/"):
            raise ValueError(
                "SMB mountpoint must be a normalized directory below /mnt: "
                f"{mountpoint}"
            )
        if normalized_mountpoint in mountpoints:
            raise ValueError(f"Duplicate SMB mountpoint: {mountpoint}")
        mountpoints.add(normalized_mountpoint)
        if not validate_host(mount_config["ip"]):
            raise ValueError(f"Invalid SMB mount host: {mount_config['ip']}")
        if not mount_config["username"] or not mount_config["password"]:
            raise ValueError("SMB mount credentials must include a non-empty username and password")
        validate_no_control_characters(mount_config["username"], "SMB mount username")
        validate_no_control_characters(mount_config["password"], "SMB mount password")
        if (
            not mount_config["share"]
            or "/" in mount_config["share"]
            or "\\" in mount_config["share"]
            or any(char.isspace() for char in mount_config["share"])
        ):
            raise ValueError(f"Invalid share name (cannot contain /, \\, or spaces): {mount_config['share']}")
        validate_no_control_characters(mount_config["subdir"], "SMB mount subdirectory")
        if mount_config["subdir"] and not mount_config["subdir"].startswith("/"):
            raise ValueError(f"Subdirectory must start with /: {mount_config['subdir']}")


def validate_samba_share_specs(
    samba_shares: Optional[list[list[str]]],
    share_credentials: Optional[list[list[str]]] = None,
) -> None:
    """Validate Samba share specs before setup or patch execution."""

    if not samba_shares:
        return

    parse_share_credentials = cast(
        Callable[[Optional[list[list[str]]]], dict[str, str]],
        _resolve_plugin_validator("parse_share_credentials"),
    )
    parse_share_spec = cast(
        Callable[[Optional[list[str]], Optional[dict[str, str]]], dict[str, Any]],
        _resolve_plugin_validator("parse_share_spec"),
    )

    credentials = parse_share_credentials(share_credentials)

    share_names: set[str] = set()
    for share_spec in samba_shares:
        share_config = parse_share_spec(share_spec, credentials)
        share_name = share_config["share_name"]
        validate_samba_share_name(share_name)
        if share_name in share_names:
            raise ValueError(f"Duplicate Samba share name: {share_name}")
        share_names.add(share_name)

        if not share_config["paths"]:
            raise ValueError(f"No paths specified for share: {share_name}")

        if len(share_config["paths"]) != 1:
            raise ValueError("Samba shares support exactly one path; create one --share per directory")

        for path in cast(list[str], share_config["paths"]):
            if not os.path.isabs(path):
                raise ValueError(f"Share path must be absolute: {path}")
            if os.path.normpath(path) == "/":
                raise ValueError("Samba share path must not be the filesystem root")
            validate_filesystem_path(path, must_exist=False)

        if not share_config["users"]:
            raise ValueError(f"No users specified for share: {share_name}")

        from lib.validators import validate_username

        for user in cast(list[dict[str, str]], share_config["users"]):
            username = user["username"]
            password = user["password"]
            if not validate_username(username):
                raise ValueError(f"Invalid Samba username: {username}")
            if not password:
                raise ValueError(f"Samba password must not be empty for user: {username}")
            validate_no_control_characters(username, "Samba username")
            validate_no_control_characters(password, "Samba password")


def validate_samba_settings(config: "SetupConfig") -> None:
    """Validate Samba-specific network policy before remote execution."""

    sources = config.samba_sources or []
    if sources and not config.enable_samba:
        raise ValueError("--samba-source requires --samba")

    normalized_sources: set[str] = set()
    for source in sources:
        normalized = validate_network_ip_or_cidr(source, "Samba source")
        canonical_source = (
            str(ipaddress.ip_network(normalized, strict=False))
            if "/" in normalized
            else str(ipaddress.ip_address(normalized))
        )
        if canonical_source in normalized_sources:
            raise ValueError(f"Duplicate --samba-source: {canonical_source}")
        normalized_sources.add(canonical_source)

    cache_path = config.samba_metadata_cache
    if not cache_path:
        return
    if not config.enable_samba:
        raise ValueError("--samba-metadata-cache requires --samba")
    if not os.path.isabs(cache_path):
        raise ValueError(f"Samba metadata cache path must be absolute: {cache_path}")
    if os.path.normpath(cache_path) == "/":
        raise ValueError("Samba metadata cache path must not be the filesystem root")
    validate_filesystem_path(cache_path, must_exist=False)

    normalized_cache = os.path.normpath(cache_path)
    for share_spec in config.samba_shares or []:
        if len(share_spec) < 3:
            continue
        normalized_share = os.path.normpath(share_spec[2])
        common_path = os.path.commonpath((normalized_cache, normalized_share))
        if common_path in {normalized_cache, normalized_share}:
            raise ValueError(
                "Samba metadata cache must not overlap share path "
                f"{share_spec[2]}"
            )


def validate_samba_share_name(share_name: str) -> None:
    """Validate a Samba share name used in config sections and Unix groups."""

    validate_no_control_characters(share_name, "Samba share name")
    if not share_name or "/" in share_name or "\\" in share_name or " " in share_name:
        raise ValueError(
            f"Invalid Samba share name (cannot contain /, \\, or spaces): {share_name}"
        )
    if not re.fullmatch(r"[A-Za-z0-9._-]+", share_name):
        raise ValueError(
            "Invalid Samba share name (use only letters, numbers, dots, "
            f"underscores, and hyphens): {share_name}"
        )
    if len(f"smb_{share_name}_write") > 32:
        raise ValueError(f"Samba share name is too long for its Unix group: {share_name}")


def validate_samba_share_credentials(config: "SetupConfig") -> None:
    """Validate Samba share credentials through the plugin registry."""

    validator = cast(
        Callable[["SetupConfig"], None],
        _resolve_plugin_validator("validate_samba_share_credentials"),
    )
    validator(config)


def validate_ssl_email(email: Optional[str]) -> None:
    """Validate the optional SSL registration email before setup or patch execution."""

    if not email:
        return

    from lib.notifications import NotificationConfig, validate_notification_config

    try:
        validate_notification_config(NotificationConfig(type="mailbox", target=email))
    except ValueError as exc:
        raise ValueError(f"Invalid SSL email address: {email}") from exc


def validate_notification_level(notification_level: object) -> None:
    """Validate the optional system-wide outbound notification threshold."""

    from lib.notifications import normalize_notification_level

    normalize_notification_level(notification_level)


def validate_apt_packages(packages: Optional[list[str]]) -> None:
    """Validate custom apt package names before setup or patch execution."""

    if not packages:
        return

    for package in packages:
        validate_package_name(package, name="--apt-install")


def validate_timezone_name(timezone: Optional[str]) -> None:
    """Validate a timezone identifier before setup or patch execution."""

    if not timezone:
        return

    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Invalid timezone: {timezone}") from exc


def validate_positive_integer(value: str, name: str = "value") -> int:
    """Validate and convert string to positive integer.
    
    Args:
        value: String value to validate
        name: Name of the value for error messages
        
    Returns:
        int: Validated positive integer
        
    Raises:
        ValueError: If validation fails
    """
    if not value:
        raise ValueError(f"{name} must be a non-empty string")
    
    try:
        value_int = int(value.strip())
    except ValueError as e:
        raise ValueError(f"{name} must be a valid integer: {value}") from e
    
    if value_int <= 0:
        raise ValueError(f"{name} must be positive: {value_int}")

    return value_int


_MEMORY_PATTERN = re.compile(r'^\d+(?:\.\d+)?[KMGT]$', re.IGNORECASE)
_INTEGER_SIZE_PATTERN = re.compile(r'^\d+[KMGT]$', re.IGNORECASE)
_MEMORY_UNIT_TO_KIB = {
    "K": 1,
    "M": 1024,
    "G": 1024 * 1024,
    "T": 1024 * 1024 * 1024,
}
_PACKAGE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9+.-]*$")
_ENVIRONMENT_VARIABLE_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_NETWORK_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_NETWORK_PROVIDER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,31}$")
_NETWORK_INTERFACE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,14}$")
_PROXMOX_STORAGE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_VM_STORAGE_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,16}$")
_HOSTNAME_LABEL_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
_SAFE_REPO_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_GIT_AUTHOR_EMAIL_PATTERN = re.compile(r"^[^<>@\s]+@[^<>@\s]+$")


def validate_git_author_name(value: str) -> str:
    """Validate a Git author name copied into target-user configuration."""

    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("Git user.name must be a non-empty trimmed string")
    validate_no_control_characters(value, "Git user.name")
    if len(value) > 256 or "<" in value or ">" in value:
        raise ValueError("Git user.name is invalid")
    return value


def validate_git_author_email(value: str) -> str:
    """Validate a Git author email copied into target-user configuration."""

    if (
        not isinstance(value, str)
        or len(value) > 320
        or value != value.strip()
        or not _GIT_AUTHOR_EMAIL_PATTERN.fullmatch(value)
    ):
        raise ValueError("Git user.email must be a valid email address")
    validate_no_control_characters(value, "Git user.email")
    return value


def parse_memory_mib(value: str, name: str = "memory") -> int:
    """Validate a binary memory size and return an exact whole-MiB value."""

    if not value:
        raise ValueError(f"{name} must be a non-empty string")
    if not _MEMORY_PATTERN.fullmatch(value):
        raise ValueError(
            f"Invalid {name} value '{value}' (e.g. 2G, 1.5G, 512M, 1T)"
        )

    whole, separator, fraction = value[:-1].partition(".")
    scale = 10 ** len(fraction) if separator else 1
    amount_numerator = int(whole + fraction)
    memory_kib_numerator = (
        amount_numerator * _MEMORY_UNIT_TO_KIB[value[-1].upper()]
    )
    if memory_kib_numerator <= 0:
        raise ValueError(f"{name} must be positive")
    mib_denominator = scale * 1024
    if memory_kib_numerator % mib_denominator:
        raise ValueError(
            f"{name} value '{value}' must resolve to a whole MiB"
        )
    return memory_kib_numerator // mib_denominator


def validate_memory_string(value: str, name: str = "memory") -> None:
    """Validate a memory string like '2G', '1.5G', '512M', or '1024K'.

    Args:
        value: Memory string to validate
        name: Field name for error messages

    Raises:
        ValueError: If validation fails
    """
    parse_memory_mib(value, name)


def validate_proxmox_storage_name(
    value: str,
    name: str = "--image-storage",
) -> None:
    """Validate a Proxmox storage ID before it enters a remote command."""
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty storage ID")
    if not _PROXMOX_STORAGE_NAME_PATTERN.fullmatch(value):
        raise ValueError(
            f"Invalid {name} storage ID '{value}'; use letters, numbers, '.', '_' or '-'"
        )


def validate_vm_storage_name(value: str) -> str:
    """Validate a logical VM data-disk name that fits its Proxmox serial."""

    if not isinstance(value, str) or not _VM_STORAGE_NAME_PATTERN.fullmatch(value):
        raise ValueError(
            "VM data-disk names must start with a lowercase letter, contain only "
            "lowercase letters, numbers, or '-', and be at most 17 characters"
        )
    if value in {"root", "template"}:
        raise ValueError(f"'{value}' is reserved and cannot name a VM data disk")
    return value


def _memory_string_kib(value: str, name: str) -> int:
    """Validate an integer storage-size string and return its value in KiB."""
    if not value:
        raise ValueError(f"{name} must be a non-empty string")
    if not _INTEGER_SIZE_PATTERN.fullmatch(value):
        raise ValueError(
            f"Invalid {name} value '{value}' (e.g. 2G, 512M, 1T)"
        )
    amount = int(value[:-1])
    if amount <= 0:
        raise ValueError(f"{name} must be positive")
    return amount * _MEMORY_UNIT_TO_KIB[value[-1].upper()]


def validate_package_name(value: str, name: str = "package") -> str:
    """Validate a package name used in apt/system package lookups."""
    normalized_value = value.strip()
    if not normalized_value:
        raise ValueError(f"{name} must be a non-empty string")

    if not _PACKAGE_NAME_PATTERN.match(normalized_value):
        raise ValueError(f"Invalid {name} name: {value}")

    return normalized_value


_DEBIAN_CODENAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,31}$")


def validate_debian_codename(value: str) -> str:
    """Validate a Debian release codename used in APT source suites."""

    if not isinstance(value, str) or not _DEBIAN_CODENAME_PATTERN.fullmatch(value):
        raise ValueError(f"Invalid Debian release codename: {value}")
    return value


def validate_environment_variable_name(
    value: str,
    name: str = "environment variable",
) -> str:
    """Validate a shell environment variable name without normalizing it."""

    if not isinstance(value, str) or not _ENVIRONMENT_VARIABLE_NAME_PATTERN.fullmatch(value):
        raise ValueError(
            f"Invalid {name} name {value!r}; expected a letter or underscore "
            "followed by letters, digits, or underscores"
        )
    return value


def _repo_name_from_git_url(git_url: str) -> str:
    repo_name = git_url.rstrip('/').split('/')[-1]
    if ':' in repo_name:
        repo_name = repo_name.rsplit(':', 1)[-1]
    if repo_name.endswith('.git'):
        repo_name = repo_name[:-4]
    return repo_name


def validate_agent_repositories(repositories: Optional[list[str]]) -> None:
    """Validate HTTPS URLs supplied through --repo for agent VM workspaces."""
    if not repositories:
        return

    seen_repo_names: set[str] = set()
    for repository in repositories:
        if not isinstance(repository, str):
            raise ValueError("--repo requires a git URL")

        git_url = repository.strip()
        if not git_url:
            raise ValueError("--repo requires a non-empty git URL")
        if git_url != repository:
            raise ValueError(f"Invalid --repo git URL: {repository}")
        if git_url.startswith('-'):
            raise ValueError(f"Invalid --repo git URL: {repository}")
        if any(ord(char) < 32 or ord(char) == 127 for char in git_url):
            raise ValueError(f"Invalid --repo git URL: {repository}")

        parsed = urlparse(git_url)
        if parsed.scheme != "https" or not parsed.netloc or not parsed.path.strip("/"):
            raise ValueError(
                "--repo must be an https:// URL; SSH/scp and other schemes are not supported"
            )
        if parsed.password is not None or parsed.username is not None:
            raise ValueError("--repo URLs must not contain embedded credentials")

        repo_name = _repo_name_from_git_url(git_url)
        if not repo_name or not _SAFE_REPO_NAME_PATTERN.match(repo_name):
            raise ValueError(f"Invalid --repo repository name derived from URL: {repository}")
        if repo_name in seen_repo_names:
            raise ValueError(f"Duplicate --repo repository name: {repo_name}")
        seen_repo_names.add(repo_name)


def validate_agent_git_settings(config: Any) -> None:
    """Validate the VM Git policy and the currently supported auth provider."""
    from lib.config import AGENT_TOOLS, GIT_ACCESS_POLICIES
    from lib.credentials import get_runtime_credential
    from lib.git_credentials import (
        decode_git_ca_pem,
        normalize_git_https_origin,
        parse_git_ca_ssh_source,
    )

    git_access = getattr(config, "git_access", "none")
    if git_access not in GIT_ACCESS_POLICIES:
        raise ValueError(
            f"--git-access must be one of: {', '.join(GIT_ACCESS_POLICIES)}"
        )

    git_host = str(getattr(config, "git_host", "")).strip()
    if not git_host or git_host != getattr(config, "git_host", None):
        raise ValueError("--git-host must be a non-empty hostname")
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", git_host):
        raise ValueError(f"Invalid --git-host: {git_host}")

    git_credentials = getattr(config, "git_credentials", None) or []
    git_ca_certificates = getattr(config, "git_ca_certificates", None) or []
    git_ca_pems = getattr(config, "git_ca_pems", None) or []
    clear_git_credentials = bool(
        getattr(config, "clear_git_credentials", False)
    )
    if clear_git_credentials and (
        git_credentials or git_ca_certificates or git_ca_pems
    ):
        raise ValueError(
            "--no-git-credentials cannot be combined with Git credential or CA options"
        )
    if git_credentials and git_access == "none":
        raise ValueError(
            "--git-credential requires --git-access read or read-write"
        )

    seen_credential_origins: set[str] = set()
    for spec in git_credentials:
        if (
            not isinstance(spec, (list, tuple))
            or len(spec) != 2
            or not all(isinstance(value, str) for value in spec)
        ):
            raise ValueError(
                "--git-credential requires HTTPS_ORIGIN and USERNAME"
            )
        origin = normalize_git_https_origin(spec[0])
        username = spec[1]
        if (
            not username
            or username != username.strip()
            or ":" in username
            or "," in username
        ):
            raise ValueError(f"Invalid Git credential username: {username!r}")
        validate_no_control_characters(username, "Git credential username")
        if urlparse(origin).hostname == "github.com":
            raise ValueError(
                "GitHub credentials must use --git-auth or --agent-auth-file gh"
            )
        if origin in seen_credential_origins:
            raise ValueError(f"Duplicate --git-credential origin: {origin}")
        seen_credential_origins.add(origin)
        if get_runtime_credential(config, username) is None:
            raise ValueError(
                f"Missing resolved credential for Git user {username} at {origin}"
            )

    seen_ca_origins: set[str] = set()
    for option, specs, encoded in (
        ("--git-ca-certificate", git_ca_certificates, False),
        ("--git-ca-pem", git_ca_pems, True),
    ):
        for spec in specs:
            if (
                not isinstance(spec, (list, tuple))
                or len(spec) != 2
                or not all(isinstance(value, str) for value in spec)
            ):
                raise ValueError(f"{option} requires HTTPS_ORIGIN and a value")
            origin = normalize_git_https_origin(spec[0])
            if origin in seen_ca_origins:
                raise ValueError(f"Duplicate Git CA certificate origin: {origin}")
            seen_ca_origins.add(origin)
            if encoded:
                decode_git_ca_pem(spec[1])
            elif parse_git_ca_ssh_source(spec[1]) is None:
                validate_filesystem_path(spec[1], must_exist=True)

    github_auth_requested = bool(
        getattr(config, "git_auth_source", None)
        or getattr(config, "git_auth_file", None)
        or getattr(config, "git_auth_token", None)
    )
    agent_auth_source = getattr(config, "agent_auth_source", None)
    github_agent_auth_requested = any(
        isinstance(spec, (list, tuple))
        and len(spec) == 2
        and spec[0] == "gh"
        for spec in getattr(config, "agent_auth_files", None) or []
    )
    if (github_auth_requested or github_agent_auth_requested) and git_access == "none":
        raise ValueError(
            "GitHub credentials require --git-access read or read-write"
        )
    if (github_auth_requested or github_agent_auth_requested) and git_host != "github.com":
        raise ValueError(
            "GitHub CLI credentials currently support only --git-host github.com; "
            "other Git hosts may be used publicly for now"
        )

    selected_tools = set(config.selected_agent_tools())
    if github_auth_requested and "gh" not in selected_tools:
        raise ValueError("GitHub auth requires --agent-tool gh")
    for tool in selected_tools:
        if tool not in AGENT_TOOLS:
            raise ValueError(f"Unsupported --agent-tool: {tool}")

    supported_auth_tools = {"gh", "codex", "claude", "opencode"}
    if (
        getattr(config, "agent_auth_source", None)
        and not selected_tools.intersection(supported_auth_tools)
    ):
        raise ValueError(
            "--agent-auth requires a selected tool with supported credentials"
        )

    seen_auth_tools: set[str] = set()
    if agent_auth_source not in {None, "login", "check"}:
        raise ValueError("--agent-auth accepts login or none; active agent credential copying was removed")
    if agent_auth_source and "codex" not in selected_tools:
        raise ValueError("--agent-auth login requires --agent-tool codex")
    for spec in getattr(config, "agent_auth_files", None) or []:
        if (
            not isinstance(spec, (list, tuple))
            or len(spec) != 2
            or not all(isinstance(value, str) for value in spec)
            or spec[0] not in selected_tools
            or spec[0] not in supported_auth_tools
        ):
            raise ValueError(
                "--agent-auth-file requires a selected agent TOOL with supported credentials "
                "and a file PATH"
            )
        tool = spec[0]
        if tool == "codex" and agent_auth_source in {"login", "check"}:
            raise ValueError("Codex credentials must use either --agent-auth login or --agent-auth-file codex, not both")
        if tool in seen_auth_tools:
            raise ValueError(f"Duplicate --agent-auth-file for tool: {tool}")
        seen_auth_tools.add(tool)
        if tool == "gh" and github_auth_requested:
            raise ValueError(
                "GitHub credentials must use either --git-auth/--git-auth-file "
                "or --agent-auth-file gh, not both"
            )
        if tool == "gh" and git_host != "github.com":
            raise ValueError(
                "GitHub CLI credentials currently support only --git-host github.com"
            )


def validate_browser_automation_settings(config: Any) -> None:
    """Validate the explicit browser provider and compatible selected agents."""
    from lib.config import BROWSER_AUTOMATION_PROVIDERS

    provider = getattr(config, "browser_automation", None)
    if provider is None:
        return
    if not isinstance(provider, str) or provider not in BROWSER_AUTOMATION_PROVIDERS:
        raise ValueError(
            "--browser-automation must be one of: "
            f"{', '.join(BROWSER_AUTOMATION_PROVIDERS)}"
        )

    compatible_tools = {"codex", "opencode"}
    selected_tools = set(config.selected_agent_tools())
    if not selected_tools.intersection(compatible_tools):
        raise ValueError(
            "--browser-automation requires --agent-tool codex or "
            "--agent-tool opencode"
        )


def validate_godot_bundle_settings(config: Any) -> None:
    """Validate repeatable Godot workflow bundle selections."""
    from lib.config import GODOT_BUNDLES

    bundles = getattr(config, "godot_bundles", None)
    if bundles is None:
        return
    if not isinstance(bundles, list):
        raise ValueError("--godot-bundle must be a repeatable option")
    for bundle in bundles:
        if not isinstance(bundle, str) or bundle not in GODOT_BUNDLES:
            raise ValueError(
                "--godot-bundle must be one of: " + ", ".join(GODOT_BUNDLES)
            )
    if "publishing" in bundles and getattr(config, "username", None) == "root":
        raise ValueError("--godot-bundle publishing requires a non-root setup user")


def validate_network_name(value: str, name: str = "network name") -> str:
    """Validate a generic network inventory name or role label."""

    normalized_value = value.strip()
    if not normalized_value or not _NETWORK_NAME_PATTERN.match(normalized_value):
        raise ValueError(
            f"{name} must start with a letter or digit and contain only "
            "letters, digits, dots, underscores, or hyphens"
        )
    return normalized_value


def validate_network_provider(value: str) -> str:
    """Validate a network inventory provider tag."""

    normalized_value = value.strip()
    if not normalized_value or not _NETWORK_PROVIDER_PATTERN.match(normalized_value):
        raise ValueError(f"Invalid provider name: {value}")
    return normalized_value


def validate_network_ip(value: str, name: str = "network address") -> str:
    """Validate an IP address and return its normalized text form."""

    try:
        return str(ipaddress.ip_address(value))
    except ValueError as exc:
        raise ValueError(f"Invalid {name}: {value}") from exc


def validate_system_hostname(value: str) -> str:
    """Validate a static system hostname or fully qualified domain name."""

    normalized = value.strip()
    if not normalized or normalized != value or len(normalized) > 63:
        raise ValueError(f"Invalid system hostname: {value}")
    validate_no_control_characters(normalized, "System hostname")
    if normalized.endswith("."):
        raise ValueError("System hostname must not end with a dot")
    if any(not _HOSTNAME_LABEL_PATTERN.fullmatch(label) for label in normalized.split(".")):
        raise ValueError(f"Invalid system hostname: {value}")
    return normalized


def validate_network_interface_name(value: str) -> str:
    """Validate a Linux network interface name used in generated config."""

    normalized = value.strip()
    if normalized != value or not _NETWORK_INTERFACE_PATTERN.fullmatch(normalized):
        raise ValueError(f"Invalid network interface: {value}")
    return normalized


def _validate_static_interface(value: str, version: int, flag: str) -> ipaddress.IPv4Interface | ipaddress.IPv6Interface:
    if "/" not in value:
        raise ValueError(f"{flag} requires CIDR notation with a prefix length")
    try:
        interface = ipaddress.ip_interface(value)
    except ValueError as exc:
        raise ValueError(f"Invalid {flag} address: {value}") from exc
    if interface.version != version:
        family = "IPv4" if version == 4 else "IPv6"
        raise ValueError(f"{flag} requires an {family} address")
    if interface.ip.is_unspecified or interface.ip.is_multicast:
        raise ValueError(f"Invalid {flag} address: {value}")
    if (
        version == 4
        and interface.network.prefixlen < 31
        and interface.ip in {interface.network.network_address, interface.network.broadcast_address}
    ):
        raise ValueError(f"{flag} must be a usable host address: {value}")
    return interface


def validate_network_setup_settings(config: Any) -> None:
    """Validate hostname and persistent static network setup options."""

    hostname = getattr(config, "system_hostname", None)
    ipv4_value = getattr(config, "static_ipv4", None)
    ipv6_value = getattr(config, "static_ipv6", None)
    gateway4_value = getattr(config, "network_gateway4", None)
    gateway6_value = getattr(config, "network_gateway6", None)
    dns_values = getattr(config, "network_dns", None) or []
    interface_name = getattr(config, "network_interface", None)
    activate_network = bool(getattr(config, "activate_network", False))

    if hostname:
        validate_system_hostname(hostname)

    network_requested = any(
        (ipv4_value, ipv6_value, gateway4_value, gateway6_value, dns_values, interface_name)
    )
    if getattr(config, "system_type", None) == "server_proxmox" and (hostname or network_requested):
        raise ValueError(
            "--hostname and static network options are not supported for server_proxmox; "
            "changing Proxmox node identity or bridge networking requires a node-specific plan"
        )

    ipv4_interface = _validate_static_interface(ipv4_value, 4, "--ip") if ipv4_value else None
    ipv6_interface = _validate_static_interface(ipv6_value, 6, "--ipv6") if ipv6_value else None

    if activate_network and not (ipv4_interface or ipv6_interface):
        raise ValueError("--activate-network requires --ip or --ipv6")

    if activate_network:
        setup_host = str(getattr(config, "host", "")).lower().rstrip(".")
        local_host = setup_host == "localhost"
        try:
            local_host = local_host or ipaddress.ip_address(setup_host).is_loopback
        except ValueError:
            pass
        if local_host:
            raise ValueError(
                "--activate-network must run from a separate controller so the new "
                "address can be verified externally"
            )

    if gateway4_value:
        if not ipv4_interface:
            raise ValueError("--gateway requires --ip")
        try:
            gateway4 = ipaddress.ip_address(gateway4_value)
        except ValueError as exc:
            raise ValueError(f"Invalid IPv4 gateway: {gateway4_value}") from exc
        if gateway4.version != 4 or gateway4.is_unspecified or gateway4.is_multicast:
            raise ValueError(f"Invalid IPv4 gateway: {gateway4_value}")
        if gateway4 == ipv4_interface.ip or gateway4 not in ipv4_interface.network:
            raise ValueError(
                f"IPv4 gateway must be another address in {ipv4_interface.network}"
            )
        if (
            ipv4_interface.network.prefixlen < 31
            and gateway4
            in {
                ipv4_interface.network.network_address,
                ipv4_interface.network.broadcast_address,
            }
        ):
            raise ValueError(f"IPv4 gateway must be a usable host address: {gateway4_value}")

    if gateway6_value:
        if not ipv6_interface:
            raise ValueError("--gateway6 requires --ipv6")
        try:
            gateway6 = ipaddress.ip_address(gateway6_value)
        except ValueError as exc:
            raise ValueError(f"Invalid IPv6 gateway: {gateway6_value}") from exc
        if gateway6.version != 6 or gateway6.is_unspecified or gateway6.is_multicast:
            raise ValueError(f"Invalid IPv6 gateway: {gateway6_value}")
        if gateway6 == ipv6_interface.ip or (
            not gateway6.is_link_local and gateway6 not in ipv6_interface.network
        ):
            raise ValueError(
                "IPv6 gateway must be link-local or another address in "
                f"{ipv6_interface.network}"
            )

    if not ipv4_interface and not ipv6_interface and (dns_values or interface_name):
        raise ValueError("A network DNS or interface option requires --ip or --ipv6")

    normalized_dns: set[str] = set()
    for dns_value in dns_values:
        try:
            dns_address = ipaddress.ip_address(dns_value)
        except ValueError as exc:
            raise ValueError(f"Invalid DNS server address: {dns_value}") from exc
        if dns_address.is_unspecified or dns_address.is_multicast:
            raise ValueError(f"Invalid DNS server address: {dns_value}")
        normalized = str(dns_address)
        if normalized in normalized_dns:
            raise ValueError(f"Duplicate DNS server address: {normalized}")
        normalized_dns.add(normalized)

    if interface_name:
        validate_network_interface_name(interface_name)

    if getattr(config, "hosted_node", None) and (ipv4_interface or ipv6_interface):
        try:
            setup_host = ipaddress.ip_address(str(getattr(config, "host", "")))
        except ValueError:
            if not ipv4_interface:
                raise ValueError(
                    "Proxmox provisioning requires a literal IPv4 setup target"
                )
            return
        if setup_host.version == 6 and not ipv4_interface:
            raise ValueError(
                "Proxmox provisioning currently requires an IPv4 target for the SSH handoff"
            )
        configured = ipv4_interface if setup_host.version == 4 else ipv6_interface
        if configured is None or configured.ip != setup_host:
            if activate_network:
                return
            raise ValueError(
                "For Proxmox provisioning, the literal setup host must match the address "
                "provided by --ip or --ipv6 unless --activate-network is used for an "
                "existing guest"
            )


def validate_network_cidr(value: str, name: str = "network CIDR") -> str:
    """Validate a CIDR and return its normalized text form."""

    try:
        return str(ipaddress.ip_network(value, strict=False))
    except ValueError as exc:
        raise ValueError(f"Invalid {name}: {value}") from exc


def validate_network_ip_or_cidr(value: str, name: str = "network address") -> str:
    """Validate either an IP address or a CIDR range."""

    if "/" in value:
        return validate_network_cidr(value, name)
    return validate_network_ip(value, name)


def validate_network_vlan_id(value: int | str) -> int:
    """Validate an IEEE 802.1Q VLAN ID."""

    try:
        vlan_id = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid VLAN ID: {value}") from exc
    if not 1 <= vlan_id <= 4094:
        raise ValueError(f"VLAN ID must be between 1 and 4094: {vlan_id}")
    return vlan_id


_VM_MOUNT_ALLOWED_PREFIXES = ("/srv/", "/var/lib/", "/opt/", "/mnt/")
_VM_MOUNT_EXACT_PATHS = {"/data"}
_VM_MOUNT_PATH_PATTERN = re.compile(r"^/[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*$")


def _nested_string_specs(value: Any, flag: str) -> list[list[str]]:
    """Normalize an argparse append/nargs value and reject malformed records."""

    if not value:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{flag} must be repeated argument records")
    if value and isinstance(value[0], str):
        raw_specs: list[Any] = [value]
    else:
        raw_specs = value
    specs: list[list[str]] = []
    for raw_spec in raw_specs:
        if not isinstance(raw_spec, list) or not all(
            isinstance(part, str) for part in raw_spec
        ):
            raise ValueError(f"{flag} must contain only string values")
        specs.append(list(raw_spec))
    return specs


def _validate_vm_mount_path(path: str, *, allow_home: bool = False) -> str:
    """Validate an empty-path mount target supported by VM storage setup."""

    if not isinstance(path, str) or not os.path.isabs(path):
        raise ValueError(f"--storage-mount PATH must be absolute: {path}")
    validate_filesystem_path(path, must_exist=False)
    normalized = os.path.normpath(path)
    if normalized != path:
        raise ValueError(
            f"--storage-mount PATH must be normalized without a trailing slash: {path}"
        )
    if not _VM_MOUNT_PATH_PATTERN.fullmatch(path):
        raise ValueError(
            "--storage-mount PATH components may contain only letters, numbers, "
            "'.', '_', or '-'"
        )
    if (path == "/home" or path.startswith("/home/")) and not allow_home:
        raise ValueError(
            "/home storage migration is not implemented; use an empty tool-owned "
            "path such as /srv/agent-workspace"
        )
    if (
        path not in _VM_MOUNT_EXACT_PATHS
        and not (allow_home and path == "/home")
        and not path.startswith(_VM_MOUNT_ALLOWED_PREFIXES)
    ):
        raise ValueError(
            "--storage-mount PATH must be /data or below /srv, /var/lib, /opt, or /mnt"
        )
    return path


def validate_vm_storage_settings(
    config: Any,
    *,
    require_provisioning: bool,
) -> None:
    """Validate named VM data disks, empty mounts, and agent placement."""

    storage_specs = _nested_string_specs(
        getattr(config, "container_storage", None), "--storage"
    )
    mount_specs = _nested_string_specs(
        getattr(config, "storage_mounts", None), "--storage-mount"
    )
    cache_specs = _nested_string_specs(
        getattr(config, "storage_caches", None), "--storage-cache"
    )
    disk_setting_specs = _nested_string_specs(
        getattr(config, "vm_disk_settings", None),
        "--disk-ssd/--disk-discard/--disk-backup",
    )
    swap_device_specs = _nested_string_specs(
        getattr(config, "swap_devices", None), "--swap-device"
    )
    agent_workspace = getattr(config, "agent_workspace", None)

    provisional_data_names = {
        spec[0]
        for spec in storage_specs
        if len(spec) == 3 and spec[0] not in {"root", "template"}
    }
    named_swap_sources = {
        spec[1]
        for spec in swap_device_specs
        if len(spec) >= 2 and spec[1] in provisional_data_names
    }
    has_storage_declaration = bool(
        storage_specs or mount_specs or cache_specs or disk_setting_specs
        or named_swap_sources
    )
    if (
        require_provisioning
        and has_storage_declaration
        and not getattr(config, "hosted_node", None)
    ):
        raise ValueError(
            "VM storage and per-device disk settings require --provision-on"
        )

    if agent_workspace is not None:
        if not isinstance(agent_workspace, str) or not os.path.isabs(agent_workspace):
            raise ValueError("--agent-workspace must be an absolute path")
        validate_filesystem_path(agent_workspace, must_exist=False)
        if os.path.normpath(agent_workspace) != agent_workspace or agent_workspace == "/":
            raise ValueError("--agent-workspace must be a normalized non-root path")
        user_home_prefix = f"/home/{getattr(config, 'username', '')}/"
        if not agent_workspace.startswith(user_home_prefix):
            try:
                _validate_vm_mount_path(agent_workspace)
            except ValueError as exc:
                raise ValueError(
                    "--agent-workspace must be below the setup user's /home directory "
                    "or use an approved tool-owned path below /srv, /var/lib, /opt, "
                    "/mnt, or at /data"
                ) from exc

    data_names: set[str] = set()
    data_sizes_kib: dict[str, int] = {}
    for spec in storage_specs:
        if not spec or spec[0] in {"root", "template"}:
            continue
        if len(spec) != 3:
            raise ValueError(
                "VM data storage requires NAME POOL AMOUNT (or NAME AMOUNT before "
                "Proxmox defaults are resolved)"
            )
        name, pool, amount = spec
        validate_vm_storage_name(name)
        if name in data_names:
            raise ValueError(f"Duplicate --storage NAME '{name}'")
        data_names.add(name)
        validate_proxmox_storage_name(pool, f"--storage {name}")
        data_sizes_kib[name] = _memory_string_kib(
            amount,
            f"--storage {name} AMOUNT",
        )
    if len(data_names) > 30:
        raise ValueError("A Proxmox VM supports at most 30 declared data disks")

    declared_disk_names = {"root", *data_names}
    disk_setting_names: set[str] = set()
    for spec in disk_setting_specs:
        if not 2 <= len(spec) <= 4:
            raise ValueError(
                "Per-device disk settings require NAME and one or both of "
                "discard=on|off, ssd=on|off, and backup=on|off"
            )
        name = spec[0]
        if name != "root":
            validate_vm_storage_name(name)
        if name in disk_setting_names:
            raise ValueError(f"Duplicate per-device disk settings for '{name}'")
        disk_setting_names.add(name)
        if name not in declared_disk_names:
            raise ValueError(
                f"Per-device disk settings reference unknown VM disk '{name}'"
            )
        setting_names: set[str] = set()
        for option in spec[1:]:
            setting, separator, enabled = option.partition("=")
            if (
                not separator
                or setting not in {"discard", "ssd", "backup"}
                or enabled not in {"on", "off"}
            ):
                raise ValueError(
                    "Per-device disk settings must use discard=on|off, "
                    "ssd=on|off, or backup=on|off"
                )
            if setting in setting_names:
                raise ValueError(
                    f"Duplicate {setting} setting for VM disk '{name}'"
                )
            setting_names.add(setting)

    mount_names: set[str] = set()
    mount_paths: list[str] = []
    machine_type = getattr(config, "machine_type", None)
    for spec in mount_specs:
        if not 2 <= len(spec) <= 4:
            raise ValueError(
                "--storage-mount requires NAME PATH [ext4|xfs] [empty]"
            )
        name, path = spec[:2]
        validate_vm_storage_name(name)
        if name in mount_names:
            raise ValueError(f"Duplicate --storage-mount NAME '{name}'")
        mount_names.add(name)
        filesystem = spec[2] if len(spec) >= 3 else "ext4"
        policy = spec[3] if len(spec) >= 4 else "empty"
        if filesystem not in {"ext4", "xfs"}:
            raise ValueError("--storage-mount filesystem must be ext4 or xfs")
        if policy != "empty":
            raise ValueError(
                "--storage-mount currently supports only the empty policy; "
                "populated-path migration is not implemented"
            )
        mount_paths.append(
            _validate_vm_mount_path(
                path,
                allow_home=machine_type == "vm",
            )
        )

    for index, path in enumerate(mount_paths):
        for other in mount_paths[index + 1:]:
            common = os.path.commonpath((path, other))
            if common in {path, other}:
                raise ValueError(
                    f"Overlapping --storage-mount paths are not supported: {path}, {other}"
                )

    cache_origins: set[str] = set()
    cache_devices: set[str] = set()
    for spec in cache_specs:
        if not 2 <= len(spec) <= 3:
            raise ValueError(
                "--storage-cache requires DATA_NAME CACHE_NAME "
                "[writethrough|writeback]"
            )
        data_name, cache_name = spec[:2]
        validate_vm_storage_name(data_name)
        validate_vm_storage_name(cache_name)
        if data_name == cache_name:
            raise ValueError("--storage-cache data and cache disks must differ")
        if data_name in cache_origins:
            raise ValueError(f"Duplicate --storage-cache data disk '{data_name}'")
        if cache_name in cache_devices:
            raise ValueError(f"Duplicate --storage-cache cache disk '{cache_name}'")
        if data_name in cache_devices or cache_name in cache_origins:
            raise ValueError("A VM disk cannot be both cache data and cache media")
        mode = spec[2] if len(spec) == 3 else "writethrough"
        if mode not in {"writethrough", "writeback"}:
            raise ValueError(
                "--storage-cache mode must be writethrough or writeback"
            )
        cache_origins.add(data_name)
        cache_devices.add(cache_name)

    unknown_cache_disks = (cache_origins | cache_devices) - data_names
    if unknown_cache_disks:
        raise ValueError(
            "--storage-cache references unknown VM data disk(s): "
            + ", ".join(sorted(unknown_cache_disks))
        )
    for cache_name in cache_devices:
        if data_sizes_kib.get(cache_name, 0) < 512 * 1024:
            raise ValueError(
                f"--storage-cache disk '{cache_name}' must be at least 512M"
            )
    mounted_cache_devices = cache_devices & mount_names
    if mounted_cache_devices:
        raise ValueError(
            "Cache disks are consumed by LVM and must not use --storage-mount: "
            + ", ".join(sorted(mounted_cache_devices))
        )

    swap_mount_conflicts = named_swap_sources & mount_names
    if swap_mount_conflicts:
        raise ValueError(
            "Swap disks must not use --storage-mount: "
            + ", ".join(sorted(swap_mount_conflicts))
        )
    swap_cache_conflicts = named_swap_sources & (cache_origins | cache_devices)
    if swap_cache_conflicts:
        raise ValueError(
            "Swap disks cannot also participate in --storage-cache: "
            + ", ".join(sorted(swap_cache_conflicts))
        )
    for spec in disk_setting_specs:
        if spec[0] not in named_swap_sources:
            continue
        if "backup=on" in spec[1:]:
            raise ValueError(
                f"Swap disk '{spec[0]}' cannot be included in Proxmox backups"
            )

    missing_mounts = data_names - mount_names - cache_devices - named_swap_sources
    if missing_mounts:
        raise ValueError(
            "Every VM data disk requires --storage-mount; missing: "
            + ", ".join(sorted(missing_mounts))
        )
    unknown_mounts = mount_names - data_names
    if unknown_mounts:
        raise ValueError(
            "--storage-mount references unknown VM data disk(s): "
            + ", ".join(sorted(unknown_mounts))
        )

    if (
        data_names or mount_names or cache_specs or disk_setting_specs
        or named_swap_sources
    ) and machine_type != "vm":
        raise ValueError(
            "Named data disks, mounts, caches, and per-device disk settings "
            "require --machine vm"
        )


_SWAP_AREA_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
_SWAP_ALGORITHM_PATTERN = re.compile(r"^[a-z0-9_-]{1,32}$")


def validate_swap_settings(config: Any) -> None:
    """Validate declarative swap areas before any block-device mutation."""

    mode = getattr(config, "swap_mode", "auto")
    if mode not in {"auto", "preserve", "none"}:
        raise ValueError("--swap-mode must be auto, preserve, or none")

    file_specs = _nested_string_specs(
        getattr(config, "swap_files", None), "--swap-file"
    )
    device_specs = _nested_string_specs(
        getattr(config, "swap_devices", None), "--swap-device"
    )
    zram_specs = _nested_string_specs(
        getattr(config, "swap_zram", None), "--swap-zram"
    )
    explicit_areas = bool(file_specs or device_specs or zram_specs)
    if explicit_areas and mode != "auto":
        raise ValueError("Explicit swap areas require --swap-mode auto")

    names: set[str] = set()
    file_paths: set[str] = set()
    device_sources: set[str] = set()

    def add_name(name: str) -> None:
        if not _SWAP_AREA_NAME_PATTERN.fullmatch(name):
            raise ValueError(
                "Swap NAME must start with a lowercase letter and contain only "
                "lowercase letters, numbers, and '-' (maximum 32 characters)"
            )
        if name in names:
            raise ValueError(f"Duplicate swap area NAME '{name}'")
        names.add(name)

    def parse_options(
        spec: list[str], start: int, allowed: set[str], flag: str
    ) -> dict[str, str]:
        options: dict[str, str] = {}
        for option in spec[start:]:
            key, separator, value = option.partition("=")
            if not separator or key not in allowed or not value:
                raise ValueError(
                    f"{flag} options must use "
                    + " or ".join(f"{item}=VALUE" for item in sorted(allowed))
                )
            if key in options:
                raise ValueError(f"Duplicate {key} option for swap area '{spec[0]}'")
            options[key] = value
        if "priority" in options:
            try:
                priority = int(options["priority"])
            except ValueError as exc:
                raise ValueError(f"{flag} priority must be an integer") from exc
            if not 0 <= priority <= 32767:
                raise ValueError(f"{flag} priority must be between 0 and 32767")
        return options

    for spec in file_specs:
        if len(spec) < 3:
            raise ValueError("--swap-file requires NAME PATH SIZE [priority=N]")
        add_name(spec[0])
        path = spec[1]
        if not os.path.isabs(path) or os.path.normpath(path) != path or path == "/":
            raise ValueError("--swap-file PATH must be an absolute normalized file path")
        validate_no_control_characters(path, "--swap-file PATH")
        size_kib = _memory_string_kib(spec[2], "--swap-file SIZE")
        if size_kib < 64 * 1024:
            raise ValueError("--swap-file SIZE must be at least 64M")
        if size_kib % 1024:
            raise ValueError("--swap-file SIZE must resolve to a whole MiB")
        if path in file_paths:
            raise ValueError(f"Duplicate --swap-file PATH '{path}'")
        file_paths.add(path)
        parse_options(spec, 3, {"priority"}, "--swap-file")

    device_names: set[str] = set()
    declared_data = {
        spec[0]
        for spec in getattr(config, "container_storage", None) or []
        if len(spec) == 3 and spec[0] not in {"root", "template"}
    }
    for spec in device_specs:
        if len(spec) < 2:
            raise ValueError(
                "--swap-device requires NAME SOURCE [priority=N] [discard=POLICY]"
            )
        add_name(spec[0])
        device_names.add(spec[0])
        source = spec[1]
        validate_no_control_characters(source, "--swap-device SOURCE")
        stable_uuid = re.fullmatch(r"UUID=[A-Fa-f0-9-]{8,64}", source)
        stable_by_id = re.fullmatch(
            r"/dev/disk/by-id/[A-Za-z0-9._:+-]+", source
        )
        if (
            source not in declared_data
            and stable_uuid is None
            and stable_by_id is None
        ):
            raise ValueError(
                "--swap-device SOURCE must name a declared VM data disk, use "
                "UUID=..., or use /dev/disk/by-id/..."
            )
        if source in device_sources:
            raise ValueError(f"Duplicate --swap-device SOURCE '{source}'")
        device_sources.add(source)
        options = parse_options(
            spec, 2, {"discard", "priority"}, "--swap-device"
        )
        if options.get("discard", "off") not in {"off", "once", "pages", "both"}:
            raise ValueError(
                "--swap-device discard must be off, once, pages, or both"
            )

    for spec in zram_specs:
        if len(spec) < 2:
            raise ValueError(
                "--swap-zram requires NAME SIZE [priority=N] [algorithm=TOKEN]"
            )
        add_name(spec[0])
        size_kib = _memory_string_kib(spec[1], "--swap-zram SIZE")
        if size_kib < 64 * 1024:
            raise ValueError("--swap-zram SIZE must be at least 64M")
        if size_kib % 1024:
            raise ValueError("--swap-zram SIZE must resolve to a whole MiB")
        options = parse_options(
            spec, 2, {"algorithm", "priority"}, "--swap-zram"
        )
        algorithm = options.get("algorithm", "auto")
        if algorithm != "auto" and not _SWAP_ALGORITHM_PATTERN.fullmatch(algorithm):
            raise ValueError("--swap-zram algorithm contains unsupported characters")

    initialize = getattr(config, "swap_initialize", None) or []
    if len(initialize) != len(set(initialize)):
        raise ValueError("--swap-initialize cannot repeat the same NAME")
    unknown_initialize = set(initialize) - device_names
    if unknown_initialize:
        raise ValueError(
            "--swap-initialize references unknown swap area(s): "
            + ", ".join(sorted(unknown_initialize))
        )

    resume = getattr(config, "swap_resume", None)
    if resume and resume not in device_names:
        raise ValueError("--swap-resume must name a declared --swap-device")

    swappiness = getattr(config, "swappiness", None)
    if swappiness is not None and (
        isinstance(swappiness, bool)
        or not isinstance(swappiness, int)
        or not 0 <= swappiness <= 200
    ):
        raise ValueError("--swappiness must be an integer between 0 and 200")
    zswap = getattr(config, "zswap", None)
    zswap_pool = getattr(config, "zswap_max_pool_percent", None)
    if zswap_pool is not None and (
        isinstance(zswap_pool, bool)
        or not isinstance(zswap_pool, int)
        or not 1 <= zswap_pool <= 50
    ):
        raise ValueError("--zswap-max-pool-percent must be between 1 and 50")
    if zswap_pool is not None and zswap is not True:
        raise ValueError("--zswap-max-pool-percent requires --zswap")
    if zram_specs and zswap is True:
        raise ValueError("Managed zram and zswap cannot both be enabled")

    if explicit_areas and getattr(config, "machine_type", None) in {
        "unprivileged",
        "privileged",
        "oci",
    }:
        raise ValueError("Explicit swap areas are not supported inside containers")
    if (
        getattr(config, "system_type", None) == "server_proxmox"
        and swappiness not in {None, 10}
    ):
        raise ValueError("Proxmox hosts require vm.swappiness=10")


def validate_proxmox_balloon_settings(config: Any) -> None:
    """Validate host target and per-VM balloon priority settings."""
    target = getattr(config, "proxmox_balloon_target", None)
    if target is not None:
        if isinstance(target, bool) or not isinstance(target, int):
            raise ValueError("--proxmox-balloon-target must be an integer")
        if not 1 <= target <= 95:
            raise ValueError("--proxmox-balloon-target must be between 1 and 95")
        if getattr(config, "system_type", None) != "server_proxmox":
            raise ValueError(
                "--proxmox-balloon-target requires setup type server_proxmox"
            )

    shares = getattr(config, "vm_balloon_shares", 1000)
    if isinstance(shares, bool) or not isinstance(shares, int):
        raise ValueError("--balloon-shares must be an integer")
    if not 1 <= shares <= 50000:
        raise ValueError("--balloon-shares must be between 1 and 50000")


def validate_hosted_flags(config: Any) -> None:
    """Validate Proxmox guest options used with ``--provision-on``.

    Args:
        config: SetupConfig instance

    Raises:
        ValueError: If required flags are missing or invalid
    """
    validate_proxmox_balloon_settings(config)
    validate_swap_settings(config)
    balloon_min = getattr(config, "vm_balloon_min", None)
    balloon_shares = getattr(config, "vm_balloon_shares", 1000)
    allow_memory_overcommit = getattr(config, "allow_memory_overcommit", False)
    image_storage = getattr(config, "vm_image_storage", None)
    image_sha512 = getattr(config, "vm_image_sha512", None)
    validate_vm_storage_settings(config, require_provisioning=True)
    if not config.hosted_node:
        if balloon_min:
            raise ValueError("--balloon-min requires --provision-on")
        if balloon_shares != 1000:
            raise ValueError("--balloon-shares requires --provision-on")
        if allow_memory_overcommit:
            raise ValueError("--allow-memory-overcommit requires --provision-on")
        if image_storage:
            raise ValueError("--image-storage requires --provision-on")
        if image_sha512:
            raise ValueError("--image-sha512 requires --provision-on")
        return

    from lib.validators import validate_host

    if not validate_host(config.hosted_node):
        raise ValueError(f"Invalid Proxmox node host: {config.hosted_node}")

    if not config.container_memory:
        raise ValueError("--memory is required with --provision-on")

    if not config.container_storage:
        raise ValueError("--storage is required with --provision-on")

    storage_specs: list[list[str]] = []
    if config.container_storage:
        raw_specs = cast(list[object], config.container_storage)
        if raw_specs and isinstance(raw_specs[0], str):
            storage_specs = [cast(list[str], raw_specs)]
        else:
            storage_specs = [cast(list[str], spec) for spec in raw_specs]

    root_seen = False
    seen_types: set[str] = set()

    for spec in storage_specs:
        if len(spec) < 2:
            raise ValueError(
                "--storage requires at least TYPE and POOL"
            )

        storage_type = spec[0]
        if storage_type in seen_types:
            raise ValueError(f"Duplicate --storage NAME '{storage_type}'")
        seen_types.add(storage_type)

        if storage_type == "root":
            if len(spec) != 3:
                raise ValueError(
                    "--storage root requires TYPE POOL AMOUNT"
                )
            root_seen = True
        elif storage_type == "template":
            if len(spec) != 2:
                raise ValueError(
                    "--storage template requires TYPE POOL"
                )
        else:
            if len(spec) != 3:
                raise ValueError(
                    f"--storage {storage_type} requires NAME POOL AMOUNT"
                )

        validate_proxmox_storage_name(spec[1], f"--storage {storage_type}")

    if not root_seen:
        raise ValueError("--storage root [POOL] AMOUNT is required with --provision-on")

    memory_mib = parse_memory_mib(config.container_memory, "--memory")

    for spec in storage_specs:
        if spec[0] == "template":
            continue
        amount = spec[2]
        _memory_string_kib(amount, f"--storage {spec[0]} AMOUNT")

    if config.container_cores < 1:
        raise ValueError("--cores must be at least 1")

    hosted_bridge = getattr(config, "hosted_bridge", None)
    if hosted_bridge:
        validate_network_interface_name(hosted_bridge)

    machine_type = getattr(config, "machine_type", None)
    vm_image = getattr(config, "vm_image", None)
    if machine_type == "vm":
        if image_storage:
            validate_proxmox_storage_name(image_storage)
        if balloon_min:
            balloon_mib = parse_memory_mib(balloon_min, "--balloon-min")
            if balloon_mib > memory_mib:
                raise ValueError("--balloon-min cannot exceed --memory")
        ssh_key = getattr(config, "ssh_key", None)
        if not ssh_key:
            raise ValueError(
                "VM provisioning requires an SSH identity with a matching .pub file; "
                "use --key PATH when no default identity is available"
            )
        expanded_ssh_key = os.path.abspath(os.path.expanduser(str(ssh_key)))
        try:
            private_key_valid = (
                os.path.isfile(expanded_ssh_key)
                and os.access(expanded_ssh_key, os.R_OK)
                and os.path.getsize(expanded_ssh_key) > 0
            )
        except OSError:
            private_key_valid = False
        if not private_key_valid:
            raise ValueError(
                f"VM provisioning requires a readable SSH private key: {expanded_ssh_key}"
            )
        pubkey_path = f"{expanded_ssh_key}.pub"
        try:
            public_key_valid = (
                os.path.isfile(pubkey_path)
                and os.access(pubkey_path, os.R_OK)
                and os.path.getsize(pubkey_path) > 0
            )
        except OSError:
            public_key_valid = False
        if not public_key_valid:
            raise ValueError(
                f"VM provisioning requires a readable SSH public key: {pubkey_path}"
            )
        config.ssh_key = expanded_ssh_key
        if getattr(config, "hosted_key", None):
            config.hosted_key = os.path.abspath(
                os.path.expanduser(str(config.hosted_key))
            )

        if not getattr(config, "static_ipv4", None):
            try:
                target_ip = ipaddress.ip_address(str(getattr(config, "host", "")))
            except ValueError as exc:
                raise ValueError(
                    "VM provisioning requires a literal IPv4 target"
                ) from exc
            if target_ip.version != 4:
                raise ValueError(
                    "VM provisioning currently requires an IPv4 target for the SSH handoff"
                )

        from lib.cloud_images import parse_image_argument, resolve_cloud_image
        for spec in storage_specs:
            if spec[0] == "template":
                raise ValueError(
                    "--storage template is not used for VMs; use --image instead"
                )
        if vm_image:
            _image_url, image_storage_ref = parse_image_argument(vm_image)
            if _image_url:
                if not image_sha512:
                    raise ValueError(
                        "Custom VM image URLs require --image-sha512 for integrity verification"
                    )
                if not re.fullmatch(r"[0-9A-Fa-f]{128}", str(image_sha512)):
                    raise ValueError("--image-sha512 must be exactly 128 hexadecimal characters")
            elif image_sha512:
                raise ValueError(
                    "--image-sha512 applies only to downloaded VM image URLs"
                )
            if image_storage and image_storage_ref:
                raise ValueError(
                    "--image-storage applies to downloaded VM images; omit it "
                    "when --image is already a Proxmox storage reference"
                )
        else:
            if image_sha512:
                raise ValueError("--image-sha512 requires --image")
            base = getattr(config, "container_base", None) or "debian"
            resolve_cloud_image(base)
    else:
        if balloon_min:
            raise ValueError("--balloon-min requires --machine vm")
        if balloon_shares != 1000:
            raise ValueError("--balloon-shares requires --machine vm")
        if allow_memory_overcommit:
            raise ValueError("--allow-memory-overcommit requires --machine vm")
        if vm_image:
            raise ValueError("--image requires --machine vm")
        if image_storage:
            raise ValueError("--image-storage requires --machine vm")


def validate_rdp_settings(config: Any) -> None:
    """Validate credentials and network policy required for xRDP.

    xRDP authenticates against the Unix account password. Hosted cloud images
    are deliberately provisioned key-only, so remote or newly-created desktop
    accounts must receive a password. A local setup may explicitly reuse the
    password of an already-existing account.
    """
    use_existing_password = bool(getattr(config, "rdp_existing_password", False))
    if not getattr(config, "enable_rdp", False):
        if use_existing_password:
            raise ValueError("--rdp-existing-password requires --rdp")
        has_rdp_policy = bool(getattr(config, "rdp_allowed_sources", None)) or any(
            (
                getattr(config, "rdp_bind_address", "0.0.0.0") != "0.0.0.0",
                not bool(getattr(config, "rdp_clipboard", True)),
                bool(getattr(config, "rdp_drive_redirection", False)),
                bool(getattr(config, "rdp_audio", False)),
                getattr(config, "rdp_idle_timeout", 0) != 0,
            )
        )
        if has_rdp_policy:
            raise ValueError("RDP policy options require --rdp")
        return

    username = str(getattr(config, "username", "")).strip()
    if username == "root":
        raise ValueError("--rdp cannot be used with the root account")

    password = getattr(config, "password", None)
    if use_existing_password:
        if getattr(config, "hosted_node", None):
            raise ValueError(
                "--rdp-existing-password cannot be used while provisioning a guest"
            )
        local_hosts = {"localhost", "127.0.0.1", "::1"}
        if str(getattr(config, "host", "")).strip().lower() not in local_hosts:
            raise ValueError("--rdp-existing-password requires a local setup target")
        if password is not None:
            raise ValueError("--rdp-existing-password cannot be combined with --password")
        try:
            pwd.getpwnam(username)
        except KeyError as exc:
            raise ValueError(
                "--rdp-existing-password requires an existing local desktop account"
            ) from exc

        try:
            password_status = subprocess.run(
                ["passwd", "-S", username],
                capture_output=True,
                text=True,
                env={**os.environ, "LC_ALL": "C"},
            )
        except OSError as exc:
            raise ValueError(
                "--rdp-existing-password could not inspect the account password status"
            ) from exc
        status_fields = (password_status.stdout or "").split()
        if password_status.returncode != 0 or len(status_fields) < 2:
            raise ValueError(
                "--rdp-existing-password could not inspect the account password status"
            )
        if status_fields[1] != "P":
            raise ValueError(
                "--rdp-existing-password requires an existing account with an unlocked password"
            )
    else:
        if not isinstance(password, str) or not password.strip():
            if not getattr(config, "dry_run", False):
                raise ValueError("--rdp requires --password for the desktop login account")
        else:
            validate_no_control_characters(password, "RDP password")

    _validate_rdp_network_policy(config)


def _validate_rdp_network_policy(config: Any) -> None:
    """Validate RDP bind, source, session, and timeout policy."""

    bind_address = getattr(config, "rdp_bind_address", "0.0.0.0")
    if not isinstance(bind_address, str):
        raise ValueError("--rdp-bind-address requires an IP address")
    validate_network_ip(bind_address, "RDP bind address")

    normalized_sources: set[str] = set()
    for source in getattr(config, "rdp_allowed_sources", None) or []:
        if not isinstance(source, str):
            raise ValueError("--rdp-source requires an IP address or CIDR")
        normalized = validate_network_ip_or_cidr(source, "RDP source")
        if normalized in normalized_sources:
            raise ValueError(f"Duplicate RDP source: {normalized}")
        normalized_sources.add(normalized)

    idle_timeout = getattr(config, "rdp_idle_timeout", 0)
    if type(idle_timeout) is not int or idle_timeout < 0:
        raise ValueError("--rdp-idle-timeout must be a non-negative integer")
