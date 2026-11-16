"""Root-local replacement of an existing managed resolver's private inventory."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
import re
from urllib.parse import urlsplit

from lib.atomic_io import parse_json
from lib.headscale_config import _domain
from lib.headscale_dns_binding import DNSBinding, parse_dns_binding, read_dns_binding
from lib.unbound_config import LocalRecord, parse_local_records


@dataclass(frozen=True)
class RecordsUpdate:
    binding: DNSBinding
    records_sha256: str
    magic_dns_suffix: str
    onboarding_domain: str | None
    records: tuple[LocalRecord, ...]


def parse_records_update(value: object) -> RecordsUpdate:
    """Validate private review syntax only, without observing the host."""
    if (not isinstance(value, dict) or set(value) != {
            "version", "binding", "records_sha256", "magic_dns_suffix", "onboarding_domain", "inventory"}
            or type(value["version"]) is not int or value["version"] != 1
            or not isinstance(value["records_sha256"], str)
            or re.fullmatch(r"[0-9a-f]{64}", value["records_sha256"]) is None):
        raise ValueError("Exact binding, inventory digest and bounded replacement required")
    binding = parse_dns_binding(value["binding"])
    suffix = _domain(value["magic_dns_suffix"])
    onboarding = None if value["onboarding_domain"] is None else _domain(value["onboarding_domain"])
    forbidden = tuple(x for x in (urlsplit(binding.network_url).hostname, suffix, onboarding) if x)
    records = parse_local_records(value["inventory"], forbidden_domains=forbidden)
    return RecordsUpdate(binding, value["records_sha256"], suffix, onboarding, records)


def inspect_records(*, expected: DNSBinding, paths=None) -> dict:
    """Read metadata without locks, native calls, or creation; CAS catches races."""
    from common import unbound_steps as steps

    if os.geteuid() != 0:
        raise ValueError("Root required")
    paths = paths or steps.UnboundPaths()
    expected = parse_dns_binding(expected.to_dict())
    binding = read_dns_binding(paths.binding)
    managed = steps._manifest(paths)
    if (binding != expected or managed is None
            or not steps._same_identity(parse_dns_binding(managed["binding"]), binding)):
        raise ValueError("Existing managed resolver and exact binding required")
    if any(steps._read(path, private=True) is not None for path in (paths.operation, paths.backup)):
        raise ValueError("Unfinished resolver transaction")
    raw = steps._read(paths.records, private=True)
    if raw is None or hashlib.sha256(raw).hexdigest() != managed["files"][paths.records]:
        raise ValueError("Inventory changed during inspection")
    records = parse_local_records(parse_json(raw))
    if read_dns_binding(paths.binding) != expected:
        raise ValueError("Binding changed during inspection")
    return {"records_sha256": hashlib.sha256(raw).hexdigest(), "record_count": len(records),
            "enabled": managed["enabled"], "advertised": binding.advertised, "readiness_claim": False}


def check_records_update(update: RecordsUpdate, *, paths, managed, binding, state) -> None:
    """Called under configure's binding lease before any transaction writes."""
    from common import unbound_steps as steps
    from lib import headscale_client as client

    if (managed is None or not managed["enabled"] or binding != update.binding
            or state["LoadState"] != "loaded" or state["ActiveState"] != "active"
            or state["UnitFileState"] != "enabled"):
        raise ValueError("Exact existing managed active enabled resolver required")
    raw = steps._read(paths.records, private=True)
    if raw is None or hashlib.sha256(raw).hexdigest() != update.records_sha256:
        raise ValueError("Prior inventory changed; inspect before replacing")
    client.require_private_daemon()
    owner = client.owned(binding.network_url)
    status, prefs = client.snapshot()
    identity = client._identity(status, prefs, binding.network_url, True)
    actual = DNSBinding(identity.login_server, identity.node_id, identity.addresses)
    tailnet = status.get("CurrentTailnet")
    if (not steps._same_identity(actual, binding) or owner["phase"] != "enrolled"
            or owner["node_id"] != identity.node_id):
        raise ValueError("Native identity changed")
    if (not isinstance(tailnet, dict) or tailnet.get("MagicDNSEnabled") is not True
            or _domain(tailnet.get("MagicDNSSuffix")) != update.magic_dns_suffix):
        raise ValueError("Native MagicDNS suffix differs or is unavailable")


def replace_records(update: RecordsUpdate, *, paths=None) -> None:
    """Replace all records through the existing rollback/recovery transaction."""
    from common import unbound_steps as steps
    from lib.config import SetupConfig

    config = SetupConfig(host="localhost", username="root", system_type="server_lite", unbound=True,
                         tailscale_login_server=update.binding.network_url,
                         headscale_base_domain=update.magic_dns_suffix,
                         headscale_onboarding_domain=update.onboarding_domain)
    steps.configure_unbound(config, paths=paths or steps.UnboundPaths(), records=update.records,
                            expected_binding=update.binding, records_update=update)
