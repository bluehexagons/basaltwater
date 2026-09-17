"""Configuration validation shared by controller and target setup."""

from __future__ import annotations

import json
from urllib.parse import urlsplit

from lib.privilege_auth import validate_auth
from lib.privilege_policy import validate_policy

DEFAULT_PRIVILEGE_BROKER_PORT = 9444


def legacy_privilege_broker_port(origin: object) -> int:
    """Read the port from a valid pre-managed-endpoint cache origin."""
    if not isinstance(origin, str):
        raise ValueError("Legacy privilege broker origin must be an HTTPS URL")
    try:
        validate_policy({"version": 1, "machine": "0" * 32, "origin": origin,
                         "requester_uid": 1000, "ttl_seconds": 300, "services": {}, "reboot": "approve"})
    except ValueError as exc:
        raise ValueError("Legacy privilege broker origin must be a valid HTTPS URL") from exc
    return urlsplit(origin).port


def privilege_broker_origin(config) -> str:
    """Build the approval origin from managed host metadata and its port."""
    host = config.privilege_broker_host or config.host
    host = host.strip("[]").lower()
    displayed_host = f"[{host}]" if ":" in host else host
    return f"https://{displayed_host}:{config.privilege_broker_port}"


def validate_broker_settings(config) -> None:
    if config.disable_privilege_broker:
        if config.privilege_broker_auth:
            raise ValueError("Cannot change approval credentials while disabling the broker")
        config.privilege_broker_port = None
        return
    if config.privilege_broker_auth:
        validate_auth(json.loads(config.privilege_broker_auth))
    if config.privilege_broker_port is None:
        # A password-only patch is resolved against saved configuration later.
        return
    if config.nopasswd or config.harden_agent or config.harden_user:
        raise ValueError("--privilege-broker cannot be combined with --nopasswd or hardened modes; explicitly disable the other posture")
    if config.username == "root" or config.system_type not in {"agent_vm", "agent_code_vm", "agent_workstation", "custom_steps"}:
        raise ValueError("--privilege-broker requires a non-root agent VM user")
    if config.machine_type not in {"auto", "vm"}:
        raise ValueError("--privilege-broker is supported on VMs only")
    if type(config.privilege_broker_port) is not int or not 1024 <= config.privilege_broker_port <= 65535:
        raise ValueError("--privilege-broker port must be between 1024 and 65535")
    validate_policy({"version": 1, "machine": "0" * 32, "origin": privilege_broker_origin(config),
                     "requester_uid": 1000, "ttl_seconds": 300, "services": {}, "reboot": "approve"})
    # The shared gateway reserves 8443 plus its automatic forward range.
    reserved = set(range(8443, 9000)) | {config.web_panel_port}
    if config.web_interfaces:
        reserved.add(config.web_interface_port)
    if config.device_pairing_providers:
        reserved.add(config.device_pairing_port)
    if config.privilege_broker_port in reserved:
        raise ValueError("Approval HTTPS port conflicts with another managed interface")
