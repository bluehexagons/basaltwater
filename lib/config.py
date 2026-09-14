#!/usr/bin/env python3

from __future__ import annotations

import argparse
import ipaddress
import shlex
import socket
import warnings
from dataclasses import dataclass, asdict
from functools import lru_cache
from typing import Optional, cast
from lib.plugin_registry import get_system_type_definition, get_system_type_names
from lib.types import StrList, NestedStrList, JSONDict, MaybeStr


SYSTEM_TYPES = get_system_type_names()

AUTO_MACHINE_TYPE = "auto"
MACHINE_TYPES = [AUTO_MACHINE_TYPE, "unprivileged", "vm", "privileged", "hardware", "oci"]
DEFAULT_MACHINE_TYPE = AUTO_MACHINE_TYPE

DESKTOP_SYSTEMS = [
    system_type.name
    for system_type in (get_system_type_definition(name) for name in SYSTEM_TYPES)
    if system_type.include_desktop
]
CLI_SYSTEMS = [
    system_type.name
    for system_type in (get_system_type_definition(name) for name in SYSTEM_TYPES)
    if system_type.include_cli_tools
]

AGENT_TOOLS = ("gh", "codex", "claude", "opencode")
WEB_INTERFACES = ("t3code",)
DEVICE_PAIRING_PROVIDERS = ("t3code",)
BROWSER_AUTOMATION_PROVIDERS = ("playwright",)
EDITORS = ("geany", "vscode")
GIT_ACCESS_POLICIES = ("none", "read", "read-write")
GODOT_BUNDLES = ("web", "publishing")
DEFAULT_SYNCTHING_ROOT = "/srv/syncthing"
GODOT_WEB_HTTPS_PORT = 8443
WEB_PANEL_DEFAULT_PORT_SENTINEL = object()
DEFAULT_AGENT_WEB_PORTS = (80, 443, 8080, 8081)
_LAN_IPV4_BLOCKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)
_LAN_IPV6_BLOCK = ipaddress.ip_network("fc00::/7")
_DEFAULT_LAN_PREFIX_LENGTHS = {4: 24, 6: 64}


def _is_lan_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Return whether an address belongs to RFC 1918 or IPv6 ULA space."""

    if isinstance(address, ipaddress.IPv4Address):
        return any(address in network for network in _LAN_IPV4_BLOCKS)
    return address in _LAN_IPV6_BLOCK


def _minimum_lan_prefix_length(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> int:
    """Return the broadest prefix that cannot escape private address space."""

    if isinstance(address, ipaddress.IPv4Address):
        return next(
            network.prefixlen
            for network in _LAN_IPV4_BLOCKS
            if address in network
        )
    return _LAN_IPV6_BLOCK.prefixlen


def _lan_network_from_address(value: str) -> Optional[str]:
    """Convert one private address or interface into its LAN access network."""

    normalized = value.strip().strip("[]")
    try:
        interface = ipaddress.ip_interface(normalized)
    except ValueError:
        return None
    if not _is_lan_address(interface.ip):
        return None
    requested_prefix_length = (
        interface.network.prefixlen
        if "/" in normalized
        else _DEFAULT_LAN_PREFIX_LENGTHS[interface.version]
    )
    prefix_length = max(
        requested_prefix_length,
        _minimum_lan_prefix_length(interface.ip),
    )
    return str(
        ipaddress.ip_network(
            f"{interface.ip}/{prefix_length}",
            strict=False,
        )
    )


def _primary_local_address(family: socket.AddressFamily) -> Optional[str]:
    """Discover the primary local address chosen for a normal routed socket."""

    destination: tuple[object, ...]
    if family == socket.AF_INET:
        destination = ("192.0.2.1", 9)
    else:
        destination = ("2001:db8::1", 9, 0, 0)
    try:
        with socket.socket(family, socket.SOCK_DGRAM) as route_socket:
            route_socket.connect(destination)
            address = route_socket.getsockname()[0]
    except OSError:
        return None
    return str(address)


@lru_cache(maxsize=128)
def _resolved_lan_networks(host: str) -> tuple[str, ...]:
    """Resolve a setup target into adjacent private LAN networks."""

    literal_network = _lan_network_from_address(host)
    if literal_network:
        return (literal_network,)

    query_hosts = [host]
    if host in {"localhost", "127.0.0.1", "::1"}:
        query_hosts = [socket.gethostname()]

    addresses: StrList = []
    for query_host in query_hosts:
        try:
            address_info = socket.getaddrinfo(
                query_host,
                None,
                family=socket.AF_UNSPEC,
                type=socket.SOCK_STREAM,
            )
        except socket.gaierror:
            continue
        for _family, _type, _protocol, _canonical, socket_address in address_info:
            addresses.append(str(socket_address[0]).split("%", 1)[0])

    if host in {"localhost", "127.0.0.1", "::1"}:
        for family in (socket.AF_INET, socket.AF_INET6):
            local_address = _primary_local_address(family)
            if local_address:
                addresses.append(local_address)

    networks = [
        network
        for address in addresses
        if (network := _lan_network_from_address(address)) is not None
    ]
    return tuple(_merge_network_sources(networks))


def _merge_network_sources(*source_lists: Optional[StrList]) -> StrList:
    """Combine address lists in order, deduplicating canonical equivalents."""

    merged: StrList = []
    seen: set[str] = set()
    for sources in source_lists:
        for source in sources or []:
            try:
                canonical = (
                    str(ipaddress.ip_network(source, strict=False))
                    if "/" in source
                    else str(ipaddress.ip_address(source))
                )
            except (TypeError, ValueError):
                canonical = source
            if canonical in seen:
                continue
            seen.add(canonical)
            merged.append(canonical)
    return merged


def _resolve_machine_type(
    args: argparse.Namespace,
    *,
    is_hosted: bool,
) -> str:
    explicit_machine_type = getattr(args, "machine_type", None)
    if explicit_machine_type:
        return explicit_machine_type

    # Proxmox provisioning creates a guest before remote setup runs, so it cannot use
    # target-side detection. Keep the existing VM default for that path;
    # callers can select an LXC explicitly with --machine unprivileged.
    if is_hosted:
        return "vm"

    return DEFAULT_MACHINE_TYPE


def _default_machine_type_for_setup(
    system_type: str,
    *,
    is_build_server: bool = False,
) -> str:
    del system_type, is_build_server
    return DEFAULT_MACHINE_TYPE


def _validate_non_negative_int(name: str, value: int) -> int:
    if value < 0:
        raise ValueError(f"{name} must be non-negative")
    return value


def _optional_bool_arg(args: argparse.Namespace, name: str) -> Optional[bool]:
    value = getattr(args, name, None)
    return value if isinstance(value, bool) else None


def _optional_int_arg(args: argparse.Namespace, name: str) -> Optional[int]:
    value = getattr(args, name, None)
    return value if isinstance(value, int) else None


def _optional_str_arg(args: argparse.Namespace, name: str) -> Optional[str]:
    value = getattr(args, name, None)
    return value if isinstance(value, str) else None


def _normalize_nested_specs(value: NestedStrList | list[str] | None) -> Optional[NestedStrList]:
    if not value:
        return None

    if isinstance(value, list) and value and isinstance(value[0], str):
        normalized: NestedStrList = []
        normalized.append(cast(list[str], value))
        return normalized

    if isinstance(value, list):
        normalized: NestedStrList = []
        for spec in value:
            normalized.append(cast(list[str], spec))
        return normalized

    return None


def _vm_disk_setting_args(value: Optional[NestedStrList]) -> StrList:
    """Return per-device disk setting flags for a reconstructed setup."""

    args: StrList = []
    for spec in _normalize_nested_specs(value) or []:
        if len(spec) < 2:
            continue
        name = shlex.quote(spec[0])
        for option in spec[1:]:
            setting, separator, enabled = option.partition("=")
            if separator and setting in {"discard", "ssd", "backup"} and enabled in {
                "on",
                "off",
            }:
                prefix = "" if enabled == "on" else "no-"
                args.append(f"--{prefix}disk-{setting} {name}")
    return args


def _strip_passwords_from_share_users(users_field: str) -> str:
    sanitized_users: StrList = []
    for user_spec in users_field.split(','):
        normalized_user = user_spec.strip()
        if not normalized_user:
            continue
        if ':' in normalized_user:
            username, _ = normalized_user.split(':', 1)
            sanitized_users.append(username.strip())
        else:
            sanitized_users.append(normalized_user)
    return ','.join(sanitized_users)


def redact_share_user_passwords(users_field: str) -> str:
    redacted_users: StrList = []
    for user_spec in users_field.split(','):
        normalized_user = user_spec.strip()
        if not normalized_user:
            continue
        if ':' in normalized_user:
            username, _ = normalized_user.split(':', 1)
            redacted_users.append(f"{username.strip()}:[REDACTED]")
        else:
            redacted_users.append(normalized_user)
    return ','.join(redacted_users)


def _strip_passwords_from_samba_shares(value: Optional[NestedStrList]) -> Optional[NestedStrList]:
    if not value:
        return value

    sanitized_shares: NestedStrList = []
    for share_spec in value:
        sanitized_share = list(share_spec)
        if len(sanitized_share) >= 4:
            sanitized_share[3] = _strip_passwords_from_share_users(sanitized_share[3])
        sanitized_shares.append(sanitized_share)
    return sanitized_shares


def _strip_passwords_from_smb_mounts(value: Optional[NestedStrList]) -> Optional[NestedStrList]:
    if not value:
        return value

    sanitized_mounts: NestedStrList = []
    for mount_spec in value:
        sanitized_mount = list(mount_spec)
        if len(sanitized_mount) >= 3 and ':' in sanitized_mount[2]:
            username, _ = sanitized_mount[2].split(':', 1)
            sanitized_mount[2] = username.strip()
        sanitized_mounts.append(sanitized_mount)
    return sanitized_mounts


def redact_mount_credentials(credentials_field: str) -> str:
    if ':' not in credentials_field:
        return credentials_field
    username, _ = credentials_field.split(':', 1)
    return f"{username}:[REDACTED]"


@dataclass
class SetupConfig:
    """Configuration for system setup.
    
    Note on browser fields:
    - browser: The primary/default browser. If browsers list is set, this will be browsers[0]
    - browsers: Optional list of browsers to install. When set, browser is the first element
    """
    host: str
    username: str
    system_type: str
    machine_type: str = DEFAULT_MACHINE_TYPE
    password: MaybeStr = None
    nopasswd: bool = False
    harden_agent: bool = False
    harden_user: bool = False
    ssh_key: MaybeStr = None
    timezone: str = "UTC"
    system_hostname: MaybeStr = None
    enable_mdns: bool = False
    clear_mdns: bool = False
    static_ipv4: MaybeStr = None
    static_ipv6: MaybeStr = None
    network_gateway4: MaybeStr = None
    network_gateway6: MaybeStr = None
    network_dns: Optional[StrList] = None
    network_interface: MaybeStr = None
    activate_network: bool = False
    friendly_name: MaybeStr = None
    tags: Optional[StrList] = None
    access_sources: Optional[StrList] = None
    clear_access_sources: bool = False
    lan_access: bool = False
    clear_lan_access: bool = False
    enable_rdp: bool = False
    rdp_existing_password: bool = False
    rdp_bind_address: str = "0.0.0.0"
    rdp_allowed_sources: Optional[StrList] = None
    clear_rdp_sources: bool = False
    rdp_clipboard: bool = True
    rdp_drive_redirection: bool = False
    rdp_audio: bool = False
    rdp_idle_timeout: int = 0
    desktop: str = "xfce"
    browser: Optional[str] = "librewolf"  # Primary browser, or first from browsers list
    browsers: Optional[StrList] = None  # List of browsers to install
    editor: MaybeStr = None
    use_flatpak: bool = False
    install_office: bool = False
    apt_packages: Optional[StrList] = None
    flatpak_packages: Optional[StrList] = None
    dark_theme: bool = False
    dry_run: bool = False
    refresh_packages: bool = False
    install_go: bool = False
    install_node: bool = False
    install_python: bool = False
    install_data_analysis_tools: bool = False
    install_av_tools: bool = False
    install_gl_tools: bool = False
    install_godot: bool = False
    install_sunshine: bool = False
    install_moonlight: bool = False
    install_gaming: bool = False
    install_obs: bool = False
    install_blender: bool = False
    install_kdenlive: bool = False
    install_krita: bool = False
    install_inkscape: bool = False
    install_scribus: bool = False
    install_audacity: bool = False
    install_ardour: bool = False
    install_lmms: bool = False
    install_freecad: bool = False
    install_kicad: bool = False
    install_shotcut: bool = False
    install_gimp: bool = False
    install_remmina: bool = False
    install_sysadmin_tools: bool = False
    godot_bundles: Optional[StrList] = None
    install_gh: bool = False
    install_codex: bool = False
    install_claude: bool = False
    install_opencode: bool = False
    agent_tools: Optional[StrList] = None
    agent_tools_removed: Optional[StrList] = None
    web_interfaces: Optional[StrList] = None
    t3code_ready: bool = False
    disable_web_interface: bool = False
    web_interface_host: MaybeStr = None
    web_interface_port: int = 3773
    web_interface_sources: Optional[StrList] = None
    clear_web_interface_sources: bool = False
    web_ports: Optional[list[int]] = None
    default_web_ports: bool = True
    device_pairing_providers: Optional[StrList] = None
    device_pairing_port: int = 3774
    device_pairing_auth_file: MaybeStr = None
    device_pairing_auth_username: MaybeStr = None
    device_pairing_auth_password: MaybeStr = None
    device_pairing_payload: bool = False
    disable_device_pairing: bool = False
    web_panel_port: Optional[int] = None
    web_panel_auth_password: MaybeStr = None
    web_panel_payload: bool = False
    web_panel_notification_ingest: Optional[bool] = None
    disable_web_panel: bool = False
    browser_automation: MaybeStr = None
    disable_browser_automation: bool = False
    copy_agent_keys: bool = False
    copy_agent_config: bool = False
    agent_repos: Optional[StrList] = None
    git_access: str = "none"
    git_host: str = "github.com"
    git_auth_source: MaybeStr = None
    git_auth_file: MaybeStr = None
    git_auth_token: MaybeStr = None
    disable_git_auth: bool = False
    git_credentials: Optional[NestedStrList] = None
    git_ca_certificates: Optional[NestedStrList] = None
    git_ca_pems: Optional[NestedStrList] = None
    clear_git_credentials: bool = False
    agent_auth_source: MaybeStr = None
    agent_auth_files: Optional[NestedStrList] = None
    disable_agent_auth: bool = False
    agent_config_source: MaybeStr = None
    agent_payload: bool = False
    github_auth_payload: bool = False
    git_identity_payload: bool = False
    agent_workspace: MaybeStr = None
    install_git_lfs: bool = False
    custom_steps: Optional[str] = None
    deploy_specs: Optional[NestedStrList] = None
    deployment_mode: str = "default"  # "default" (smart cache), "lite" (cached only), "full" (always fresh)
    full_deploy: bool = False
    deploy_latest: bool = False
    enable_ssl: bool = False
    ssl_email: Optional[str] = None
    enable_cloudflare: bool = False
    enable_cicd: bool = False
    is_build_server: bool = False
    is_app_server: bool = False
    deploy_targets: Optional[StrList] = None
    enable_samba: bool = False
    samba_sources: Optional[StrList] = None
    clear_samba_sources: bool = False
    samba_metadata_cache: MaybeStr = None
    clear_samba_metadata_cache: bool = False
    samba_shares: Optional[NestedStrList] = None
    share_credentials: Optional[NestedStrList] = None
    enable_smbclient: bool = False
    smb_mounts: Optional[NestedStrList] = None
    enable_syncthing: bool = False
    disable_syncthing: bool = False
    syncthing_admin: MaybeStr = None
    syncthing_root: MaybeStr = None
    sync_specs: Optional[NestedStrList] = None
    backup_specs: Optional[NestedStrList] = None
    scrub_specs: Optional[NestedStrList] = None
    notify_specs: Optional[NestedStrList] = None
    notification_level: MaybeStr = None
    notification_strict_https: Optional[bool] = None
    antistatic_server: MaybeStr = None  # "DOMAIN[:port]" spec
    antistatic_admin: MaybeStr = None  # Username; password stays in the credential store
    antistatic_db: MaybeStr = None  # "DOMAIN[:port]" spec
    gogs: Optional[StrList] = None  # ["DOMAIN[:port]", "DATA_PATH"?]
    gogs_sources: Optional[StrList] = None
    homebox: Optional[StrList] = None  # [] explicitly disables the managed service
    homebox_version: MaybeStr = None
    homebox_admin: MaybeStr = None
    homebox_port: Optional[int] = None
    auto_restart: bool = True
    auto_restart_force_days: int = 7
    auto_restart_grace: int = 5
    proxmox_balloon_target: Optional[int] = None
    # Proxmox guest provisioning
    hosted_node: MaybeStr = None
    hosted_user: str = "root"
    hosted_key: MaybeStr = None
    hosted_bridge: MaybeStr = None
    container_memory: MaybeStr = None
    vm_balloon_min: MaybeStr = None
    vm_balloon_shares: int = 1000
    allow_memory_overcommit: bool = False
    container_storage: Optional[NestedStrList] = None  # [[name, pool, amount?], ...]
    storage_mounts: Optional[NestedStrList] = None  # [[name, path, filesystem?, policy?], ...]
    storage_caches: Optional[NestedStrList] = None  # [[data_name, cache_name, mode?], ...]
    swap_mode: str = "auto"
    swap_files: Optional[NestedStrList] = None  # [[name, path, size, priority=N?], ...]
    swap_devices: Optional[NestedStrList] = None  # [[name, source, priority=N?, discard=...?], ...]
    swap_zram: Optional[NestedStrList] = None  # [[name, size, priority=N?, algorithm=...?], ...]
    swappiness: Optional[int] = None
    zswap: Optional[bool] = None
    zswap_max_pool_percent: Optional[int] = None
    swap_resume: MaybeStr = None
    swap_initialize: Optional[StrList] = None  # One-shot destructive authorization
    container_cores: int = 1
    vm_cpu_type: str = "host"
    vm_disk_discard: bool = True
    vm_disk_ssd: bool = False
    vm_disk_backup: bool = True
    vm_disk_settings: Optional[NestedStrList] = None  # [[name, discard=on?, ssd=on?, backup=on?], ...]
    container_base: str = "debian"
    vm_image: MaybeStr = None  # HTTPS URL or 'storage:import/file.qcow2'
    vm_image_sha512: MaybeStr = None  # Required for custom HTTPS VM images
    vm_image_storage: MaybeStr = None  # Storage for downloaded VM image sources
    include_desktop: bool = False
    include_cli_tools: bool = False
    include_control_plane_tools: bool = False
    include_desktop_apps: bool = False
    include_workstation_dev_apps: bool = False
    include_pc_dev_apps: bool = False
    include_web_server: bool = False
    include_web_firewall: bool = False

    def __post_init__(self) -> None:
        # "full" deployment mode means "always pull fresh repositories and
        # rebuild everything" (a full redeploy). That necessarily implies
        # full_deploy, so keep the two in sync. Without this, passing
        # --deployment-full alone would still report "Full deploy: No" and
        # should_redeploy() could skip unchanged deployments, contradicting
        # the requested full redeploy.
        if self.deployment_mode == "full":
            self.full_deploy = True

        if self.harden_user:
            self.harden_agent = True
        if self.nopasswd and self.harden_agent:
            raise ValueError(
                "--nopasswd cannot be combined with --harden-agent or "
                "--harden-user"
            )
        if self.harden_agent and self.username == "root":
            flag = "--harden-user" if self.harden_user else "--harden-agent"
            raise ValueError(f"{flag} requires a non-root setup user")
        if self.harden_user and self.enable_rdp:
            raise ValueError(
                "--harden-user locks password authentication and cannot be "
                "combined with --rdp"
            )

        if self.install_data_analysis_tools:
            self.install_python = True

        cachyos_native_software = (
            self.install_sunshine,
            self.install_moonlight,
            self.install_gaming,
            self.install_obs,
            self.install_blender,
            self.install_kdenlive,
            self.install_krita,
            self.install_inkscape,
            self.install_scribus,
            self.install_audacity,
            self.install_ardour,
            self.install_lmms,
            self.install_freecad,
            self.install_kicad,
            self.install_shotcut,
            self.install_gimp,
            self.install_remmina,
            self.install_sysadmin_tools,
        )
        if self.system_type != "agent_cachyos" and any(cachyos_native_software):
            raise ValueError(
                "--gaming, --sunshine, --moonlight, --obs, --blender, "
                "--kdenlive, --krita, --inkscape, --scribus, --audacity, "
                "--ardour, --lmms, --freecad, --kicad, --shotcut, --gimp, "
                "--remmina, and --sysadmin-tools require the agent_cachyos profile"
            )

        if self.enable_syncthing and self.syncthing_admin is None:
            self.syncthing_admin = "syncthing-admin"

        if self.t3code_ready:
            if self.disable_web_interface:
                raise ValueError("--t3code-ready cannot be combined with --no-web-interface")
            ready_tools = list(self.agent_tools or [])
            removed_tools = set(self.agent_tools_removed or [])
            for tool in ("gh", "codex"):
                if tool not in removed_tools and tool not in ready_tools:
                    ready_tools.append(tool)
            self.agent_tools = ready_tools or None
            ready_interfaces = list(self.web_interfaces or [])
            if "t3code" not in ready_interfaces:
                ready_interfaces.append("t3code")
            self.web_interfaces = ready_interfaces
            if self.git_access == "none":
                self.git_access = "read-write"
            if not self.disable_device_pairing and not self.device_pairing_providers:
                self.device_pairing_providers = ["t3code"]

        from lib.validation import (
            validate_godot_bundle_settings,
            validate_notification_level,
            validate_syncthing_settings,
        )

        validate_godot_bundle_settings(self)
        validate_notification_level(self.notification_level)
        if self.notification_strict_https is not None and not isinstance(
            self.notification_strict_https, bool
        ):
            raise ValueError("notification_strict_https must be boolean")
        validate_syncthing_settings(self)
        selected_godot_bundles = list(dict.fromkeys(self.godot_bundles or []))
        self.godot_bundles = selected_godot_bundles or None
        if self.godot_bundles:
            self.install_godot = True

        if self.git_access not in GIT_ACCESS_POLICIES:
            raise ValueError(
                f"git_access must be one of: {', '.join(GIT_ACCESS_POLICIES)}"
            )
        if not isinstance(self.git_host, str) or not self.git_host or any(
            character.isspace() or ord(character) < 32 for character in self.git_host
        ):
            raise ValueError("git_host must be a non-empty hostname")

        from lib.git_credentials import (
            normalize_git_ca_source,
            normalize_git_https_origin,
        )

        for specs in (
            self.git_credentials,
            self.git_ca_certificates,
            self.git_ca_pems,
        ):
            for spec in specs or []:
                if len(spec) == 2 and isinstance(spec[0], str):
                    spec[0] = normalize_git_https_origin(spec[0])
        for spec in self.git_ca_certificates or []:
            if len(spec) == 2 and isinstance(spec[1], str):
                spec[1] = normalize_git_ca_source(spec[1])

        if self.editor is not None:
            if self.editor not in EDITORS:
                raise ValueError(f"editor must be one of: {', '.join(EDITORS)}")
            if not self.include_desktop:
                raise ValueError(
                    "--editor requires a desktop-capable setup or --rdp"
                )

        selected = list(self.agent_tools or [])
        for tool in selected:
            if tool not in AGENT_TOOLS:
                raise ValueError(f"Unsupported agent tool: {tool}")
        selected = list(dict.fromkeys(selected))
        removed_tools = list(dict.fromkeys(self.agent_tools_removed or []))
        for tool in removed_tools:
            if tool not in AGENT_TOOLS:
                raise ValueError(f"Unsupported removed agent tool: {tool}")
        selected = [tool for tool in selected if tool not in removed_tools]
        self.agent_tools = selected or None
        self.agent_tools_removed = removed_tools or None
        self.install_gh = "gh" in selected or self.install_gh
        self.install_codex = "codex" in selected or self.install_codex
        self.install_claude = "claude" in selected or self.install_claude
        self.install_opencode = "opencode" in selected or self.install_opencode
        web_interfaces = list(dict.fromkeys(self.web_interfaces or []))
        for interface in web_interfaces:
            if interface not in WEB_INTERFACES:
                raise ValueError(f"Unsupported web interface: {interface}")
        self.web_interfaces = web_interfaces or None
        if self.clear_access_sources:
            self.access_sources = None
        if self.lan_access:
            self.clear_lan_access = False
        elif self.clear_lan_access:
            self.lan_access = False
        if self.enable_mdns:
            self.clear_mdns = False
        elif self.clear_mdns:
            self.enable_mdns = False
        if self.disable_web_interface:
            self.web_interfaces = None
            self.web_interface_sources = None
            self.clear_web_interface_sources = False
        if self.clear_web_interface_sources:
            self.web_interface_sources = None
        if self.clear_rdp_sources:
            self.rdp_allowed_sources = None
        if self.clear_samba_sources:
            self.samba_sources = None
        if self.clear_samba_metadata_cache:
            self.samba_metadata_cache = None
        if self.clear_git_credentials:
            self.git_credentials = None
            self.git_ca_certificates = None
            self.git_ca_pems = None
        if self.disable_browser_automation:
            self.browser_automation = None
        pairing_providers = list(dict.fromkeys(self.device_pairing_providers or []))
        if self.disable_device_pairing:
            pairing_providers = []
            self.device_pairing_port = 3774
        for provider in pairing_providers:
            if provider not in DEVICE_PAIRING_PROVIDERS:
                raise ValueError(f"Unsupported device pairing provider: {provider}")
        self.device_pairing_providers = pairing_providers or None
        if self.disable_web_panel:
            self.web_panel_port = None
            self.web_panel_payload = False
            self.web_panel_notification_ingest = False
        if (
            self.device_pairing_providers
            and self.device_pairing_auth_password is not None
            and not self.device_pairing_auth_file
            and not self.device_pairing_auth_username
        ):
            self.device_pairing_auth_username = self.username
        if self.web_interfaces:
            if not self.install_codex and not self.install_claude and not self.install_opencode:
                raise ValueError(
                    "T3 Code requires at least one provider CLI: "
                    "--agent-tool codex, claude, or opencode"
                )
        if self.web_interfaces:
            if self.web_interface_host is None:
                self.web_interface_host = (
                    "0.0.0.0"
                    if self.effective_web_interface_sources()
                    else "127.0.0.1"
                )
            if not 1 <= self.web_interface_port <= 65535:
                raise ValueError("web_interface_port must be between 1 and 65535")
            # T3 Code's headless server requires the Node runtime even when
            # the operator did not select --node separately.
            self.install_node = True
        if self.device_pairing_providers:
            if not 1 <= self.device_pairing_port <= 65535:
                raise ValueError("device_pairing_port must be between 1 and 65535")
            if self.device_pairing_port == self.web_interface_port:
                raise ValueError(
                    "device_pairing_port must differ from web_interface_port"
                )

        from lib.validation import (
            validate_web_panel_settings,
            validate_web_port_settings,
        )

        validate_web_port_settings(self)
        validate_web_panel_settings(self)
        self.web_ports = list(dict.fromkeys(self.web_ports or [])) or None

    def selected_agent_tools(self) -> StrList:
        """Return selected coding agents in stable display/install order."""
        tools: StrList = []
        for name, enabled in (
            ("gh", self.install_gh),
            ("codex", self.install_codex),
            ("claude", self.install_claude),
            ("opencode", self.install_opencode),
        ):
            if enabled:
                tools.append(name)
        return tools

    def has_agent_features(self) -> bool:
        """Return whether this setup declares an agent-oriented workload."""

        return bool(
            self.selected_agent_tools()
            or self.web_interfaces
            or self.browser_automation
            or self.agent_repos
            or self.git_access != "none"
            or self.agent_workspace
            or self.install_git_lfs
        )

    def effective_web_ports(self) -> list[int]:
        """Return managed TCP web ports for this resolved target."""

        ports = list(self.web_ports or [])
        if self.homebox:
            from lib.homebox_config import parse_homebox_spec

            homebox_domain, homebox_port = parse_homebox_spec(self.homebox[0])
            if homebox_domain:
                ports.extend((80, homebox_port))
        if "t3code" in (self.web_interfaces or []):
            # The shared internal HTTPS host serves the VM CA and landing page
            # used by the managed T3 HTTPS forwards.
            ports.append(GODOT_WEB_HTTPS_PORT)
        if "web" in (self.godot_bundles or []):
            ports.append(GODOT_WEB_HTTPS_PORT)
        if self.web_panel_port is not None:
            ports.append(self.web_panel_port)
            if self.enable_ssl:
                # HTTPS panel certificates and client enrollment use the
                # shared per-machine internal web origin.
                ports.append(GODOT_WEB_HTTPS_PORT)
        if self.include_web_firewall:
            ports.extend((80, 443))
        if (
            self.default_web_ports
            and self.machine_type == "vm"
            and self.system_type != "server_lite"
            and self.has_agent_features()
        ):
            ports.extend(DEFAULT_AGENT_WEB_PORTS)
        return sorted(set(ports))

    def effective_access_sources(self) -> StrList:
        """Return generic sources, including inferred target-adjacent LANs."""

        lan_sources = self.inferred_lan_access_sources() if self.lan_access else None
        return _merge_network_sources(lan_sources, self.access_sources)

    def inferred_lan_access_sources(self) -> StrList:
        """Infer trusted LANs from explicit target addressing and resolution."""

        declared_sources = [
            source
            for value in (self.static_ipv4, self.static_ipv6)
            if value and (source := _lan_network_from_address(value)) is not None
        ]
        return _merge_network_sources(
            declared_sources,
            list(_resolved_lan_networks(self.host)),
        )

    def effective_rdp_sources(self) -> StrList:
        """Return generic sources plus RDP-specific additions."""

        return _merge_network_sources(
            self.effective_access_sources(),
            self.rdp_allowed_sources,
        )

    def effective_web_interface_sources(self) -> StrList:
        """Return generic sources plus web-interface-specific additions."""

        return _merge_network_sources(
            self.effective_access_sources(),
            self.web_interface_sources,
        )

    def effective_gogs_sources(self) -> StrList:
        """Return generic sources plus direct-Gogs-specific additions."""

        return _merge_network_sources(
            self.effective_access_sources(),
            self.gogs_sources,
        )

    def effective_samba_sources(self) -> StrList:
        """Return generic sources plus Samba-specific additions."""

        return _merge_network_sources(
            self.effective_access_sources(),
            self.samba_sources,
        )

    def _swap_args(self, *, include_initialize: bool = False) -> StrList:
        """Return the complete target-side swap policy as CLI fragments."""

        args: StrList = [f"--swap-mode {shlex.quote(self.swap_mode)}"]
        for flag, specs in (
            ("--swap-file", self.swap_files),
            ("--swap-device", self.swap_devices),
            ("--swap-zram", self.swap_zram),
        ):
            for spec in _normalize_nested_specs(specs) or []:
                args.append(
                    f"{flag} "
                    + " ".join(shlex.quote(str(part)) for part in spec)
                )
        if self.swappiness is not None:
            args.append(f"--swappiness {self.swappiness}")
        if self.zswap is not None:
            args.append("--zswap" if self.zswap else "--no-zswap")
        if self.zswap_max_pool_percent is not None:
            args.append(
                f"--zswap-max-pool-percent {self.zswap_max_pool_percent}"
            )
        if self.swap_resume == "":
            args.append("--no-swap-resume")
        elif self.swap_resume:
            args.append(f"--swap-resume {shlex.quote(self.swap_resume)}")
        if include_initialize:
            for name in self.swap_initialize or []:
                args.append(f"--swap-initialize {shlex.quote(name)}")
        return args

    def to_remote_args(self) -> StrList:
        """Generate command line arguments for remote execution."""
        args: StrList = []
        args.extend(self._homebox_args())

        if self.t3code_ready:
            args.append("--t3code-ready")
        
        args.append(f"--system-type {shlex.quote(self.system_type)}")
        args.append(f"--username {shlex.quote(self.username)}")
        args.append(f"--machine {shlex.quote(self.machine_type)}")
        for storage_spec in _normalize_nested_specs(self.container_storage) or []:
            if not storage_spec or storage_spec[0] in {"root", "template"}:
                continue
            escaped_spec = " ".join(shlex.quote(str(part)) for part in storage_spec)
            args.append(f"--storage {escaped_spec}")
        for mount_spec in _normalize_nested_specs(self.storage_mounts) or []:
            escaped_spec = " ".join(shlex.quote(str(part)) for part in mount_spec)
            args.append(f"--storage-mount {escaped_spec}")
        for cache_spec in _normalize_nested_specs(self.storage_caches) or []:
            escaped_spec = " ".join(shlex.quote(str(part)) for part in cache_spec)
            args.append(f"--storage-cache {escaped_spec}")
        args.extend(self._swap_args(include_initialize=True))
        if self.include_control_plane_tools:
            args.append("--control-plane")
        
        if self.password:
            args.append(f"--password {shlex.quote(self.password)}")
        if self.nopasswd:
            args.append("--nopasswd")
        if self.harden_user:
            args.append("--harden-user")
        elif self.harden_agent:
            args.append("--harden-agent")
        
        if self.timezone:
            args.append(f"--timezone {shlex.quote(self.timezone)}")

        if self.system_hostname:
            args.append(f"--hostname {shlex.quote(self.system_hostname)}")

        if self.enable_mdns:
            args.append("--mdns")
        elif self.clear_mdns:
            args.append("--no-mdns")

        if self.static_ipv4:
            args.append(f"--ip {shlex.quote(self.static_ipv4)}")

        if self.static_ipv6:
            args.append(f"--ipv6 {shlex.quote(self.static_ipv6)}")

        if self.network_gateway4:
            args.append(f"--gateway {shlex.quote(self.network_gateway4)}")

        if self.network_gateway6:
            args.append(f"--gateway6 {shlex.quote(self.network_gateway6)}")

        for dns_server in self.network_dns or []:
            args.append(f"--dns {shlex.quote(dns_server)}")

        if self.network_interface:
            args.append(f"--network-interface {shlex.quote(self.network_interface)}")

        if self.activate_network:
            args.append("--activate-network")
        
        if self.friendly_name:
            args.append(f"--name {shlex.quote(self.friendly_name)}")

        if self.lan_access:
            lan_sources = self.inferred_lan_access_sources()
            if not lan_sources:
                raise ValueError(
                    "--lan-access could not infer a private target subnet; "
                    "remove it and use --access-source explicitly"
                )
            for source in _merge_network_sources(lan_sources, self.access_sources):
                args.append(f"--access-source {shlex.quote(source)}")
        else:
            if self.clear_lan_access:
                args.append("--no-lan-access")
            for source in self.access_sources or []:
                args.append(f"--access-source {shlex.quote(source)}")
        if self.clear_access_sources and not self.lan_access:
            args.append("--no-access-source")
        
        if self.enable_rdp:
            args.append("--rdp")
            if self.rdp_existing_password:
                args.append("--rdp-existing-password")
            args.append(f"--rdp-bind-address {shlex.quote(self.rdp_bind_address)}")
            for source in self.rdp_allowed_sources or []:
                args.append(f"--rdp-source {shlex.quote(source)}")
            if not self.rdp_clipboard:
                args.append("--no-rdp-clipboard")
            if self.rdp_drive_redirection:
                args.append("--rdp-drive-redirection")
            if self.rdp_audio:
                args.append("--rdp-audio")
            args.append(f"--rdp-idle-timeout {self.rdp_idle_timeout}")
        
        if self.desktop and (self.include_desktop or self.enable_rdp):
            args.append(f"--desktop {shlex.quote(self.desktop)}")
        
        # Send browsers - only use browsers list if available, otherwise use browser
        if self.browsers:
            for browser in self.browsers:
                args.append(f"--browser {shlex.quote(browser)}")
        elif self.browser:
            args.append(f"--browser {shlex.quote(self.browser)}")

        if self.editor:
            args.append(f"--editor {shlex.quote(self.editor)}")
        
        if self.use_flatpak:
            args.append("--flatpak")
        
        if self.install_office:
            args.append("--office")
        
        if self.apt_packages:
            for package in self.apt_packages:
                args.append(f"--apt-install {shlex.quote(package)}")
        
        if self.flatpak_packages:
            for package in self.flatpak_packages:
                args.append(f"--flatpak-install {shlex.quote(package)}")
        
        if self.dark_theme:
            args.append("--dark")

        if self.refresh_packages:
            args.append("--refresh-packages")
        
        if self.dry_run:
            args.append("--dry-run")
        
        if self.install_go:
            args.append("--go")
        
        if self.install_node:
            args.append("--node")
        
        if self.install_python:
            args.append("--python")

        if self.install_data_analysis_tools:
            args.append("--data-analysis")

        if self.install_av_tools:
            args.append("--av-tools")

        if self.install_gl_tools:
            args.append("--gl-tools")

        if self.install_godot:
            args.append("--godot")
        if self.install_sunshine:
            args.append("--sunshine")
        if self.install_moonlight:
            args.append("--moonlight")
        if self.install_gaming:
            args.append("--gaming")
        if self.install_obs:
            args.append("--obs")
        if self.install_blender:
            args.append("--blender")
        if self.install_kdenlive:
            args.append("--kdenlive")
        if self.install_krita:
            args.append("--krita")
        if self.install_inkscape:
            args.append("--inkscape")
        if self.install_scribus:
            args.append("--scribus")
        if self.install_audacity:
            args.append("--audacity")
        if self.install_ardour:
            args.append("--ardour")
        if self.install_lmms:
            args.append("--lmms")
        if self.install_freecad:
            args.append("--freecad")
        if self.install_kicad:
            args.append("--kicad")
        if self.install_shotcut:
            args.append("--shotcut")
        if self.install_gimp:
            args.append("--gimp")
        if self.install_remmina:
            args.append("--remmina")
        if self.install_sysadmin_tools:
            args.append("--sysadmin-tools")
        for bundle in self.godot_bundles or []:
            args.append(f"--godot-bundle {shlex.quote(bundle)}")

        for tool in self.selected_agent_tools():
            args.append(f"--agent-tool {shlex.quote(tool)}")

        for interface in self.web_interfaces or []:
            args.append(f"--web-interface {shlex.quote(interface)}")
        if self.web_interfaces:
            args.append(
                f"--web-interface-host {shlex.quote(self.web_interface_host or '127.0.0.1')}"
            )
            args.append(f"--web-interface-port {self.web_interface_port}")
            for source in self.web_interface_sources or []:
                args.append(f"--web-interface-source {shlex.quote(source)}")
        for port in self.web_ports or []:
            args.append(f"--web-port {port}")
        if not self.default_web_ports:
            args.append("--no-default-web-ports")
        for provider in self.device_pairing_providers or []:
            args.append(f"--device-pairing {shlex.quote(provider)}")
        if self.device_pairing_providers:
            args.append(f"--device-pairing-port {self.device_pairing_port}")
        if self.device_pairing_payload:
            args.append("--device-pairing-payload")
        if self.disable_web_panel:
            args.append("--no-web-panel")
        elif self.web_panel_port is not None:
            args.append(f"--web-panel {self.web_panel_port}")
            if self.web_panel_payload:
                args.append("--web-panel-payload")
            if self.web_panel_notification_ingest is True:
                args.append("--web-panel-notification-ingest")

        if self.browser_automation:
            args.append(
                f"--browser-automation {shlex.quote(self.browser_automation)}"
            )

        if self.git_access != "none":
            args.append(f"--git-access {shlex.quote(self.git_access)}")
        if self.git_host != "github.com":
            args.append(f"--git-host {shlex.quote(self.git_host)}")
        for origin, username in self.git_credentials or []:
            args.append(
                "--git-credential "
                f"{shlex.quote(origin)} {shlex.quote(username)}"
            )
        for origin, encoded_pem in self.git_ca_pems or []:
            args.append(
                "--git-ca-pem "
                f"{shlex.quote(origin)} {shlex.quote(encoded_pem)}"
            )
        if self.clear_git_credentials:
            args.append("--no-git-credentials")
        if self.copy_agent_config or self.copy_agent_keys or self.agent_payload:
            args.append("--agent-payload")
        if self.github_auth_payload:
            args.append("--github-auth-payload")
        if self.git_identity_payload:
            args.append("--git-identity-payload")

        if self.agent_repos:
            for git_url in self.agent_repos:
                args.append(f"--repo {shlex.quote(git_url)}")
        if self.agent_workspace:
            args.append(f"--agent-workspace {shlex.quote(self.agent_workspace)}")
        if self.install_git_lfs:
            args.append("--git-lfs")
        
        if self.custom_steps:
            args.append(f"--steps {shlex.quote(self.custom_steps)}")
        
        if self.deploy_specs:
            if self.deployment_mode == "lite":
                args.append("--deployment-lite")
            elif self.deployment_mode == "full":
                # --deployment-full already implies a full rebuild on the remote
                args.append("--deployment-full")
            elif self.full_deploy:
                args.append("--full-deploy")
            # Use --deploy-latest for each spec if deploy_latest is set, otherwise --deploy
            flag = "--deploy-latest" if self.deploy_latest else "--deploy"
            for deploy_spec, git_url in self.deploy_specs:
                args.append(f"{flag} {shlex.quote(deploy_spec)} {shlex.quote(git_url)}")
        elif self.deploy_latest:
            # deploy_latest without specs doesn't make sense, but keep for backward compat
            args.append("--deploy-latest")
        
        if self.enable_ssl:
            args.append("--ssl")
            if self.ssl_email:
                args.append(f"--ssl-email {shlex.quote(self.ssl_email)}")
        
        if self.enable_cloudflare:
            args.append("--cloudflare")
        
        if self.enable_cicd:
            args.append("--cicd")
        
        if self.is_build_server:
            args.append("--build-server")
        
        if self.is_app_server:
            args.append("--app-server")
        
        if self.deploy_targets:
            for target in self.deploy_targets:
                args.append(f"--deploy-target {shlex.quote(target)}")
        
        if self.enable_samba:
            args.append("--samba")
        if self.clear_samba_sources:
            args.append("--no-samba-source")
        else:
            for source in self.samba_sources or []:
                args.append(f"--samba-source {shlex.quote(source)}")
        if self.clear_samba_metadata_cache:
            args.append("--no-samba-metadata-cache")
        elif self.samba_metadata_cache:
            args.append(
                "--samba-metadata-cache "
                f"{shlex.quote(self.samba_metadata_cache)}"
            )
        
        if self.samba_shares:
            for share_spec in self.samba_shares:
                escaped_spec = ' '.join(shlex.quote(str(s)) for s in share_spec)
                args.append(f"--share {escaped_spec}")

        if self.share_credentials:
            for credential_spec in self.share_credentials:
                escaped_spec = ' '.join(shlex.quote(str(s)) for s in credential_spec)
                args.append(f"--credential {escaped_spec}")
        
        if self.enable_smbclient:
            args.append("--smbclient")
        
        if self.smb_mounts:
            for mount_spec in self.smb_mounts:
                escaped_spec = ' '.join(shlex.quote(str(s)) for s in mount_spec)
                args.append(f"--mount-smb {escaped_spec}")

        if self.disable_syncthing:
            args.append("--no-syncthing")
            if self.syncthing_root:
                args.append(
                    f"--syncthing-root {shlex.quote(self.syncthing_root)}"
                )
        elif self.enable_syncthing:
            args.append("--syncthing")
            args.append(
                f"--syncthing-admin {shlex.quote(self.syncthing_admin or '')}"
            )
            args.append(
                "--syncthing-root "
                f"{shlex.quote(self.syncthing_root or DEFAULT_SYNCTHING_ROOT)}"
            )
        
        if self.sync_specs:
            for sync_spec in self.sync_specs:
                escaped_spec = ' '.join(shlex.quote(str(s)) for s in sync_spec)
                args.append(f"--sync {escaped_spec}")

        if self.backup_specs:
            for backup_spec in self.backup_specs:
                escaped_spec = ' '.join(shlex.quote(str(s)) for s in backup_spec)
                args.append(f"--backup {escaped_spec}")
        
        if self.scrub_specs:
            for scrub_spec in self.scrub_specs:
                escaped_spec = ' '.join(shlex.quote(str(s)) for s in scrub_spec)
                args.append(f"--scrub {escaped_spec}")
        
        if self.notify_specs:
            for notify_spec in self.notify_specs:
                escaped_spec = ' '.join(shlex.quote(str(s)) for s in notify_spec)
                args.append(f"--notify {escaped_spec}")
        if self.notification_level is not None:
            args.append(
                f"--notification-level {shlex.quote(self.notification_level)}"
            )
        if self.notification_strict_https is True:
            args.append("--notification-strict-https")
        
        if self.antistatic_server:
            args.append(f"--antistatic-server {shlex.quote(self.antistatic_server)}")

        if self.antistatic_admin:
            args.append(f"--antistatic-admin {shlex.quote(self.antistatic_admin)}")

        if self.antistatic_db:
            args.append(f"--antistatic-db {shlex.quote(self.antistatic_db)}")

        if self.gogs:
            escaped_gogs = " ".join(shlex.quote(str(part)) for part in self.gogs)
            args.append(f"--gogs {escaped_gogs}")
        for source in self.gogs_sources or []:
            args.append(f"--gogs-source {shlex.quote(source)}")
        
        if self.auto_restart:
            args.append("--auto-restart")
        else:
            args.append("--no-auto-restart")
        args.append(f"--auto-restart-force-days {self.auto_restart_force_days}")
        args.append(f"--auto-restart-grace {self.auto_restart_grace}")
        if self.proxmox_balloon_target is not None:
            args.append(f"--proxmox-balloon-target {self.proxmox_balloon_target}")
                
        return args
    
    def to_setup_command(self, include_username: bool = True) -> StrList:
        """Generate command line for the unified setup entry point.
        
        Returns a list of command parts that can be joined with spaces or newlines.
        """
        setup_host = self.host
        provisioned_ipv4 = False
        if self.hosted_node and self.static_ipv4:
            try:
                guest_interface = ipaddress.ip_interface(self.static_ipv4)
            except ValueError:
                guest_interface = None
            if isinstance(guest_interface, ipaddress.IPv4Interface):
                provisioned_ipv4 = True
                setup_host = str(guest_interface.ip)
                if guest_interface.network.prefixlen != 24:
                    setup_host = str(guest_interface)

        cmd_parts: StrList = [
            f"infra-tools setup {shlex.quote(self.system_type)}",
            shlex.quote(setup_host),
        ]

        if self.t3code_ready:
            cmd_parts.append("--t3code-ready")
        
        # Add username if different from current user or if requested
        if include_username:
            cmd_parts.append(self.username)
        cmd_parts.extend(self._homebox_args())
        
        # SSH key
        if self.ssh_key:
            cmd_parts.append(f"-k {shlex.quote(self.ssh_key)}")

        if self.nopasswd:
            cmd_parts.append("--nopasswd")
        if self.harden_user:
            cmd_parts.append("--harden-user")
        elif self.harden_agent:
            cmd_parts.append("--harden-agent")

        if self.hosted_node:
            cmd_parts.append(f"--provision-on {shlex.quote(self.hosted_node)}")
            if self.hosted_user != "root":
                cmd_parts.append(f"--provision-user {shlex.quote(self.hosted_user)}")
            if self.hosted_key and self.hosted_key != self.ssh_key:
                cmd_parts.append(f"--provision-key {shlex.quote(self.hosted_key)}")
            if self.hosted_bridge:
                cmd_parts.append(f"--bridge {shlex.quote(self.hosted_bridge)}")
            if self.container_memory:
                cmd_parts.append(f"--memory {shlex.quote(self.container_memory)}")
            if self.vm_balloon_min:
                cmd_parts.append(f"--balloon-min {shlex.quote(self.vm_balloon_min)}")
            if self.vm_balloon_shares != 1000:
                cmd_parts.append(f"--balloon-shares {self.vm_balloon_shares}")
            if self.allow_memory_overcommit:
                cmd_parts.append("--allow-memory-overcommit")
            for storage_spec in _normalize_nested_specs(self.container_storage) or []:
                escaped_spec = " ".join(shlex.quote(str(part)) for part in storage_spec)
                cmd_parts.append(f"--storage {escaped_spec}")
            for mount_spec in _normalize_nested_specs(self.storage_mounts) or []:
                escaped_spec = " ".join(shlex.quote(str(part)) for part in mount_spec)
                cmd_parts.append(f"--storage-mount {escaped_spec}")
            for cache_spec in _normalize_nested_specs(self.storage_caches) or []:
                escaped_spec = " ".join(shlex.quote(str(part)) for part in cache_spec)
                cmd_parts.append(f"--storage-cache {escaped_spec}")
            if self.container_cores != 1:
                cmd_parts.append(f"--cores {self.container_cores}")
            if self.vm_cpu_type != "host":
                cmd_parts.append(f"--cpu-type {shlex.quote(self.vm_cpu_type)}")
            if not self.vm_disk_discard:
                cmd_parts.append("--no-disk-discard")
            if self.vm_disk_ssd:
                cmd_parts.append("--disk-ssd")
            if not self.vm_disk_backup:
                cmd_parts.append("--no-disk-backup")
            cmd_parts.extend(_vm_disk_setting_args(self.vm_disk_settings))
            if self.container_base != "debian":
                cmd_parts.append(f"--base {shlex.quote(self.container_base)}")
            if self.vm_image:
                cmd_parts.append(f"--image {shlex.quote(self.vm_image)}")
            if self.vm_image_sha512:
                cmd_parts.append(
                    f"--image-sha512 {shlex.quote(self.vm_image_sha512)}"
                )
            if self.vm_image_storage:
                cmd_parts.append(
                    f"--image-storage {shlex.quote(self.vm_image_storage)}"
                )

        cmd_parts.extend(self._swap_args())

        if self.proxmox_balloon_target is not None:
            cmd_parts.append(
                f"--proxmox-balloon-target {self.proxmox_balloon_target}"
            )
        
        # Password is intentionally not included in the command line for security reasons.
        # If a password is required, it should be provided interactively or via a secure
        # mechanism instead of as a command-line argument.
        
        # Timezone
        if self.timezone and self.timezone != "UTC":
            cmd_parts.append(f"-t {shlex.quote(self.timezone)}")

        if self.system_hostname:
            cmd_parts.append(f"--hostname {shlex.quote(self.system_hostname)}")

        if self.enable_mdns:
            cmd_parts.append("--mdns")
        elif self.clear_mdns:
            cmd_parts.append("--no-mdns")

        if self.static_ipv4 and not provisioned_ipv4:
            cmd_parts.append(f"--ip {shlex.quote(self.static_ipv4)}")

        if self.static_ipv6:
            cmd_parts.append(f"--ipv6 {shlex.quote(self.static_ipv6)}")

        if self.network_gateway4:
            cmd_parts.append(f"--gateway {shlex.quote(self.network_gateway4)}")

        if self.network_gateway6:
            cmd_parts.append(f"--gateway6 {shlex.quote(self.network_gateway6)}")

        for dns_server in self.network_dns or []:
            cmd_parts.append(f"--dns {shlex.quote(dns_server)}")

        if self.network_interface:
            cmd_parts.append(f"--network-interface {shlex.quote(self.network_interface)}")

        if self.activate_network:
            cmd_parts.append("--activate-network")
        
        # Machine type (if not the current setup default for this flow)
        default_machine_type = (
            "vm"
            if self.hosted_node
            else _default_machine_type_for_setup(
                self.system_type,
                is_build_server=self.is_build_server,
            )
        )
        if self.machine_type != default_machine_type:
            cmd_parts.append(f"--machine {shlex.quote(self.machine_type)}")

        system_type_defaults = get_system_type_definition(self.system_type)
        if (
            self.include_control_plane_tools
            and not system_type_defaults.include_control_plane_tools
        ):
            cmd_parts.append("--control-plane")
        
        # Name and tags
        if self.friendly_name:
            cmd_parts.append(f"--name {shlex.quote(self.friendly_name)}")
        
        if self.tags and len(self.tags) > 0:
            cmd_parts.append(f"--tags {shlex.quote(','.join(self.tags))}")

        if self.lan_access:
            cmd_parts.append("--lan-access")
        elif self.clear_lan_access:
            cmd_parts.append("--no-lan-access")
        for source in self.access_sources or []:
            cmd_parts.append(f"--access-source {shlex.quote(source)}")
        if self.clear_access_sources:
            cmd_parts.append("--no-access-source")
        
        # Desktop/workstation flags
        if self.enable_rdp != system_type_defaults.default_enable_rdp:
            cmd_parts.append("--rdp" if self.enable_rdp else "--no-rdp")
        if self.enable_rdp:
            if self.rdp_existing_password:
                cmd_parts.append("--rdp-existing-password")
            if self.rdp_bind_address != "0.0.0.0":
                cmd_parts.append(
                    f"--rdp-bind-address {shlex.quote(self.rdp_bind_address)}"
                )
            if self.clear_rdp_sources:
                cmd_parts.append("--no-rdp-source")
            else:
                for source in self.rdp_allowed_sources or []:
                    cmd_parts.append(f"--rdp-source {shlex.quote(source)}")
            if not self.rdp_clipboard:
                cmd_parts.append("--no-rdp-clipboard")
            if self.rdp_drive_redirection:
                cmd_parts.append("--rdp-drive-redirection")
            if self.rdp_audio:
                cmd_parts.append("--rdp-audio")
            if self.rdp_idle_timeout != 0:
                cmd_parts.append(f"--rdp-idle-timeout {self.rdp_idle_timeout}")
        
        if self.desktop and (self.include_desktop or self.enable_rdp) and (
            self.desktop != "xfce" or self.system_type not in DESKTOP_SYSTEMS
        ):
            cmd_parts.append(f"--desktop {shlex.quote(self.desktop)}")
        
        # Only include browser args if not default or if using multiple browsers
        if self.browsers:
            for browser in self.browsers:
                cmd_parts.append(f"--browser {shlex.quote(browser)}")
        elif self.browser and self.browser != (
            system_type_defaults.default_browser or "librewolf"
        ):
            cmd_parts.append(f"--browser {shlex.quote(self.browser)}")

        if self.editor and self.editor != system_type_defaults.default_editor:
            cmd_parts.append(f"--editor {shlex.quote(self.editor)}")
        
        if self.use_flatpak:
            cmd_parts.append("--flatpak")
        
        if self.install_office:
            cmd_parts.append("--office")
        
        if self.apt_packages:
            for package in self.apt_packages:
                cmd_parts.append(f"--apt-install {shlex.quote(package)}")
        
        if self.flatpak_packages:
            for package in self.flatpak_packages:
                cmd_parts.append(f"--flatpak-install {shlex.quote(package)}")
        
        if self.dark_theme:
            cmd_parts.append("--dark")

        # Development tools
        if self.install_go:
            cmd_parts.append("--go")
        
        if self.install_node:
            cmd_parts.append("--node")
        
        if self.install_python:
            cmd_parts.append("--python")

        if self.install_data_analysis_tools:
            cmd_parts.append("--data-analysis")

        if self.install_av_tools:
            cmd_parts.append("--av-tools")

        if self.install_gl_tools:
            cmd_parts.append("--gl-tools")

        if self.install_godot:
            cmd_parts.append("--godot")
        if self.install_sunshine:
            cmd_parts.append("--sunshine")
        if self.install_moonlight:
            cmd_parts.append("--moonlight")
        if self.install_gaming:
            cmd_parts.append("--gaming")
        if self.install_obs:
            cmd_parts.append("--obs")
        if self.install_blender:
            cmd_parts.append("--blender")
        if self.install_kdenlive:
            cmd_parts.append("--kdenlive")
        if self.install_krita:
            cmd_parts.append("--krita")
        if self.install_inkscape:
            cmd_parts.append("--inkscape")
        if self.install_scribus:
            cmd_parts.append("--scribus")
        if self.install_audacity:
            cmd_parts.append("--audacity")
        if self.install_ardour:
            cmd_parts.append("--ardour")
        if self.install_lmms:
            cmd_parts.append("--lmms")
        if self.install_freecad:
            cmd_parts.append("--freecad")
        if self.install_kicad:
            cmd_parts.append("--kicad")
        if self.install_shotcut:
            cmd_parts.append("--shotcut")
        if self.install_gimp:
            cmd_parts.append("--gimp")
        if self.install_remmina:
            cmd_parts.append("--remmina")
        if self.install_sysadmin_tools:
            cmd_parts.append("--sysadmin-tools")
        for bundle in self.godot_bundles or []:
            cmd_parts.append(f"--godot-bundle {shlex.quote(bundle)}")

        selected_agent_tools = self.selected_agent_tools()
        default_agent_tools = list(system_type_defaults.default_agent_tools)
        for tool in selected_agent_tools:
            if tool not in default_agent_tools:
                cmd_parts.append(f"--agent-tool {shlex.quote(tool)}")
        for tool in self.agent_tools_removed or []:
            if tool in default_agent_tools:
                cmd_parts.append(f"--no-agent-tool {shlex.quote(tool)}")

        web_interfaces = self.web_interfaces or []
        web_interfaces_are_default = web_interfaces == list(
            system_type_defaults.default_web_interfaces
        )
        if self.disable_web_interface:
            cmd_parts.append("--no-web-interface")
        elif not web_interfaces_are_default:
            for interface in web_interfaces:
                cmd_parts.append(f"--web-interface {shlex.quote(interface)}")
        if self.web_interfaces:
            inferred_web_host = (
                "0.0.0.0"
                if self.effective_web_interface_sources()
                else "127.0.0.1"
            )
            if (
                not web_interfaces_are_default
                or self.web_interface_host != inferred_web_host
            ):
                cmd_parts.append(
                    f"--web-interface-host {shlex.quote(self.web_interface_host or '127.0.0.1')}"
                )
            if not web_interfaces_are_default or self.web_interface_port != 3773:
                cmd_parts.append(f"--web-interface-port {self.web_interface_port}")
            if self.clear_web_interface_sources:
                cmd_parts.append("--no-web-interface-source")
            else:
                for source in self.web_interface_sources or []:
                    cmd_parts.append(f"--web-interface-source {shlex.quote(source)}")
        for port in self.web_ports or []:
            cmd_parts.append(f"--web-port {port}")
        if not self.default_web_ports:
            cmd_parts.append("--no-default-web-ports")
        default_pairing_providers = list(
            system_type_defaults.default_device_pairing_providers
        )
        if self.disable_device_pairing:
            if default_pairing_providers:
                cmd_parts.append("--no-device-pairing")
        elif list(self.device_pairing_providers or []) != default_pairing_providers:
            for provider in self.device_pairing_providers or []:
                cmd_parts.append(f"--device-pairing {shlex.quote(provider)}")
        if self.device_pairing_providers and self.device_pairing_port != 3774:
            cmd_parts.append(f"--device-pairing-port {self.device_pairing_port}")
        if self.disable_web_panel:
            cmd_parts.append("--no-web-panel")
        elif self.web_panel_port is not None:
            default_web_panel_port = 443 if self.enable_ssl else 80
            if self.web_panel_port == default_web_panel_port:
                cmd_parts.append("--web-panel")
            else:
                cmd_parts.append(f"--web-panel {self.web_panel_port}")
            if self.web_panel_notification_ingest is True:
                cmd_parts.append("--web-panel-notification-ingest")

        if self.disable_browser_automation:
            cmd_parts.append("--no-browser-automation")
        elif (
            self.browser_automation
            and self.browser_automation
            != system_type_defaults.default_browser_automation
        ):
            cmd_parts.append(
                f"--browser-automation {shlex.quote(self.browser_automation)}"
            )

        if self.git_access != system_type_defaults.default_git_access:
            cmd_parts.append(f"--git-access {shlex.quote(self.git_access)}")
        if self.disable_git_auth:
            cmd_parts.append("--git-auth none")
        if self.disable_agent_auth:
            cmd_parts.append("--agent-auth none")
        if self.git_host != "github.com":
            cmd_parts.append(f"--git-host {shlex.quote(self.git_host)}")
        if self.clear_git_credentials:
            cmd_parts.append("--no-git-credentials")
        else:
            for origin, username in self.git_credentials or []:
                cmd_parts.append(
                    "--git-credential "
                    f"{shlex.quote(origin)} {shlex.quote(username)}"
                )
            for origin, source_path in self.git_ca_certificates or []:
                cmd_parts.append(
                    "--git-ca-certificate "
                    f"{shlex.quote(origin)} {shlex.quote(source_path)}"
                )

        if self.agent_repos:
            for git_url in self.agent_repos:
                cmd_parts.append(f"--repo {shlex.quote(git_url)}")
        if self.agent_workspace:
            cmd_parts.append(f"--agent-workspace {shlex.quote(self.agent_workspace)}")
        if self.install_git_lfs:
            cmd_parts.append("--git-lfs")
        
        # Custom steps
        if self.custom_steps:
            cmd_parts.append(f"--steps {shlex.quote(self.custom_steps)}")
        
        # Deployments
        if self.deploy_specs:
            if self.deployment_mode == "lite":
                cmd_parts.append("--deployment-lite")
            elif self.deployment_mode == "full":
                # --deployment-full already implies a full rebuild on the remote
                cmd_parts.append("--deployment-full")
            elif self.full_deploy:
                cmd_parts.append("--full-deploy")
            # Use --deploy-latest for each spec if deploy_latest is set, otherwise --deploy
            flag = "--deploy-latest" if self.deploy_latest else "--deploy"
            for deploy_spec, git_url in self.deploy_specs:
                cmd_parts.append(f"{flag} {shlex.quote(deploy_spec)} {shlex.quote(git_url)}")
        elif self.deploy_latest:
            # deploy_latest without specs doesn't make sense, but keep for backward compat
            cmd_parts.append("--deploy-latest")
        
        # SSL
        if self.enable_ssl:
            cmd_parts.append("--ssl")
            if self.ssl_email:
                cmd_parts.append(f"--ssl-email {shlex.quote(self.ssl_email)}")
        
        # Cloudflare
        if self.enable_cloudflare:
            cmd_parts.append("--cloudflare")
        
        # CI/CD
        if self.enable_cicd:
            cmd_parts.append("--cicd")
        
        # Build/App Server
        if self.is_build_server:
            cmd_parts.append("--build-server")
        
        if self.is_app_server:
            cmd_parts.append("--app-server")
        
        if self.deploy_targets:
            for target in self.deploy_targets:
                cmd_parts.append(f"--deploy-target {shlex.quote(target)}")
        
        # Samba
        if self.enable_samba:
            cmd_parts.append("--samba")
        if self.clear_samba_sources:
            cmd_parts.append("--no-samba-source")
        else:
            for source in self.samba_sources or []:
                cmd_parts.append(f"--samba-source {shlex.quote(source)}")
        if self.clear_samba_metadata_cache:
            cmd_parts.append("--no-samba-metadata-cache")
        elif self.samba_metadata_cache:
            cmd_parts.append(
                "--samba-metadata-cache "
                f"{shlex.quote(self.samba_metadata_cache)}"
            )

        SHARE_USERS_INDEX = 3
        MIN_SHARE_FIELDS = SHARE_USERS_INDEX + 1
        required_share_credentials: StrList = []
        seen_share_credentials: set[str] = set()
        for _origin, username in self.git_credentials or []:
            if username not in seen_share_credentials:
                required_share_credentials.append(username)
                seen_share_credentials.add(username)
        if self.antistatic_admin:
            if self.antistatic_admin not in seen_share_credentials:
                required_share_credentials.append(self.antistatic_admin)
                seen_share_credentials.add(self.antistatic_admin)
        if self.syncthing_admin:
            if self.syncthing_admin not in seen_share_credentials:
                required_share_credentials.append(self.syncthing_admin)
                seen_share_credentials.add(self.syncthing_admin)
        redacted_share_specs: list[list[str]] = []
        if self.samba_shares:
            for share_spec in self.samba_shares:
                redacted_share_spec = list(share_spec)
                if len(redacted_share_spec) >= MIN_SHARE_FIELDS:
                    users_field = redacted_share_spec[SHARE_USERS_INDEX]
                    redacted_users: StrList = []
                    for user_spec in users_field.split(','):
                        user_spec = user_spec.strip()
                        if not user_spec:
                            continue
                        if ':' in user_spec:
                            username, _ = user_spec.split(':', 1)
                            redacted_users.append(f"{username.strip()}:[REDACTED]")
                        else:
                            redacted_users.append(user_spec)
                            if user_spec not in seen_share_credentials:
                                seen_share_credentials.add(user_spec)
                                required_share_credentials.append(user_spec)
                    redacted_share_spec[SHARE_USERS_INDEX] = ','.join(redacted_users)
                redacted_share_specs.append(redacted_share_spec)

        for username in required_share_credentials:
            cmd_parts.append(f"--credential {shlex.quote(username)} [REDACTED]")

        for redacted_share_spec in redacted_share_specs:
            escaped_spec = ' '.join(shlex.quote(str(s)) for s in redacted_share_spec)
            cmd_parts.append(f"--share {escaped_spec}")
        
        # SMB client
        if self.enable_smbclient:
            cmd_parts.append("--smbclient")
        
        # SMB mounts
        if self.smb_mounts:
            for mount_spec in self.smb_mounts:
                redacted_mount_spec = list(mount_spec)
                if len(redacted_mount_spec) >= 3 and ':' in redacted_mount_spec[2]:
                    redacted_mount_spec[2] = redact_mount_credentials(redacted_mount_spec[2])
                escaped_spec = ' '.join(shlex.quote(str(s)) for s in redacted_mount_spec)
                cmd_parts.append(f"--mount-smb {escaped_spec}")

        if self.disable_syncthing:
            cmd_parts.append("--no-syncthing")
            if self.syncthing_root:
                cmd_parts.append(
                    f"--syncthing-root {shlex.quote(self.syncthing_root)}"
                )
        elif self.enable_syncthing:
            cmd_parts.append("--syncthing")
            cmd_parts.append(
                f"--syncthing-admin {shlex.quote(self.syncthing_admin or '')}"
            )
            cmd_parts.append(
                "--syncthing-root "
                f"{shlex.quote(self.syncthing_root or DEFAULT_SYNCTHING_ROOT)}"
            )
        
        # Sync
        if self.sync_specs:
            for sync_spec in self.sync_specs:
                escaped_spec = ' '.join(shlex.quote(str(s)) for s in sync_spec)
                cmd_parts.append(f"--sync {escaped_spec}")

        if self.backup_specs:
            for backup_spec in self.backup_specs:
                escaped_spec = ' '.join(shlex.quote(str(s)) for s in backup_spec)
                cmd_parts.append(f"--backup {escaped_spec}")
        
        # Scrub
        if self.scrub_specs:
            for scrub_spec in self.scrub_specs:
                escaped_spec = ' '.join(shlex.quote(str(s)) for s in scrub_spec)
                cmd_parts.append(f"--scrub {escaped_spec}")
        
        # Notifications
        if self.notify_specs:
            for notify_spec in self.notify_specs:
                escaped_spec = ' '.join(shlex.quote(str(s)) for s in notify_spec)
                cmd_parts.append(f"--notify {escaped_spec}")
        if self.notification_level is not None:
            cmd_parts.append(
                f"--notification-level {shlex.quote(self.notification_level)}"
            )
        if self.notification_strict_https is True:
            cmd_parts.append("--notification-strict-https")
        
        # Antistatic lobby server
        if self.antistatic_server:
            cmd_parts.append(f"--antistatic-server {shlex.quote(self.antistatic_server)}")

        if self.antistatic_admin:
            cmd_parts.append(f"--antistatic-admin {shlex.quote(self.antistatic_admin)}")

        # Antistatic DB service
        if self.antistatic_db:
            cmd_parts.append(f"--antistatic-db {shlex.quote(self.antistatic_db)}")

        if self.gogs:
            escaped_gogs = " ".join(shlex.quote(str(part)) for part in self.gogs)
            cmd_parts.append(f"--gogs {escaped_gogs}")
        for source in self.gogs_sources or []:
            cmd_parts.append(f"--gogs-source {shlex.quote(source)}")
        
        # Restart control
        system_defaults = get_system_type_definition(self.system_type)
        if self.auto_restart != system_defaults.default_auto_restart:
            cmd_parts.append("--auto-restart" if self.auto_restart else "--no-auto-restart")
        if self.auto_restart_force_days != system_defaults.default_auto_restart_force_days:
            cmd_parts.append(f"--auto-restart-force-days {self.auto_restart_force_days}")
        if self.auto_restart_grace != 5:
            cmd_parts.append(f"--auto-restart-grace {self.auto_restart_grace}")
        
        return cmd_parts

    def _homebox_args(self) -> StrList:
        if self.homebox == []:
            return ["--no-homebox"]
        args: StrList = []
        if self.homebox:
            args.append("--homebox " + " ".join(shlex.quote(p) for p in self.homebox))
        for flag, value in (("version", self.homebox_version), ("admin", self.homebox_admin), ("port", self.homebox_port)):
            if value is not None:
                args.append(f"--homebox-{flag} {shlex.quote(str(value))}")
        return args

    def to_dict(self) -> JSONDict:
        data = asdict(self)
        data.pop('host', None)
        data.pop('system_type', None)
        data.pop('password', None)
        data.pop('share_credentials', None)
        data.pop('deploy_latest', None)
        for transient_field in (
            'refresh_packages',
            'copy_agent_keys',
            'copy_agent_config',
            'git_auth_source',
            'git_auth_file',
            'git_auth_token',
            'git_ca_pems',
            'clear_git_credentials',
            'agent_auth_source',
            'agent_auth_files',
            'agent_config_source',
            'agent_payload',
            'github_auth_payload',
            'git_identity_payload',
            'device_pairing_auth_file',
            'device_pairing_auth_username',
            'device_pairing_auth_password',
            'device_pairing_payload',
            'web_panel_auth_password',
            'web_panel_payload',
            'swap_initialize',
            'disable_syncthing',
        ):
            data.pop(transient_field, None)
        for legacy_field in (
            'install_gh',
            'install_codex',
            'install_claude',
            'install_opencode',
        ):
            data.pop(legacy_field, None)
        data['agent_tools'] = self.selected_agent_tools() or None
        data['agent_tools_removed'] = list(self.agent_tools_removed or []) or None
        # argparse uses None to distinguish an omitted BooleanOptionalAction
        # during patch merges. Persist a concrete boolean once the setup state
        # is saved so the cache schema does not retain that transient sentinel.
        data['install_data_analysis_tools'] = bool(
            self.install_data_analysis_tools
        )
        data['install_av_tools'] = bool(self.install_av_tools)
        data['install_gl_tools'] = bool(self.install_gl_tools)
        data['install_sunshine'] = bool(self.install_sunshine)
        data['install_moonlight'] = bool(self.install_moonlight)
        data['install_gaming'] = bool(self.install_gaming)
        data['install_obs'] = bool(self.install_obs)
        data['install_blender'] = bool(self.install_blender)
        data['install_kdenlive'] = bool(self.install_kdenlive)
        data['install_krita'] = bool(self.install_krita)
        data['install_inkscape'] = bool(self.install_inkscape)
        data['install_scribus'] = bool(self.install_scribus)
        data['install_audacity'] = bool(self.install_audacity)
        data['install_ardour'] = bool(self.install_ardour)
        data['install_lmms'] = bool(self.install_lmms)
        data['install_freecad'] = bool(self.install_freecad)
        data['install_kicad'] = bool(self.install_kicad)
        data['install_shotcut'] = bool(self.install_shotcut)
        data['install_gimp'] = bool(self.install_gimp)
        data['install_remmina'] = bool(self.install_remmina)
        data['install_sysadmin_tools'] = bool(self.install_sysadmin_tools)
        data['harden_agent'] = bool(self.harden_agent)
        data['harden_user'] = bool(self.harden_user)
        data['enable_syncthing'] = bool(self.enable_syncthing)
        data['web_panel_notification_ingest'] = bool(
            self.web_panel_notification_ingest
        )
        data['notification_strict_https'] = bool(self.notification_strict_https)
        if self.enable_syncthing and not self.syncthing_root:
            data['syncthing_root'] = DEFAULT_SYNCTHING_ROOT
        # Live activation is a one-shot controller operation. Persisting it
        # would make a later deploy retry a sensitive address change without
        # the operator explicitly requesting another handoff.
        data.pop('activate_network', None)
        data['samba_shares'] = _strip_passwords_from_samba_shares(self.samba_shares)
        data['smb_mounts'] = _strip_passwords_from_smb_mounts(self.smb_mounts)
        if self.tags:
            data['tags'] = ','.join(self.tags)
        return data
    
    @classmethod
    def from_dict(cls, host: str, system_type: str, data: JSONDict) -> 'SetupConfig':
        data = dict(data)
        retired = {
            "rdp_max_sessions": (None, 1, 10),
            "rdp_kill_disconnected": (None, False),
            "rdp_disconnected_timeout": (None, 0),
        }
        changed = [key for key, defaults in retired.items() if data.get(key) not in defaults]
        if changed:
            warnings.warn(
                "Migrating legacy desktop policy to one persistent session; removed: "
                + ", ".join(changed),
                UserWarning,
                stacklevel=2,
            )
        for key in retired:
            data.pop(key, None)
        # Older cache entries may contain this one-shot flag. Never replay a
        # sensitive live handoff merely because a saved configuration is
        # loaded for deploy, patch, or reconstruction.
        data.pop('activate_network', None)
        data.pop('disable_syncthing', None)
        # Ignore removed feature fields when loading older saved setup state so
        # upgrades remain usable.
        for removed_field in (
            'install_ruby',
            'reset_migrations',
            'api_subdomain',
            'desktop_interfaces',
            'syncthing_devices',
            'syncthing_folders',
            'syncthing_versioning',
        ):
            data.pop(removed_field, None)
        tags_str = data.get('tags')
        if tags_str and isinstance(tags_str, str):
            data['tags'] = [tag.strip() for tag in tags_str.split(',') if tag.strip()]
        elif not tags_str:
            data['tags'] = None

        data['container_storage'] = _normalize_nested_specs(data.get('container_storage'))
        data['storage_mounts'] = _normalize_nested_specs(data.get('storage_mounts'))
        data['storage_caches'] = _normalize_nested_specs(data.get('storage_caches'))
        data['swap_files'] = _normalize_nested_specs(data.get('swap_files'))
        data['swap_devices'] = _normalize_nested_specs(data.get('swap_devices'))
        data['swap_zram'] = _normalize_nested_specs(data.get('swap_zram'))
        data['vm_disk_settings'] = _normalize_nested_specs(data.get('vm_disk_settings'))
        data['git_credentials'] = _normalize_nested_specs(data.get('git_credentials'))
        data['git_ca_certificates'] = _normalize_nested_specs(
            data.get('git_ca_certificates')
        )
        system_defaults = get_system_type_definition(system_type)
        if not data.get('agent_tools') and not data.get('agent_tools_removed'):
            data['agent_tools'] = list(system_defaults.default_agent_tools) or None
        if not data.get('browser') and not data.get('browsers'):
            data['browser'] = system_defaults.default_browser
        if data.get('disable_web_interface'):
            data['web_interfaces'] = None
        elif not data.get('web_interfaces'):
            data['web_interfaces'] = (
                list(system_defaults.default_web_interfaces) or None
            )
        if not data.get('editor'):
            data['editor'] = system_defaults.default_editor
        if data.get('disable_browser_automation'):
            data['browser_automation'] = None
        elif not data.get('browser_automation'):
            data['browser_automation'] = system_defaults.default_browser_automation
        if 'enable_rdp' not in data or data.get('enable_rdp') is None:
            data['enable_rdp'] = system_defaults.default_enable_rdp
        if not data.get('disable_device_pairing') and not data.get(
            'device_pairing_providers'
        ):
            data['device_pairing_providers'] = (
                list(system_defaults.default_device_pairing_providers) or None
            )
        if 'git_access' not in data or data.get('git_access') is None:
            data['git_access'] = system_defaults.default_git_access
        if (
            not data.get('disable_git_auth')
            and not data.get('git_auth_file')
            and 'gh' in (data.get('agent_tools') or system_defaults.default_agent_tools)
        ):
            data['git_auth_source'] = system_defaults.default_git_auth_source
        if (
            not data.get('disable_agent_auth')
            and not data.get('agent_auth_files')
            and data.get('agent_tools')
        ):
            data['agent_auth_source'] = system_defaults.default_agent_auth_source
        if 'auto_restart' not in data or data.get('auto_restart') is None:
            if 'no_restart' in data and data.get('no_restart') is not None:
                data['auto_restart'] = not bool(data.pop('no_restart'))
            else:
                data['auto_restart'] = system_defaults.default_auto_restart
        else:
            data.pop('no_restart', None)
        if 'auto_restart_force_days' not in data or data.get('auto_restart_force_days') is None:
            data['auto_restart_force_days'] = system_defaults.default_auto_restart_force_days
        data['auto_restart_force_days'] = _validate_non_negative_int(
            'auto_restart_force_days', int(data['auto_restart_force_days'])
        )
        if 'auto_restart_grace' not in data or data.get('auto_restart_grace') is None:
            data['auto_restart_grace'] = 5
        data['auto_restart_grace'] = _validate_non_negative_int(
            'auto_restart_grace', int(data['auto_restart_grace'])
        )
            
        if 'friendly_name' not in data:
            data['friendly_name'] = None
            
        return cls(host=host, system_type=system_type, **data)
    
    @classmethod
    def from_args(cls, args: argparse.Namespace, system_type: str) -> 'SetupConfig':
        from lib.system_utils import get_current_username, get_local_timezone

        system_type_definition = get_system_type_definition(system_type)
        tags = None
        if hasattr(args, 'tags') and args.tags:
            tags = [tag.strip() for tag in args.tags.split(',') if tag.strip()]
        
        username = args.username if args.username else get_current_username()
        timezone = args.timezone if args.timezone else get_local_timezone()
        desktop = args.desktop or "xfce"
        
        # Handle browser - support both single value and list
        browser = None
        browsers = getattr(args, 'browsers', None)
        
        # If browsers list is provided, use it
        if browsers and len(browsers) > 0:
            # First browser becomes the default
            browser = browsers[0]
        elif hasattr(args, 'browser') and args.browser:
            # Single browser provided
            browser = args.browser
        elif system_type_definition.default_browser:
            browser = system_type_definition.default_browser
        
        install_office = args.install_office
        if install_office is None and system_type_definition.default_install_office:
            install_office = True
        elif install_office is None:
            install_office = False
        
        enable_rdp = args.enable_rdp
        if enable_rdp is None and system_type_definition.default_enable_rdp:
            enable_rdp = True
        elif enable_rdp is None:
            enable_rdp = False
        
        smb_mounts = getattr(args, 'smb_mounts', None)
        enable_smbclient = getattr(args, 'enable_smbclient', None)
        if enable_smbclient is None and (system_type_definition.default_enable_smbclient or smb_mounts):
            enable_smbclient = True
        elif enable_smbclient is None:
            enable_smbclient = False

        enable_syncthing = getattr(args, 'enable_syncthing', None)
        if enable_syncthing is not None and not isinstance(enable_syncthing, bool):
            enable_syncthing = None
        disable_syncthing = bool(getattr(args, 'disable_syncthing', False))
        if disable_syncthing:
            enable_syncthing = False
        syncthing_admin = _optional_str_arg(args, 'syncthing_admin')
        syncthing_root = _optional_str_arg(args, 'syncthing_root')

        is_build_server = bool(getattr(args, 'is_build_server', False))
        is_app_server = bool(getattr(args, 'is_app_server', False))
        machine_type = _resolve_machine_type(
            args,
            is_hosted=bool(getattr(args, 'hosted_node', None)),
        )
        
        include_desktop = (
            system_type_definition.include_desktop
            or enable_rdp
            or bool(getattr(args, 'desktop', None))
        )
        include_cli_tools = system_type_definition.include_cli_tools
        include_control_plane_tools = (
            system_type_definition.include_control_plane_tools
            or bool(getattr(args, 'control_plane', False))
        )
        include_desktop_apps = system_type_definition.include_desktop_apps
        include_workstation_dev_apps = system_type_definition.include_workstation_dev_apps
        include_pc_dev_apps = system_type_definition.include_pc_dev_apps
        include_web_server = system_type_definition.include_web_server
        include_web_firewall = system_type_definition.include_web_firewall
        
        auto_restart = _optional_bool_arg(args, 'auto_restart')
        if auto_restart is None:
            auto_restart = system_type_definition.default_auto_restart

        auto_restart_force_days = _optional_int_arg(args, 'auto_restart_force_days')
        if auto_restart_force_days is None:
            auto_restart_force_days = system_type_definition.default_auto_restart_force_days
        auto_restart_force_days = _validate_non_negative_int(
            'auto_restart_force_days', auto_restart_force_days
        )

        auto_restart_grace = _optional_int_arg(args, 'auto_restart_grace')
        if auto_restart_grace is None:
            auto_restart_grace = 5
        auto_restart_grace = _validate_non_negative_int('auto_restart_grace', auto_restart_grace)

        raw_agent_tools = getattr(args, 'agent_tools', None)
        removed_agent_tools = list(
            getattr(args, 'no_agent_tools', None) or []
        )
        agent_tools = list(system_type_definition.default_agent_tools)
        for tool in raw_agent_tools or []:
            if tool not in agent_tools:
                agent_tools.append(tool)
        agent_tools = [tool for tool in agent_tools if tool not in removed_agent_tools]
        agent_tools = agent_tools or None
        raw_web_ports = getattr(args, 'web_ports', None)
        web_ports = raw_web_ports if isinstance(raw_web_ports, list) else None
        disable_browser_automation = bool(
            getattr(args, 'disable_browser_automation', False)
        )
        browser_automation = (
            None
            if disable_browser_automation
            else (
                _optional_str_arg(args, 'browser_automation')
                or system_type_definition.default_browser_automation
            )
        )
        editor = (
            _optional_str_arg(args, 'editor')
            or system_type_definition.default_editor
        )
        disable_web_interface = bool(getattr(args, 'disable_web_interface', False))
        raw_web_interfaces = getattr(args, 'web_interfaces', None)
        web_interfaces = (
            None
            if disable_web_interface
            else (
                raw_web_interfaces
                if isinstance(raw_web_interfaces, list) and raw_web_interfaces
                else list(system_type_definition.default_web_interfaces) or None
            )
        )
        raw_agent_repos = getattr(args, 'agent_repos', None)
        agent_repos = raw_agent_repos if isinstance(raw_agent_repos, list) else None
        raw_git_access = getattr(args, 'git_access', None)
        if raw_git_access is None:
            raw_git_access = system_type_definition.default_git_access
        git_access = raw_git_access if raw_git_access in GIT_ACCESS_POLICIES else 'none'
        raw_git_host = getattr(args, 'git_host', 'github.com')
        git_host = raw_git_host if isinstance(raw_git_host, str) else 'github.com'
        clear_git_credentials = bool(
            getattr(args, 'clear_git_credentials', False)
        )
        raw_git_credentials = _normalize_nested_specs(
            getattr(args, 'git_credentials', None)
        )
        raw_git_ca_certificates = _normalize_nested_specs(
            getattr(args, 'git_ca_certificates', None)
        )
        raw_git_ca_pems = _normalize_nested_specs(
            getattr(args, 'git_ca_pems', None)
        )
        if clear_git_credentials and (
            raw_git_credentials
            or raw_git_ca_certificates
            or raw_git_ca_pems
        ):
            raise ValueError(
                "--no-git-credentials cannot be combined with Git credential or CA options"
            )
        git_credentials = (
            None
            if clear_git_credentials
            else raw_git_credentials
        )
        git_ca_certificates = (
            None
            if clear_git_credentials
            else raw_git_ca_certificates
        )
        git_ca_pems = (
            None
            if clear_git_credentials
            else raw_git_ca_pems
        )
        raw_agent_auth_files = getattr(args, 'agent_auth_files', None)
        agent_auth_files = (
            raw_agent_auth_files if isinstance(raw_agent_auth_files, list) else None
        )
        raw_git_auth_source = _optional_str_arg(args, 'git_auth_source')
        disable_git_auth = raw_git_auth_source == 'none'
        git_auth_file = _optional_str_arg(args, 'git_auth_file')
        git_auth_token = _optional_str_arg(args, 'git_auth_token')
        git_auth_source = (
            None
            if disable_git_auth or git_auth_file or git_auth_token
            else (
                raw_git_auth_source
                or (
                    system_type_definition.default_git_auth_source
                    if "gh" in (agent_tools or [])
                    else None
                )
            )
        )
        raw_agent_auth_source = _optional_str_arg(args, 'agent_auth_source')
        disable_agent_auth = raw_agent_auth_source == 'none'
        agent_auth_source = (
            None
            if disable_agent_auth or agent_auth_files
            else (
                raw_agent_auth_source
                or (
                    system_type_definition.default_agent_auth_source
                    if agent_tools
                    else None
                )
            )
        )
        agent_config_source = _optional_str_arg(args, 'agent_config_source')
        device_pairing_port = _optional_int_arg(args, 'device_pairing_port')
        if device_pairing_port is None:
            device_pairing_port = 3774
        enable_ssl = _optional_bool_arg(args, 'enable_ssl')
        disable_web_panel = bool(
            getattr(args, 'disable_web_panel', False)
        )
        raw_web_panel_port = getattr(args, 'web_panel_port', None)
        web_panel_port = (
            None
            if raw_web_panel_port is WEB_PANEL_DEFAULT_PORT_SENTINEL
            else _optional_int_arg(args, 'web_panel_port')
        )
        if disable_web_panel:
            web_panel_port = None
        elif raw_web_panel_port is WEB_PANEL_DEFAULT_PORT_SENTINEL:
            web_panel_port = 443 if enable_ssl is True else 80
        default_web_ports = _optional_bool_arg(args, 'default_web_ports')
        if default_web_ports is None:
            default_web_ports = True

        clear_access_sources = bool(getattr(args, 'clear_access_sources', False))
        raw_access_sources = getattr(args, 'access_sources', None)
        access_sources = (
            None
            if clear_access_sources
            else (
                raw_access_sources
                if isinstance(raw_access_sources, list) and raw_access_sources
                else None
            )
        )
        raw_lan_access = _optional_bool_arg(args, 'lan_access')
        lan_access = raw_lan_access is True
        clear_lan_access = raw_lan_access is False

        raw_mdns = _optional_bool_arg(args, 'enable_mdns')
        enable_mdns = raw_mdns is True
        clear_mdns = raw_mdns is False

        clear_rdp_sources = bool(getattr(args, 'clear_rdp_sources', False))
        raw_rdp_sources = getattr(args, 'rdp_allowed_sources', None)
        rdp_allowed_sources = (
            None
            if clear_rdp_sources
            else (
                raw_rdp_sources
                if isinstance(raw_rdp_sources, list) and raw_rdp_sources
                else None
            )
        )
        clear_web_interface_sources = bool(
            getattr(args, 'clear_web_interface_sources', False)
        )
        raw_web_sources = getattr(args, 'web_interface_sources', None)
        web_interface_sources = (
            None
            if clear_web_interface_sources or disable_web_interface
            else (
                raw_web_sources
                if isinstance(raw_web_sources, list) and raw_web_sources
                else None
            )
        )
        clear_samba_sources = bool(
            getattr(args, 'clear_samba_sources', False)
        )
        raw_samba_sources = getattr(args, 'samba_sources', None)
        samba_sources = (
            None
            if clear_samba_sources
            else (
                raw_samba_sources
                if isinstance(raw_samba_sources, list) and raw_samba_sources
                else None
            )
        )
        clear_samba_metadata_cache = bool(
            getattr(args, 'clear_samba_metadata_cache', False)
        )
        samba_metadata_cache = (
            None
            if clear_samba_metadata_cache
            else _optional_str_arg(args, 'samba_metadata_cache')
        )
        disable_device_pairing = bool(
            getattr(args, 'disable_device_pairing', False)
        )
        raw_pairing_providers = getattr(args, 'device_pairing_providers', None)
        device_pairing_providers = (
            None
            if disable_device_pairing
            else (
                raw_pairing_providers
                if isinstance(raw_pairing_providers, list) and raw_pairing_providers
                else list(system_type_definition.default_device_pairing_providers) or None
            )
        )
        
        return cls(
            host=args.host,
            username=username,
            system_type=system_type,
            machine_type=machine_type,
            password=getattr(args, 'password', None),
            nopasswd=_optional_bool_arg(args, 'nopasswd') is True,
            harden_agent=_optional_bool_arg(args, 'harden_agent'),
            harden_user=_optional_bool_arg(args, 'harden_user'),
            ssh_key=getattr(args, 'ssh_key', None),
            timezone=timezone,
            system_hostname=getattr(args, 'system_hostname', None),
            enable_mdns=enable_mdns,
            clear_mdns=clear_mdns,
            static_ipv4=getattr(args, 'static_ipv4', None),
            static_ipv6=getattr(args, 'static_ipv6', None),
            network_gateway4=getattr(args, 'network_gateway4', None),
            network_gateway6=getattr(args, 'network_gateway6', None),
            network_dns=getattr(args, 'network_dns', None),
            network_interface=getattr(args, 'network_interface', None),
            activate_network=getattr(args, 'activate_network', False),
            friendly_name=getattr(args, 'friendly_name', None),
            tags=tags,
            access_sources=access_sources,
            clear_access_sources=clear_access_sources,
            lan_access=lan_access,
            clear_lan_access=clear_lan_access,
            enable_rdp=enable_rdp,
            rdp_existing_password=getattr(args, 'rdp_existing_password', False),
            rdp_bind_address=getattr(args, 'rdp_bind_address', '0.0.0.0'),
            rdp_allowed_sources=rdp_allowed_sources,
            clear_rdp_sources=clear_rdp_sources,
            rdp_clipboard=getattr(args, 'rdp_clipboard', True),
            rdp_drive_redirection=getattr(args, 'rdp_drive_redirection', False),
            rdp_audio=getattr(args, 'rdp_audio', False),
            rdp_idle_timeout=getattr(args, 'rdp_idle_timeout', 0),
            desktop=desktop,
            browser=browser,
            browsers=browsers,
            editor=editor,
            use_flatpak=getattr(args, 'use_flatpak', False),
            install_office=install_office,
            apt_packages=getattr(args, 'apt_packages', None),
            flatpak_packages=getattr(args, 'flatpak_packages', None),
            dark_theme=getattr(args, 'dark_theme', False),
            dry_run=getattr(args, 'dry_run', False),
            refresh_packages=getattr(args, 'refresh_packages', False),
            install_go=getattr(args, 'install_go', False),
            install_node=getattr(args, 'install_node', False),
            install_python=getattr(args, 'install_python', False),
            install_data_analysis_tools=getattr(
                args, 'install_data_analysis_tools', False
            ),
            install_av_tools=getattr(args, 'install_av_tools', False),
            install_gl_tools=getattr(args, 'install_gl_tools', False),
            install_godot=getattr(args, 'install_godot', False),
            install_sunshine=getattr(args, 'install_sunshine', False) is True,
            install_moonlight=getattr(args, 'install_moonlight', False) is True,
            install_gaming=getattr(args, 'install_gaming', False) is True,
            install_obs=getattr(args, 'install_obs', False) is True,
            install_blender=getattr(args, 'install_blender', False) is True,
            install_kdenlive=getattr(args, 'install_kdenlive', False) is True,
            install_krita=getattr(args, 'install_krita', False) is True,
            install_inkscape=getattr(args, 'install_inkscape', False) is True,
            install_scribus=getattr(args, 'install_scribus', False) is True,
            install_audacity=getattr(args, 'install_audacity', False) is True,
            install_ardour=getattr(args, 'install_ardour', False) is True,
            install_lmms=getattr(args, 'install_lmms', False) is True,
            install_freecad=getattr(args, 'install_freecad', False) is True,
            install_kicad=getattr(args, 'install_kicad', False) is True,
            install_shotcut=getattr(args, 'install_shotcut', False) is True,
            install_gimp=getattr(args, 'install_gimp', False) is True,
            install_remmina=getattr(args, 'install_remmina', False) is True,
            install_sysadmin_tools=(
                getattr(args, 'install_sysadmin_tools', False) is True
            ),
            godot_bundles=(
                getattr(args, 'godot_bundles', None)
                if isinstance(getattr(args, 'godot_bundles', None), list)
                else None
            ),
            agent_tools=agent_tools,
            agent_tools_removed=removed_agent_tools or None,
            web_interfaces=web_interfaces,
            t3code_ready=_optional_bool_arg(args, 't3code_ready') is True,
            disable_web_interface=disable_web_interface,
            web_interface_host=getattr(args, 'web_interface_host', None),
            web_interface_port=getattr(args, 'web_interface_port', 3773),
            web_interface_sources=web_interface_sources,
            clear_web_interface_sources=clear_web_interface_sources,
            web_ports=web_ports,
            default_web_ports=default_web_ports,
            device_pairing_providers=device_pairing_providers,
            device_pairing_port=device_pairing_port,
            device_pairing_auth_file=_optional_str_arg(
                args, 'device_pairing_auth_file'
            ),
            device_pairing_auth_username=_optional_str_arg(
                args, 'device_pairing_auth_username'
            ),
            device_pairing_auth_password=_optional_str_arg(
                args, 'device_pairing_auth_password'
            ),
            device_pairing_payload=(
                _optional_bool_arg(args, 'device_pairing_payload') is True
            ),
            disable_device_pairing=disable_device_pairing,
            web_panel_port=web_panel_port,
            web_panel_auth_password=_optional_str_arg(
                args, 'web_panel_auth_password'
            ),
            web_panel_payload=(
                _optional_bool_arg(args, 'web_panel_payload') is True
            ),
            web_panel_notification_ingest=_optional_bool_arg(
                args, 'web_panel_notification_ingest'
            ),
            disable_web_panel=disable_web_panel,
            browser_automation=browser_automation,
            disable_browser_automation=disable_browser_automation,
            copy_agent_keys=bool(
                git_auth_source
                or git_auth_file
                or git_auth_token
                or agent_auth_source
                or agent_auth_files
            ),
            copy_agent_config=bool(agent_config_source),
            agent_repos=agent_repos,
            git_access=git_access,
            git_host=git_host,
            git_auth_source=git_auth_source,
            git_auth_file=git_auth_file,
            git_auth_token=git_auth_token,
            disable_git_auth=disable_git_auth,
            git_credentials=git_credentials,
            git_ca_certificates=git_ca_certificates,
            git_ca_pems=git_ca_pems,
            clear_git_credentials=clear_git_credentials,
            agent_auth_source=agent_auth_source,
            agent_auth_files=agent_auth_files,
            disable_agent_auth=disable_agent_auth,
            agent_config_source=agent_config_source,
            agent_payload=_optional_bool_arg(args, 'agent_payload') is True,
            github_auth_payload=(
                _optional_bool_arg(args, 'github_auth_payload') is True
            ),
            git_identity_payload=(
                _optional_bool_arg(args, 'git_identity_payload') is True
            ),
            agent_workspace=_optional_str_arg(args, 'agent_workspace'),
            install_git_lfs=getattr(args, 'install_git_lfs', False),
            custom_steps=getattr(args, 'custom_steps', None),
            deploy_specs=getattr(args, 'deploy_specs', None),
            deployment_mode=getattr(args, 'deployment_mode', 'default'),
            full_deploy=getattr(args, 'full_deploy', False),
            deploy_latest=getattr(args, 'deploy_latest', False),
            enable_ssl=enable_ssl,
            ssl_email=getattr(args, 'ssl_email', None),
            enable_cloudflare=getattr(args, 'enable_cloudflare', False),
            enable_cicd=getattr(args, 'enable_cicd', False),
            is_build_server=is_build_server,
            is_app_server=is_app_server,
            deploy_targets=getattr(args, 'deploy_targets', None),
            enable_samba=getattr(args, 'enable_samba', False),
            samba_sources=samba_sources,
            clear_samba_sources=clear_samba_sources,
            samba_metadata_cache=samba_metadata_cache,
            clear_samba_metadata_cache=clear_samba_metadata_cache,
            samba_shares=getattr(args, 'samba_shares', None),
            share_credentials=getattr(args, 'share_credentials', None),
            enable_smbclient=enable_smbclient,
            smb_mounts=smb_mounts,
            enable_syncthing=enable_syncthing,
            disable_syncthing=disable_syncthing,
            syncthing_admin=syncthing_admin,
            syncthing_root=syncthing_root,
            sync_specs=getattr(args, 'sync_specs', None),
            backup_specs=getattr(args, 'backup_specs', None),
            scrub_specs=getattr(args, 'scrub_specs', None),
            notify_specs=getattr(args, 'notify_specs', None),
            notification_level=_optional_str_arg(args, 'notification_level'),
            notification_strict_https=_optional_bool_arg(
                args, 'notification_strict_https'
            ),
            antistatic_server=getattr(args, 'antistatic_server', None),
            antistatic_admin=getattr(args, 'antistatic_admin', None),
            antistatic_db=getattr(args, 'antistatic_db', None),
            gogs=getattr(args, 'gogs', None),
            gogs_sources=getattr(args, 'gogs_sources', None),
            homebox=getattr(args, 'homebox', None),
            homebox_version=getattr(args, 'homebox_version', None),
            homebox_admin=getattr(args, 'homebox_admin', None),
            homebox_port=getattr(args, 'homebox_port', None),
            auto_restart=auto_restart,
            auto_restart_force_days=auto_restart_force_days,
            auto_restart_grace=auto_restart_grace,
            proxmox_balloon_target=getattr(args, 'proxmox_balloon_target', None),
            hosted_node=getattr(args, 'hosted_node', None),
            hosted_user=getattr(args, 'hosted_user', None) or 'root',
            hosted_key=getattr(args, 'hosted_key', None),
            hosted_bridge=getattr(args, 'hosted_bridge', None),
            container_memory=getattr(args, 'container_memory', None),
            vm_balloon_min=getattr(args, 'vm_balloon_min', None),
            vm_balloon_shares=getattr(args, 'vm_balloon_shares', 1000),
            allow_memory_overcommit=getattr(args, 'allow_memory_overcommit', False),
            container_storage=_normalize_nested_specs(getattr(args, 'container_storage', None)),
            storage_mounts=_normalize_nested_specs(getattr(args, 'storage_mounts', None)),
            storage_caches=_normalize_nested_specs(getattr(args, 'storage_caches', None)),
            swap_mode=getattr(args, 'swap_mode', 'auto'),
            swap_files=_normalize_nested_specs(getattr(args, 'swap_files', None)),
            swap_devices=_normalize_nested_specs(getattr(args, 'swap_devices', None)),
            swap_zram=_normalize_nested_specs(getattr(args, 'swap_zram', None)),
            swappiness=_optional_int_arg(args, 'swappiness'),
            zswap=_optional_bool_arg(args, 'zswap'),
            zswap_max_pool_percent=_optional_int_arg(args, 'zswap_max_pool_percent'),
            swap_resume=_optional_str_arg(args, 'swap_resume'),
            swap_initialize=getattr(args, 'swap_initialize', None),
            container_cores=getattr(args, 'container_cores', 1),
            vm_cpu_type=getattr(args, 'vm_cpu_type', 'host'),
            vm_disk_discard=getattr(args, 'vm_disk_discard', True),
            vm_disk_ssd=getattr(args, 'vm_disk_ssd', False),
            vm_disk_backup=getattr(args, 'vm_disk_backup', True),
            vm_disk_settings=_normalize_nested_specs(getattr(args, 'vm_disk_settings', None)),
            container_base=getattr(args, 'container_base', 'debian'),
            vm_image=getattr(args, 'vm_image', None),
            vm_image_sha512=getattr(args, 'vm_image_sha512', None),
            vm_image_storage=getattr(args, 'vm_image_storage', None),
            include_desktop=include_desktop,
            include_cli_tools=include_cli_tools,
            include_control_plane_tools=include_control_plane_tools,
            include_desktop_apps=include_desktop_apps,
            include_workstation_dev_apps=include_workstation_dev_apps,
            include_pc_dev_apps=include_pc_dev_apps,
            include_web_server=include_web_server,
            include_web_firewall=include_web_firewall
        )
