"""Recoverable replacement of related systemd units."""

from __future__ import annotations

import os
import shutil
import stat
import tempfile

from lib.atomic_io import remove_file_durable, write_json_atomic, write_text_atomic
from lib.operation_state import OperationStateStore
from lib.remote_utils import is_dry_run, run
from lib.validation import validate_filesystem_path, validate_service_name_uniqueness


def _command(*args: str) -> str:
    result = run(list(args), capture_output=True, timeout=120)
    return result.stdout or ""


def _snapshot(path: str) -> dict | None:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "r", encoding="utf-8") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError(f"Unsafe systemd unit: {path}")
        content = stream.read(1024 * 1024 + 1)
        if len(content.encode("utf-8")) > 1024 * 1024:
            raise ValueError(f"Systemd unit exceeds 1 MiB: {path}")
    return dict(content=content, mode=stat.S_IMODE(info.st_mode), uid=info.st_uid, gid=info.st_gid)


def _state(unit: str) -> dict[str, str]:
    output = _command("systemctl", "show", unit, "--property=LoadState,ActiveState,UnitFileState", "--no-pager")
    state = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
    if state.get("LoadState") not in {"loaded", "not-found"} or state.get("ActiveState") not in {"active", "inactive", "failed"}:
        raise RuntimeError(f"Cannot replace busy or uninspectable unit: {unit}")
    if state.get("UnitFileState") not in {"", "enabled", "enabled-runtime", "disabled", "static", "indirect"}:
        raise RuntimeError(f"Unsupported enablement state for {unit}")
    return state


def replace_units(units: dict[str, str], *, activate: tuple[str, ...], unit_dir: str = "/etc/systemd/system") -> None:
    """Validate, replace, enable and restart units, restoring on any failure.

    Only ``activate`` units have their running/enabled state changed. This lets
    timer/path replacements leave an executing oneshot service alone. Recovery
    backups and a blocking marker survive failed rollback or process death.
    """
    validate_filesystem_path(unit_dir, must_exist=False)
    if not units or not set(activate) <= units.keys():
        raise ValueError("Activation requires staged units")
    for name, content in units.items():
        base, separator, kind = name.rpartition(".")
        if not separator or kind not in {"service", "timer", "path"}:
            raise ValueError(f"Unsupported systemd unit: {name}")
        validate_service_name_uniqueness(base, [])
        if not isinstance(content, str) or len(content.encode("utf-8")) > 1024 * 1024:
            raise ValueError("Unit content must be text no larger than 1 MiB")
    if is_dry_run():
        print(f"  [DRY-RUN] Would replace units: {', '.join(units)}")
        return

    store = OperationStateStore(os.path.join(unit_dir, ".basaltwater-unit-operation.json"))
    backup_dir = ""
    retain = False
    try:
        record = store.begin("unit-replacement", unit_dir, "staging", context={"units": list(units)})
        modified = False
        touched: list[str] = []
        try:
            snapshots = {name: _snapshot(os.path.join(unit_dir, name)) for name in units}
            states = {name: _state(name) for name in activate}
            backup_dir = tempfile.mkdtemp(prefix=".basaltwater-units-", dir=unit_dir)
            write_json_atomic(os.path.join(backup_dir, "previous.json"), {"units": snapshots, "states": states})
            store.transition(record.operation_id, "validating", context={"units": list(units), "backup_dir": backup_dir})
            candidates = []
            for name, content in units.items():
                path = os.path.join(backup_dir, name)
                write_text_atomic(path, content, mode=0o644)
                candidates.append(path)
            _command("systemd-analyze", "verify", *candidates)
            store.transition(record.operation_id, "replacing", context={"units": list(units), "backup_dir": backup_dir})
            modified = True
            for name, content in units.items():
                write_text_atomic(os.path.join(unit_dir, name), content, mode=0o644)
            _command("systemctl", "daemon-reload")
            for name in activate:
                touched.append(name)
                _command("systemctl", "enable", name)
                _command("systemctl", "restart", name)
                state = _state(name)
                if state["ActiveState"] != "active" or state["UnitFileState"] != "enabled":
                    raise RuntimeError(f"Unit activation failed verification: {name}")
            store.complete(record.operation_id)
        except BaseException:
            errors = []

            def attempt(action):
                try:
                    action()
                except BaseException as exc:
                    errors.append(type(exc).__name__)

            if modified:
                for name in touched:
                    attempt(lambda name=name: _command("systemctl", "stop", name))
                    attempt(lambda name=name: _command("systemctl", "disable", name))
                for name, previous in snapshots.items():
                    path = os.path.join(unit_dir, name)
                    if previous is None:
                        attempt(lambda path=path: remove_file_durable(path))
                    else:
                        attempt(lambda path=path, previous=previous: write_text_atomic(path, **previous))
                attempt(lambda: _command("systemctl", "daemon-reload"))
                for name in touched:
                    state = states[name]
                    if state["UnitFileState"] in {"enabled", "enabled-runtime"}:
                        flags = ["--runtime"] if state["UnitFileState"] == "enabled-runtime" else []
                        attempt(lambda name=name, flags=flags: _command("systemctl", "enable", *flags, name))
                    if state["ActiveState"] == "active":
                        attempt(lambda name=name: _command("systemctl", "restart", name))
                for name in touched:
                    def verify(name=name):
                        actual = _state(name)
                        previous = states[name]
                        if (actual["ActiveState"] == "active") != (previous["ActiveState"] == "active") or actual["UnitFileState"] != previous["UnitFileState"]:
                            raise RuntimeError("Restored unit state did not match")
                    attempt(verify)
            if errors:
                retain = True
                store.transition(record.operation_id, "rollback-failed", status="recovery_required", context={"backup_dir": backup_dir, "errors": errors})
                raise RuntimeError(f"Systemd rollback needs recovery; inspect {store.path} and {backup_dir}")
            store.complete(record.operation_id)
            raise
    finally:
        store.close()
        if backup_dir and not retain:
            shutil.rmtree(backup_dir)
