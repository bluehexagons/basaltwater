"""Validate the optional Proxmox local kiosk without resolving its homepage."""

from __future__ import annotations

import re
import unicodedata
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from lib.machine_state import can_manage_system_services
from lib.validation import (
    validate_network_ip,
    validate_network_port,
    validate_no_control_characters,
)
from lib.validators import validate_host, validate_ip_address

if TYPE_CHECKING:
    from lib.config import SetupConfig


def validate_kiosk_url(value: str) -> str:
    """Return an unchanged HTTP(S) URL with an unambiguous, credential-free authority."""
    if not isinstance(value, str) or not value:
        raise ValueError("--kiosk-url requires a non-empty HTTP or HTTPS URL")
    validate_no_control_characters(value, "--kiosk-url")
    if any(char.isspace() or unicodedata.category(char) in {"Cc", "Cf"} for char in value) or "\\" in value:
        raise ValueError("--kiosk-url must not contain whitespace, controls, or backslashes")
    if re.search(r"%(?![0-9A-Fa-f]{2})", value):
        raise ValueError("--kiosk-url contains an invalid percent escape")
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or "@" in parsed.netloc:
            raise ValueError("invalid scheme or authority")
        # Parse the raw authority as well: urlsplit accepts an empty port and
        # text following a closing IPv6 bracket that browsers interpret differently.
        if parsed.netloc.startswith("["):
            match = re.fullmatch(r"\[([^\[\]%]+)\](?::([0-9]+))?", parsed.netloc)
            if match is None or ":" not in match[1]:
                raise ValueError("invalid IPv6 authority")
            validate_network_ip(match[1], "kiosk IPv6 address")
        else:
            match = re.fullmatch(r"([^:]+)(?::([0-9]+))?", parsed.netloc)
            if match is None or not validate_host(match[1]):
                raise ValueError("invalid hostname")
            host = match[1].removesuffix(".")
            if re.fullmatch(r"[0-9.]+", host):
                if not validate_ip_address(host):
                    raise ValueError("invalid IPv4 address")
                validate_network_ip(host, "kiosk IPv4 address")
        if match[2] is not None:
            validate_network_port(int(match[2]), "Kiosk URL port")
    except ValueError as exc:
        raise ValueError("--kiosk-url requires a valid HTTP or HTTPS URL without credentials") from exc
    return value


def validate_kiosk_settings(config: SetupConfig, complete: bool = True) -> None:
    """Validate a plan, allowing URL-only partial patches before mode inheritance."""
    mode = config.kiosk_mode
    if mode is not None and (not isinstance(mode, str) or mode not in {"web", "cli", "off"}):
        raise ValueError("kiosk_mode must be web, cli, off, or None")
    if config.kiosk_url is not None:
        validate_kiosk_url(config.kiosk_url)
        if mode == "off" or (complete and mode is None):
            raise ValueError("--kiosk-url requires an enabled kiosk (--kiosk web or cli)")
    if mode is None and config.kiosk_url is None:
        return
    if config.system_type != "server_proxmox":
        raise ValueError("--kiosk, --no-kiosk, and --kiosk-url require the server_proxmox profile")
    if mode in {"web", "cli"}:
        if config.include_desktop or config.enable_rdp or config.guacamole:
            raise ValueError("--kiosk cannot be combined with a shared desktop, --desktop, --rdp, or --guacamole")
        if complete and config.machine_type != "auto" and not can_manage_system_services(config.machine_type):
            raise ValueError("--kiosk requires a machine that can manage persistent system services")
