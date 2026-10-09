"""Recoverable replacement and removal of related systemd units."""

from __future__ import annotations

import os
import shutil
import stat
import tempfile

from lib.atomic_io import remove_file_durable, write_json_atomic, write_text_atomic
from lib.operation_state import OperationStateStore
from lib.remote_utils import is_dry_run, run
from lib.validation import validate_filesystem_path, validate_service_name_uniqueness


class UnitRecoveryError(RuntimeError):
    """A unit operation still owns recovery evidence after failure."""

    def __init__(self, marker_path: str, backup_dir: str):
        self.marker_path = marker_path
        self.backup_dir = backup_dir
        super().__init__(f"Systemd rollback needs recovery; inspect {marker_path} and {backup_dir}")


def _command(*args: str) -> str:
    result = run(list(args), capture_output=True, timeout=120)
    return result.stdout or ""


def snapshot_unit_file(path: str) -> dict | None:
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


def inspect_unit_state(unit: str) -> dict[str, str]:
    output = _command("systemctl", "show", unit, "--property=LoadState,ActiveState,UnitFileState", "--no-pager")
    state = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
    if state.get("LoadState") not in {"loaded", "not-found"} or state.get("ActiveState") not in {"active", "inactive", "failed"}:
        raise RuntimeError(f"Cannot change busy or uninspectable unit: {unit}")
    if state.get("UnitFileState") not in {"", "enabled", "enabled-runtime", "disabled", "static", "indirect"}:
        raise RuntimeError(f"Unsupported enablement state for {unit}")
    return state


def _reconcile_units(units: dict[str, str], *, activate: tuple[str, ...], remove: tuple[str, ...], unit_dir: str) -> None:
    validate_filesystem_path(unit_dir, must_exist=False)
    if (not units and not remove) or not set(activate) <= units.keys():
        raise ValueError("Activation requires staged units")
    names = (*units, *remove)
    if len(set(names)) != len(names) or len(set(activate)) != len(activate):
        raise ValueError("Systemd unit names must be unique")
    for name in names:
        base, separator, kind = name.rpartition(".")
        kinds = {"service", "timer", "path", "mount"} if name in remove else {"service", "timer", "path"}
        if not separator or kind not in kinds:
            raise ValueError(f"Unsupported systemd unit: {name}")
        validate_service_name_uniqueness(base, [])
    for content in units.values():
        if not isinstance(content, str) or len(content.encode("utf-8")) > 1024 * 1024:
            raise ValueError("Unit content must be text no larger than 1 MiB")
    operation = "unit-removal" if remove else "unit-replacement"
    if is_dry_run():
        print(f"  [DRY-RUN] Would {'remove' if remove else 'replace'} units: {', '.join(names)}")
        return

    store = OperationStateStore(os.path.join(unit_dir, ".basaltwater-unit-operation.json"))
    backup_dir = ""
    resolved = False
    record = None
    try:
        record = store.begin(operation, unit_dir, "staging", context={"units": list(names)})
        modified = False
        touched: list[str] = []
        try:
            snapshots = {name: snapshot_unit_file(os.path.join(unit_dir, name)) for name in names}
            removals = tuple(name for name in remove if snapshots[name] is not None)
            states = {name: inspect_unit_state(name) for name in (*activate, *removals)}
            if not units and not removals:
                store.complete(record.operation_id)
                resolved = True
                return
            backup_dir = tempfile.mkdtemp(prefix=".basaltwater-units-", dir=unit_dir)
            write_json_atomic(os.path.join(backup_dir, "previous.json"), {"units": snapshots, "states": states})
            context = {"units": list(names), "backup_dir": backup_dir}
            store.transition(record.operation_id, "validating", context=context)
            candidates = []
            for name, content in units.items():
                path = os.path.join(backup_dir, name)
                write_text_atomic(path, content, mode=0o644)
                candidates.append(path)
            if candidates:
                _command("systemd-analyze", "verify", *candidates)
            store.transition(record.operation_id, "removing" if remove else "replacing", context=context)
            modified = True
            # Stop activators before their service, but retain every file until
            # the entire group is quiescent. A timed-out stop may have acted.
            for name in removals:
                touched.append(name)
                _command("systemctl", "stop", name)
                if states[name]["UnitFileState"] in {"enabled", "enabled-runtime"}:
                    flags = ["--runtime"] if states[name]["UnitFileState"] == "enabled-runtime" else []
                    _command("systemctl", "disable", *flags, name)
                state = inspect_unit_state(name)
                if state["ActiveState"] == "active" or state["UnitFileState"] in {"enabled", "enabled-runtime"}:
                    raise RuntimeError(f"Unit removal failed verification: {name}")
            for name in removals:
                remove_file_durable(os.path.join(unit_dir, name))
            for name, content in units.items():
                write_text_atomic(os.path.join(unit_dir, name), content, mode=0o644)
            _command("systemctl", "daemon-reload")
            for name in removals:
                state = inspect_unit_state(name)
                if state["ActiveState"] == "active" or state["UnitFileState"] in {"enabled", "enabled-runtime"}:
                    raise RuntimeError(f"Removed unit is still active or enabled: {name}")
            for name in activate:
                touched.append(name)
                _command("systemctl", "enable", name)
                _command("systemctl", "restart", name)
                state = inspect_unit_state(name)
                if state["ActiveState"] != "active" or state["UnitFileState"] != "enabled":
                    raise RuntimeError(f"Unit activation failed verification: {name}")
            store.complete(record.operation_id)
            resolved = True
        except BaseException:
            errors = []
            try:
                store.transition(record.operation_id, "rolling-back")
            except Exception as exc:
                errors.append(type(exc).__name__)

            def attempt(action):
                try:
                    action()
                    return True
                except BaseException as exc:
                    errors.append(type(exc).__name__)
                    return False

            if modified:
                for name in touched:
                    attempt(lambda name=name: _command("systemctl", "stop", name))
                    if name in activate or states[name]["UnitFileState"] in {"enabled", "enabled-runtime"}:
                        flags = ["--runtime"] if name not in activate and states[name]["UnitFileState"] == "enabled-runtime" else []
                        attempt(lambda name=name, flags=flags: _command("systemctl", "disable", *flags, name))
                restored = set()
                for name, previous in snapshots.items():
                    path = os.path.join(unit_dir, name)
                    if previous is None:
                        safe = attempt(lambda path=path: remove_file_durable(path))
                    else:
                        safe = attempt(lambda path=path, previous=previous: write_text_atomic(path, **previous))
                    if safe:
                        restored.add(name)
                reloaded = attempt(lambda: _command("systemctl", "daemon-reload"))
                # Related activators can start another unit in this group.
                # Keep the group quiescent if any old definition is unsafe.
                if reloaded and len(restored) == len(snapshots):
                    # Restore the service before rearming its timer/path.
                    for name in reversed(touched) if removals else touched:
                        state = states[name]
                        if state["UnitFileState"] in {"enabled", "enabled-runtime"}:
                            flags = ["--runtime"] if state["UnitFileState"] == "enabled-runtime" else []
                            attempt(lambda name=name, flags=flags: _command("systemctl", "enable", *flags, name))
                        if state["ActiveState"] == "active":
                            attempt(lambda name=name: _command("systemctl", "restart", name))
                for name in touched:
                    def verify(name=name):
                        actual = inspect_unit_state(name)
                        previous = states[name]
                        if (actual["ActiveState"] == "active") != (previous["ActiveState"] == "active") or actual["UnitFileState"] != previous["UnitFileState"]:
                            raise RuntimeError("Restored unit state did not match")
                    attempt(verify)
            if errors:
                store.transition(record.operation_id, "rollback-failed", status="recovery_required", context={"backup_dir": backup_dir, "errors": errors})
                raise UnitRecoveryError(store.path, backup_dir)
            store.complete(record.operation_id, outcome="rolled_back" if modified else "failed")
            resolved = True
            raise
    except Exception as error:
        if record is not None and not resolved and not isinstance(error, UnitRecoveryError):
            raise UnitRecoveryError(store.path, backup_dir) from error
        raise
    finally:
        store.close()
        if backup_dir and resolved:
            try:
                shutil.rmtree(backup_dir)
            except OSError as exc:
                print(f"  ⚠ Systemd backup cleanup failed at {backup_dir}: {exc}")


def replace_units(units: dict[str, str], *, activate: tuple[str, ...], unit_dir: str = "/etc/systemd/system") -> None:
    """Validate, replace, enable and restart units, restoring on any failure.

    Only ``activate`` units have their running/enabled state changed. This lets
    timer/path replacements leave an executing oneshot service alone. Recovery
    backups and a blocking marker survive failed rollback or process death.
    """
    _reconcile_units(units, activate=activate, remove=(), unit_dir=unit_dir)


def remove_units(names: tuple[str, ...], *, unit_dir: str = "/etc/systemd/system") -> None:
    """Remove managed units together, restoring files and states on failure.

    Pass activators before their service. Missing managed files are skipped;
    vendor units are not stopped. Removal shares replacement's recovery lock.
    """
    _reconcile_units({}, activate=(), remove=names, unit_dir=unit_dir)
