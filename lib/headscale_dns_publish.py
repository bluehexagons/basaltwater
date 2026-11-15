"""Explicit control-host DNS publication with durable, fail-closed recovery.

All entrypoints take explicit SSH authority; saved configuration never publishes.
The management binding lease is the same exclusion used by NativeAdmin teardown.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
import os
import pwd
import uuid

from lib.atomic_io import parse_json, remove_file_durable, write_json_atomic
from lib.headscale_admin import NativeAdmin, identifier
from lib.headscale_dns_binding import DNSBinding, dns_binding_lease, parse_dns_binding
from lib.headscale_dns_peer import DNSRemote
from lib.headscale_server import health, private_directory, publish_config, regular_bytes
from lib.operation_state import OperationStateStore

__all__ = ["publish_dns", "withdraw_dns", "inspect_dns", "reconcile_dns", "dns_candidate"]


def _validated(binding: DNSBinding) -> DNSBinding:
    result = parse_dns_binding(binding.to_dict())
    identifier(result.node_id)
    return result


def _current(admin: NativeAdmin, binding: DNSBinding, *, online: bool = True) -> None:
    if binding.network_url != admin.endpoint:
        raise ValueError("DNS network mismatch")
    nodes = [n for n in admin.nodes() if n["node_id"] == binding.node_id]
    if (len(nodes) != 1 or set(nodes[0]["addresses"]) != set(binding.addresses)
            or (online and not nodes[0]["online"])):
        raise ValueError("Current native DNS/peer identity mismatch")


def dns_candidate(config: dict, old: DNSBinding | None, new: DNSBinding | None) -> dict:
    """Preserve every unrelated setting; refuse foreign/adopted DNS policies."""
    candidate = deepcopy(config)
    dns = candidate["dns"]
    if (dns.get("magic_dns") is not True or not dns.get("base_domain")
            or dns.get("override_local_dns") is not (old is not None)
            or dns.get("nameservers") != {"global": list(old.addresses) if old else [], "split": {}}):
        raise ValueError("Incompatible DNS policy; explicit adoption required")
    dns["nameservers"]["global"] = list(new.addresses) if new else []
    dns["override_local_dns"] = new is not None
    return candidate


def _paths(admin):
    return (admin.paths.private / "dns-publication.json",
            admin.paths.private / "dns-transaction.json")


def _read_config(admin):
    return parse_json(regular_bytes(admin.paths.config))


def _journal(admin):
    path = _paths(admin)[1]
    if not os.path.lexists(path):
        return None
    if path.stat().st_mode & 0o077:
        raise ValueError("Unsafe DNS journal")
    value = parse_json(regular_bytes(path))
    if (type(value) is not dict or set(value) != {"version", "old", "new", "before", "after"}
            or type(value["version"]) is not int or value["version"] != 1):
        raise ValueError("Invalid DNS journal; preserve recovery state")
    old = _validated(parse_dns_binding(value["old"])) if value["old"] else None
    new = _validated(parse_dns_binding(value["new"])) if value["new"] else None
    if (old is None and new is None or any(b and not b.advertised for b in (old, new))
            or dns_candidate(value["before"], old, new) != value["after"]):
        raise ValueError("Invalid DNS recovery transition")
    return value


def _node(remote: DNSRemote, binding: DNSBinding) -> None:
    if remote.call("node", binding=binding.to_dict()) != {"binding": binding.to_dict()}:
        raise ValueError("DNS node verification incomplete")


def _cas(remote: DNSRemote, before: DNSBinding, after: DNSBinding) -> None:
    if remote.call("cas", binding=before.to_dict(), desired=after.to_dict()) != {"binding": after.to_dict()}:
        raise ValueError("DNS node reservation unconfirmed; reconcile")


def _peer(admin, remote, binding, peer, effective=None):
    if (peer.network_url != binding.network_url or peer.node_id == binding.node_id
            or set(peer.addresses) & set(binding.addresses)):
        raise ValueError("Independent enrolled peer required")
    _current(admin, binding, online=effective != "withdrawn")
    _current(admin, peer)
    if remote.call("peer", binding=binding.to_dict(), peer=peer.to_dict(), effective=effective) != {
            "peer": peer.to_dict(), "checked": True}:
        raise ValueError("Independent peer check incomplete")


def _verify_active(admin, config, binding, peer_remote, peer, reference):
    health(admin.paths)
    admin.verify()
    if _read_config(admin) != config:
        raise ValueError("Effective control configuration differs")
    _peer(admin, peer_remote, binding or reference, peer,
          "published" if binding else "withdrawn")


def publish_dns(binding: DNSBinding, *, dns_remote: DNSRemote, peer: DNSBinding,
                peer_remote: DNSRemote, expected: DNSBinding | None = None,
                old_remote: DNSRemote | None = None, dry_run: bool = False) -> dict:
    """Publish or replace one resolver. Replacement requires exact old guard/SSH."""
    binding, peer = _validated(binding), _validated(peer)
    if binding.advertised or peer.advertised or (expected is not None and not _validated(expected).advertised):
        raise ValueError("Explicit unpublished candidate and exact prior publication required")
    if expected and (old_remote is None or expected.node_id == binding.node_id):
        raise ValueError("Replacement requires a different resolver and old SSH authority")
    return _change(binding, dns_remote, peer, peer_remote, expected, old_remote, dry_run)


def withdraw_dns(expected: DNSBinding, *, dns_remote: DNSRemote, peer: DNSBinding,
                 peer_remote: DNSRemote, dry_run: bool = False) -> dict:
    expected, peer = _validated(expected), _validated(peer)
    if not expected.advertised or peer.advertised:
        raise ValueError("Exact publication and independent peer required")
    return _change(None, None, peer, peer_remote, expected, dns_remote, dry_run)


def _change(new, remote, peer, peer_remote, old, old_remote, dry_run):
    reference = new or old
    if (reference.network_url != peer.network_url or reference.node_id == peer.node_id
            or set(reference.addresses) & set(peer.addresses)
            or (old and (old.network_url != reference.network_url or old.node_id == peer.node_id))):
        raise ValueError("Independent peer/network mismatch")
    for authority in (remote, peer_remote, old_remote):
        if authority:
            authority.validate_files()
    if dry_run:
        return {"dry_run": True}
    admin = NativeAdmin()
    admin.verify()
    guard, journal = _paths(admin)
    with dns_binding_lease(str(guard)) as lease:
        if _journal(admin) is not None or lease.binding != old:
            raise ValueError("DNS state changed or interrupted; inspect and reconcile")
        before = _read_config(admin)
        reserved = replace(new, advertised=True, publication_id=str(uuid.uuid4())) if new else None
        after = dns_candidate(before, old, reserved)
        if new:
            _node(remote, new)
            _peer(admin, peer_remote, new, peer)
        elif old:
            _current(admin, old, online=False)
            _current(admin, peer)
        private_directory(admin.paths.private)
        store = OperationStateStore(str(admin.paths.private / "operation.json"))
        with store.ownership():
            if store.load() is not None:
                raise ValueError("Unfinished native operation; reconcile first")
            write_json_atomic(str(journal), dict(version=1, old=old.to_dict() if old else None,
                                                new=reserved.to_dict() if reserved else None,
                                                before=before, after=after), mode=0o600)
            record = store.begin("dns-publication", "headscale", "mutating")
            # Both guards precede native DNS mutation. Reserve management first
            # so a lost node acknowledgement also blocks native node removal.
            # The shared lease excludes old-node teardown throughout replacement.
            if new:
                lease.save(reserved)
                _cas(remote, new, reserved)
            if _read_config(admin) != before:
                raise ValueError("Control configuration changed during verification")
            if new:
                _current(admin, new)
            publish_config(json.dumps(after, indent=2) + "\n", admin.paths,
                           gid=pwd.getpwnam("headscale").pw_gid)
            _verify_active(admin, after, reserved, peer_remote, peer, reference)
            if old:
                _cas(old_remote, old, replace(old, advertised=False, publication_id=None))
            if reserved is None:
                lease.save(replace(old, advertised=False, publication_id=None))
                lease.clear()
            store.complete(record.operation_id, keep_ownership=True)
        remove_file_durable(str(journal))
    return {"binding": reserved.to_dict() if reserved else None}


def inspect_dns() -> dict:
    """Read private journal, actual config and guard without repairing anything."""
    admin = NativeAdmin()
    admin.verify()
    with dns_binding_lease(str(_paths(admin)[0])) as lease:
        return {"binding": lease.binding.to_dict() if lease.binding else None,
                "journal": _journal(admin), "config": _read_config(admin)}


def reconcile_dns(*, expected_journal: dict, keep: str, peer: DNSBinding,
                  peer_remote: DNSRemote, new_remote: DNSRemote | None = None,
                  old_remote: DNSRemote | None = None, dry_run: bool = False) -> dict:
    """Finish observed before/after state, never retry activation or invent IDs.

    Caller reviews inspect_dns() output and chooses the exact currently active
    side. Guards release only after native/peer checks; unknown state blocks.
    """
    peer = _validated(peer)
    if keep not in {"before", "after"} or type(expected_journal) is not dict:
        raise ValueError("Select inspected recovery side")
    for authority in (peer_remote, new_remote, old_remote):
        if authority:
            authority.validate_files()
    if dry_run:
        return {"dry_run": True}
    admin = NativeAdmin()
    admin.verify()
    guard, journal_path = _paths(admin)
    store = OperationStateStore(str(admin.paths.private / "operation.json"))
    with dns_binding_lease(str(guard)) as lease, store.ownership():
        record = store.load()
        if record is not None and record.operation_type != "dns-publication":
            raise ValueError("Unrelated runtime operation requires separate recovery")
        journal = _journal(admin)
        if journal is None or journal != expected_journal or _read_config(admin) != journal[keep]:
            raise ValueError("Inspected recovery/config changed; retain all guards")
        old = parse_dns_binding(journal["old"]) if journal["old"] else None
        new = parse_dns_binding(journal["new"]) if journal["new"] else None
        retained, released = (new, old) if keep == "after" else (old, new)
        retained_remote, released_remote = (new_remote, old_remote) if keep == "after" else (old_remote, new_remote)
        if lease.binding not in (old, new):
            raise ValueError("Management reservation changed")
        if retained:
            if retained_remote is None:
                raise ValueError("Retained resolver SSH authority required")
            _node(retained_remote, retained)
            lease.save(retained)
        _verify_active(admin, journal[keep], retained, peer_remote, peer, new or old)
        if released:
            if released_remote is None:
                raise ValueError("Released resolver SSH authority required")
            plain = replace(released, advertised=False, publication_id=None)
            # Read first; a lost release ack can be reconciled without replay.
            observed = released_remote.call("inspect", binding=plain.to_dict())
            if observed != {"binding": plain.to_dict()}:
                _cas(released_remote, released, plain)
        if retained is None and lease.binding:
            lease.save(replace(lease.binding, advertised=False, publication_id=None))
            lease.clear()
        # Complete only this operation kind after proving both remote guards.
        if record is not None:
            store.complete(record.operation_id, keep_ownership=True)
        remove_file_durable(str(journal_path))
    return {"binding": retained.to_dict() if retained else None}
