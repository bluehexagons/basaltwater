"""Root-owned pause controls, independent of cadence and private findings."""

from __future__ import annotations

import os

from lib.atomic_io import read_json_file
from lib.machine_state import STATE_DIR
from lib.privilege_policy import protected_path
from lib.samba_accounts import HEX_REVISION
from lib.storage_routines import MAX_ROUTINES, SPEC_FIELDS, routine_identity

CONTROLS_FILE = "/var/lib/storage-ops/controls.json"


def load_paused() -> set[str]:
    try:
        os.lstat(CONTROLS_FILE)
    except FileNotFoundError:
        return set()
    protected_path(CONTROLS_FILE)
    value = read_json_file(CONTROLS_FILE, max_bytes=65536)
    if (not isinstance(value, dict) or set(value) != {"version", "paused"}
            or type(value["version"]) is not int or value["version"] != 1
            or not isinstance(value["paused"], list) or len(value["paused"]) > MAX_ROUTINES
            or any(not isinstance(identifier, str) or not HEX_REVISION.fullmatch(identifier) for identifier in value["paused"])
            or len(set(value["paused"])) != len(value["paused"])):
        raise ValueError("Storage pause state is invalid; retain it and inspect before resuming work")
    return set(value["paused"])


def require_no_setup_recovery() -> None:
    # A routine save spans setup and pause state. Interrupted publication must
    # stop scheduled work, including after the operation lock is released.
    try:
        os.lstat(STATE_DIR + "/setup-operation.json")
    except FileNotFoundError:
        return
    raise ValueError("Setup or routine recovery must finish before storage work")


def scheduled_specs(config, paused: set[str]) -> tuple[list, list]:
    def active(kind: str, spec: list) -> bool:
        # Preserve the orchestrator's existing invalid-spec reporting. Never
        # turn a malformed declaration into a silently skipped healthy job.
        if not isinstance(spec, (list, tuple)) or len(spec) < 2 or any(not isinstance(path, str) for path in spec[:2]):
            return True
        return routine_identity(kind, spec) not in paused

    mirrors, scrubs = [], []
    for kind, field in SPEC_FIELDS.items():
        selected = [spec for spec in getattr(config, field) or [] if active(kind, spec)]
        (scrubs if kind == "scrub" else mirrors).extend(selected)
    return mirrors, scrubs
