"""Reconcile direct web access alongside a Cloudflare tunnel."""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Callable
from typing import Any

from lib.validation import validate_network_ip_or_cidr


RULE_PREFIX = "basaltwater cloudflare web "
SSH_RULE_PREFIX = "basaltwater cloudflare ssh "


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
    result = command_runner(['ufw', 'status', 'numbered'], check=False, capture_output=True)
    if result.returncode != 0 or 'Status: active' not in (result.stdout or ''):
        raise RuntimeError("Could not inspect active Cloudflare web firewall rules")
    stale_numbers = []
    for line in result.stdout.splitlines():
        match = re.match(r'^\[\s*(\d+)\]\s+.*?#\s*(.+)$', line.strip())
        if match and match[2].startswith((RULE_PREFIX, SSH_RULE_PREFIX)) and match[2] not in desired_comments:
            stale_numbers.append(int(match[1]))
    # Numbered UFW rules shift after deletion, including IPv6 counterparts.
    for number in sorted(stale_numbers, reverse=True):
        command_runner(['ufw', '--force', 'delete', str(number)])

    if allow_direct:
        print("  ✓ Cloudflare tunnel and unrestricted direct HTTP/HTTPS access enabled")
    elif sources:
        print("  ✓ Direct HTTP/HTTPS allowed from: " + ', '.join(sources))
    else:
        print("  ✓ Direct HTTP/HTTPS ports closed after tunnel verification")
