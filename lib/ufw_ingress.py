"""Verify how new connections traverse the filter chains used by UFW."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from ipaddress import IPv4Network, IPv6Network, ip_address, ip_network
import shlex
from typing import Any


Network = IPv4Network | IPv6Network
_DENY = {"DROP", "REJECT"}
_CONTINUE = {"LOG", "NFLOG", "MARK", "CONNMARK", "AUDIT"}
_RATE_LIMIT_CHAINS = {"ufw-user-limit", "ufw6-user-limit"}
_USER_INPUT_CHAINS = {"ufw-user-input", "ufw6-user-input"}
_USER_ACCEPT_TARGETS = {"ACCEPT", "ufw-user-limit-accept", "ufw6-user-limit-accept"}


def _minus(networks: Sequence[Network], excluded: Network) -> list[Network]:
    result = []
    for network in networks:
        if network.version != excluded.version or not network.overlaps(excluded):
            result.append(network)
        elif excluded.subnet_of(network):
            result.extend(network.address_exclude(excluded))
    return result


def _port_in_spec(port: int, spec: str) -> bool:
    for item in spec.split(","):
        low, _, high = item.partition(":")
        if int(low or 0) <= port <= int(high or (65535 if ":" in item else low)):
            return True
    return False


@dataclass(frozen=True)
class _InputRule:
    line: str
    options: dict[str, tuple[str, bool]]
    target: str
    goto: bool

    @classmethod
    def parse(cls, line: str) -> tuple[str, _InputRule]:
        _, chain, *tokens = shlex.split(line)
        options: dict[str, tuple[str, bool]] = {}
        target = ""
        goto = False
        inverted = False
        while tokens:
            key = tokens.pop(0)
            if key == "!":
                inverted = True
                continue
            if key in {"-j", "-g"}:
                target = tokens[0]
                goto = key == "-g"
                break  # Following arguments belong to the target.
            value = ""
            if tokens and not tokens[0].startswith(("-", "!")):
                value = tokens.pop(0)
            if key == "-m":
                key = f"-m:{value}"
            options[key] = (value, inverted)
            inverted = False
        return chain, cls(line, options, target, goto)

    def matching_sources(
        self, networks: Sequence[Network], protocol: str, port: int, destination: str | None,
    ) -> tuple[list[Network], bool]:
        """Return possible source matches and whether the match is certain."""
        if not networks or not self.target or self.target in _CONTINUE:
            return [], False
        matched = list(networks)
        certain = True
        for key, (value, inverted) in self.options.items():
            if key == "-s":
                scope = ip_network(value, strict=False)
                if inverted:
                    matched = _minus(matched, scope)
                else:
                    matched = [
                        network if network.subnet_of(scope) else scope
                        for network in matched if network.overlaps(scope)
                    ]
                continue
            if key == "-p":
                applies = value in {protocol, "all", {"tcp": "6", "udp": "17"}[protocol]}
            elif key in {"--dport", "--dports"}:
                applies = _port_in_spec(port, value)
            elif key in {"--ctstate", "--state"}:
                applies = "NEW" in value.split(",")
            elif key == "-d":
                scope = ip_network(value, strict=False)
                if destination is not None:
                    applies = ip_address(destination) in scope
                elif scope.prefixlen == 0 or scope.is_loopback or scope.is_multicast:
                    applies = scope.prefixlen == 0
                else:
                    certain = False
                    continue
            elif key == "-i" and value == "lo":
                applies = False
            elif key == "--dst-type" and value in {"LOCAL", "MULTICAST", "BROADCAST"}:
                applies = value == "LOCAL"
            elif key == "--rt-type" and value == "0":
                # The stock IPv6 UFW rule drops deprecated routing headers.
                applies = False
            elif key in {"-m:tcp", "-m:udp", "-m:multiport", "-m:state", "-m:conntrack",
                         "-m:addrtype", "-m:rt", "-m:comment", "--comment"}:
                continue
            else:
                certain = False
                continue
            if applies == inverted:
                return [], False
        return matched, certain


def verify_filter_ingress(
    table: str, *, family: int, protocol: str, port: int, sources: Sequence[str],
    peer: tuple[str, str] | None = None, before_changes: bool = False,
) -> None:
    """Check a kernel filter snapshot in rule order for a unicast listener.

    Only ordinary new external connections are in scope. For unmodeled
    selectors (for example an ipset or interface), consider both possible
    outcomes so a rule cannot silently grant access or obstruct the SSH peer.
    """
    chains: dict[str, list[_InputRule]] = {}
    default = ""
    for line in table.splitlines():
        if line.startswith(":"):
            name, policy, _counters = line[1:].split()
            chains.setdefault(name, [])
            if name == "INPUT":
                default = policy
        elif line.startswith("-A "):
            chain, rule = _InputRule.parse(line)
            chains.setdefault(chain, []).append(rule)
    if default not in {"ACCEPT", "DROP"}:
        raise RuntimeError(f"Could not verify UFW IPv{family} input chains")

    def inspect(
        chain: str, remaining: list[Network], *, wanted: bool, destination: str | None, depth: int = 0,
    ) -> list[Network]:
        if depth > 32:
            raise RuntimeError(f"Could not verify nested UFW input chain {chain}")
        returned: list[Network] = []
        for rule in chains[chain]:
            matches, certain = rule.matching_sources(remaining, protocol, port, destination)
            if not matches:
                continue
            if before_changes and wanted and chain in _USER_INPUT_CHAINS and rule.target in _USER_ACCEPT_TARGETS:
                # A legacy allow ahead of an operator deny may be removed.
                continue
            if wanted and rule.target in _RATE_LIMIT_CHAINS:
                continue
            if rule.target == "ACCEPT" or rule.target in _DENY:
                if wanted == (rule.target in _DENY):
                    problem = "can block the current SSH peer" if wanted else "allows sources outside the requested policy"
                    raise RuntimeError(f"UFW IPv{family} {protocol.upper()} {port} rule {problem}: {rule.line}")
                unresolved = []
            elif rule.target == "RETURN":
                unresolved = matches
            elif rule.target in chains:
                unresolved = inspect(rule.target, matches, wanted=wanted, destination=destination, depth=depth + 1)
            else:
                raise RuntimeError(f"Could not verify UFW IPv{family} input target in: {rule.line}")
            if certain:
                for network in matches:
                    remaining = _minus(remaining, network)
            if rule.target == "RETURN" or rule.goto:
                returned.extend(unresolved)
            else:
                remaining.extend(unresolved if certain else [])
        return list(dict.fromkeys(remaining + returned))

    if not before_changes:
        outside = [ip_network("0.0.0.0/0" if family == 4 else "::/0")]
        for source in sources:
            outside = _minus(outside, ip_network(source, strict=False))
        if inspect("INPUT", outside, wanted=False, destination=None) and default == "ACCEPT":
            raise RuntimeError(f"UFW IPv{family} input policy allows sources outside the requested {protocol.upper()} {port} policy")
    if peer is not None:
        address = ip_address(peer[0])
        if address.version == family and not address.is_loopback:
            remaining = inspect("INPUT", [ip_network(address)], wanted=True, destination=peer[1])
            if remaining and default == "DROP" and not before_changes:
                raise RuntimeError(f"UFW IPv{family} {protocol.upper()} {port} policy cannot admit the current SSH peer")


def verify_live_ufw_ingress(
    policies: dict[tuple[str, int], list[str]],
    command_runner: Callable[..., Any],
    *, families: tuple[int, ...] = (4, 6), peer: tuple[str, str] | None = None, before_changes: bool = False,
) -> None:
    """Verify listeners using the live IPv4/IPv6 filter tables, including earlier chains."""
    if not policies:
        return
    for family in families:
        executable = "iptables-save" if family == 4 else "ip6tables-save"
        result = command_runner([executable, "-t", "filter"], check=False, capture_output=True)
        if result.returncode != 0 or not isinstance(result.stdout, str):
            raise RuntimeError(f"Could not inspect live UFW IPv{family} filter chains")
        for (protocol, port), sources in policies.items():
            verify_filter_ingress(
                result.stdout, family=family, protocol=protocol, port=port, sources=sources,
                peer=peer if (protocol, port) == ("tcp", 22) else None, before_changes=before_changes,
            )
