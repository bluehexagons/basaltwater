#!/usr/bin/env python3
"""Fixed root-only DNS observation/CAS helper; no commands or health documents."""

from __future__ import annotations

from dataclasses import replace
import json
import os
import sys
import time

if __name__ == "__main__" and not __package__:
    sys.path.insert(0, "/opt/basaltwater")


def execute_request(payload: bytes) -> dict:
    from common.unbound_steps import (
        UnboundPaths, _manifest, _observe, _same_identity, _stock_fragments,
        _unit_configuration, verify_resolver,
    )
    from lib.atomic_io import parse_json
    from lib.headscale_admin import identifier
    from lib.headscale_client import native, reject_control_host
    from lib.headscale_dns_binding import dns_binding_lease, parse_dns_binding

    try:
        if os.geteuid() != 0 or len(payload) > 16384:
            raise ValueError()
        request = parse_json(payload)
        if type(request) is not dict or set(request) != {"action", "parameters"}:
            raise ValueError()
        action, p = request["action"], request["parameters"]
        if type(p) is not dict or action not in {"node", "inspect", "cas", "peer"}:
            raise ValueError()
        fields = {"binding"} if action in {"node", "inspect"} else (
            {"binding", "desired"} if action == "cas" else {"binding", "peer", "effective"})
        if set(p) != fields:
            raise ValueError()
        binding = parse_dns_binding(p["binding"])
        identifier(binding.node_id)
        reject_control_host(binding.network_url)
        if action == "peer":
            peer = parse_dns_binding(p["peer"])
            identifier(peer.node_id)
            if (peer.network_url != binding.network_url or peer.node_id == binding.node_id
                    or set(peer.addresses) & set(binding.addresses)):
                raise ValueError()
            if _observe(binding.network_url) != replace(peer, advertised=False, publication_id=None):
                raise ValueError()
            effective = p["effective"]
            if effective not in {None, "published", "withdrawn"}:
                raise ValueError()
            if effective is not None:
                _wait_effective_dns(binding, effective)
            if effective != "withdrawn":
                verify_resolver(binding)
                if effective == "published":
                    from lib.unbound_config import HEALTH_NAME, health_identity
                    for protocol in (["+notcp", "+ignore"], ["+tcp"]):
                        output = native(["dig", "@100.100.100.100", HEALTH_NAME, "TXT", "+short",
                                         "+time=3", "+tries=1", *protocol], timeout=15, limit=4096)
                        if output.strip() != '"' + health_identity(binding) + '"':
                            raise ValueError()
            if _observe(binding.network_url) != replace(peer, advertised=False, publication_id=None):
                raise ValueError()
            result = {"peer": peer.to_dict(), "checked": True}
        else:
            paths = UnboundPaths()
            with dns_binding_lease(paths.binding) as lease:
                if action == "inspect":
                    if lease.binding is None or not _same_identity(lease.binding, binding):
                        raise ValueError()
                    return {"ok": True, "result": {"binding": lease.binding.to_dict()}}
                if lease.binding != binding:
                    raise ValueError()
                if action == "cas":
                    desired = parse_dns_binding(p["desired"])
                    if not _same_identity(binding, desired):
                        raise ValueError()
                    # Releasing a guard must remain possible during resolver outages.
                    if desired.advertised:
                        _owned_node(paths, binding, _manifest, _observe, _same_identity,
                                    _stock_fragments, _unit_configuration, verify_resolver)
                    lease.save(desired)
                    binding = desired
                else:
                    _owned_node(paths, binding, _manifest, _observe, _same_identity,
                                _stock_fragments, _unit_configuration, verify_resolver)
                result = {"binding": binding.to_dict()}
        return {"ok": True, "result": result}
    except Exception:
        return {"ok": False, "result": {"error": "DNS operation unconfirmed; reconcile"}}


def _wait_effective_dns(binding, effective: str) -> None:
    """Observe propagation without repeating activation or accepting a fallback."""
    from lib.atomic_io import parse_json
    from lib.headscale_client import native

    deadline = time.monotonic() + 10
    while True:
        status = parse_json(native(["tailscale", "dns", "status", "--json"], timeout=5, limit=65536))
        if type(status) is not dict:
            raise ValueError()
        resolvers = status.get("Resolvers") or []
        routes = status.get("SplitDNSRoutes") or {}
        tailnet = status.get("CurrentTailnet") or {}
        if (type(resolvers) is not list or type(routes) is not dict or type(tailnet) is not dict
                or any(type(r) is not dict or type(r.get("Addr")) is not str for r in resolvers)):
            raise ValueError()
        addresses = [r["Addr"] for r in resolvers]
        matches = not (status.get("Nameservers") or status.get("FallbackResolvers")
                       or any(routes.values()))
        if effective == "published":
            matches = (matches and status.get("TailscaleDNS") is True
                       and tailnet.get("MagicDNSEnabled") is True
                       and addresses == list(binding.addresses))
        else:
            matches = matches and not addresses
        if matches:
            return
        if time.monotonic() >= deadline:
            raise ValueError("Native resolver map did not converge")
        time.sleep(.25)


def _owned_node(paths, binding, manifest, observe, same, stocks, unit, verify) -> None:
    from lib.headscale_dns_binding import parse_dns_binding

    if os.path.lexists(paths.operation) or os.path.lexists(paths.backup):
        raise ValueError("Reconcile unfinished resolver recovery before DNS publication")
    managed = manifest(paths)
    if (managed is None or managed["enabled"] is not True
            or not same(parse_dns_binding(managed["binding"]), binding)
            or not same(observe(binding.network_url), binding)):
        raise ValueError("Managed DNS identity unavailable")
    stocks(paths, managed=True)
    unit(paths, managed=True)
    verify(binding)


def main() -> int:
    result = execute_request(sys.stdin.buffer.read(16385))
    print(json.dumps(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
