"""Pure setup contracts for Headscale hosting, clients and managed Unbound.

Registry resolution belongs to the controller. Resolver addresses here are
unverified candidates; no setup field authorizes DNS publication or migration.
Explicit disable preserves native data and identity; leave/purge are separate.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import ipaddress
import re
import shlex
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from lib.validation import validate_network_ip, validate_no_control_characters
from lib.validators import validate_host

if TYPE_CHECKING:
    from lib.config import SetupConfig

SUPPORTED_PROFILES = frozenset({
    "workstation_desktop", "pc_dev", "workstation_dev", "agent_workstation",
    "agent_code_vm", "control_plane", "agent_vm", "server_dev", "server_web",
    "server_github_runner", "server_lite",
})
HOST_FIELDS = (
    "headscale_domain", "headscale_base_domain", "headscale_onboarding_domain",
    "headscale_onboarding_control_url",
    "headscale_public_ipv4", "headscale_public_ipv6", "headscale_resolvers",
)
CLIENT_FIELDS = ("tailscale", "tailscale_server", "tailscale_server_id", "tailscale_login_server")
CONTROLLER_FIELDS = ("headscale_name", "headscale_registration_id", "headscale_registration_binding", "tailscale_tags")
FIELDS = ("headscale", "unbound", *HOST_FIELDS, *CLIENT_FIELDS, *CONTROLLER_FIELDS)
# Local listeners reserved for the later service installer (TCP); STUN is UDP.
HEADSCALE_TCP_PORTS = frozenset({8080, 9090, 50443})


def validate_headscale_url(value: str) -> str:
    """Accept a credential-free HTTPS origin, normalized for identity checks."""
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise ValueError("Tailscale login server must be an HTTPS origin")
    validate_no_control_characters(value, "Tailscale login server")
    if any(c.isspace() for c in value) or any(c in value for c in "\\%$`;|&<>\"'(){}"):
        raise ValueError("Invalid Tailscale login server")
    try:
        parsed = urlsplit(value)
        port = parsed.port
        host = parsed.hostname
        valid_host = bool(host and validate_host(host))
        if host and ":" in host:
            valid_host = ipaddress.ip_address(validate_network_ip(host)).version == 6
        if (parsed.scheme != "https" or not valid_host
                or parsed.username is not None or parsed.password is not None
                or parsed.path not in {"", "/"} or "?" in value or "#" in value
                or parsed.netloc.endswith(":")):
            raise ValueError("Invalid HTTPS origin")
        if port is not None and not 1 <= port <= 65535:
            raise ValueError("Invalid HTTPS port")
    except ValueError as exc:
        raise ValueError("Tailscale login server must be a plain HTTPS origin without credentials, query or fragment") from exc
    normalized_host = host.lower().removesuffix(".")
    if ":" in normalized_host:
        normalized_host = "[" + validate_network_ip(normalized_host) + "]"
    return "https://" + normalized_host + (f":{port}" if port not in {None, 443} else "")


def _reference(value: str, label: str) -> str:
    if (not isinstance(value, str) or value != value.strip()
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_. -]{0,63}", value)):
        raise ValueError(f"Invalid {label}; use 1-64 simple characters")
    return value


def _domain(value: str) -> str:
    if not isinstance(value, str) or not validate_host(value) or "." not in value.rstrip("."):
        raise ValueError("Headscale domains must be DNS hostnames")
    try:
        ipaddress.ip_address(value.removesuffix("."))
    except ValueError:
        return value.lower().removesuffix(".")
    raise ValueError("Headscale domains must not be IP addresses")


def add_headscale_arguments(parser: argparse.ArgumentParser, *, for_remote: bool = False) -> None:
    """Register independent tri-state capabilities and non-secret values."""
    for name, help_text in (
        ("headscale", "Manage Headscale hosting; disable preserves server data"),
        ("unbound", "Manage Unbound on an enrolled Debian DNS node; does not publish DNS"),
    ):
        parser.add_argument(f"--{name}", action=argparse.BooleanOptionalAction, default=None, help=help_text)
    selection = parser.add_mutually_exclusive_group()
    if not for_remote:
        selection.add_argument("--tailscale", dest="tailscale_server", metavar="NAME", help="Join the saved Headscale server selected by name")
        parser.add_argument("--headscale-name", help="Register this managed control server by name")
        parser.add_argument("--tailscale-tag", dest="tailscale_tags", action="append", help="Authorize automatic enrollment with this service tag; repeatable")
    selection.add_argument("--tailscale-login-server", metavar="HTTPS_URL", type=validate_headscale_url, help="Join this explicit HTTPS control endpoint")
    selection.add_argument("--no-tailscale", dest="tailscale", action="store_false", default=None, help="Disable managed client setup; preserve device identity (does not leave)")
    for name in HOST_FIELDS:
        flag = "--headscale-resolver" if name == "headscale_resolvers" else "--" + name.replace("_", "-")
        parser.add_argument(
            flag,
            dest=name, action="append" if name == "headscale_resolvers" else "store",
            **({"type": validate_headscale_url} if name == "headscale_onboarding_control_url" else {}),
            help="Candidate overlay resolver IP; repeat; publication requires later validation" if name == "headscale_resolvers" else name.replace("_", " "),
        )


def headscale_from_args(args: argparse.Namespace) -> dict[str, Any]:
    # Read declared namespace values only; omitted fields must remain inert
    # for callers supplying partial argument objects as well as argparse.
    values = {name: vars(args).get(name) for name in FIELDS}
    if values["tailscale_server"] is not None or values["tailscale_login_server"] is not None:
        if values["tailscale"] is False:
            raise ValueError("Cannot select and disable Tailscale together")
        values["tailscale"] = True
    return values


def validate_headscale_settings(config: SetupConfig, *, complete: bool = False) -> None:
    """Validate patch syntax early; require resolved values before transport."""
    if config.headscale_name is not None:
        _reference(config.headscale_name, "managed server name")
    if config.headscale_registration_id is not None:
        import uuid

        if (not isinstance(config.headscale_registration_id, str)
                or str(uuid.UUID(config.headscale_registration_id)) != config.headscale_registration_id):
            raise ValueError("Invalid managed server registration identity")
    if config.headscale_registration_binding is not None:
        from lib.headscale_transport import parse_ssh_binding

        binding = config.headscale_registration_binding
        if type(binding) is not dict or set(binding) != {"endpoint", "ssh"} or not config.headscale_registration_id:
            raise ValueError("Invalid retained hosting authority")
        validate_headscale_url(binding["endpoint"])
        parse_ssh_binding(binding["ssh"])
    if config.tailscale_tags is not None:
        from lib.headscale_admin import AdminError, approved_tags

        try:
            config.tailscale_tags = approved_tags(config.tailscale_tags)
        except AdminError as exc:
            raise ValueError(str(exc)) from exc
        if config.tailscale is not False and not config.tailscale_server:
            raise ValueError("Automatic enrollment requires a named saved network")
    for name in ("headscale", "tailscale", "unbound"):
        value = getattr(config, name)
        if value is not None and type(value) is not bool:
            raise ValueError(f"{name} must be boolean or None")
    if not any(getattr(config, name) is not None for name in FIELDS):
        return
    if config.system_type not in SUPPORTED_PROFILES or config.container_base != "debian":
        raise ValueError("Headscale, Tailscale and Unbound setup require a supported Debian profile")
    for name in ("headscale_domain", "headscale_base_domain", "headscale_onboarding_domain"):
        if getattr(config, name) is not None:
            setattr(config, name, _domain(getattr(config, name)))
    for name, version in (("headscale_public_ipv4", 4), ("headscale_public_ipv6", 6)):
        value = getattr(config, name)
        if value is not None:
            if not isinstance(value, str):
                raise ValueError(f"{name} must be an IP string")
            address = ipaddress.ip_address(validate_network_ip(value, name))
            if (address.version != version or address.is_unspecified or address.is_loopback
                    or address.is_multicast or address.is_link_local):
                raise ValueError(f"{name} requires a unicast IPv{version} address reachable by clients")
            setattr(config, name, str(address))
    if config.headscale_resolvers is not None:
        if not isinstance(config.headscale_resolvers, list) or not config.headscale_resolvers:
            raise ValueError("headscale_resolvers must be a nonempty list of candidate overlay IPs")
        addresses = []
        for value in config.headscale_resolvers:
            if not isinstance(value, str):
                raise ValueError("Resolver candidates must be IP strings")
            address = ipaddress.ip_address(validate_network_ip(value, "Headscale resolver"))
            network = ipaddress.ip_network("100.64.0.0/10" if address.version == 4 else "fd7a:115c:a1e0::/48")
            if address not in network:
                raise ValueError("Resolver candidates require overlay addresses; publication is a separate operation")
            addresses.append(str(address))
        config.headscale_resolvers = list(dict.fromkeys(addresses))
    if config.tailscale_server is not None:
        _reference(config.tailscale_server, "saved server name")
    if config.tailscale_server_id is not None:
        _reference(config.tailscale_server_id, "saved server ID")
        if config.tailscale_login_server is None:
            raise ValueError("Saved server ID requires its resolved login endpoint")
    if config.tailscale_login_server is not None:
        config.tailscale_login_server = validate_headscale_url(config.tailscale_login_server)
    if config.headscale_onboarding_control_url is not None:
        config.headscale_onboarding_control_url = validate_headscale_url(config.headscale_onboarding_control_url)
    selected = config.tailscale_server is not None or config.tailscale_login_server is not None
    if config.tailscale_server and config.tailscale is False:
        raise ValueError("Cannot select and disable Tailscale together")
    # A disabled saved config retains its endpoint/ID as recovery metadata.
    # CLI alternatives reject selecting an endpoint and opting out together.
    if selected and config.tailscale is not False:
        config.tailscale = True
    if config.tailscale_server and config.tailscale_login_server and not config.tailscale_server_id:
        raise ValueError("Named and explicit login selections are mutually exclusive; resolved names require a stable server ID")
    suffix = config.headscale_base_domain
    control = config.headscale_domain
    onboarding = config.headscale_onboarding_domain
    onboarding_control = config.headscale_onboarding_control_url
    if config.headscale and control and onboarding_control and onboarding_control != "https://" + control:
        raise ValueError("Hosted Headscale onboarding must use its own control endpoint")
    # Static hosting metadata never selects the local native client. The legacy
    # client URL remains a fallback only when no dedicated endpoint was given.
    page_control = (control if config.headscale else
                    urlsplit(onboarding_control or config.tailscale_login_server or "").hostname)
    if onboarding and page_control and onboarding == page_control:
        raise ValueError("Control and onboarding hostnames must differ")
    if control and onboarding and control == onboarding:
        raise ValueError("Control and onboarding hostnames must differ")
    for hostname in (control, onboarding, page_control if onboarding else None):
        if suffix and hostname and (hostname == suffix or hostname.endswith("." + suffix) or suffix.endswith("." + hostname)):
            raise ValueError("MagicDNS suffix collides with a public hostname")
    if config.headscale and config.unbound:
        raise ValueError("Unbound must run on a separate enrolled DNS node")
    if config.headscale and config.tailscale and config.tailscale_login_server:
        endpoint = urlsplit(config.tailscale_login_server).hostname
        identities = {control, config.host.lower().rstrip("."), (config.target_host or "").lower().rstrip("."), config.headscale_public_ipv4, config.headscale_public_ipv6}
        if endpoint in identities:
            raise ValueError("The Headscale control host cannot join its own network")
    if config.headscale:
        _validate_host_conflicts(config)
    if config.unbound and 53 in _declared_tcp_ports(config):
        raise ValueError("Unbound DNS TCP 53 conflicts with another service")
    if config.unbound and config.tailscale is False:
        raise ValueError("Managed Unbound requires an enabled Tailscale client")
    if complete:
        if onboarding_control and not onboarding:
            raise ValueError("Onboarding control URL requires an onboarding domain")
        if onboarding and not page_control:
            raise ValueError("Onboarding requires a trusted Headscale HTTPS endpoint")
        if config.headscale and (not control or not suffix or not (config.headscale_public_ipv4 or config.headscale_public_ipv6)):
            raise ValueError("Headscale hosting requires control domain, MagicDNS base domain and public relay IP")
        if config.tailscale and not config.tailscale_login_server:
            raise ValueError("Resolve the saved Tailscale selection on the controller before remote setup")
        if config.unbound and not (config.tailscale and config.tailscale_login_server):
            raise ValueError("Managed Unbound requires an explicitly selected enrolled network")


def _validate_host_conflicts(config: SetupConfig) -> None:
    """Reject declared hostname reuse and direct listener collisions, without I/O."""
    services = [config.antistatic_server, config.antistatic_db, config.webhook_domain]
    services.extend(values[0] for values in (config.gogs, config.homebox) if values)
    services.extend(target.split("/")[0] for spec in config.deploy_specs or [] if spec for target in spec[0].split(","))
    for spec in services:
        if spec and spec.split(":")[0].lower().rstrip(".") in {
            config.headscale_domain, config.headscale_onboarding_domain,
        }:
            ingress = "Cloudflare ingress" if config.enable_cloudflare else "service ingress"
            raise ValueError(f"Headscale hostname conflicts with {ingress}")
    if config.antistatic_server:
        raise ValueError("Headscale STUN UDP 3478 conflicts with antistatic-server")
    if config.gogs and not config.enable_cloudflare and config.gogs[0].rsplit(":", 1)[-1] in {"80", "443"}:
        raise ValueError("Direct Gogs listener conflicts with Headscale HTTPS ingress")
    if any(port in HEADSCALE_TCP_PORTS for port in _declared_tcp_ports(config)):
        raise ValueError("Headscale local TCP listener conflicts with another service")


def _declared_tcp_ports(config: SetupConfig) -> list[int]:
    ports = [port for port in (config.web_panel_port, config.privilege_broker_port, config.homebox_port) if port is not None]
    if config.web_interfaces:
        ports.append(config.web_interface_port)
    if config.device_pairing_providers:
        ports.append(config.device_pairing_port)
    if config.enable_rdp:
        ports.append(3389)
    for spec in (config.antistatic_server, config.antistatic_db, *(v[0] for v in (config.gogs, config.homebox) if v)):
        if spec and ":" in spec and spec.rsplit(":", 1)[-1].isdigit():
            ports.append(int(spec.rsplit(":", 1)[-1]))
    return ports


def headscale_args(config: SetupConfig, *, for_remote: bool = False) -> list[str]:
    """Reconstruct selection locally; transport only the resolved endpoint."""
    validate_headscale_settings(config, complete=for_remote)
    result = []
    if not for_remote:
        if config.headscale_name:
            result.append("--headscale-name " + shlex.quote(config.headscale_name))
        if config.tailscale is not False:
            result.extend("--tailscale-tag " + shlex.quote(tag) for tag in config.tailscale_tags or [])
    for name in ("headscale", "unbound"):
        if getattr(config, name) is not None:
            result.append("--" + ("" if getattr(config, name) else "no-") + name)
    if config.tailscale is False:
        result.append("--no-tailscale")
    elif config.tailscale_server and not for_remote:
        result.append("--tailscale " + shlex.quote(config.tailscale_server))
    elif config.tailscale_login_server:
        result.append("--tailscale-login-server " + shlex.quote(config.tailscale_login_server))
    for name in HOST_FIELDS:
        value = getattr(config, name)
        if name == "headscale_resolvers":
            # Candidate metadata belongs on the controller until health/identity
            # verification and a separate explicit publication operation exist.
            if not for_remote:
                result.extend("--headscale-resolver " + shlex.quote(v) for v in value or [])
        elif value is not None:
            result.append("--" + name.replace("_", "-") + " " + shlex.quote(value))
    return result


def merge_headscale_settings(cached: SetupConfig, patch: SetupConfig, *, preserve_keys: set[str] | None = None) -> dict[str, Any]:
    """Merge tri-state patches while retaining the enrollment endpoint binding.

    Off does not leave or forget the network. Re-enabling a different endpoint
    still requires the future explicit migration operation. Stable IDs permit
    renames, never silent replacement records or endpoint edits.
    """
    preserved = preserve_keys or set()
    values = {name: deepcopy(getattr(cached, name)) for name in FIELDS}
    for name in ("headscale", "unbound", *HOST_FIELDS, "headscale_name"):
        if name not in preserved and getattr(patch, name) is not None:
            values[name] = deepcopy(getattr(patch, name))
    if patch.tailscale_tags is not None and "tailscale_tags" not in preserved:
        if cached.tailscale_tags is not None and patch.tailscale_tags != cached.tailscale_tags:
            raise ValueError("Changing saved enrollment tags requires explicit device migration")
        values["tailscale_tags"] = deepcopy(patch.tailscale_tags)
    if not preserved.intersection(CLIENT_FIELDS):
        if patch.tailscale is False:
            values["tailscale"] = False
        elif patch.tailscale is not None:
            old_url, new_url = cached.tailscale_login_server, patch.tailscale_login_server
            if old_url and (new_url != old_url or (cached.tailscale_server_id and patch.tailscale_server_id != cached.tailscale_server_id)):
                raise ValueError("Changing a saved Tailscale endpoint or server identity requires explicit device migration")
            if patch.tailscale_server and not patch.tailscale_server_id:
                raise ValueError("Resolve saved Tailscale selections by stable ID before merging")
            values.update({name: deepcopy(getattr(patch, name)) for name in CLIENT_FIELDS})
    # Keep the endpoint binding on disable for migration guards, clearing the
    # active friendly selector so ordinary reconstruction emits only opt-out.
    if values["tailscale"] is False:
        values["tailscale_server"] = None
    return values
