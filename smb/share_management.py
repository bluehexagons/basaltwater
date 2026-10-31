"""Serialize share-only changes and retain target state and recovery evidence."""

from __future__ import annotations

import json
import os

from lib.atomic_io import write_json_atomic
from lib.config import SetupConfig
from lib.machine_state import SETUP_CONFIG_FILE, STATE_DIR, load_setup_config
from lib.operation_state import OperationStateStore
from lib.privilege_policy import protected_path
from lib.validation import validate_gogs_settings, validate_samba_settings, validate_samba_share_specs
from smb.samba_steps import parse_share_credentials, parse_share_spec, preflight_samba_shares, reconcile_samba_shares


def _identity(path: str) -> tuple[int, ...]:
    metadata = os.stat(path, follow_symlinks=False)
    return metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns


def apply_share_update(config: SetupConfig, *, preserve_accounts: bool = False) -> None:
    """Apply a complete desired share list under the ordinary setup exclusion.

    Only share declarations are persisted. Mount guards, cache and internal-data
    protections come from the fresh target configuration, never a stale/minimal
    controller request. An interrupted or failed operation retains its guard;
    it is not silently retried and filesystem/account effects are not rolled back.
    """
    protected_path(STATE_DIR, directory=True)
    marker = STATE_DIR + "/setup-operation.json"
    for path in (marker, marker + ".lock"):
        if os.path.lexists(path):
            protected_path(path)
    with OperationStateStore(marker).ownership() as store:
        if store.load() is not None:
            raise ValueError("An unfinished setup or share change requires recovery before updating shares")
        protected_path(SETUP_CONFIG_FILE)
        saved = load_setup_config()
        if saved is None:
            raise ValueError("Share updates require a saved target setup")
        identity = _identity(SETUP_CONFIG_FILE)
        desired = config.samba_shares or []
        validate_samba_share_specs(desired, config.share_credentials, require_passwords=not preserve_accounts)
        credentials = parse_share_credentials(config.share_credentials)
        public = []
        for declaration in desired:
            parsed = parse_share_spec(declaration, credentials, require_passwords=not preserve_accounts)
            public.append([parsed["access_type"], parsed["share_name"], parsed["paths"][0],
                           ",".join(user["username"] for user in parsed["users"])])
        updated = {**saved, "enable_samba": True, "samba_shares": public or None}
        updated.pop("share_credentials", None)
        # A future reader must be able to load this file after publication.
        if len((json.dumps(updated, indent=2) + "\n").encode()) > 1024 * 1024:
            raise ValueError("Updated setup exceeds its 1 MiB read limit")
        data = {**saved, "enable_samba": True, "samba_shares": desired or None}
        system_type = data.pop("system_type", None)
        data.pop("host", None)
        if not isinstance(system_type, str):
            raise ValueError("Saved setup has no system type")
        target = SetupConfig.from_dict("localhost", system_type, data)
        target.share_credentials = config.share_credentials
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
            # Annotation failure must not hide the original error or clear the
            # existing durable guard. Context never contains resolved passwords.
            try:
                current = store.load()
                if current is not None:
                    store.transition(record.operation_id, current.phase, status="recovery_required",
                                     context={**record.context, "error_type": type(exc).__name__})
            except (OSError, ValueError):
                pass
            raise
