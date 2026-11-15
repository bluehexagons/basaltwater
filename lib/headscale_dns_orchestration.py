"""Saved-host convenience selection backed by explicit private SSH authority.

No discovery record grants management authority. Unknown mutation results retain
the exact controller request for explicit advanced DNS inspect/reconcile.
"""

from __future__ import annotations

import os

from common.service_tools.headscale_dns_authority import public_key
from common.service_tools.headscale_dns_control import pinned_entries
from lib.atomic_io import parse_json, remove_file_durable
from lib.cache import _load_cache_file
from lib.concurrency import resource_lock
from lib.headscale_dns_binding import parse_dns_binding
from lib.headscale_management import resolve_management
from lib.headscale_private import read_private, write_private
from lib.headscale_selection import resolve_client_server
from lib.headscale_transport import SSHBinding, _credential, exchange
from lib.workspace import get_known_hosts_path, get_setup_cache_dir, normalize_workspace_dir


class DNSPublicationExists(ValueError):
    """Existing publication must be inspected rather than activated again."""


def saved_target(reference: str, management, workspace: str) -> SSHBinding:
    """Resolve exact host/name uniquely, never the cache's first shared-tag match."""
    if not isinstance(reference, str) or not reference or len(reference) > 255:
        raise ValueError('Exact saved host or name required')
    directory = get_setup_cache_dir(workspace)
    matches = []
    for filename in sorted(os.listdir(directory)):
        if not filename.endswith('.json'):
            continue
        path = os.path.join(directory, filename)
        # Ordinary caches may have a readable (0755) parent. Require an owned,
        # non-writable parent and a private regular file without creating state.
        with os.fdopen(_credential(path, private=True), 'rb') as stream:
            content = stream.read(65537)
        if len(content) > 65536:
            raise ValueError('Saved host exceeds size limit')
        record = parse_json(content)
        if (type(record) is not dict or type(record.get('args')) is not dict or type(record.get('host')) is not str):
            raise ValueError('Invalid saved host inventory')
        names = [record['host'], record['args'].get('friendly_name'), record.get('name')]
        if any(isinstance(name, str) and name.casefold() == reference.casefold() for name in names):
            matches.append((path, record))
    if len(matches) != 1:
        raise ValueError('Saved host missing or ambiguous')
    path, record = matches[0]
    args = record['args']
    if args.get('host', record['host']) != record['host']:
        raise ValueError('Saved transport host mismatch')
    # DNSRemote has a fixed port-22 contract. Reject even future saved overrides.
    for field in ('ssh_port', 'port'):
        if field in args and (type(args[field]) is not int or args[field] != 22):
            raise ValueError('DNS convenience requires target SSH port 22')
    config = _load_cache_file(path, record['host'])
    if config is None or config.host != record['host'] or config.tailscale is False or not config.ssh_key:
        raise ValueError('Saved enrolled target with explicit SSH key required')
    if config.tailscale_login_server != management.endpoint:
        raise ValueError('Saved target requires the exact intended endpoint')
    selected = resolve_client_server(config.tailscale_server, server_id=config.tailscale_server_id,
                                     endpoint=config.tailscale_login_server, workspace=workspace)
    if selected.id != management.server_id or selected.url != management.endpoint:
        raise ValueError('Saved target belongs to another network')
    return SSHBinding(config.host, config.ssh_key, get_known_hosts_path(workspace))


def _pins(target: SSHBinding) -> str:
    fd = _credential(target.known_hosts_file, private=False)
    with os.fdopen(fd, 'rb') as stream:
        content = stream.read(65537)
    if len(content) > 65536:
        raise ValueError('Workspace pins exceed limit')
    return pinned_entries(target.host, content.decode())


def _control(binding, action: str, parameters: dict) -> dict:
    return exchange(binding.ssh, 'dns-control', {'endpoint': binding.endpoint, 'action': action,
                                               'parameters': parameters}, timeout=300)


def _observe(target, endpoint: str, role: str):
    result = exchange(target, 'dns-authority', {'endpoint': endpoint, 'action': 'observe',
                                              'parameters': {'role': role}})
    if set(result) != {'binding'}:
        raise ValueError('Incomplete observed DNS identity')
    binding = parse_dns_binding(result['binding'])
    if binding.network_url != endpoint:
        raise ValueError('Observed network differs from selection')
    return binding


def manage_dns(reference: str, *, action: str, resolver: str | None = None, peer: str | None = None,
               workspace: str | None = None, dry_run: bool = False) -> dict:
    """Publish/withdraw once or inspect retained state; dry runs are local reads."""
    if action not in {'publish', 'withdraw', 'inspect'}:
        raise ValueError('Invalid DNS management action')
    workspace = normalize_workspace_dir(workspace)
    binding = resolve_management(reference, workspace=workspace)
    journal = os.path.join(workspace, 'headscale-dns-attempt.json')
    retained = read_private(journal)
    if action == 'inspect':
        return {'attempt': retained, **({'dry_run': True} if dry_run else {'native': _control(binding, 'inspect', {})})}
    if retained is not None:
        raise ValueError('Retained DNS attempt requires explicit inspect/reconcile before retry')
    dns = saved_target(resolver, binding, workspace)
    check = saved_target(peer, binding, workspace)
    if dns.host.casefold().rstrip('.') == check.host.casefold().rstrip('.'):
        raise ValueError('Distinct resolver and peer required')
    if dry_run:
        return {'dry_run': True, 'action': action, 'endpoint': binding.endpoint,
                'resolver_host': dns.host, 'peer_host': check.host}
    with resource_lock('headscale-management', workspace), resource_lock('headscale-dns-convenience', workspace):
        if (resolve_management(reference, workspace=workspace) != binding or read_private(journal) is not None
                or saved_target(resolver, binding, workspace) != dns or saved_target(peer, binding, workspace) != check):
            raise ValueError('DNS selection changed')
        pins = {'resolver_pins': _pins(dns), 'peer_pins': _pins(check)}
        state = _control(binding, 'inspect', {})
        if state.get('journal') is not None:
            raise ValueError('Interrupted DNS transaction; explicit inspect/reconcile required')
        if action == 'publish' and state.get('binding') is not None:
            raise DNSPublicationExists('Existing publication; explicit inspect/reconcile required; activation refused')
        node = _observe(dns, binding.endpoint, 'resolver')
        other = _observe(check, binding.endpoint, 'peer')
        if (node.node_id == other.node_id or set(node.addresses) & set(other.addresses) or other.advertised):
            raise ValueError('Independent native DNS peer required')
        if action == 'withdraw' and (not node.advertised or state.get('binding') != node.to_dict()):
            raise ValueError('Exact actual advertised publication required')
        if action == 'publish' and node.advertised:
            raise DNSPublicationExists('Resolver has retained publication; inspect/reconcile')
        key_result = _control(binding, 'key', {})
        if set(key_result) != {'key'}:
            raise ValueError('Invalid publication authority response')
        key = public_key(key_result['key'])
        for target, role in ((dns, 'resolver'), (check, 'peer')):
            result = exchange(target, 'dns-authority', {'endpoint': binding.endpoint, 'action': 'authorize',
                                                       'parameters': {'key': key, 'role': role}})
            if result != {'authorized': True}:
                raise ValueError('Publication authority unconfirmed')
        parameters = {'resolver': node.to_dict(), 'peer': other.to_dict(), 'resolver_host': dns.host,
                      'peer_host': check.host, **pins}
        # No secret material. This precedes the single mutation and survives lost replies.
        write_private(journal, {'endpoint': binding.endpoint, 'action': action, 'parameters': parameters})
        result = _control(binding, action, parameters)
        if set(result) != {'binding'}:
            raise ValueError('DNS mutation result unconfirmed; inspect/reconcile retained attempt')
        if action == 'publish':
            published = parse_dns_binding(result['binding'])
            if (not published.advertised or published.network_url != node.network_url
                    or published.node_id != node.node_id or published.addresses != node.addresses):
                raise ValueError('DNS publication result differs; inspect/reconcile')
        elif result['binding'] is not None:
            raise ValueError('Withdrawal unconfirmed; inspect/reconcile')
        remove_file_durable(journal)
        return result


def reconcile_dns_attempt(reference: str, *, expected: dict, workspace: str | None = None,
                          dry_run: bool = False) -> dict:
    """Complete an exact reviewed controller attempt after native reconciliation."""
    workspace = normalize_workspace_dir(workspace)
    binding = resolve_management(reference, workspace=workspace)
    journal = os.path.join(workspace, 'headscale-dns-attempt.json')
    if (type(expected) is not dict or set(expected) != {'endpoint', 'action', 'parameters'}
            or expected['endpoint'] != binding.endpoint or expected['action'] not in {'publish', 'withdraw'}
            or type(expected['parameters']) is not dict
            or set(expected['parameters']) != {'resolver', 'peer', 'resolver_host', 'peer_host', 'resolver_pins', 'peer_pins'}
            or read_private(journal) != expected):
        raise ValueError('Exact retained attempt for this network required')
    p = expected['parameters']
    original, original_peer = parse_dns_binding(p['resolver']), parse_dns_binding(p['peer'])
    if original.network_url != binding.endpoint or original_peer.network_url != binding.endpoint:
        raise ValueError('Retained attempt network differs')
    if dry_run:
        return {'dry_run': True, 'readiness_claim': False}
    with resource_lock('headscale-management', workspace), resource_lock('headscale-dns-convenience', workspace):
        if resolve_management(reference, workspace=workspace) != binding or read_private(journal) != expected:
            raise ValueError('Retained attempt changed')
        dns = saved_target(p['resolver_host'], binding, workspace)
        check = saved_target(p['peer_host'], binding, workspace)
        if _pins(dns) != p['resolver_pins'] or _pins(check) != p['peer_pins']:
            raise ValueError('Retained SSH authority changed')
        state = _control(binding, 'inspect', {})
        if state.get('journal') is not None or not {'binding', 'config'} <= state.keys():
            raise ValueError('Reconcile native transaction first')
        node = _observe(dns, binding.endpoint, 'resolver')
        peer = _observe(check, binding.endpoint, 'peer')
        if ((node.network_url, node.node_id, node.addresses) !=
                (original.network_url, original.node_id, original.addresses)
                or peer != original_peer or (node.to_dict() if node.advertised else None) != state['binding']
                or (expected['action'] == 'withdraw' and node.advertised and node != original)):
            raise ValueError('Settled native identity differs from retained attempt')
        confirmed = _control(binding, 'confirm', {**p, 'resolver': node.to_dict(), 'peer': peer.to_dict()})
        if confirmed != {'confirmed': True, 'binding': state['binding']} or read_private(journal) != expected:
            raise ValueError('Completion unconfirmed; retain controller attempt')
        remove_file_durable(journal)
        return {'reconciled': True, 'binding': state['binding']}
