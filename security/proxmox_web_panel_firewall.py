"""Reconcile only the optional panel's native Proxmox node firewall rules."""

from __future__ import annotations

import json
import re
import socket
from typing import Any

from lib.config import SetupConfig, WEB_PANEL_CA_HTTP_PORT
from lib.remote_utils import is_dry_run, run
from lib.validation import validate_network_ip_or_cidr, validate_network_port
from lib.validators import validate_host


_RULE_COMMENT = "Managed by basaltwater web panel"


def _rules(endpoint: str) -> list[dict[str, Any]]:
    result = run(["pvesh", "get", endpoint, "--output-format", "json"],
                 capture_output=True, timeout=30)
    output = result.stdout or ""
    if len(output.encode("utf-8")) > 1024 * 1024:
        raise RuntimeError("Proxmox node firewall rules exceed 1 MiB")
    try:
        rules = json.loads(output)
    except ValueError as exc:
        raise RuntimeError("Could not read Proxmox node firewall rules") from exc
    if not isinstance(rules, list) or any(
        not isinstance(rule, dict) or type(rule.get("pos")) is not int
        or rule["pos"] != index for index, rule in enumerate(rules)
    ):
        raise RuntimeError("Invalid Proxmox node firewall rule positions")
    return rules


def _matches(rule: dict[str, Any], desired: dict[str, str]) -> bool:
    return (
        all(str(rule.get(key, "")) == value for key, value in desired.items())
        and not any(rule.get(key) for key in ("dest", "sport", "iface", "macro", "icmp-type"))
    )


def _reconcile(desired: list[dict[str, str]]) -> None:
    node = socket.gethostname().split(".")[0]
    if not validate_host(node):
        raise ValueError("Invalid local Proxmox node name")
    endpoint = f"/nodes/{node}/firewall/rules"
    rules = _rules(endpoint)
    # Install all requested allows before removing the previous configuration.
    # The native API prepends new rules and validates them under its own lock.
    # No cluster options, IP sets, backend, or firewall enablement are changed.
    for spec in desired:
        if any(_matches(rule, spec) for rule in rules):
            continue
        command = ["pvesh", "create", endpoint]
        for key, value in spec.items():
            if value:
                command.extend((f"--{key}", value))
        run(command, capture_output=True, timeout=30)
        rules = _rules(endpoint)
    if any(not any(_matches(rule, spec) for rule in rules) for spec in desired):
        raise RuntimeError("Proxmox did not retain the requested panel firewall rules")

    # Positions can move when another operator edits rules. Re-read before each
    # deletion and pass the API's digest to refuse a concurrent modification.
    limit = len(rules) + 1
    for _attempt in range(limit):
        rules = _rules(endpoint)
        remaining = list(desired)
        stale = []
        for rule in rules:
            if rule.get("comment") != _RULE_COMMENT:
                continue
            match = next((spec for spec in remaining if _matches(rule, spec)), None)
            if match is None:
                stale.append(rule)
            else:
                remaining.remove(match)
        if remaining:
            raise RuntimeError("Proxmox panel firewall rules changed during setup; rerun setup")
        if not stale:
            return
        rule = stale[-1]
        digest = rule.get("digest")
        if not isinstance(digest, str) or not re.fullmatch(r"[a-fA-F0-9]{40}", digest):
            raise RuntimeError("Cannot safely remove panel firewall rules without a Proxmox digest")
        run(["pvesh", "delete", f"{endpoint}/{rule['pos']}", "--digest", digest],
            capture_output=True, timeout=30)
    raise RuntimeError("Proxmox panel firewall rules kept changing; rerun setup")


def configure_proxmox_web_panel_firewall(config: SetupConfig) -> None:
    """Allow the selected panel and CA ports using the existing firewall engine."""
    port = validate_network_port(config.web_panel_port, "Web panel port")
    if port == 8006:
        raise ValueError("Web panel port 8006 is reserved for the Proxmox web UI")
    sources = list(dict.fromkeys(
        validate_network_ip_or_cidr(source, "Web panel access source")
        for source in config.effective_access_sources()
    )) or [""]
    ports = list(dict.fromkeys([port, *([WEB_PANEL_CA_HTTP_PORT] if config.enable_ssl else [])]))
    desired = [
        {"type": "in", "action": "ACCEPT", "enable": "1", "proto": "tcp",
         "dport": str(listener), "source": source, "comment": _RULE_COMMENT}
        for listener in ports for source in sources
    ]
    if is_dry_run():
        print(f"  [DRY-RUN] Would reconcile Proxmox panel firewall ports {ports}")
        return
    _reconcile(desired)
    print(f"  ✓ Native Proxmox node firewall rules configured for panel ports {ports}")


def remove_proxmox_web_panel_firewall() -> None:
    """Remove only rules carrying this feature's exact ownership marker."""
    if is_dry_run():
        print("  [DRY-RUN] Would remove Proxmox panel firewall rules")
        return
    _reconcile([])
