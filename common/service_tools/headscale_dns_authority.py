#!/usr/bin/env python3
"""Bounded root observation and forced-command publication-key authorization."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import stat
import struct
import sys

if __name__ == "__main__" and not __package__:
    sys.path.insert(0, "/opt/basaltwater")

from lib.atomic_io import parse_json, write_bytes_atomic
from lib.concurrency import resource_lock
from lib.headscale_config import validate_headscale_url
from lib.setup_directories import directory_fd

COMMAND = '/usr/bin/python3 -I /opt/basaltwater/common/service_tools/headscale_dns.py'


def public_key(value: object) -> str:
    if not isinstance(value, str) or len(value) > 256:
        raise ValueError("Invalid publication public key")
    parts = value.split()
    if len(parts) != 2 or parts[0] != "ssh-ed25519":
        raise ValueError("Invalid publication public key")
    blob = base64.b64decode(parts[1], validate=True)
    if blob[:19] != struct.pack('>I', 11) + b'ssh-ed25519' + struct.pack('>I', 32) or len(blob) != 51:
        raise ValueError("Invalid publication public key")
    return " ".join(parts)


def root_directory(path: str, *, create: bool = False) -> None:
    """Every ancestor must be root owned, non-writable and free of symlinks."""
    current = Path('/')
    for component in Path(path).parts[1:]:
        current /= component
        with directory_fd(str(current), create=create, mode=0o700) as fd:
            info = os.fstat(fd)
            if info.st_uid != 0 or info.st_mode & 0o022:
                raise ValueError("Unsafe root authority directory")


def private_bytes(path: str) -> bytes | None:
    root_directory(str(Path(path).parent))
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1
                or info.st_mode & 0o077 or info.st_size > 65536):
            raise ValueError("Unsafe root authority file")
        result = stream.read(65537)
    if len(result) > 65536:
        raise ValueError("Authority file exceeds limit")
    return result


def authorize(key: str, *, directory: str = '/root/.ssh') -> dict:
    key = public_key(key)
    root_directory(directory, create=True)
    path = directory + '/authorized_keys'
    with resource_lock('headscale-dns-authorize', directory):
        before = private_bytes(path) or b''
        line = f'restrict,command="{COMMAND}" {key}\n'.encode()
        # Refuse the same material with broader/different options, including comments.
        occurrences = [entry for entry in before.splitlines() if key.split()[1].encode() in entry.split()]
        if occurrences:
            if occurrences != [line.rstrip(b'\n')]:
                raise ValueError("Publication key has foreign authorization")
        else:
            after = before + (b'\n' if before and not before.endswith(b'\n') else b'') + line
            if len(after) > 65536:
                raise ValueError("Authorization exceeds limit")
            write_bytes_atomic(path, after, mode=0o600)
    return {'authorized': True}


def observe(endpoint: str, role: str) -> dict:
    from common.unbound_steps import UnboundPaths, _manifest, _observe, _same_identity
    from lib.headscale_client import owned, reject_control_host, client_lock
    from lib.headscale_dns_binding import parse_dns_binding, read_dns_binding

    reject_control_host(endpoint)
    with client_lock():
        ownership = owned(endpoint)
        if ownership['phase'] != 'enrolled':
            raise ValueError('Owned enrolled client required')
        observed = _observe(endpoint)
        if ownership['node_id'] != observed.node_id or owned(endpoint) != ownership:
            raise ValueError('Owned native client identity differs')
    if role == 'resolver':
        paths = UnboundPaths()
        binding = read_dns_binding(paths.binding)
        managed = _manifest(paths)
        if (os.path.lexists(paths.operation) or os.path.lexists(paths.backup)
                or binding is None or managed is None or managed['enabled'] is not True
                or not _same_identity(binding, observed)
                or not _same_identity(parse_dns_binding(managed['binding']), binding)):
            raise ValueError("Resolver identity unavailable; inspect/reconcile")
        observed = binding
    return {'binding': observed.to_dict()}


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
        p = request['parameters']
        if request['action'] == 'observe' and set(p) == {'role'} and p['role'] in {'resolver', 'peer'}:
            result = observe(endpoint, p['role'])
        elif request['action'] == 'authorize' and set(p) == {'key', 'role'} and p['role'] in {'resolver', 'peer'}:
            observe(endpoint, p['role'])  # Authority is never installed on a foreign/unowned client.
            result = authorize(p['key'])
        else:
            raise ValueError()
        return {'ok': True, 'result': result}
    except Exception:
        return {'ok': False, 'result': {'error': 'DNS authority unconfirmed; inspect/reconcile'}}


def main() -> int:
    result = execute_request(sys.stdin.buffer.read(16385))
    print(json.dumps(result))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
