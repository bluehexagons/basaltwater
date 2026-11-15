"""Stopped, private managed resolver snapshots; never restore publication leases."""

from __future__ import annotations

import base64
from dataclasses import replace
import hashlib
import os
from pathlib import Path
import tempfile

from common.unbound_steps import (
    UnboundPaths, _candidate, _files, _manifest, _observe, _read,
    _private_directory, _same_identity, _stock_fragments, _write,
)
from lib.atomic_io import parse_json, write_json_atomic
from lib.headscale_dns_binding import DNSBinding, dns_binding_lease, parse_dns_binding
from lib.headscale_server import require_capabilities
from lib.operation_state import OperationStateStore
from lib.remote_utils import run
from lib.setup_directories import directory_fd, check_directory_path
from lib.unbound_config import parse_local_records, render_unbound
from lib.validation import validate_filesystem_path

MAX_SNAPSHOT = 256 * 1024


def private_json(path: Path, *, limit: int = MAX_SNAPSHOT) -> object:
    """Bounded private input, using the resolver's no-link, owned-file reader."""
    validate_filesystem_path(str(path))
    # The shared reader imposes a tighter per-file bound too.
    raw = _read(str(path), private=True)
    if raw is None or len(raw) > limit:
        raise ValueError("Missing or oversized private recovery input")
    return parse_json(raw)


def _stopped() -> None:
    state = run(["systemctl", "show", "unbound.service", "--property=ActiveState", "--value"],
                capture_output=True, timeout=30)
    if state.stdout.strip() not in {"inactive", "failed"}:
        raise ValueError("Unbound must already be stopped")


def _mapping(paths: UnboundPaths) -> dict[str, str]:
    return dict(zip(("fragment", "defaults", "dropin", "records"), _files(paths)))


def validate_unbound_backup(source: Path, *, expected: DNSBinding,
                            paths: UnboundPaths = UnboundPaths()) -> dict:
    """Pure reads and validation; no native checks, locks, staging or writes."""
    expected = parse_dns_binding(expected.to_dict())
    doc = private_json(source)
    if (not isinstance(doc, dict) or set(doc) != {"version", "binding", "manifest", "files"}
            or type(doc["version"]) is not int or doc["version"] != 1
            or parse_dns_binding(doc["binding"]) != expected
            or not isinstance(doc["files"], dict) or set(doc["files"]) != set(_mapping(paths))):
        raise ValueError("Resolver snapshot binding/schema mismatch")
    manifest = doc["manifest"]
    if (not isinstance(manifest, dict) or set(manifest) != {"version", "enabled", "binding", "files"}
            or type(manifest["version"]) is not int or manifest["version"] != 1
            or type(manifest["enabled"]) is not bool
            or parse_dns_binding(manifest["binding"]) != expected
            or not isinstance(manifest["files"], dict)
            or set(manifest["files"]) != set(_files(paths))):
        raise ValueError("Resolver snapshot manifest mismatch; explicit layout migration required")
    contents = {}
    for name, path in _mapping(paths).items():
        entry = doc["files"][name]
        if (not isinstance(entry, dict) or set(entry) != {"content", "mode", "sha256"}
                or type(entry["mode"]) is not int
                or entry["mode"] & ~(0o600 if name == "records" else 0o644)
                or not entry["mode"] & 0o400):
            raise ValueError("Unsafe resolver snapshot file metadata")
        raw = base64.b64decode(entry["content"], validate=True)
        digest = hashlib.sha256(raw).hexdigest()
        if len(raw) > 256 * 1024 or digest != entry["sha256"] or digest != manifest["files"][path]:
            raise ValueError("Resolver snapshot integrity failure")
        contents[name] = raw
    records = parse_local_records(parse_json(contents["records"]))
    if contents["fragment"] != render_unbound(expected, records).encode():
        raise ValueError("Resolver configuration does not match snapshot identity/records")
    if (contents["defaults"] != b"# Managed by Basaltwater\nRESOLVCONF=false\nROOT_TRUST_ANCHOR_UPDATE=true\n"
            or contents["dropin"] != b"[Unit]\nAfter=network-online.target tailscaled.service\nWants=network-online.target tailscaled.service\n"):
        raise ValueError("Unrecognized managed resolver configuration")
    return doc


def backup_unbound(destination: Path, *, paths: UnboundPaths = UnboundPaths()) -> DNSBinding:
    """Capture consistent managed files and identity while holding DNS exclusion."""
    require_capabilities()
    validate_filesystem_path(str(destination))
    check_directory_path(str(destination.parent))
    with dns_binding_lease(paths.binding) as lease:
        _stopped()
        _private_directory(paths.state_dir)
        store = OperationStateStore(paths.operation)
        with store.ownership():
            if store.load() is not None or _read(paths.backup, private=True) is not None:
                raise ValueError("Unfinished resolver operation")
            manifest = _manifest(paths)
            binding = lease.binding
            if manifest is None or binding is None or not _same_identity(parse_dns_binding(manifest["binding"]), binding):
                raise ValueError("Managed resolver identity unavailable")
            observed = _observe(binding.network_url)
            if not _same_identity(observed, binding):
                raise ValueError("Native resolver identity changed")
            _stock_fragments(paths, managed=True)
            files = {}
            for name, path in _mapping(paths).items():
                raw = _read(path, private=name == "records")
                if raw is None:
                    raise ValueError("Missing resolver configuration")
                mode = os.stat(path).st_mode & 0o7777
                files[name] = dict(content=base64.b64encode(raw).decode(), mode=mode,
                                   sha256=hashlib.sha256(raw).hexdigest())
            # Publication state is captured as evidence only, never replayed.
            manifest = dict(manifest, binding=binding.to_dict())
            doc = dict(version=1, binding=binding.to_dict(), manifest=manifest, files=files)
            # Validate in private staging before publishing a new destination.
            with tempfile.TemporaryDirectory(prefix=".unbound-backup-", dir=destination.parent) as temporary:
                staged = Path(temporary) / "snapshot.json"
                write_json_atomic(str(staged), doc)
                validate_unbound_backup(staged, expected=binding, paths=paths)
                # Exclusive publication prevents overwriting prior recovery material.
                os.link(staged, destination, follow_symlinks=False)
                with directory_fd(str(destination.parent)) as descriptor:
                    os.fsync(descriptor)
            return binding


def restore_unbound(source: Path, *, expected: DNSBinding,
                    reviewed_rebind: DNSBinding | None = None,
                    paths: UnboundPaths = UnboundPaths()) -> None:
    """Restore stopped only; new identity needs explicit unpublished review.

    Activation, firewall reconciliation and peer verification/publication are
    separate steps. Failure retains a blocking marker; no blind rollback.
    """
    require_capabilities()
    doc = validate_unbound_backup(source, expected=expected, paths=paths)
    candidate = replace(expected, advertised=False, publication_id=None)
    if reviewed_rebind is not None:
        reviewed_rebind = parse_dns_binding(reviewed_rebind.to_dict())
        if reviewed_rebind.advertised or reviewed_rebind.network_url != expected.network_url:
            raise ValueError("Rebind must be unpublished and on the same network")
        candidate = reviewed_rebind
    with dns_binding_lease(paths.binding) as lease:
        if lease.binding is not None:
            if lease.binding.advertised:
                raise ValueError("Withdraw advertised/reserved resolver before recovery")
            if not any(_same_identity(lease.binding, item) for item in (expected, candidate)):
                raise ValueError("Current resolver binding conflicts with recovery")
        _stopped()
        if not _same_identity(_observe(candidate.network_url), candidate):
            raise ValueError("Observed native identity differs; explicit reviewed rebind required")
        _private_directory(paths.state_dir)
        store = OperationStateStore(paths.operation)
        with store.ownership():
            if store.load() is not None or _read(paths.backup, private=True) is not None:
                raise ValueError("Unfinished resolver operation; reconcile before recovery")
            stocks = _stock_fragments(paths, managed=True)
            contents = {name: base64.b64decode(entry["content"], validate=True)
                        for name, entry in doc["files"].items()}
            records = parse_local_records(parse_json(contents["records"]))
            contents["fragment"] = render_unbound(candidate, records).encode()
            _candidate(paths, contents["fragment"], stocks)
            operation = store.begin("unbound-restore", paths.fragment, "restoring")
            # From this point any uncertainty leaves the operation blocking.
            _stopped()
            for name, path in _mapping(paths).items():
                _write(path, contents[name], doc["files"][name]["mode"])
            manifest = dict(doc["manifest"], binding=candidate.to_dict(), enabled=False,
                            files={path: hashlib.sha256(contents[name]).hexdigest()
                                   for name, path in _mapping(paths).items()})
            write_json_atomic(paths.manifest, manifest)
            lease.save(candidate)
            run(["unbound-checkconf", paths.main], capture_output=True, timeout=30)
            _stopped()
            store.complete(operation.operation_id, keep_ownership=True)
