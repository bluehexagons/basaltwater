"""Reconcile direct web access alongside a Cloudflare tunnel."""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Callable
from typing import Any

from lib.validation import validate_network_ip_or_cidr


RULE_PREFIX = "basaltwater cloudflare web "
SSH_RULE_PREFIX = "basaltwater cloudflare ssh "
_INBOUND_RULE = re.compile(r'^\[\s*\d+\]\s+(.+?)\s+(?:ALLOW|LIMIT)\s+IN\s+(.+)$')
_PORT_SPEC = re.compile(r'\d+(?::\d+)?(?:,\d+(?::\d+)?)*(?:/(?:tcp|udp))?')
_POLICY_PORTS = {22, 80, 443}


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
    if allow_direct and not sources:
        return
    networks = [ipaddress.ip_network(source) for source in sources]
    profiles: dict[str, set[int]] = {}
    for line in stdout.splitlines():
        match = _INBOUND_RULE.match(line.split('#', 1)[0].strip())
        if not match:
            continue
        ports = _policy_ports(match[1], command_runner, profiles)
        web_restricted = not allow_direct and bool(ports & {80, 443})
        ssh_restricted = bool(sources) and 22 in ports
        if not web_restricted and not ssh_restricted:
            continue
        source = match[2].split()[0]
        try:
            if source == 'Anywhere':
                source = '::/0' if '(v6)' in match[2] else '0.0.0.0/0'
            network = ipaddress.ip_network(source, strict=False)
        except ValueError as exc:
            raise RuntimeError(f"Could not inspect UFW rule source: {line.strip()}") from exc
        broad = network.prefixlen == 0
        web_allowed = any(
            network.version == allowed.version and network.subnet_of(allowed)
            for allowed in networks
        )
        # Keep separately configured, source-specific management SSH rules.
        if (web_restricted and not web_allowed) or (ssh_restricted and broad and not web_allowed):
            raise RuntimeError(
                "UFW rule grants access outside the requested Cloudflare policy: "
                f"{line.strip()}. Review 'ufw status numbered' and remove or narrow this rule."
            )


def reconcile_tunnel_firewall(
    *, allow_direct: bool, sources: list[str], command_runner: Callable[..., Any],
) -> None:
    """Apply web and SSH source policy, activate UFW, and remove stale owned rules."""
    if not isinstance(allow_direct, bool):
        raise ValueError("Cloudflare direct access must be a boolean")
    sources = normalize_access_sources(sources)
    desired_comments = set()
    if sources:
        for source in sources:
            comment = f"{SSH_RULE_PREFIX}{source}"
            command_runner([
                'ufw', 'allow', 'from', source, 'to', 'any', 'port', '22',
                'proto', 'tcp', 'comment', comment,
            ])
            desired_comments.add(comment)
        ssh_rules = ('allow ssh', 'allow 22/tcp', 'limit ssh', 'limit 22/tcp')
    else:
        command_runner(['ufw', 'limit', 'ssh'])
        ssh_rules = ('allow ssh', 'allow 22/tcp')
    for rule in ssh_rules:
        command_runner(['ufw', 'delete', *rule.split()], check=False)

    for port in (80, 443):
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

    if not allow_direct:
        # Install scoped replacements before removing the broad setup rules.
        for rule in ('80/tcp', '443/tcp', '80', '443'):
            command_runner(['ufw', 'delete', 'allow', rule], check=False)

    command_runner(['ufw', '--force', 'enable'])
    stdout = _active_rules(command_runner)
    stale_numbers = []
    for line in stdout.splitlines():
        match = re.match(r'^\[\s*(\d+)\]\s+.*?#\s*(.+)$', line.strip())
        if match and match[2].startswith((RULE_PREFIX, SSH_RULE_PREFIX)) and match[2] not in desired_comments:
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
