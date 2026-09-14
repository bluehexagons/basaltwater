#!/usr/bin/env python3
from __future__ import annotations

from collections.abc import Mapping
from typing import Optional
from lib.config import GODOT_WEB_HTTPS_PORT, SetupConfig
from lib.notifications import (
    normalize_notification_level,
    notification_target_summary,
    parse_notification_args,
)


def _rdp_access_summary(config: SetupConfig) -> str:
    sources = config.effective_rdp_sources()
    if sources:
        return ", ".join(sources)
    return "global (rate-limited; use --access-source or --rdp-source to restrict)"


def _url_host(host: str) -> str:
    """Format an IPv6 host for use in a URL."""

    if ":" in host and not host.startswith("["):
        return f"[{host}]"
    return host


def _http_url(host: str, port: int | None = None, *, scheme: str = "http") -> str:
    """Build a display-only HTTP URL without exposing credentials."""

    default_port = 443 if scheme == "https" else 80
    port_suffix = "" if port is None or port == default_port else f":{port}"
    return f"{scheme}://{_url_host(host)}{port_suffix}/"


def _access_host(config: SetupConfig, bind: str) -> tuple[str, bool]:
    """Return the useful client host and whether the listener is loopback-only."""

    normalized = bind.strip().lower()
    if normalized in {"localhost", "127.0.0.1", "::1"}:
        return bind, True
    if normalized in {"0.0.0.0", "::"}:
        return config.host, False
    return bind, False


def _split_service_spec(spec: str, default_port: int) -> tuple[str, int]:
    """Parse the simple DOMAIN[:PORT] forms used by managed web services."""

    normalized = spec.strip()
    if normalized.isdigit():
        return "", int(normalized)
    if ":" not in normalized:
        return normalized, default_port
    domain, _, raw_port = normalized.rpartition(":")
    if raw_port.isdigit():
        return domain, int(raw_port)
    return normalized, default_port


def _service_url(
    config: SetupConfig,
    spec: str,
    default_port: int,
    *,
    scheme: str,
    source_restricted: bool = False,
    direct_host: str = "127.0.0.1",
) -> tuple[str, bool]:
    """Return a service URL and whether it is loopback-only."""

    domain, port = _split_service_spec(spec, default_port)
    if domain:
        return _http_url(domain, scheme=scheme), False
    host = config.host if source_restricted else direct_host
    return _http_url(host, port, scheme=scheme), not source_restricted


def print_name_and_tags(config: SetupConfig) -> None:
    if config.friendly_name:
        print(f"Name: {config.friendly_name}")
    if config.tags and len(config.tags) > 0:
        print(f"Tags: {', '.join(config.tags)}")


def print_rdp_info(config: SetupConfig) -> None:
    if config.enable_rdp:
        print(f"RDP: {config.host}:3389")
        print(f"  Bind address: {config.rdp_bind_address}")
        print(f"  Allowed sources: {_rdp_access_summary(config)}")
        print(f"  Client: Remmina, Microsoft Remote Desktop")


def print_service_access_summary(
    config: SetupConfig,
    *,
    remote_access_details: Mapping[str, str] | None = None,
) -> None:
    """Print concise access links and one-line descriptions."""

    lines: list[tuple[str, str, str | None]] = []
    access_details = remote_access_details or {}

    lines.append(("SSH", f"ssh {config.username}@{config.host}", "shell access"))

    if config.web_panel_port is not None:
        from common.web_panel_steps import web_panel_url

        panel_url = web_panel_url(config)
        if panel_url:
            lines.append(
                (
                    "Web panel",
                    panel_url,
                    "sign in with the setup username",
                )
            )

    if config.system_type == "server_web":
        lines.append(
            ("Web server", _http_url(config.host, scheme="http"), "web interface")
        )

    if "web" in (config.godot_bundles or []):
        game_root = _http_url(
            config.host,
            GODOT_WEB_HTTPS_PORT,
            scheme="https",
        ) + f"games/{config.username}/"
        lines.append(
            (
                "Godot web exports",
                game_root,
                "publish games with godot-web-publish GAME_NAME",
            )
        )

    if config.web_interfaces:
        bind = config.web_interface_host or "127.0.0.1"
        access_host, _loopback_only = _access_host(config, bind)
        web_url = _http_url(access_host, config.web_interface_port)
        for interface in config.web_interfaces:
            label = "T3 Code web" if interface == "t3code" else f"{interface} web"
            if interface == "t3code":
                if access_details.get("t3code"):
                    lines.append(
                        ("T3 Code", access_details["t3code"], "coding workspace")
                    )
            else:
                lines.append((label, web_url, "web interface"))

        if "t3code" in config.web_interfaces:
            if (
                config.device_pairing_providers
                and access_details.get("t3code-pairing")
            ):
                lines.append(
                    (
                        "T3 Code device pairing",
                        access_details["t3code-pairing"],
                        "protected device enrollment",
                    )
                )
            elif not config.device_pairing_providers:
                lines.append(
                    (
                        "T3 Code pairing",
                        f"infra-tools agent web pair {config.host} {config.username}",
                        "create a one-time client link",
                    )
                )

    if config.enable_syncthing:
        if access_details.get("syncthing-admin"):
            lines.append(
                (
                    "Syncthing admin",
                    access_details["syncthing-admin"],
                    "authenticated administration",
                )
            )
        if access_details.get("syncthing-device-id"):
            lines.append(
                (
                    "Syncthing device ID",
                    access_details["syncthing-device-id"],
                    "share with trusted peers",
                )
            )

    if config.gogs:
        from web.gogs_steps import effective_gogs_ipv4_sources

        gogs_spec = str(config.gogs[0])
        scheme = "https" if config.enable_ssl or config.enable_cloudflare else "http"
        domain, port = _split_service_spec(gogs_spec, 3000)
        if domain:
            public_port = None if config.enable_cloudflare else port
            gogs_url = _http_url(domain, public_port, scheme=scheme)
        else:
            access_host = (
                config.host
                if effective_gogs_ipv4_sources(config)
                else "127.0.0.1"
            )
            gogs_url = _http_url(access_host, port, scheme=scheme)
        lines.append(("Gogs web", gogs_url, "web interface"))
        lines.append(
            ("Gogs Git over SSH", f"git@{domain or config.host}", "Git access")
        )

    if config.homebox:
        from lib.homebox_config import homebox_settings

        homebox = homebox_settings(config)
        address = _http_url(homebox["domain"] or "127.0.0.1", homebox["public_port"],
                            scheme="https" if homebox["domain"] else "http")
        note = "inventory" if homebox["domain"] else "inventory; connect through an SSH tunnel"
        lines.append(("HomeBox", address, note))

    if config.antistatic_server:
        antistatic_domain, _antistatic_port = _split_service_spec(
            config.antistatic_server,
            8080,
        )
        scheme = (
            "https"
            if antistatic_domain and (config.enable_ssl or config.enable_cloudflare)
            else "http"
        )
        server_url, _loopback_only = _service_url(
            config,
            config.antistatic_server,
            8080,
            scheme=scheme,
            direct_host=config.host,
        )
        lines.append(("Antistatic lobby", server_url, "game lobby"))
        lines.append(
            (
                "Antistatic STUN",
                f"{config.host}:3478/udp",
                "voice/game networking",
            )
        )

    if config.antistatic_db:
        antistatic_db_domain, _antistatic_db_port = _split_service_spec(
            config.antistatic_db,
            8081,
        )
        scheme = (
            "https"
            if antistatic_db_domain and (config.enable_ssl or config.enable_cloudflare)
            else "http"
        )
        db_url, _loopback_only = _service_url(
            config,
            config.antistatic_db,
            8081,
            scheme=scheme,
            direct_host=config.host,
        )
        lines.append(("Antistatic DB", db_url, "game database"))

    if config.enable_rdp:
        lines.append(
            (
                "RDP",
                f"{_url_host(config.host)}:3389",
                "remote desktop",
            )
        )

    if config.enable_samba:
        lines.append(("Samba/SMB", f"//{_url_host(config.host)}", "file sharing"))

    if not lines:
        return

    web_labels = {
        "Web server",
        "Web panel",
        "Godot web exports",
        "Gogs web",
        "HomeBox",
        "Antistatic lobby",
        "Antistatic DB",
        "T3 Code",
        "T3 Code device pairing",
        "T3 Code pairing",
        "Syncthing admin",
    }
    sections: list[tuple[str, list[tuple[str, str, str | None]]]] = [
        ("Web", []),
        ("Network", []),
    ]
    section_map = {title: entries for title, entries in sections}
    for line in lines:
        section = "Web" if line[0] in web_labels else "Network"
        section_map[section].append(line)

    print("Access:")
    for title, entries in sections:
        if not entries:
            continue
        print(f"  {title}:")
        for label, address, note in entries:
            suffix = f" — {note}" if note else ""
            print(f"    {label}: {address}{suffix}")


def print_setup_summary(config: SetupConfig, description: Optional[str] = None) -> None:
    """Print a summary of the setup configuration."""
    if description:
        print("=" * 60)
        print(f"{description}")
        print("=" * 60)
    
    if config.host != "localhost":
        print(f"Host: {config.host}")
    
    if config.system_type != "server_proxmox":
        print(f"User: {config.username}")
    
    print(f"Timezone: {config.timezone}")

    access_sources = config.effective_access_sources()
    if access_sources:
        print(f"General access sources: {', '.join(access_sources)}")

    if config.system_hostname:
        print(f"System hostname: {config.system_hostname}")
    if config.static_ipv4:
        print(f"Static IPv4: {config.static_ipv4}")
    if config.static_ipv6:
        print(f"Static IPv6: {config.static_ipv6}")
    if config.network_gateway4:
        print(f"IPv4 gateway: {config.network_gateway4}")
    if config.network_gateway6:
        print(f"IPv6 gateway: {config.network_gateway6}")
    if config.network_dns:
        print(f"DNS servers: {', '.join(config.network_dns)}")
    if config.network_interface:
        print(f"Network interface: {config.network_interface}")
    if config.activate_network:
        print("Network activation: SSH-verified live handoff enabled")
    
    if config.enable_rdp:
        print("RDP: Yes")
        print(f"RDP bind address: {config.rdp_bind_address}")
        print(f"RDP allowed sources: {_rdp_access_summary(config)}")
        enabled_channels = ["dynamic-resize"]
        if config.rdp_clipboard:
            enabled_channels.append("clipboard")
        if config.rdp_drive_redirection:
            enabled_channels.append("drive/device")
        if config.rdp_audio:
            enabled_channels.append("audio")
        print(f"RDP enabled channels: {', '.join(enabled_channels)}")
        print("RDP maximum sessions: 1 (shared by the desktop owner and agents)")
        print("RDP disconnected session retention: unlimited, until logout")
        if config.rdp_idle_timeout:
            print(f"RDP idle disconnect: {config.rdp_idle_timeout} seconds")
        else:
            print("RDP idle disconnect: disabled")
    if config.enable_smbclient:
        print("SMB Client: Yes")
    
    if config.desktop != "xfce" and (config.include_desktop or config.enable_rdp):
        print(f"Desktop: {config.desktop}")
    
    if config.browser and config.browser != "brave" and (config.include_desktop or config.include_desktop_apps or config.include_pc_dev_apps or config.include_workstation_dev_apps):
        print(f"Browser: {config.browser}")

    if config.editor:
        print(f"Editor: {config.editor}")
    
    if config.use_flatpak:
        print("Flatpak: Yes")
    if config.install_office:
        print("Office: Yes")
    
    if config.dry_run:
        print("Dry-run: Yes")
    
    if config.custom_steps:
        print(f"Steps: {config.custom_steps}")

    if config.selected_agent_tools():
        print(f"Agent tools: {', '.join(config.selected_agent_tools())}")
    if config.godot_bundles:
        print(f"Godot bundles: {', '.join(config.godot_bundles)}")
    if config.install_sunshine:
        print("Sunshine: Yes (native CachyOS package)")
    if config.install_moonlight:
        print("Moonlight: Yes (native CachyOS package)")
    if config.install_gaming:
        print("Gaming bundle: Yes (native CachyOS packages)")
    effective_web_ports = config.effective_web_ports()
    if effective_web_ports:
        exposure = "source-restricted" if access_sources else "global"
        print(
            f"Managed web TCP ports ({exposure}): "
            + ", ".join(str(port) for port in effective_web_ports)
        )
    if config.browser_automation:
        print(f"Agent browser automation: {config.browser_automation}")
    if config.selected_agent_tools() or config.agent_repos or config.git_access != "none":
        print(f"Agent Git access: {config.git_access}")
    if config.agent_repos or config.git_host != "github.com":
        print(f"Agent Git host: {config.git_host}")
    managed_git_origins = sorted(
        {
            spec[0]
            for spec in (config.git_credentials or []) + (config.git_ca_pems or [])
            if len(spec) == 2
        }
    )
    if managed_git_origins:
        print(f"Managed Git HTTPS origins: {', '.join(managed_git_origins)}")
    elif config.clear_git_credentials:
        print("Managed Git HTTPS credentials: remove")
    if config.agent_config_source:
        print("Agent config source: active user")
    if config.git_auth_source or config.git_auth_file or config.git_auth_token:
        print("GitHub auth: supplied for this setup")
    if config.agent_auth_source or config.agent_auth_files:
        print("Agent auth: supplied for this setup")
    if config.agent_repos:
        print(f"Agent repositories: {len(config.agent_repos)}")
        for git_url in config.agent_repos:
            print(f"  - {git_url}")
    if config.web_interfaces:
        bind = config.web_interface_host or "127.0.0.1"
        print(
            f"Web interfaces: {', '.join(config.web_interfaces)} "
            f"({bind}:{config.web_interface_port})"
        )
        web_sources = config.effective_web_interface_sources()
        if web_sources:
            print(
                "Web interface sources: "
                + ", ".join(web_sources)
            )
        else:
            print("Web interface sources: loopback only")
    if config.device_pairing_providers:
        print(
            "Device pairing: "
            f"{', '.join(config.device_pairing_providers)} "
            f"({config.web_interface_host or '127.0.0.1'}:{config.device_pairing_port})"
        )
        print("Device pairing protection: Nginx Basic Auth + provider-native sessions")
        if config.device_pairing_auth_file or config.device_pairing_auth_username:
            print("Device pairing auth: supplied for this setup")
    if config.web_panel_port is not None:
        scheme = "HTTPS" if config.enable_ssl else "HTTP"
        print(
            f"Web panel: {scheme} on TCP {config.web_panel_port} "
            f"(Basic Auth user: {config.username})"
        )
        if config.web_panel_auth_password is not None:
            print("Web panel auth: supplied for this setup")
    elif config.disable_web_panel:
        print("Web panel: remove")

    if config.deploy_specs:
        print(f"Deployments: {len(config.deploy_specs)} repository(ies)")
        for location, git_url in config.deploy_specs:
            print(f"  - {git_url} -> {location}")
        if config.deployment_mode == "lite":
            print("Deploy mode: Lite (use uploaded repository copy, no remote updates)")
        elif config.deployment_mode == "full":
            print("Deploy mode: Full (upload fresh repository copy, rebuild all deployments)")
        else:
            print("Deploy mode: Default (upload repository copy, redeploy if changed)")
        if config.full_deploy and config.deployment_mode != "full":
            print("Full deploy: Yes (rebuild all deployments even if unchanged)")
        if config.enable_ssl:
            print("SSL: Yes (Let's Encrypt)")
            if config.ssl_email:
                print(f"SSL Email: {config.ssl_email}")
        if config.enable_cloudflare:
            print("Cloudflare: Yes (tunnel preconfiguration)")
    
    if config.enable_samba:
        print("Samba: Yes")
        if config.samba_shares:
            print(f"Samba Shares: {len(config.samba_shares)} share(s)")
            for share in config.samba_shares:
                print(f"  - {share[1]}_{share[0]}: {share[2]}")
    
    if config.smb_mounts:
        print(f"SMB Mounts: {len(config.smb_mounts)} mount(s)")
        for mountpoint, ip, creds, share, subdir in config.smb_mounts:
            username = creds.split(':', 1)[0] if ':' in creds else creds
            print(f"  - {mountpoint} from //{ip}/{share}{subdir} (user: {username})")
    
    if config.sync_specs:
        print(f"Sync Jobs: {len(config.sync_specs)} job(s)")
        for source, dest, interval in config.sync_specs:
            print(f"  - {source} → {dest} ({interval})")

    if config.backup_specs:
        print(f"Backup Jobs: {len(config.backup_specs)} job(s)")
        for source, dest, interval in config.backup_specs:
            print(f"  - {source} → {dest} ({interval})")
    
    if config.scrub_specs:
        print(f"Scrub Jobs: {len(config.scrub_specs)} job(s)")
        for directory, _db_path, redundancy, frequency in config.scrub_specs:
            print(f"  - {directory} ({redundancy}, {frequency})")
    
    notification_level = normalize_notification_level(config.notification_level)
    notification_configs = parse_notification_args(
        config.notify_specs,
        notification_level=notification_level,
        strict_https=config.notification_strict_https,
    )
    if notification_configs:
        tls_mode = (
            "strict HTTPS"
            if config.notification_strict_https is True
            else "self-signed HTTPS accepted"
        )
        print(
            f"Notifications: {len(notification_configs)} target(s) "
            f"(level: {notification_level}; {tls_mode})"
        )
        for notification_config in notification_configs:
            print(
                f"  - {notification_config.type}: "
                + notification_target_summary(
                    notification_config.type,
                    notification_config.target,
                )
            )
    elif config.notification_level is not None:
        print(f"Notifications: no targets (level: {notification_level})")

    if config.antistatic_server:
        print(f"Antistatic server: {config.antistatic_server}")
        print("  - Persistent reports: /var/lib/antistatic")
        if config.antistatic_admin:
            print(f"  - Admin user: {config.antistatic_admin}")

    if config.gogs:
        print(f"Gogs: {' '.join(config.gogs)}")
    if config.homebox:
        print(f"HomeBox: {' '.join(config.homebox)}")
        print(f"  Version: {config.homebox_version or 'retain installed version; v0.26.2 on first setup'}")
    elif config.homebox == []:
        print("HomeBox: remove service and proxy, retain inventory")
    
    print("=" * 60)
    print()
