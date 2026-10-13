"""Explicit direct UDP access for repository-owned services."""

from __future__ import annotations

from lib.project_manifest import PublicUDP
from lib.remote_utils import install_package, run
from lib.validation import validate_network_port


def configure_public_udp(listeners: list[PublicUDP]) -> None:
    """Persist UFW allowances without changing unrelated firewall rules."""
    if not listeners:
        return
    for listener in listeners:
        validate_network_port(listener.port, "Public UDP port", minimum=1024)
    if not install_package("UFW", "ufw", "apt-get install -y -qq ufw"):
        raise RuntimeError("UFW is required for declared public UDP listeners")
    for listener in listeners:
        run(["ufw", "allow", f"{listener.port}/udp", "comment", "basaltwater manifest UDP"])
        print(f"  ✓ Public UDP allowed: {listener.port}/udp")
        if listener.hostname:
            print(
                f"  DNS: {listener.hostname} must use DNS-only A/AAAA records for this server's public IP; "
                f"also allow {listener.port}/udp in the provider firewall"
            )
