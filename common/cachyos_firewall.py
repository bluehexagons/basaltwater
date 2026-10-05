"""Explicit, source-restricted UFW access for a human-operated workstation."""

from __future__ import annotations

import ipaddress
import json
import re
import fcntl
import os
from pathlib import Path
from subprocess import CompletedProcess

from lib.config import SetupConfig
from lib.machine_state import can_manage_firewall
from lib.remote_utils import CommandExecutionError, is_dry_run, run
from lib.streamed_process import run_streamed
from lib.validation import validate_network_ip_or_cidr


PREFIX = "basaltwater-cachyos:"
PRIVATE = tuple(ipaddress.ip_network(value) for value in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
))
# Guard previous unrestricted T3, Sunshine and RDP rules even when deselected.
# Sunshine administration (47990) remains local; pairing/streaming is LAN-only.
GUARDED = (("tcp", "3773"), ("udp", "3773"), ("tcp", "3389"),
           ("udp", "3389"), ("tcp", "47984:47990"), ("tcp", "48010"),
           ("udp", "47998:48000"))
STREAMING = (("tcp", "47984"), ("tcp", "47989"), ("tcp", "48010"),
             ("udp", "47998:48000"))


def _sudo_capture(command: list[str]) -> CompletedProcess[str]:
    """Capture noninteractive sudo output without losing its authenticated tty."""
    output: list[str] = []
    size = 0

    def collect(chunk: str) -> None:
        nonlocal size
        size += len(chunk.encode("utf-8"))
        if size > 1024 * 1024:
            raise RuntimeError("Firewall command output exceeded its limit")
        output.append(chunk)

    # The normal captured runner creates a new session. That discards the tty
    # timestamp established by sudo -v and makes sudo -n fail on CachyOS.
    # Streaming can retain the controlling tty while collecting command output.
    code = run_streamed(command, timeout=60, interactive=True, on_output=collect)
    result = CompletedProcess(command, code, "".join(output), "")
    if code:
        raise CommandExecutionError(" ".join(command), code, result.stdout, result=result)
    return result


def firewall_requested(config: SetupConfig) -> bool:
    return bool(config.lan_access or config.access_sources or config.clear_lan_access
                or config.clear_access_sources)


def validate_sources(values: list[str]) -> list[str]:
    result = []
    for value in values:
        network = ipaddress.ip_network(validate_network_ip_or_cidr(value), strict=False)
        if network.version != 4 or not any(network.subnet_of(parent) for parent in PRIVATE):
            raise ValueError("CachyOS access sources must be private RFC1918 IPv4 addresses/subnets")
        result.append(str(network))
    return sorted(set(result))


def access_sources(config: SetupConfig) -> list[str]:
    # Explicit sources override discovery, rather than silently widening access.
    if config.access_sources:
        return validate_sources(config.access_sources)
    if not config.lan_access:
        return []
    routes = json.loads(run(["ip", "-j", "-4", "route", "show", "default"],
                            capture_output=True, timeout=10).stdout)
    devices = {route.get("dev") for route in routes if route.get("gateway")}
    addresses = json.loads(run(["ip", "-j", "-d", "-4", "address", "show", "up"],
                               capture_output=True, timeout=10).stdout)
    sources = set()
    for interface in addresses:
        if (interface.get("ifname") not in devices or interface.get("link_type") != "ether"
                or interface.get("linkinfo", {}).get("info_kind") in {"tun", "wireguard", "veth", "bridge"}):
            continue
        for address in interface.get("addr_info", []):
            if address.get("scope") != "global":
                continue
            value = f"{address['local']}/{address['prefixlen']}"
            try:
                sources.update(validate_sources([value]))
            except ValueError:
                continue
    if len(devices) != 1 or len(sources) != 1:
        raise ValueError("Cannot identify one private default-route LAN; use --access-source IP_OR_CIDR")
    return sorted(sources)


def preflight_firewall(config: SetupConfig) -> None:
    if not can_manage_firewall():
        raise ValueError("This machine cannot manage its firewall")
    for service in ("firewalld.service", "nftables.service"):
        status = run(["systemctl", "is-active", service], check=False,
                     capture_output=True, timeout=10)
        if status.returncode not in (3, 4):
            raise ValueError(f"Resolve active or indeterminate {service} before selecting UFW management")
    defaults = Path("/etc/default/ufw")
    if defaults.exists() and not re.search(r"^IPV6=yes\s*$", defaults.read_text(), re.M):
        raise ValueError("Enable IPV6=yes in /etc/default/ufw before managing workstation access")
    access_sources(config)


def configure_firewall(config: SetupConfig) -> None:
    if config.dry_run or is_dry_run():
        print("  Would enable UFW: deny incoming/routed, allow outgoing; restrict selected T3/Sunshine to private sources")
        return
    from common.cachyos_steps import _directory, _home

    directory = _home(config) / ".local/state/basaltwater/cachyos"
    _directory(directory)
    descriptor = os.open(directory / "firewall.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Another workstation firewall update is running") from exc
        _configure_firewall(config)
    finally:
        os.close(descriptor)


def _configure_firewall(config: SetupConfig) -> None:
    preflight_firewall(config)
    run(["sudo", "-v"], interactive=True, timeout=120)
    sources = access_sources(config)
    from common.cachyos_steps import install_missing_packages

    install_missing_packages(["ufw"])
    # Recheck installed defaults before any rule changes.
    preflight_firewall(config)

    def ufw(*arguments: str):
        return _sudo_capture(["sudo", "-n", "env", "LC_ALL=C", "ufw", *arguments])

    # UFW insert/prepend skips equivalent rules, even with a different action.
    # A non-positional deny replaces an exact legacy allow in place. Retain
    # guards permanently, and verify overlapping rule precedence before success.
    generation = PREFIX + "guard"
    guarded = set(GUARDED) | {("tcp", str(config.web_interface_port))}
    for protocol, port in sorted(guarded):
        arguments = ("deny", "in", "proto", protocol, "from", "any", "to", "any",
                     "port", port, "comment", generation)
        result = ufw("prepend", *arguments)
        if "Skipping" in result.stdout:
            ufw(*arguments)
    status = ufw("status", "numbered").stdout
    if "Status: inactive" in status:
        # Enable with guards already on disk, then obtain authoritative numbers.
        ufw("default", "deny", "incoming")
        ufw("default", "allow", "outgoing")
        ufw("default", "deny", "routed")
        ufw("--force", "enable")
        status = ufw("status", "numbered").stdout
    if "Status: active" not in status:
        raise RuntimeError("Cannot inspect UFW rules; restrictive guards retained")
    obsolete = []
    for line in status.splitlines():
        match = re.match(r"\[\s*(\d+)\]\s+.*# (basaltwater-cachyos:[a-z0-9-]+)\s*$", line)
        if match and match[2] == PREFIX + "allow":
            obsolete.append(int(match[1]))
    for number in sorted(obsolete, reverse=True):
        ufw("--force", "delete", str(number))
    allowed = set()
    if config.t3code_desktop or config.web_interfaces:
        allowed.add(("tcp", str(config.web_interface_port)))
    if config.install_sunshine:
        allowed.update(STREAMING)
    for source in sources:
        for protocol, port in sorted(allowed):
            arguments = ("allow", "in", "proto", protocol, "from", source,
                         "to", "any", "port", port, "comment", PREFIX + "allow")
            result = ufw("prepend", *arguments)
            if "Skipping" in result.stdout:
                # Adopt only the exact selected source/port rule. Removing an
                # allow is restrictive; a failed reinsert leaves its guard.
                ufw("--force", "delete", *arguments[:-2])
                ufw("prepend", *arguments)
    ufw("default", "deny", "incoming")
    ufw("default", "allow", "outgoing")
    ufw("default", "deny", "routed")
    ufw("--force", "enable")
    _sudo_capture(["sudo", "-n", "systemctl", "enable", "--now", "ufw.service"])
    verify_rule_order(ufw("status", "numbered").stdout, guarded)
    print("  UFW restricts new T3/Sunshine connections to: " + (", ".join(sources) or "local only"))
    print("  Sunshine administration and legacy RDP remain local; unrelated rules are retained. "
          "Review sudo ufw status verbose. Existing connections may remain established.")


def verify_rule_order(status: str, guarded: set[tuple[str, str]]) -> None:
    """Refuse success when older overlapping rules precede an expected guard."""
    if "Status: active" not in status:
        raise RuntimeError("UFW is not active after configuration")
    lines = [line for line in status.splitlines() if re.match(r"\[\s*\d+\]", line)]
    for protocol, ports in guarded:
        for ipv6 in (False, True):
            candidates = [line for line in lines if ("(v6)" in line) == ipv6]
            guard = next((index for index, line in enumerate(candidates)
                          if re.search(r"\]\s+" + re.escape(ports + "/" + protocol) + r"\s", line)
                          and "DENY IN" in line and "# " + PREFIX + "guard" in line), None)
            if guard is None:
                raise RuntimeError("Expected UFW IPv4/IPv6 guard missing; inspect sudo ufw status numbered")
            for line in candidates[:guard]:
                if "ALLOW IN" not in line and "LIMIT IN" not in line:
                    continue
                if "# " + PREFIX + "allow" in line:
                    continue
                destination = re.sub(r"^\[\s*\d+\]\s*", "", line).split()[0]
                number, _, rule_protocol = destination.partition("/")
                if rule_protocol and rule_protocol != protocol:
                    continue
                try:
                    low, _, high = ports.partition(":")
                    overlap = any(int(part.split(":")[0]) <= int(high or low)
                                  and int(part.split(":")[-1]) >= int(low)
                                  for part in number.split(","))
                except ValueError:
                    overlap = True  # application/any-address rules need review
                if overlap:
                    raise RuntimeError("An earlier unmanaged UFW rule may override workstation restrictions; "
                                       "review sudo ufw status numbered and rerun")
