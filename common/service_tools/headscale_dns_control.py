#!/usr/bin/env python3
"""Control-root authority bootstrap and exact observed DNS transaction dispatch."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import struct
import subprocess
import sys

if __name__ == '__main__' and not __package__:
    sys.path.insert(0, '/opt/basaltwater')

from common.service_tools.headscale_dns_authority import private_bytes, public_key, root_directory
from lib.atomic_io import parse_json, write_bytes_atomic
from lib.concurrency import resource_lock
from lib.headscale_admin import NativeAdmin, identifier
from lib.headscale_config import validate_headscale_url
from lib.headscale_dns_binding import parse_dns_binding
from lib.headscale_dns_peer import DNSRemote
from lib.headscale_dns_publish import inspect_dns, publish_dns, withdraw_dns
from lib.validators import validate_host


def pinned_entries(host: str, text: str) -> str:
    """Select exact or OpenSSH hashed pins; never scan, expand patterns or trust DNS."""
    if not isinstance(host, str) or not validate_host(host) or type(text) is not str or len(text) > 65536:
        raise ValueError('Invalid pinned authority')
    selected = []
    for line in text.splitlines():
        fields = line.split()
        if not fields or fields[0].startswith('#'):
            continue
        if fields[0].startswith('@') or any(c in fields[0] for c in '*?!'):
            raise ValueError('Marked or patterned pins require explicit authority handling')
        names = fields[0].split(',')
        match = False
        for name in names:
            if name.startswith('|1|'):
                bits = name.split('|')
                if len(bits) != 4:
                    raise ValueError('Invalid hashed pin')
                salt, digest = (base64.b64decode(v, validate=True) for v in bits[2:])
                if len(salt) != 20 or len(digest) != 20:
                    raise ValueError('Invalid hashed pin')
                match |= hmac.compare_digest(hmac.new(salt, host.encode(), 'sha1').digest(), digest)
            else:
                match |= name.casefold() == host.casefold()
        if not match:
            continue
        if len(fields) < 3 or fields[1] not in {'ssh-ed25519', 'ssh-rsa', 'ecdsa-sha2-nistp256', 'ecdsa-sha2-nistp384', 'ecdsa-sha2-nistp521'}:
            raise ValueError('Unsupported pinned host key')
        blob = base64.b64decode(fields[2], validate=True)
        kind = fields[1].encode()
        if len(blob) > 8192 or not blob.startswith(struct.pack('>I', len(kind)) + kind):
            raise ValueError('Invalid pinned host key')
        # Rewrite only the public hostname selector, retaining the validated key.
        selected.append(host + ' ' + fields[1] + ' ' + fields[2])
    if not selected or len(selected) > 16:
        raise ValueError('Exact workspace host pin required')
    return '\n'.join(sorted(set(selected))) + '\n'


def publication_key(admin) -> tuple[str, str]:
    directory = str(admin.paths.private / 'dns-authority')
    root_directory(directory, create=True)
    if os.stat(directory).st_mode & 0o077:
        raise ValueError('Unsafe publication directory')
    with resource_lock('headscale-dns-authority', directory):
        marker, key = directory + '/endpoint', directory + '/identity'
        previous = private_bytes(marker)
        if previous is None:
            if os.path.lexists(key) or os.path.lexists(key + '.pub'):
                raise ValueError('Foreign publication key; inspect before recovery')
            write_bytes_atomic(marker, admin.endpoint.encode(), mode=0o600)
            # An interrupted generation retains the marker and cannot issue a replacement.
            subprocess.run(['/usr/bin/ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-C', '', '-f', key],
                           check=True, timeout=15, capture_output=True,
                           env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'})
            os.chmod(key + '.pub', 0o600)
        elif previous != admin.endpoint.encode():
            raise ValueError('Publication endpoint changed')
        if not private_bytes(key):
            raise ValueError('Publication key generation unconfirmed; inspect')
        result = subprocess.run(['/usr/bin/ssh-keygen', '-y', '-f', key], check=True,
                                timeout=15, capture_output=True,
                                env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'})
        if len(result.stdout) > 256:
            raise ValueError('Invalid public key')
        public = public_key(result.stdout.decode().strip())
        if os.path.lexists(key + '.pub'):
            saved = private_bytes(key + '.pub')
            if saved is None or public_key(saved.decode().strip()) != public:
                raise ValueError('Publication public identity changed; inspect')
        return key, public


def remote(admin, host: str, pins: str, key: str) -> DNSRemote:
    content = pinned_entries(host, pins).encode()
    path = str(admin.paths.private / 'dns-authority' / ('hosts-' + hashlib.sha256(host.encode()).hexdigest()))
    old = private_bytes(path)
    if old is not None and old != content:
        raise ValueError('Pinned authority changed; explicit reconciliation required')
    if old is None:
        write_bytes_atomic(path, content, mode=0o600)
    return DNSRemote(host, key, path)


def change(admin, action: str, p: dict) -> dict:
    if set(p) != {'resolver', 'peer', 'resolver_host', 'peer_host', 'resolver_pins', 'peer_pins'}:
        raise ValueError('Invalid transaction schema')
    binding, peer = parse_dns_binding(p['resolver']), parse_dns_binding(p['peer'])
    identifier(binding.node_id)
    identifier(peer.node_id)
    if (binding.network_url != admin.endpoint or peer.network_url != admin.endpoint or peer.advertised
            or binding.node_id == peer.node_id or set(binding.addresses) & set(peer.addresses)
            or p['resolver_host'] == p['peer_host']):
        raise ValueError('Independent intended network identities required')
    # Validate both authorities before writing any authority files.
    for role in ('resolver', 'peer'):
        if pinned_entries(p[role + '_host'], p[role + '_pins']) != p[role + '_pins']:
            raise ValueError('Noncanonical pinned authority')
    state = inspect_dns()
    if state['journal'] is not None:
        raise ValueError('Interrupted DNS publication; use explicit inspect/reconcile')
    expected = state['binding']
    if action == 'publish' and (expected is not None or binding.advertised):
        raise ValueError('Existing publication requires explicit inspect/reconcile; activation refused')
    if action == 'withdraw' and (not binding.advertised or expected != binding.to_dict()):
        raise ValueError('Exact inspected publication required')
    key, _ = publication_key(admin)
    dns = remote(admin, p['resolver_host'], p['resolver_pins'], key)
    check = remote(admin, p['peer_host'], p['peer_pins'], key)
    if action == 'publish':
        return publish_dns(binding, dns_remote=dns, peer=peer, peer_remote=check)
    return withdraw_dns(binding, dns_remote=dns, peer=peer, peer_remote=check)


def execute_request(payload: bytes) -> dict:
    try:
        if os.geteuid() != 0 or len(payload) > 16384:
            raise ValueError()
        request = parse_json(payload)
        if type(request) is not dict or set(request) != {'endpoint', 'action', 'parameters'}:
            raise ValueError()
        endpoint = validate_headscale_url(request['endpoint'])
        if endpoint != request['endpoint'] or type(request['parameters']) is not dict:
            raise ValueError()
        admin = NativeAdmin()
        admin.verify()
        if admin.endpoint != endpoint:
            raise ValueError()
        action, p = request['action'], request['parameters']
        if action == 'inspect' and not p:
            result = inspect_dns()
        elif action == 'key' and not p:
            if inspect_dns()['journal'] is not None:
                raise ValueError('Inspect/reconcile existing journal')
            _, key = publication_key(admin)
            result = {'key': key}
        elif action in {'publish', 'withdraw'}:
            result = change(admin, action, p)
        else:
            raise ValueError()
        return {'ok': True, 'result': result}
    except Exception:
        return {'ok': False, 'result': {'error': 'DNS control unconfirmed; use explicit inspect/reconcile'}}


def main() -> int:
    result = execute_request(sys.stdin.buffer.read(16385))
    print(json.dumps(result))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
