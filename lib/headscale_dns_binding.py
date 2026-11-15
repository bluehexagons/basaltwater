"""Private resolver identity and exclusion for DNS publication and node teardown."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
import ipaddress
import os
import re
import stat
import uuid

from lib.atomic_io import read_json_file, write_json_atomic
from lib.concurrency import resource_lock
from lib.headscale_config import validate_headscale_url
from lib.setup_directories import check_directory_path, directory_fd
from lib.state_read import StateReadError
from lib.validation import validate_filesystem_path, validate_network_ip

DNS_BINDING_PATH = "/var/lib/basaltwater/unbound/binding.json"


@dataclass(frozen=True)
class DNSBinding:
    network_url: str
    node_id: str
    addresses: tuple[str, ...]
    advertised: bool = False
    publication_id: str | None = None

    def to_dict(self) -> dict:
        return {"version": 1, "network_url": self.network_url, "node_id": self.node_id,
                "addresses": list(self.addresses), "advertised": self.advertised,
                "publication_id": self.publication_id}


def parse_dns_binding(value: object) -> DNSBinding:
    """Validate observed metadata; no credentials or caller-selected commands."""
    fields = {"version", "network_url", "node_id", "addresses", "advertised", "publication_id"}
    if not isinstance(value, dict) or set(value) != fields or type(value["version"]) is not int or value["version"] != 1:
        raise ValueError("Invalid DNS binding schema")
    url = validate_headscale_url(value["network_url"])
    node_id = value["node_id"]
    if not isinstance(node_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9:_.-]{0,127}", node_id):
        raise ValueError("Invalid DNS node identity")
    addresses = value["addresses"]
    if not isinstance(addresses, list) or not 1 <= len(addresses) <= 8:
        raise ValueError("DNS binding requires observed overlay addresses")
    normalized = []
    for text in addresses:
        if not isinstance(text, str):
            raise ValueError("Invalid resolver address")
        address = ipaddress.ip_address(validate_network_ip(text, "Resolver address"))
        prefix = "100.64.0.0/10" if address.version == 4 else "fd7a:115c:a1e0::/48"
        if address not in ipaddress.ip_network(prefix):
            raise ValueError("Resolver address must belong to the intended overlay")
        normalized.append(str(address))
    if len(set(normalized)) != len(normalized):
        raise ValueError("Duplicate resolver address")
    advertised, publication_id = value["advertised"], value["publication_id"]
    if type(advertised) is not bool or advertised != (publication_id is not None):
        raise ValueError("Invalid resolver publication guard")
    if publication_id is not None:
        if not isinstance(publication_id, str) or str(uuid.UUID(publication_id)) != publication_id:
            raise ValueError("Invalid resolver publication identity")
    return DNSBinding(url, node_id, tuple(normalized), advertised, publication_id)


def read_dns_binding(path: str = DNS_BINDING_PATH) -> DNSBinding | None:
    """Read without creating files; retain malformed or unsafe state for recovery."""
    validate_filesystem_path(path)
    check_directory_path(os.path.dirname(path))
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return None
    try:
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != os.geteuid() or info.st_mode & 0o077):
            raise ValueError("Unsafe resolver binding file")
        return parse_dns_binding(read_json_file(path, max_bytes=64 * 1024))
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        raise StateReadError(path, "invalid private resolver identity or publication state") from exc


def save_dns_binding(
    binding: DNSBinding, *, expected: DNSBinding | None, path: str = DNS_BINDING_PATH,
) -> None:
    """Compare the inspected state under the same lock used by client teardown."""
    candidate = parse_dns_binding(binding.to_dict())
    validate_filesystem_path(path)
    parent = os.path.dirname(os.path.abspath(path))
    check_directory_path(parent)
    with resource_lock("unbound-binding", os.path.abspath(path)):
        current = read_dns_binding(path)
        if current != expected:
            raise ValueError("Resolver binding changed; inspect it before retrying")
        if current == candidate:
            return
        with directory_fd(parent, create=True, mode=0o700) as descriptor:
            info = os.fstat(descriptor)
            if info.st_uid != os.geteuid() or info.st_mode & 0o022:
                raise ValueError("Unsafe resolver binding directory")
            os.fchmod(descriptor, 0o700)
            write_json_atomic(path, candidate.to_dict(), mode=0o600)


@contextmanager
def resolver_lifecycle_guard(path: str = DNS_BINDING_PATH) -> Iterator[DNSBinding | None]:
    """Hold exclusion through disable/leave; publication reservations also block."""
    validate_filesystem_path(path)
    with resource_lock("unbound-binding", os.path.abspath(path)):
        binding = read_dns_binding(path)
        if binding is not None and binding.advertised:
            raise ValueError("Withdraw or replace the advertised DNS resolver before disabling or leaving")
        yield binding
