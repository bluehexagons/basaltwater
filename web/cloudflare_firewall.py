"""Reconcile direct web access alongside a Cloudflare tunnel."""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Callable
from typing import Any

from lib.validation import validate_network_ip_or_cidr


RULE_PREFIX = "basaltwater cloudflare web "
WEB_RULE_PREFIX = "basaltwater web TCP"
ORIGIN_PORTS = (80, 443)
_INBOUND_RULE = re.compile(r'^\[\s*\d+\]\s+(.+?)\s+(?:ALLOW|LIMIT)\s+IN\s+(.+)$')
_PORT_SPEC = re.compile(r'\d+(?::\d+)?(?:,\d+(?::\d+)?)*(?:/(?:tcp|udp))?')
_POLICY_PORTS = set(ORIGIN_PORTS)


def validate_direct_service_port(port: int) -> None:
    """Keep a direct service from claiming a Cloudflare-managed origin port."""
    if port in ORIGIN_PORTS:
        raise ValueError(f"TCP {port} is reserved by Cloudflare origin policy; select another direct service port")


def _origin_port(line: str) -> int | None:
    match = _INBOUND_RULE.match(line.split("#", 1)[0].strip())
    if match:
        destination = match[1].removesuffix(" (v6)")
        return next((port for port in ORIGIN_PORTS if destination == f"{port}/tcp"), None)
    return None


def setup_origin_rule(line: str, comment: str) -> bool:
    """Identify origin rules from before Cloudflare owned the standard web ports."""
    port = _origin_port(line)
    return port is not None and (
        comment == f"{WEB_RULE_PREFIX} {port}"
        or comment.startswith(f"{WEB_RULE_PREFIX} {port} source ")
    )


def legacy_gogs_origin_rule(line: str, comment: str) -> bool:
    """Identify standard web ports that older direct Gogs setups owned."""
    port = _origin_port(line)
    return port is not None and (
        comment in {"gogs web", "gogs direct HTTP"}
        or comment.startswith(f"basaltwater Gogs {port}/tcp source ")
    )


def normalize_access_sources(sources: list[str] | None) -> list[str]:
    """Validate and deduplicate the explicit or inferred direct-access networks."""
    if sources is None:
        return []
    if not isinstance(sources, list):
        raise ValueError("Cloudflare access sources must be a list of IPs or CIDRs")
    normalized = []
    for source in sources:
        if not isinstance(source, str):
            raise ValueError("Cloudflare access sources require IP addresses or CIDRs")
        value = validate_network_ip_or_cidr(source, "Cloudflare access source")
        network = str(ipaddress.ip_network(value, strict=False))
        if network not in normalized:
            normalized.append(network)
    return normalized


def _active_rules(command_runner: Callable[..., Any]) -> str:
    """Require an inspectable active firewall before claiming policy success."""
    result = command_runner(['ufw', 'status', 'numbered'], check=False, capture_output=True)
    if result.returncode != 0 or not isinstance(result.stdout, str) or 'Status: active' not in result.stdout:
        raise RuntimeError("Could not inspect active Cloudflare web firewall rules")
    return result.stdout


def _policy_ports(destination: str, command_runner: Callable[..., Any], profiles: dict[str, set[int]]) -> set[int]:
    """Resolve numeric rules and application profiles to relevant TCP ports."""
    destination = destination.split(' on ', 1)[0].removesuffix(' (v6)')
    if destination == 'Anywhere':
        return _POLICY_PORTS
    try:
        ipaddress.ip_network(destination, strict=False)
    except ValueError:
        pass
    else:
        # A destination address without a port opens all ports at that address.
        return _POLICY_PORTS
    port_spec = destination.rsplit(' ', 1)[-1]
    if _PORT_SPEC.fullmatch(port_spec):
        specs = [port_spec]
    else:
        if destination in profiles:
            return profiles[destination]
        result = command_runner(['ufw', 'app', 'info', destination], check=False, capture_output=True)
        match = re.search(r'^Ports?:[ \t]*\n([\s\S]+)', result.stdout or '', re.MULTILINE)
        if result.returncode != 0 or not match:
            raise RuntimeError(f"Could not inspect UFW application profile: {destination}")
        specs = re.split(r'[\s|]+', match[1].strip())
        if not all(_PORT_SPEC.fullmatch(spec) for spec in specs):
            raise RuntimeError(f"Could not inspect UFW application profile ports: {destination}")
    ports = set()
    for spec in specs:
        if spec.endswith('/udp'):
            continue
        for item in spec.split('/')[0].split(','):
            bounds = [int(value) for value in item.split(':')]
            ports.update(port for port in _POLICY_PORTS if bounds[0] <= port <= bounds[-1])
    profiles[destination] = ports
    return ports


def _verify_access_policy(stdout: str, *, allow_direct: bool, sources: list[str], command_runner: Callable[..., Any]) -> None:
    """Refuse success when remaining rules grant access outside the web policy."""
    if allow_direct:
        return
    networks = [ipaddress.ip_network(source) for source in sources]
    profiles: dict[str, set[int]] = {}
    for line in stdout.splitlines():
        match = _INBOUND_RULE.match(line.split('#', 1)[0].strip())
        if not match:
            continue
        ports = _policy_ports(match[1], command_runner, profiles)
        if not ports:
            continue
        source = match[2].split()[0]
        try:
            if source == 'Anywhere':
                source = '::/0' if '(v6)' in match[2] else '0.0.0.0/0'
            network = ipaddress.ip_network(source, strict=False)
        except ValueError as exc:
            raise RuntimeError(f"Could not inspect UFW rule source: {line.strip()}") from exc
        allowed_source = any(
            network.version == allowed.version and network.subnet_of(allowed)
            for allowed in networks
        )
        if not allowed_source:
            raise RuntimeError(
                "UFW rule grants access outside the requested Cloudflare policy: "
                f"{line.strip()}. Review 'ufw status numbered' and remove or narrow this rule."
            )


def reconcile_tunnel_firewall(
    *, allow_direct: bool, sources: list[str], command_runner: Callable[..., Any],
) -> None:
    """Apply only the origin web policy on an already active firewall."""
    if not isinstance(allow_direct, bool):
        raise ValueError("Cloudflare direct access must be a boolean")
    sources = normalize_access_sources(sources)
    try:
        status = command_runner(['ufw', 'status', 'verbose'], check=False, capture_output=True)
    except FileNotFoundError as exc:
        raise RuntimeError("Cloudflare origin changes require an active UFW firewall") from exc
    if status.returncode != 0 or not isinstance(status.stdout, str) or "Status: active" not in status.stdout:
        raise RuntimeError("Could not inspect active UFW; configure the firewall before changing Cloudflare origin access")
    if not re.search(r"^Default:\s+(?:deny|reject)\s+\(incoming\)", status.stdout, re.MULTILINE):
        raise RuntimeError("Cloudflare origin changes require a deny or reject incoming UFW default")

    desired_comments = set()
    for port in ORIGIN_PORTS:
        if allow_direct:
            comment = f"{RULE_PREFIX}{port}"
            command_runner(['ufw', 'allow', f'{port}/tcp', 'comment', comment])
            desired_comments.add(comment)
        else:
            for source in sources:
                comment = f"{RULE_PREFIX}{port} source {source}"
                command_runner([
                    'ufw', 'allow', 'from', source, 'to', 'any', 'port', str(port),
                    'proto', 'tcp', 'comment', comment,
                ])
                desired_comments.add(comment)

    stdout = _active_rules(command_runner)
    stale_numbers = []
    for line in stdout.splitlines():
        match = re.match(r'^\[\s*(\d+)\]\s+.*?#\s*(.+)$', line.strip())
        if match and (
            setup_origin_rule(line, match[2])
            or legacy_gogs_origin_rule(line, match[2])
            or (_origin_port(line) is not None and match[2].startswith(RULE_PREFIX) and match[2] not in desired_comments)
        ):
            stale_numbers.append(int(match[1]))
    # Numbered UFW rules shift after deletion, including IPv6 counterparts.
    for number in sorted(stale_numbers, reverse=True):
        command_runner(['ufw', '--force', 'delete', str(number)])
    if stale_numbers:
        stdout = _active_rules(command_runner)
    _verify_access_policy(stdout, allow_direct=allow_direct, sources=sources, command_runner=command_runner)

    if allow_direct:
        print("  ✓ Cloudflare tunnel and unrestricted direct HTTP/HTTPS access enabled")
    elif sources:
        print("  ✓ Direct HTTP/HTTPS allowed from: " + ', '.join(sources))
    else:
        print("  ✓ Direct HTTP/HTTPS ports closed after tunnel verification")
