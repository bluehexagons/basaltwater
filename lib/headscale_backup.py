"""Private stopped-service snapshots and matching database recovery."""

from __future__ import annotations

from dataclasses import asdict, replace
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import pwd
import sqlite3
import tempfile
from collections.abc import Sequence

from lib.atomic_io import fsync_tree, read_json_file, rename_path_durable, write_bytes_atomic, write_json_atomic
from lib.headscale_server import (
    Release, ServerManifest, ServerPaths, configtest, health, install_deb,
    installed_version, load_manifest, private_directory, regular_bytes, require_capabilities,
    runtime_operation, stopped, verify_package, version_tuple,
)
from lib.remote_utils import run
from lib.setup_directories import check_directory_path
from lib.validation import validate_filesystem_path


def snapshot_files(paths: ServerPaths) -> dict[str, Path]:
    """Fixed allowlist: no archive extraction or arbitrary destination paths."""
    return {"config.yaml": paths.config, "policy.hujson": paths.etc / "policy.hujson",
            "noise.key": paths.data / "noise_private.key",
            "derp.key": paths.data / "derp_server_private.key",
            "manifest.json": paths.manifest, "db.sqlite": paths.data / "db.sqlite"}


def _sqlite_check(path: Path) -> None:
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise ValueError("Headscale SQLite integrity check failed")


def _capture(destination: Path, paths: ServerPaths, manifest: ServerManifest) -> None:
    stopped()
    validate_filesystem_path(str(destination))
    check_directory_path(str(destination.parent))
    if destination.exists() or destination.is_symlink():
        raise ValueError("Backup destination must be new")
    with tempfile.TemporaryDirectory(prefix=".headscale-backup-", dir=destination.parent) as temporary:
        staging = Path(temporary) / "snapshot"
        staging.mkdir(mode=0o700)
        for name, source in snapshot_files(paths).items():
            data = regular_bytes(source)
            if name == "db.sqlite":
                # SQLite backup consolidates committed WAL contents as well.
                with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as src:
                    with closing(sqlite3.connect(staging / name)) as dst:
                        src.backup(dst)
                os.chmod(staging / name, 0o600)
                _sqlite_check(staging / name)
            else:
                write_bytes_atomic(str(staging / name), data)
        write_json_atomic(str(staging / "backup.json"), {
            "schema": 1, "manifest": asdict(manifest),
            "sha256": {name: hashlib.sha256(regular_bytes(staging / name)).hexdigest()
                       for name in snapshot_files(paths)},
        })
        write_bytes_atomic(str(staging / "RECOVERY.txt"),
                           b"Stop Headscale. Restore only with this exact version and endpoint.\n"
                           b"Retain matching DEBs; never roll back a migrated database by binary alone.\n"
                           b"Reconcile revocations since this snapshot before reopening ingress.\n")
        fsync_tree(str(staging))
        rename_path_durable(str(staging), str(destination))


def backup_headscale(destination: Path, *, paths: ServerPaths = ServerPaths()) -> ServerManifest:
    """Require stopped service; capture config, policy, keys, manifest and SQLite."""
    require_capabilities()
    with runtime_operation(paths, "headscale-backup"):
        manifest = load_manifest(paths)
        if manifest is None:
            raise ValueError("Cannot back up unmanaged Headscale")
        if installed_version() != manifest.version:
            raise ValueError("Backup binary differs from managed version")
        _capture(destination, paths, manifest)
        validate_backup(destination, expected=manifest, paths=paths)
        return manifest


def validate_backup(source: Path, *, expected: ServerManifest,
                    paths: ServerPaths = ServerPaths()) -> dict[str, bytes]:
    """Validate all digests, binding and SQLite before any restore writes."""
    check_directory_path(str(source))
    if source.stat().st_mode & 0o077:
        raise ValueError("Backup directory must be private")
    # Metadata is part of the private snapshot too: reject linked, oversized,
    # foreign-owned or world-readable inputs before interpreting their binding.
    metadata_path = source / "backup.json"
    regular_bytes(metadata_path)
    if metadata_path.stat().st_mode & 0o077:
        raise ValueError("Backup metadata must be private")
    metadata = read_json_file(str(metadata_path), max_bytes=64 * 1024)
    names = snapshot_files(paths)
    if (not isinstance(metadata, dict) or metadata.get("schema") != 1
            or metadata.get("manifest") != asdict(expected)
            or not isinstance(metadata.get("sha256"), dict) or set(metadata["sha256"]) != set(names)):
        raise ValueError("Backup/version/endpoint mismatch")
    contents = {}
    for name in names:
        if (source / name).stat().st_mode & 0o077:
            raise ValueError("Backup file must be private")
        contents[name] = regular_bytes(source / name)
        if hashlib.sha256(contents[name]).hexdigest() != metadata["sha256"][name]:
            raise ValueError("Backup checksum mismatch")
    if json.loads(contents["manifest.json"]) != asdict(expected):
        raise ValueError("Backup manifest mismatch")
    config = json.loads(contents["config.yaml"])
    if config.get("server_url") != expected.endpoint or config.get("dns", {}).get("base_domain") != expected.base_domain:
        raise ValueError("Backup config endpoint mismatch")
    # Only the managed native layout is restorable by this bounded adapter.
    if (config.get("database", {}).get("sqlite", {}).get("path") != str(paths.data / "db.sqlite")
            or config.get("policy", {}).get("path") != str(paths.etc / "policy.hujson")
            or config.get("noise", {}).get("private_key_path") != str(paths.data / "noise_private.key")
            or config.get("derp", {}).get("server", {}).get("private_key_path") != str(paths.data / "derp_server_private.key")):
        raise ValueError("Backup native layout mismatch; explicit migration required")
    _sqlite_check(source / "db.sqlite")
    # Test against snapshot keys/policy/database, never the live state.
    with tempfile.TemporaryDirectory(prefix=".validate-", dir=source.parent) as temporary:
        staging = Path(temporary)
        for name, content in contents.items():
            write_bytes_atomic(str(staging / name), content)
        config["database"]["sqlite"]["path"] = str(staging / "db.sqlite")
        config["noise"]["private_key_path"] = str(staging / "noise.key")
        config["derp"]["server"]["private_key_path"] = str(staging / "derp.key")
        config["policy"]["path"] = str(staging / "policy.hujson")
        write_json_atomic(str(staging / "candidate.yaml"), config)
        configtest(staging / "candidate.yaml")
    return contents


def _restore(contents: dict[str, bytes], paths: ServerPaths) -> None:
    stopped()
    account = pwd.getpwnam("headscale")
    for name, destination in snapshot_files(paths).items():
        private_directory(destination.parent, uid=0 if destination.parent != paths.data else account.pw_uid,
                          gid=account.pw_gid if destination.parent != paths.private else 0,
                          mode=0o750 if destination.parent == paths.etc else 0o700)
        write_bytes_atomic(str(destination), contents[name], mode=0o640 if name in {"config.yaml", "policy.hujson"} else 0o600,
                           uid=0 if name in {"config.yaml", "policy.hujson", "manifest.json"} else account.pw_uid,
                           gid=account.pw_gid if name != "manifest.json" else 0)
    for suffix in ("-wal", "-shm"):
        (paths.data / ("db.sqlite" + suffix)).unlink(missing_ok=True)
    configtest(paths.config)


def restore_headscale(source: Path, *, expected: ServerManifest,
                      paths: ServerPaths = ServerPaths(), activate: bool = False) -> None:
    """Explicit exact snapshot binding; leave stopped unless activation requested."""
    require_capabilities()
    with runtime_operation(paths, "headscale-restore"):
        stopped()
        current = load_manifest(paths)
        if current is not None and current != expected:
            raise ValueError("Restore conflicts with managed endpoint/version")
        if installed_version() != expected.version:
            raise ValueError("Install the exact matching Headscale version before restore")
        contents = validate_backup(source, expected=expected, paths=paths)
        _restore(contents, paths)
        if activate:
            try:
                run(["systemctl", "unmask", "headscale.service"])
                run(["systemctl", "start", "headscale"])
                health(paths)
            except BaseException:
                run(["systemctl", "stop", "headscale"])
                stopped()
                raise


def validate_upgrade(current: Release, target: Release, *, reviewed_releases: Sequence[Release]) -> None:
    """Require latest reviewed patch on both sides and no skipped minor."""
    old, new = version_tuple(current.version), version_tuple(target.version)
    if current.architecture != target.architecture or new <= old or new[1] not in {old[1], old[1] + 1}:
        raise ValueError("Upgrade requires increasing patch or sequential minor on the same architecture")
    for release in (current, target):
        peers = [item for item in reviewed_releases if item.architecture == release.architecture
                 and version_tuple(item.version)[:2] == version_tuple(release.version)[:2]]
        if release not in peers or max(version_tuple(item.version) for item in peers) != version_tuple(release.version):
            # Patch upgrades naturally begin below the latest patch.
            if release == current and new[:2] == old[:2] and release in peers:
                continue
            raise ValueError("Use the latest patch from an explicitly reviewed upstream release inventory")


def upgrade_headscale(target_package: Path, recovery_package: Path, backup: Path, *,
                      current: Release, target: Release, reviewed_releases: Sequence[Release],
                      paths: ServerPaths = ServerPaths()) -> None:
    """Stopped upgrade with prebackup and matching binary AND database recovery."""
    require_capabilities()
    validate_upgrade(current, target, reviewed_releases=reviewed_releases)
    verify_package(target_package, target)
    verify_package(recovery_package, current)
    with runtime_operation(paths, "headscale-upgrade"):
        stopped()
        manifest = load_manifest(paths)
        if manifest is None or manifest.version != current.version:
            raise ValueError("Upgrade source does not match managed version")
        if installed_version() != current.version:
            raise ValueError("Upgrade source binary differs from managed version")
        _capture(backup, paths, manifest)
        contents = validate_backup(backup, expected=manifest, paths=paths)
        try:
            install_deb(target_package, target)
            configtest(paths.config)
            run(["systemctl", "unmask", "headscale.service"])
            run(["systemctl", "start", "headscale"])
            health(paths)
            write_json_atomic(str(paths.manifest), asdict(replace(manifest, version=target.version)))
        except BaseException:
            run(["systemctl", "stop", "headscale"])
            stopped()
            install_deb(recovery_package, current)
            _restore(contents, paths)
            run(["systemctl", "unmask", "headscale.service"])
            run(["systemctl", "start", "headscale"])
            health(paths)
            raise
