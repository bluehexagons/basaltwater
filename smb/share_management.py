"""Serialize share-only changes and retain target state and recovery evidence."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
import secrets

from lib.atomic_io import write_json_atomic
from lib.config import SetupConfig
from lib.machine_state import SETUP_CONFIG_FILE, STATE_DIR, load_setup_config
from lib.operation_state import OperationStateStore
from lib.privilege_policy import protected_path
from lib.samba_shares import validate_share, validate_share_action
from lib.shares_data_inventory import configuration_inventory
from lib.validation import validate_gogs_settings, validate_samba_settings, validate_samba_share_specs
from smb.account_management import _accounts
from smb.samba_steps import parse_share_credentials, parse_share_spec, preflight_samba_shares, reconcile_samba_shares


def _identity(path: str) -> tuple[int, ...]:
    metadata = os.stat(path, follow_symlinks=False)
    return metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns


@contextmanager
def _owner():
    protected_path(STATE_DIR, directory=True)
    marker = STATE_DIR + "/setup-operation.json"
    for path in (marker, marker + ".lock"):
        if os.path.lexists(path):
            protected_path(path)
    with OperationStateStore(marker).ownership() as store:
        if store.load() is not None:
            raise ValueError("An unfinished setup or share change requires recovery before updating shares")
        protected_path(SETUP_CONFIG_FILE)
        identity = _identity(SETUP_CONFIG_FILE)
        saved = load_setup_config()
        if saved is None or identity != _identity(SETUP_CONFIG_FILE):
            raise ValueError("Share updates require a saved target setup")
        yield store, saved, identity


def _apply(store, saved: dict, identity: tuple[int, ...], desired: list, credentials: list | None,
           *, preserve_accounts: bool) -> None:
    validate_samba_share_specs(desired, credentials, require_passwords=not preserve_accounts)
    passwords = parse_share_credentials(credentials)
    public = []
    for declaration in desired:
        parsed = parse_share_spec(declaration, passwords, require_passwords=not preserve_accounts)
        public.append([parsed["access_type"], parsed["share_name"], parsed["paths"][0],
                       ",".join(user["username"] for user in parsed["users"])])
    updated = {**saved, "enable_samba": True, "samba_shares": public or None}
    updated.pop("share_credentials", None)
    if len((json.dumps(updated, indent=2) + "\n").encode()) > 1024 * 1024:
        raise ValueError("Updated setup exceeds its 1 MiB read limit")
    data = {**saved, "enable_samba": True, "samba_shares": desired or None}
    system_type = data.pop("system_type", None)
    data.pop("host", None)
    if not isinstance(system_type, str):
        raise ValueError("Saved setup has no system type")
    target = SetupConfig.from_dict("localhost", system_type, data)
    target.share_credentials = credentials
    validate_samba_settings(target)
    validate_gogs_settings(target)
    preflight_samba_shares(target, preserve_accounts=preserve_accounts)
    record = store.begin("samba_shares", "localhost", "reconciling", context={"samba_shares": public})
    try:
        reconcile_samba_shares(target, preserve_accounts=preserve_accounts)
        store.transition(record.operation_id, "saving")
        protected_path(SETUP_CONFIG_FILE)
        if _identity(SETUP_CONFIG_FILE) != identity:
            raise ValueError("Saved target setup changed during share reconciliation")
        write_json_atomic(SETUP_CONFIG_FILE, updated, mode=0o600, uid=0, gid=0)
        store.complete(record.operation_id)
    except BaseException as exc:
        try:
            current = store.load()
            if current is not None:
                store.transition(record.operation_id, current.phase, status="recovery_required",
                                 context={**record.context, "error_type": type(exc).__name__})
        except (OSError, ValueError):
            pass
        raise


def apply_share_update(config: SetupConfig, *, preserve_accounts: bool = False) -> None:
    """Apply only share settings, preserving fresh target guards and evidence."""
    with _owner() as (store, saved, identity):
        _apply(store, saved, identity, config.samba_shares or [], config.share_credentials,
               preserve_accounts=preserve_accounts)


def _rows(saved: dict) -> list[dict]:
    inventory = configuration_inventory(saved)
    if (not inventory.get("available") or not inventory["samba_enabled"] or inventory["omitted_shares"]
            or any(row["omitted_users"] for row in inventory["shares"])):
        raise ValueError("A complete enabled Samba share inventory is required")
    rows = [validate_share({key: row[key] for key in ("name", "access", "path", "users")})
            for row in inventory["shares"]]
    if len({row["name"].casefold() for row in rows}) != len(rows):
        raise ValueError("Case-insensitive Samba share names must be distinct")
    return rows


def _revision() -> str:
    paths = [SETUP_CONFIG_FILE, "/etc/samba/smb.conf"]
    dropin = "/etc/systemd/system/smbd.service.d/basaltwater-storage.conf"
    metadata = []
    for path in [*paths, dropin]:
        if path == dropin and not os.path.lexists(path):
            parent = os.path.dirname(path)
            protected_path(parent if os.path.lexists(parent) else os.path.dirname(parent), directory=True)
            metadata.append(None)
        else:
            protected_path(path)
            metadata.append(_identity(path))
    return hashlib.sha256(json.dumps(metadata).encode()).hexdigest()


def collect_share_snapshot(saved: dict) -> dict:
    """Bind an exported form to local config metadata without probing NAS paths."""
    try:
        with _owner() as (_store, fresh, _identity_value):
            if fresh != saved:
                raise ValueError("Saved setup changed during export")
            _rows(saved)
            return {"available": True, "revision": _revision()}
    except (OSError, ValueError, RuntimeError):
        return {"available": False}


def apply_reviewed_share_update(value: dict) -> None:
    value = validate_share_action(value)
    with _owner() as (store, saved, identity):
        if not secrets.compare_digest(value["revision"], _revision()):
            raise ValueError("The reviewed Samba configuration changed")
        rows = _rows(saved)
        name = (value["after"] or value["before"])["name"]
        previous = next((row for row in rows if row["name"].casefold() == name.casefold()), None)
        if previous != value["before"]:
            raise ValueError("The reviewed share changed")
        desired = [value["after"] if row == previous else row for row in rows]
        if previous is None:
            desired.append(value["after"])
        desired = [row for row in desired if row is not None]
        if len(desired) > 64:
            raise ValueError("Browser management supports up to 64 shares")
        users = {user for row in desired for user in row["users"]}
        eligible = {row["username"] for row in _accounts() if row["manageable"]}
        if not users <= eligible:
            raise ValueError("Share access requires matching regular local Samba accounts")
        specs = [[row["access"], row["name"], row["path"], ",".join(row["users"])] for row in desired]
        _apply(store, saved, identity, specs, None, preserve_accounts=True)
