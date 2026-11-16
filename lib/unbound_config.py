"""Bounded local records and private, recursive Unbound configuration."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import ipaddress
import json

from lib.headscale_dns_binding import DNSBinding, parse_dns_binding
from lib.validation import validate_network_ip
from lib.validators import validate_host

HEALTH_NAME = "health.basaltwater-resolver.invalid."
MAX_RECORDS = 256


@dataclass(frozen=True)
class LocalRecord:
    zone: str
    name: str
    rrtype: str
    value: str
    ttl: int = 300

    def to_dict(self) -> dict:
        return dict(zone=self.zone, name=self.name, type=self.rrtype, value=self.value, ttl=self.ttl)


def _dns_name(value: object) -> str:
    if not isinstance(value, str) or "." not in value.rstrip(".") or not validate_host(value):
        raise ValueError("Local DNS names must be fully qualified hostnames")
    try:
        ipaddress.ip_address(value.rstrip("."))
    except ValueError:
        return value.lower().rstrip(".") + "."
    raise ValueError("Local DNS names cannot be IP addresses")


def _collides(left: str, right: str) -> bool:
    left, right = left.rstrip("."), right.rstrip(".")
    return left == right or left.endswith("." + right) or right.endswith("." + left)


def parse_local_records(value: object, *, forbidden_domains: tuple[str, ...] = ()) -> tuple[LocalRecord, ...]:
    """Accept a small A/AAAA/CNAME inventory, never native configuration text."""
    if (not isinstance(value, dict) or set(value) != {"version", "records"}
            or type(value["version"]) is not int or value["version"] != 1
            or not isinstance(value["records"], list) or len(value["records"]) > MAX_RECORDS):
        raise ValueError("Invalid local record document (version 1 and at most 256 records required)")
    records, seen, types = [], set(), {}
    forbidden = ("basaltwater-resolver.invalid", *forbidden_domains)
    for item in value["records"]:
        if not isinstance(item, dict) or set(item) != {"zone", "name", "type", "value", "ttl"}:
            raise ValueError("Invalid local record fields")
        zone, name = _dns_name(item["zone"]), _dns_name(item["name"])
        if name != zone and not name.endswith("." + zone):
            raise ValueError("Local record must be inside its declared zone")
        if any(_collides(zone, _dns_name(domain)) for domain in forbidden):
            raise ValueError("Local zone collides with a control hostname, MagicDNS or reserved health zone")
        rrtype, address, ttl = item["type"], item["value"], item["ttl"]
        if not isinstance(rrtype, str):
            raise ValueError("Local record type must be a string")
        if type(ttl) is not int or not 30 <= ttl <= 86400:
            raise ValueError("Local record TTL must be 30-86400 seconds")
        if rrtype in {"A", "AAAA"}:
            if not isinstance(address, str):
                raise ValueError("Local address must be a string")
            address = validate_network_ip(address, "Local DNS record address")
            parsed = ipaddress.ip_address(address)
            if parsed.version != (4 if rrtype == "A" else 6) or parsed.is_unspecified or parsed.is_multicast:
                raise ValueError("Local record address does not match its type")
        elif rrtype == "CNAME":
            address = _dns_name(address)
            if address == name:
                raise ValueError("A CNAME cannot point to itself")
        else:
            raise ValueError("Local records support A, AAAA and CNAME only")
        key = (name, rrtype, address)
        if key in seen:
            raise ValueError("Duplicate local DNS record")
        seen.add(key)
        previous = types.setdefault(name, set())
        if (rrtype == "CNAME" and previous) or "CNAME" in previous:
            raise ValueError("CNAME must be the only record at a name")
        previous.add(rrtype)
        records.append(LocalRecord(zone, name, rrtype, address, ttl))
    aliases = {record.name: record.value for record in records if record.rrtype == "CNAME"}
    for name in aliases:
        visited = set()
        while name in aliases:
            if name in visited:
                raise ValueError("Local CNAME records form a loop")
            visited.add(name)
            name = aliases[name]
    return tuple(sorted(records, key=lambda record: (record.zone, record.name, record.rrtype, record.value)))


def records_document(records: tuple[LocalRecord, ...]) -> dict:
    return {"version": 1, "records": [record.to_dict() for record in records]}


def health_identity(binding: DNSBinding) -> str:
    binding = parse_dns_binding(binding.to_dict())
    identity = [binding.network_url, binding.node_id, sorted(binding.addresses)]
    return hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()


def render_unbound(binding: DNSBinding, records: tuple[LocalRecord, ...] = (), *,
                   forbidden_domains: tuple[str, ...] = ()) -> str:
    """Serve only observed overlay IPs; native iterator has no recursive vendor."""
    binding = parse_dns_binding(binding.to_dict())
    records = parse_local_records(records_document(records), forbidden_domains=forbidden_domains)
    lines = [
        "# Managed by Basaltwater; local records are managed separately.",
        "server:", "    interface-automatic: no", "    ip-freebind: yes",
        "    port: 53", "    do-udp: yes", "    do-tcp: yes",
        "    access-control: 0.0.0.0/0 refuse", "    access-control: ::/0 refuse",
        "    access-control: 100.64.0.0/10 allow", "    access-control: fd7a:115c:a1e0::/48 allow",
        '    module-config: "validator iterator"', "    val-permissive-mode: no",
        "    harden-dnssec-stripped: yes", "    qname-minimisation: yes",
        "    hide-identity: yes", "    hide-version: yes", "    verbosity: 0",
        "    log-queries: no", "    log-replies: no", "    log-servfail: no",
        "    log-local-actions: no", "    statistics-interval: 0",
    ]
    lines.extend("    interface: " + address for address in binding.addresses)
    lines.extend([
        '    local-zone: "basaltwater-resolver.invalid." static',
        f"    local-data: '{HEALTH_NAME} 30 IN TXT \"{health_identity(binding)}\"'",
    ])
    for zone in sorted({record.zone for record in records}):
        lines.append(f'    local-zone: "{zone}" static')
    for record in records:
        lines.append(f'    local-data: "{record.name} {record.ttl} IN {record.rrtype} {record.value}"')
    # Debian maintains /var/lib/unbound/root.key in its packaged fragment/helper.
    # Keep its control socket local and disable it; no remote management needed.
    lines.extend(["remote-control:", "    control-enable: no", ""])
    return "\n".join(lines)
