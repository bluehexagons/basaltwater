"""Configuration validation shared by controller and target setup."""

from __future__ import annotations

import ipaddress
import json

from lib.privilege_auth import validate_auth
from lib.privilege_policy import DEFAULT_REQUEST_TTL_SECONDS, validate_policy

DEFAULT_PRIVILEGE_BROKER_PORT = 9444


def broker_enabled(config) -> bool:
    """Install the local engine whenever either approval surface needs it."""
    return not config.disable_privilege_broker and (
        config.web_panel_port is not None or config.privilege_broker_port is not None
    )


def integrated_approvals(config) -> bool:
    return broker_enabled(config) and config.web_panel_port is not None


def approval_origin(config) -> str:
    """Prefer the main panel for request review when it is installed."""
    if not integrated_approvals(config):
        return privilege_broker_origin(config)
    host = config.privilege_broker_host or config.target_host or config.host
    if "/" in host:
        host = str(ipaddress.IPv4Interface(host).ip)
    host = host.strip("[]").lower()
    displayed = f"[{host}]" if ":" in host else host
    scheme = "https" if config.enable_ssl else "http"
    return f"{scheme}://{displayed}:{config.web_panel_port}"


def privilege_broker_origin(config) -> str:
    """Build the approval origin from managed host metadata and its port."""
    host = config.privilege_broker_host or config.host
    if not config.privilege_broker_host and "/" in host:
        try:
            host = str(ipaddress.IPv4Interface(host).ip)
        except (ipaddress.AddressValueError, ipaddress.NetmaskValueError, TypeError) as exc:
            raise ValueError("Approval host CIDR must be a valid IPv4 address and prefix") from exc
    host = host.strip("[]").lower()
    displayed_host = f"[{host}]" if ":" in host else host
    return f"https://{displayed_host}:{config.privilege_broker_port}"


def validate_broker_settings(config) -> None:
    if config.disable_approval_panel is not None and type(config.disable_approval_panel) is not bool:
        raise ValueError("disable_approval_panel must be a boolean")
    if config.disable_approval_panel:
        if config.privilege_broker_auth:
            raise ValueError("Cannot change approval panel credentials while removing it")
        config.privilege_broker_port = None
    if config.disable_privilege_broker:
        if config.privilege_broker_auth:
            raise ValueError("Cannot change approval credentials while disabling the broker")
        config.privilege_broker_port = None
        return
    if config.privilege_broker_auth:
        validate_auth(json.loads(config.privilege_broker_auth))
    if integrated_approvals(config):
        validate_policy({"version": 1, "machine": "0" * 32, "origin": approval_origin(config),
                         "requester_uid": 1000, "ttl_seconds": DEFAULT_REQUEST_TTL_SECONDS,
                         "services": {}, "reboot": "approve"})
    if config.privilege_broker_port is None:
        # A password-only patch is resolved against saved configuration later.
        return
    if config.nopasswd or config.harden_agent or config.harden_user:
        raise ValueError("--approval-panel cannot be combined with --nopasswd or hardened modes; explicitly disable the other posture")
    if config.username == "root" or config.system_type not in {"agent_vm", "agent_code_vm", "agent_workstation", "custom_steps", "server_github_runner"}:
        raise ValueError("--approval-panel requires a non-root agent or runner VM operator")
    if config.machine_type not in {"auto", "vm"}:
        raise ValueError("--approval-panel is supported on VMs only")
    if type(config.privilege_broker_port) is not int or not 1024 <= config.privilege_broker_port <= 65535:
        raise ValueError("--approval-panel port must be between 1024 and 65535")
    validate_policy({"version": 1, "machine": "0" * 32, "origin": privilege_broker_origin(config),
                     "requester_uid": 1000, "ttl_seconds": DEFAULT_REQUEST_TTL_SECONDS,
                     "services": {}, "reboot": "approve"})
    # The shared gateway reserves 8443 plus its automatic forward range.
    reserved = set(range(8443, 9000)) | {config.web_panel_port}
    if config.web_interfaces:
        reserved.add(config.web_interface_port)
    if config.device_pairing_providers:
        reserved.add(config.device_pairing_port)
    if config.privilege_broker_port in reserved:
        raise ValueError("Approval HTTPS port conflicts with another managed interface")
