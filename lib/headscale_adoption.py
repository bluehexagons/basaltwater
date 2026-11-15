"""Explicit reviewed ownership of an already compatible, stopped native server.

No discovery grants authority. The root caller must independently review the
private inspection document and explicitly approve its exact network binding.
"""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import sqlite3
import stat
import tempfile

from lib.atomic_io import fsync_tree, parse_json, rename_path_durable, write_bytes_atomic, write_json_atomic
from lib.config import SetupConfig
from lib.headscale_backup import snapshot_files, validate_backup
from lib.headscale_private import read_private
from lib.headscale_server import (
    SHA256, VERSION, Release, ServerManifest, ServerPaths, configtest, installed_version,
    manifest_for, preflight, private_directory, regular_bytes, render_config, render_nginx,
    require_capabilities, stopped, verify_package,
)
from lib.operation_state import OperationStateStore
from lib.remote_utils import is_dry_run, run
from lib.setup_directories import check_directory_path
from lib.validation import validate_filesystem_path


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _identity(path: Path, *, uid: int, gid: int, mode: int) -> dict:
    content = regular_bytes(path)
    info = path.lstat()
    if (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (uid, gid, mode):
        raise ValueError("Adoption requires existing exact native ownership and permissions")
    return dict(sha256=_digest(content), device=info.st_dev, inode=info.st_ino,
                uid=info.st_uid, gid=info.st_gid, mode=stat.S_IMODE(info.st_mode), size=len(content))


def _ancestors(path: Path, service_uid: int) -> None:
    check_directory_path(str(path))
    for parent in (path, *path.parents):
        info = parent.stat()
        if info.st_uid not in ({0, service_uid} if parent == path else {0}) or info.st_mode & 0o022:
            raise ValueError("Unsafe adoption directory ownership or permissions")


def _unit_state(unit: Path) -> dict:
    state = run(["systemctl", "show", "headscale.service", "--property=FragmentPath,DropInPaths,"
                 "NeedDaemonReload,LoadState,Environment,EnvironmentFiles,UnitFileState,"
                 "ActiveState,SubState,MainPID,ControlPID"], capture_output=True).stdout
    fields = dict(line.split("=", 1) for line in state.splitlines() if "=" in line)
    if (fields.get("FragmentPath") not in {str(unit), "/lib/systemd/system/headscale.service"}
            or fields.get("DropInPaths") != "" or fields.get("NeedDaemonReload") != "no"
            or fields.get("LoadState") != "loaded" or fields.get("Environment") != ""
            or fields.get("EnvironmentFiles") != "" or fields.get("UnitFileState") != "disabled"
            or fields.get("ActiveState") != "inactive" or fields.get("SubState") != "dead"
            or fields.get("MainPID") != "0" or fields.get("ControlPID") != "0"):
        raise ValueError("Adoption requires the disabled, stopped, unmodified package unit without environment overrides")
    return fields


def _package_identity(package: Path) -> dict:
    """Compare executable and unit bytes against the pinned, verified DEB."""
    architecture = run(["dpkg", "--print-architecture"], capture_output=True).stdout.strip()
    if architecture not in SHA256:
        raise ValueError("Unsupported native architecture")
    release = Release(VERSION, architecture, SHA256[architecture])
    # Freeze the exact verified input before dpkg-deb reads it again.
    content = regular_bytes(package)
    if _digest(content) != release.sha256:
        raise ValueError("Adoption requires the exact pinned package")
    with tempfile.TemporaryDirectory(prefix="headscale-adoption-package-") as temporary:
        root = Path(temporary)
        frozen = root / "headscale.deb"
        write_bytes_atomic(str(frozen), content)
        verify_package(frozen, release)
        run(["dpkg-deb", "--extract", str(frozen), str(root / "payload")], capture_output=True)
        binary = Path("/usr/bin/headscale")
        unit = Path("/usr/lib/systemd/system/headscale.service")
        _ancestors(binary.parent, 0)
        _ancestors(unit.parent, 0)
        extracted_unit = root / "payload/usr/lib/systemd/system/headscale.service"
        if not extracted_unit.exists():
            extracted_unit = root / "payload/lib/systemd/system/headscale.service"
        for actual, expected in ((binary, root / "payload/usr/bin/headscale"), (unit, extracted_unit)):
            if regular_bytes(actual) != regular_bytes(expected):
                raise ValueError("Native binary/unit differs from pinned package")
        identities = {"binary": _identity(binary, uid=0, gid=0, mode=0o755),
                      "unit": _identity(unit, uid=0, gid=0, mode=0o644)}
    if shutil.which("headscale") != str(binary) or installed_version() != VERSION:
        raise ValueError("Installed binary/PATH differs from pinned native binary")
    package_state = run(["dpkg-query", "-W", "-f=${Status}|${Version}|${Architecture}", "headscale"],
                        capture_output=True).stdout.strip()
    if package_state != f"hold ok installed|{VERSION}|{architecture}":
        raise ValueError("Installed package identity mismatch")
    if "headscale" not in run(["apt-mark", "showhold"], capture_output=True).stdout.splitlines():
        raise ValueError("Hold the pinned package explicitly before adoption")
    return dict(architecture=architecture, sha256=release.sha256, installed=package_state,
                systemd=_unit_state(unit), **identities)


def _inspect(config: SetupConfig, package: Path, paths: ServerPaths) -> dict:
    stopped()
    if os.path.lexists(paths.manifest):
        raise ValueError("Already managed; adoption is unavailable")
    account = pwd.getpwnam("headscale")
    if account.pw_uid <= 0 or account.pw_gid <= 0:
        raise ValueError("Adoption requires an isolated non-root native service account")
    for directory in (paths.etc, paths.data, paths.nginx.parent):
        _ancestors(directory, account.pw_uid)
    for directory, owner, group, mode in ((paths.etc, 0, account.pw_gid, 0o750),
                                         (paths.data, account.pw_uid, account.pw_gid, 0o700)):
        info = directory.stat()
        if (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (owner, group, mode):
            raise ValueError("Adoption requires preexisting managed directory permissions")
    native = parse_json(regular_bytes(paths.config))
    if json.dumps(native, sort_keys=True) != json.dumps(parse_json(render_config(config, paths)), sort_keys=True):
        raise ValueError("Unsupported native config/DNS/layout; use manual migration")
    if regular_bytes(paths.nginx) != render_nginx(str(config.headscale_domain)).encode():
        raise ValueError("Ingress must already match the managed direct TLS contract")
    for suffix in ("-wal", "-shm", "-journal"):
        if os.path.lexists(paths.data / ("db.sqlite" + suffix)):
            raise ValueError("SQLite sidecars present; obtain a clean stopped native backup manually")
    identities = {}
    for name, path in snapshot_files(paths).items():
        if name == "manifest.json":
            continue
        configuration = name in {"config.yaml", "policy.hujson"}
        identities[name] = _identity(path, uid=0 if configuration else account.pw_uid,
                                     gid=account.pw_gid, mode=0o640 if configuration else 0o600)
    identities["nginx.conf"] = _identity(paths.nginx, uid=0, gid=0, mode=0o644)
    connection = sqlite3.connect((paths.data / "db.sqlite").as_uri() + "?mode=ro&immutable=1", uri=True)
    try:
        if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise ValueError("Headscale SQLite integrity check failed")
    finally:
        connection.close()
    package_identity = _package_identity(package)
    run(["nginx", "-t"], capture_output=True)
    firewall = run(["ufw", "status"], capture_output=True).stdout
    if "Status: active" not in firewall:
        raise ValueError("Active managed firewall required")
    for port in ("80/tcp", "443/tcp", "3478/udp"):
        if not re.search(r"^" + re.escape(port) + r"\s+ALLOW\s+Anywhere\s*$", firewall, re.MULTILINE):
            raise ValueError("Explicit native ingress firewall allowances required")
    stopped()
    return dict(schema=1, authority=False, binding=asdict(manifest_for(config)),
                files=identities, package=package_identity, firewall=firewall)


def inspect_adoption(config: SetupConfig, package: Path) -> dict:
    """Return private review material; never infer authority from the endpoint.

    Caller writes this document privately, independently reviews all identities
    and external routing, then changes only ``authority`` to JSON true.
    """
    require_capabilities(firewall=True)
    return _inspect(config, package, ServerPaths())


def _backup(destination: Path, paths: ServerPaths, review: dict) -> None:
    validate_filesystem_path(str(destination))
    if not destination.is_absolute() or ".." in destination.parts:
        raise ValueError("Backup destination must be absolute")
    _ancestors(destination.parent, 0)
    if destination.parent.stat().st_mode & 0o077 or os.path.lexists(destination):
        raise ValueError("Adoption backup requires a new destination in a root-private directory")
    with tempfile.TemporaryDirectory(prefix=".adoption-", dir=destination.parent) as temporary:
        staging = Path(temporary) / "snapshot"
        staging.mkdir(mode=0o700)
        for name, source in snapshot_files(paths).items():
            content = (json.dumps(review["binding"]).encode() if name == "manifest.json"
                       else regular_bytes(source))
            write_bytes_atomic(str(staging / name), content)
        write_bytes_atomic(str(staging / "nginx.conf"), regular_bytes(paths.nginx))
        write_json_atomic(str(staging / "adoption-review.json"), review)
        write_json_atomic(str(staging / "backup.json"), dict(schema=1, manifest=review["binding"],
            sha256={name: _digest(regular_bytes(staging / name)) for name in snapshot_files(paths)}))
        validate_backup(staging, expected=ServerManifest(**review["binding"]), paths=paths)
        # With no sidecars and stopped service, the copied SQLite bytes are exact.
        for name, identity in review["files"].items():
            if _digest(regular_bytes(staging / name)) != identity["sha256"]:
                raise ValueError("Backup differs from reviewed source")
        fsync_tree(str(staging))
        rename_path_durable(str(staging), str(destination))


def adopt_headscale(config: SetupConfig, package: Path, review_file: Path,
                    backup: Path, *, dry_run: bool = False) -> ServerManifest:
    """CAS reviewed native identities, back up, publish metadata; never activate.

    No alternate source layout is exposed. Root CLI integration must pass the
    explicit intended SetupConfig and private reviewed file, never discovered
    endpoint values. Dry run validates review authority/binding without writes.
    """
    require_capabilities(firewall=True)
    paths = ServerPaths()
    _ancestors(review_file.parent, 0)
    reviewed = read_private(str(review_file))
    if reviewed is None or reviewed.get("authority") is not True:
        raise ValueError("Explicit root-reviewed adoption authority required")
    if (set(reviewed) != {"schema", "authority", "binding", "files", "package", "firewall"}
            or type(reviewed["schema"]) is not int or reviewed["schema"] != 1):
        raise ValueError("Invalid adoption review schema")
    manifest = manifest_for(config)
    if reviewed.get("binding") != asdict(manifest):
        raise ValueError("Review differs from explicitly requested network binding")
    if dry_run or config.dry_run or is_dry_run():
        return manifest
    expected = dict(reviewed, authority=False)
    if _inspect(config, package, paths) != expected:
        raise ValueError("Adoption review no longer matches inspected identity")
    ancestor = paths.private
    while not ancestor.exists():
        ancestor = ancestor.parent
    _ancestors(ancestor, 0)
    private_directory(paths.private)
    nginx = OperationStateStore(str(paths.nginx.parent.parent / ".basaltwater-nginx-operation.json"))
    store = OperationStateStore(str(paths.private / "operation.json"))
    with nginx.ownership(), store.ownership():
        if nginx.load() is not None:
            raise ValueError("Unfinished Nginx operation requires recovery")
        record = store.begin("headscale-adopt", "headscale", "reviewed", context={
            "backup": str(backup), "review_sha256": _digest(regular_bytes(review_file))})
        _backup(backup, paths, reviewed)
        configtest(paths.config, expected_database_schema=paths.data / "db.sqlite")
        if _inspect(config, package, paths) != expected:
            raise ValueError("Adoption CAS failed; preserve backup and operation marker")
        # Check effective ingress/conflicts through the ordinary contract using
        # an isolated manifest path, without granting production ownership yet.
        with tempfile.TemporaryDirectory(prefix="headscale-adoption-preflight-") as temporary:
            candidate = ServerPaths(paths.etc, paths.data, Path(temporary), paths.nginx, paths.socket)
            write_json_atomic(str(candidate.manifest), asdict(manifest))
            preflight(config, candidate)
        stopped()
        if _inspect(config, package, paths) != expected or read_private(str(review_file)) != reviewed:
            raise ValueError("Adoption final CAS failed")
        write_json_atomic(str(paths.private / "adoption.json"), reviewed)
        write_json_atomic(str(paths.manifest), asdict(manifest))
        store.complete(record.operation_id)
    return manifest
