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
    if len(value.encode("utf-8")) > 8192:
        raise ValueError("--kiosk-url must be at most 8192 UTF-8 bytes")
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


def validate_kiosk_destinations(value: list[dict[str, str]] | None) -> list[dict[str, str]] | None:
    """Return the unchanged destination list after validating its saved schema."""
    if value is None:
        return None
    if not isinstance(value, list) or len(value) > 16:
        raise ValueError("kiosk_destinations must be a list of at most 16 destinations")
    names: set[str] = set()
    for destination in value:
        if not isinstance(destination, dict) or set(destination) != {"name", "url"}:
            raise ValueError("Each kiosk destination must contain exactly name and url")
        name = destination["name"]
        if not isinstance(name, str) or not name or name != name.strip() or len(name) > 80:
            raise ValueError("Kiosk destination names must be trimmed, non-empty strings of at most 80 characters")
        validate_no_control_characters(name, "Kiosk destination name")
        if any(unicodedata.category(char) in {"Cc", "Cf"} for char in name):
            raise ValueError("Kiosk destination names must not contain control or format characters")
        if name.casefold() in names:
            raise ValueError("Kiosk destination names must be unique (case-insensitive)")
        names.add(name.casefold())
        validate_kiosk_url(destination["url"])
    return value


def parse_kiosk_destination(value: str) -> dict[str, str]:
    """Parse one NAME=URL argument, preserving equals signs in the URL."""
    name, separator, url = value.partition("=")
    if not separator:
        raise ValueError("--kiosk-destination requires NAME=URL")
    if any(unicodedata.category(char) in {"Cc", "Cf"} for char in name):
        raise ValueError("Kiosk destination names must not contain control or format characters")
    destination = {"name": name.strip(), "url": url}
    validate_kiosk_destinations([destination])
    return destination


def validate_kiosk_settings(config: SetupConfig, complete: bool = True) -> None:
    """Validate a plan, allowing partial homepage patches before mode inheritance."""
    mode = config.kiosk_mode
    if mode is not None and (not isinstance(mode, str) or mode not in {"web", "cli", "off"}):
        raise ValueError("kiosk_mode must be web, cli, off, or None")
    if config.kiosk_url is not None:
        validate_kiosk_url(config.kiosk_url)
        if mode == "off" or (complete and mode is None):
            raise ValueError("--kiosk-url requires an enabled kiosk (--kiosk web or cli)")
    destinations = validate_kiosk_destinations(config.kiosk_destinations)
    if destinations and (mode == "off" or (complete and mode is None)):
        raise ValueError("--kiosk-destination requires an enabled kiosk (--kiosk web or cli)")
    if mode is None and config.kiosk_url is None and destinations is None:
        return
    if config.system_type != "server_proxmox":
        raise ValueError("--kiosk, --no-kiosk, --kiosk-url, --kiosk-destination, and --clear-kiosk-destinations require the server_proxmox profile")
    if mode in {"web", "cli"}:
        if config.include_desktop or config.enable_rdp or config.guacamole:
            raise ValueError("--kiosk cannot be combined with a shared desktop, --desktop, --rdp, or --guacamole")
        if complete and config.machine_type != "auto" and not can_manage_system_services(config.machine_type):
            raise ValueError("--kiosk requires a machine that can manage persistent system services")
        if complete and config.machine_type not in {"auto", "hardware", "vm"}:
            raise ValueError("--kiosk requires a hardware or VM console, not a container or WSL")
