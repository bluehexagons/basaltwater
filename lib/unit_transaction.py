"""Recoverable replacement and removal of related systemd units."""

from __future__ import annotations

from collections.abc import Callable
import os
import re
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
    """Reject transitions, but allow repairing a service waiting to retry failure."""
    output = _command("systemctl", "show", unit, "--property=LoadState,ActiveState,SubState,UnitFileState", "--no-pager")
    state = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
    # Restart=on-failure reports activating/auto-restart between failed starts.
    # No start is executing then; refusing this state blocks the setup that
    # would repair its configuration. Preserve the actual state in snapshots.
    retrying = (unit.endswith(".service") and state.get("ActiveState") == "activating"
                and state.get("SubState") == "auto-restart")
    if (state.get("LoadState") not in {"loaded", "not-found"}
            or (state.get("ActiveState") not in {"active", "inactive", "failed"} and not retrying)):
        raise RuntimeError(f"Cannot change busy or uninspectable unit: {unit} "
                           f"(load={state.get('LoadState', 'unknown')}, "
                           f"active={state.get('ActiveState', 'unknown')}, sub={state.get('SubState', 'unknown')})")
    if state.get("UnitFileState") not in {"", "enabled", "enabled-runtime", "disabled", "static", "indirect"}:
        raise RuntimeError(f"Unsupported enablement state for {unit}")
    return state


def _reconcile_units(units: dict[str, str], *, activate: tuple[str, ...], remove: tuple[str, ...], unit_dir: str,
                     deactivate: tuple[str, ...] = (), prepare: Callable[[], None] | None = None) -> None:
    validate_filesystem_path(unit_dir, must_exist=False)
    if (not units and not remove) or not (set(activate) | set(deactivate)) <= units.keys():
        raise ValueError("Activation requires staged units")
    names = (*units, *remove)
    if (len(set(names)) != len(names) or len(set(activate)) != len(activate)
            or len(set(deactivate)) != len(deactivate)):
        raise ValueError("Systemd unit names must be unique")
    if any(name.endswith("@.service") for name in activate):
        raise ValueError("Activate a socket or concrete instance, not a service template")
    for name in names:
        base, separator, kind = name.rpartition(".")
        kinds = {"service", "socket", "timer", "path", "mount", "automount"}
        if not separator or kind not in kinds:
            raise ValueError(f"Unsupported systemd unit: {name}")
        # Accept service templates used by socket activation, while retaining
        # the ordinary validator for their fixed, non-instance name.
        if kind in {"mount", "automount"}:
            # systemd-escape path names may start with digits, contain literal
            # dashes and use \\xHH for other path bytes. Never accept a slash,
            # control character, arbitrary backslash escape or path traversal.
            if (len(name) > 255 or base in {"", ".", ".."}
                    or not re.fullmatch(r"(?:[A-Za-z0-9:_.-]|\\x[0-9A-Fa-f]{2})+", base)):
                raise ValueError(f"Invalid systemd mount unit: {name}")
        else:
            validate_service_name_uniqueness(base.removesuffix("@") if kind == "service" else base, [])
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
            # Templates have files but no running state. The owning activator
            # stops its instances (e.g. through PartOf) before file removal.
            managed_removals = tuple(name for name in removals if not name.endswith("@.service"))
            states = {name: inspect_unit_state(name) for name in dict.fromkeys((*activate, *deactivate, *managed_removals))}
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
            for name in (*deactivate, *managed_removals):
                previous = states[name]
                # Reloading an unlinked unit can report not-found while its
                # processes remain active. Enablement is independent too.
                stop_required = previous["LoadState"] != "not-found" or previous["ActiveState"] != "inactive"
                disable_required = previous["UnitFileState"] in {"enabled", "enabled-runtime"}
                if not stop_required and not disable_required:
                    continue
                touched.append(name)
                if stop_required:
                    _command("systemctl", "stop", name)
                if disable_required:
                    flags = ["--runtime"] if previous["UnitFileState"] == "enabled-runtime" else []
                    _command("systemctl", "disable", *flags, name)
                state = inspect_unit_state(name)
                if state["ActiveState"] in {"active", "activating"} or state["UnitFileState"] in {"enabled", "enabled-runtime"}:
                    raise RuntimeError(f"Unit removal failed verification: {name}")
            if prepare is not None:
                prepare()
            for name in removals:
                remove_file_durable(os.path.join(unit_dir, name))
            for name, content in units.items():
                write_text_atomic(os.path.join(unit_dir, name), content, mode=0o644)
            _command("systemctl", "daemon-reload")
            for name in managed_removals:
                state = inspect_unit_state(name)
                if state["ActiveState"] in {"active", "activating"} or state["UnitFileState"] in {"enabled", "enabled-runtime"}:
                    raise RuntimeError(f"Removed unit is still active or enabled: {name}")
            for name in activate:
                if name not in touched:
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
                # Publication can fail before activation is reached, while an
                # existing timer/socket/path still references a changed file.
                rollback_units = list(dict.fromkeys((*touched, *activate)))
                for name in rollback_units:
                    if (name not in touched and states[name]["LoadState"] == "not-found"
                            and not os.path.lexists(os.path.join(unit_dir, name))):
                        continue
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
                    restore_order = list(reversed(rollback_units)) if removals or deactivate else list(rollback_units)
                    # An automount cannot start over an already mounted path.
                    # Keep the other unit ordering, but rearm each native
                    # automount before restoring its associated active mount.
                    for name in rollback_units:
                        if name.endswith(".automount"):
                            mount = name.removesuffix(".automount") + ".mount"
                            if mount in restore_order and restore_order.index(name) > restore_order.index(mount):
                                restore_order.remove(name)
                                restore_order.insert(restore_order.index(mount), name)
                    for name in restore_order:
                        state = states[name]
                        if state["UnitFileState"] in {"enabled", "enabled-runtime"}:
                            flags = ["--runtime"] if state["UnitFileState"] == "enabled-runtime" else []
                            attempt(lambda name=name, flags=flags: _command("systemctl", "enable", *flags, name))
                        if state["ActiveState"] == "active":
                            attempt(lambda name=name: _command("systemctl", "restart", name))
                for name in rollback_units:
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


def replace_units(units: dict[str, str], *, activate: tuple[str, ...], unit_dir: str = "/etc/systemd/system",
                  deactivate: tuple[str, ...] = (), prepare: Callable[[], None] | None = None) -> None:
    """Validate, replace, enable and restart units, restoring on any failure.

    Only ``activate`` and ``deactivate`` units change running/enabled state. This lets
    timer/path replacements leave an executing oneshot service alone. Recovery
    backups and a blocking marker survive failed rollback or process death.
    ``deactivate`` units stop and disable before publication, in the supplied
    order (activators before their mount/service), and stay stopped unless also
    in ``activate``. Rollback restores their previous state in reverse order,
    except that an automount is restored before its matching mount.

    ``prepare`` runs once after validation and grouped deactivation, before
    publication, under the same lock and rollback boundary. It never runs in
    dry-run mode. Its failures restore unit files and activation state, but its
    filesystem, credential or other side effects are not rolled back.
    """
    _reconcile_units(units, activate=activate, remove=(), unit_dir=unit_dir,
                     deactivate=deactivate, prepare=prepare)


def remove_units(names: tuple[str, ...], *, unit_dir: str = "/etc/systemd/system",
                 prepare: Callable[[], None] | None = None) -> None:
    """Remove managed units together, restoring files and states on failure.

    Pass activators before their service. Missing managed files are skipped;
    vendor units are not stopped. Removal shares replacement's recovery lock.

    ``prepare`` runs once after grouped deactivation, before unlinking files,
    under the same lock and rollback boundary. Dry runs and removals with no
    managed files skip it. Its failures restore unit files and activation
    state, but its own side effects are not rolled back.
    """
    _reconcile_units({}, activate=(), remove=names, unit_dir=unit_dir, prepare=prepare)
