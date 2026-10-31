"""Recoverable replacement and removal of related systemd units."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
import os
import re
import shutil
import stat
import tempfile

from lib.atomic_io import remove_file_durable, write_json_atomic, write_text_atomic
from lib.operation_state import OperationStateError, OperationStateStore, RECOVERY_GUIDANCE
from lib.remote_utils import is_dry_run, run
from lib.validation import validate_filesystem_path, validate_service_name_uniqueness


class UnitRecoveryError(RuntimeError):
    """A unit operation still owns recovery evidence after failure."""

    def __init__(self, marker_path: str, backup_dir: str):
        self.marker_path = marker_path
        self.backup_dir = backup_dir
        super().__init__(f"Systemd rollback needs recovery; inspect {marker_path} and {backup_dir}")


class UnitOwnership:
    """A scoped unit lock that explicit nested removals may borrow."""

    def __init__(self, store: OperationStateStore):
        self._store = store
        self._active = False

    @property
    def unit_dir(self) -> str:
        return os.path.dirname(self._store.path)

    def _borrow(self, unit_dir: str) -> OperationStateStore:
        if not self._active or os.path.abspath(unit_dir) != self.unit_dir:
            raise ValueError("Unit ownership is inactive or belongs to another directory")
        return self._store


@contextmanager
def unit_ownership(unit_dir: str = "/etc/systemd/system") -> Iterator[UnitOwnership]:
    """Exclude unit operations and refuse unfinished recovery before teardown.

    Hold this scope across manual mutations. Pass its owner explicitly to nested
    ``remove_units`` calls; their completion retains the outer lock. This scope
    adds exclusion, not rollback or a journal for the caller's other effects.
    """
    validate_filesystem_path(unit_dir, must_exist=False)
    unit_dir = os.path.abspath(unit_dir)
    store = OperationStateStore(os.path.join(unit_dir, ".basaltwater-unit-operation.json"))
    with store.ownership():
        record = store.load()
        if record is not None:
            raise OperationStateError(
                f"Unfinished {record.operation_type} operation {record.operation_id} "
                f"is recorded in {store.path} at phase {record.phase}; {RECOVERY_GUIDANCE}"
            )
        owner = UnitOwnership(store)
        owner._active = True
        try:
            yield owner
        finally:
            owner._active = False


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


def inspect_unit_state(unit: str, *, allow_oneshot: bool = False) -> dict[str, str]:
    """Reject transitions, but allow repairing a service waiting to retry failure."""
    properties = "LoadState,ActiveState,SubState,UnitFileState" + (",Type" if allow_oneshot else "")
    output = _command("systemctl", "show", unit, "--property=" + properties, "--no-pager")
    state = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
    if allow_oneshot and state.get("LoadState") == "loaded" and state.get("Type") != "oneshot":
        raise RuntimeError(f"Expected a oneshot service: {unit}")
    # Restart=on-failure reports activating/auto-restart between failed starts.
    # No start is executing then; refusing this state blocks the setup that
    # would repair its configuration. Preserve the actual state in snapshots.
    retrying = (unit.endswith(".service") and state.get("ActiveState") == "activating"
                and state.get("SubState") == "auto-restart")
    running_job = allow_oneshot and state.get("Type") == "oneshot" and state.get("ActiveState") == "activating"
    if (state.get("LoadState") not in {"loaded", "not-found"}
            or (state.get("ActiveState") not in {"active", "inactive", "failed"} and not retrying and not running_job)):
        raise RuntimeError(f"Cannot change busy or uninspectable unit: {unit} "
                           f"(load={state.get('LoadState', 'unknown')}, "
                           f"active={state.get('ActiveState', 'unknown')}, sub={state.get('SubState', 'unknown')})")
    if state.get("UnitFileState") not in {"", "enabled", "enabled-runtime", "disabled", "static", "indirect"}:
        raise RuntimeError(f"Unsupported enablement state for {unit}")
    return state


def _activators_unchanged(units: dict[str, str], activate: tuple[str, ...], unit_dir: str) -> bool:
    """Recognize a loaded, healthy timer/path group without changing its schedule."""
    # Abstract templates have no inspectable running instance. Keep their
    # installation on the ordinary transaction path, which never queries them.
    if any(name.endswith("@.service") for name in units):
        return False
    for name, content in units.items():
        path = os.path.abspath(os.path.join(unit_dir, name))
        if snapshot_unit_file(path) != dict(content=content, mode=0o644, uid=os.geteuid(), gid=os.getegid()):
            return False
        output = _command(
            "systemctl", "show", name,
            "--property=LoadState,ActiveState,UnitFileState,FragmentPath,NeedDaemonReload", "--no-pager",
        )
        state = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
        # Disk equality alone cannot establish what systemd has loaded. Include
        # the companion service and drop-in freshness, even while it executes.
        if (state.get("LoadState") != "loaded" or state.get("FragmentPath") != path
                or state.get("NeedDaemonReload") != "no"):
            return False
        if name in activate and (state.get("ActiveState") != "active" or state.get("UnitFileState") != "enabled"):
            return False
    return True


def _reconcile_units(units: dict[str, str], *, activate: tuple[str, ...], remove: tuple[str, ...], unit_dir: str,
                     deactivate: tuple[str, ...] = (), prepare: Callable[[], None] | None = None,
                     preserve_unchanged: bool = False, verify: Callable[[], None] | None = None,
                     start_once: tuple[str, ...] = (), owner: UnitOwnership | None = None) -> None:
    validate_filesystem_path(unit_dir, must_exist=False)
    if (not units and not remove) or not (set(activate) | set(deactivate) | set(start_once)) <= units.keys():
        raise ValueError("Activation requires staged units")
    names = (*units, *remove)
    if (len(set(names)) != len(names) or len(set(activate)) != len(activate)
            or len(set(deactivate)) != len(deactivate) or len(set(start_once)) != len(start_once)):
        raise ValueError("Systemd unit names must be unique")
    if start_once and (not activate or not set(activate) <= set(deactivate)
                       or set(start_once) & (set(activate) | set(deactivate))
                       or any(not name.endswith(".timer") for name in activate)
                       or any(not name.endswith(".service") or name.endswith("@.service") for name in start_once)):
        raise ValueError("Oneshot starts require separate services and quiesced timer activation")
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
    if type(preserve_unchanged) is not bool:
        raise ValueError("Unchanged activator selection must be a boolean")
    if verify is not None and not callable(verify):
        raise ValueError("Unit verification must be callable")
    if preserve_unchanged and (not activate or remove or deactivate or start_once or prepare is not None
                               or verify is not None
                               or any(not name.endswith((".timer", ".path")) for name in activate)):
        raise ValueError("Preserving unchanged units requires timer/path activation without removal or callbacks")
    operation = "unit-removal" if remove else "unit-replacement"
    if is_dry_run():
        print(f"  [DRY-RUN] Would {'remove' if remove else 'replace'} units: {', '.join(names)}")
        return

    store = owner._borrow(unit_dir) if owner is not None else OperationStateStore(
        os.path.join(unit_dir, ".basaltwater-unit-operation.json"))
    backup_dir = ""
    resolved = False
    record = None
    try:
        if preserve_unchanged:
            with store.ownership():
                # Never let matching files bypass an unfinished operation. If
                # anything needs repair, begin below rechecks under its lock and
                # takes fresh snapshots before the ordinary transaction.
                if store.load() is None and _activators_unchanged(units, activate, unit_dir):
                    return
        record = store.begin(operation, unit_dir, "staging", context={"units": list(names)})
        modified = False
        touched: list[str] = []
        context = {"units": list(names)}
        try:
            snapshots = {name: snapshot_unit_file(os.path.join(unit_dir, name)) for name in names}
            removals = tuple(name for name in remove if snapshots[name] is not None)
            # Templates have files but no running state. The owning activator
            # stops its instances (e.g. through PartOf) before file removal.
            managed_removals = tuple(name for name in removals if not name.endswith("@.service"))
            states = {name: inspect_unit_state(name) for name in dict.fromkeys((*activate, *deactivate, *managed_removals))}
            states.update({name: inspect_unit_state(name, allow_oneshot=True) for name in start_once})
            if not units and not removals:
                store.complete(record.operation_id, keep_ownership=owner is not None)
                resolved = True
                return
            backup_dir = tempfile.mkdtemp(prefix=".basaltwater-units-", dir=unit_dir)
            write_json_atomic(os.path.join(backup_dir, "previous.json"), {"units": snapshots, "states": states})
            context = {"units": list(names), "backup_dir": backup_dir}
            if start_once:
                context["start_once"] = list(start_once)
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
            # A job can start while its old timer is being stopped. Preserve
            # jobs observed running either before or after that stop; a job
            # finishing naturally must never be replayed during rollback.
            preserved_jobs = set()
            for name in start_once:
                current = inspect_unit_state(name, allow_oneshot=True)
                if any(state["ActiveState"] in {"active", "activating"} for state in (states[name], current)):
                    preserved_jobs.add(name)
            if start_once:
                context["preserved_jobs"] = sorted(preserved_jobs)
                store.transition(record.operation_id, "preparing-oneshots", context=context)
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
            for name in start_once:
                current = inspect_unit_state(name, allow_oneshot=True)
                if name in preserved_jobs or current["ActiveState"] in {"active", "activating"}:
                    if name not in preserved_jobs:
                        preserved_jobs.add(name)
                        context["preserved_jobs"] = sorted(preserved_jobs)
                        store.transition(record.operation_id, "preserving-oneshot", context=context)
                    continue
                # Own a requested job before issuing start: an uncertain
                # response can leave it executing and needing a checked stop.
                touched.append(name)
                context["started_jobs"] = [job for job in touched if job in start_once]
                store.transition(record.operation_id, "starting-oneshot", context=context)
                _command("systemctl", "start", name)
                current = inspect_unit_state(name, allow_oneshot=True)
                if current["ActiveState"] not in {"inactive", "active"}:
                    raise RuntimeError(f"Oneshot start failed verification: {name}")
            for name in activate:
                if name not in touched:
                    touched.append(name)
                _command("systemctl", "enable", name)
                _command("systemctl", "restart", name)
                state = inspect_unit_state(name)
                if state["ActiveState"] != "active" or state["UnitFileState"] != "enabled":
                    raise RuntimeError(f"Unit activation failed verification: {name}")
            if verify is not None:
                verify()
            store.complete(record.operation_id, keep_ownership=owner is not None)
            resolved = True
        except BaseException as original_error:
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
                quiescent = True
                for name in rollback_units:
                    if (name not in touched and states[name]["LoadState"] == "not-found"
                            and not os.path.lexists(os.path.join(unit_dir, name))):
                        continue
                    if not attempt(lambda name=name: _command("systemctl", "stop", name)):
                        quiescent = False
                    if name in activate or states[name]["UnitFileState"] in {"enabled", "enabled-runtime"}:
                        flags = ["--runtime"] if name not in activate and states[name]["UnitFileState"] == "enabled-runtime" else []
                        if not attempt(lambda name=name, flags=flags: _command("systemctl", "disable", *flags, name)):
                            quiescent = False
                    def confirm_stopped(name=name):
                        state = inspect_unit_state(name)
                        if state["ActiveState"] not in {"inactive", "failed"}:
                            raise RuntimeError(f"Rollback unit is still running: {name}")
                    if not attempt(confirm_stopped):
                        quiescent = False
                restored = set()
                # Retain both live candidates and private originals when any
                # stop is refused or uncertain. Do not reload or restart a
                # group that could still be using its replacement definitions.
                reloaded = False
                if quiescent:
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
                    def verify_restored_state(name=name):
                        actual = inspect_unit_state(name)
                        previous = states[name]
                        if (actual["ActiveState"] == "active") != (previous["ActiveState"] == "active") or actual["UnitFileState"] != previous["UnitFileState"]:
                            raise RuntimeError("Restored unit state did not match")
                    attempt(verify_restored_state)
            if errors:
                store.transition(record.operation_id, "rollback-failed", status="recovery_required",
                                 context={**context, "backup_dir": backup_dir, "errors": errors})
                raise UnitRecoveryError(store.path, backup_dir) from original_error
            store.complete(record.operation_id, outcome="rolled_back" if modified else "failed",
                           keep_ownership=owner is not None)
            resolved = True
            raise
    except Exception as error:
        if record is not None and not resolved and not isinstance(error, UnitRecoveryError):
            raise UnitRecoveryError(store.path, backup_dir) from error
        raise
    finally:
        if owner is None:
            store.close()
        if backup_dir and resolved:
            try:
                shutil.rmtree(backup_dir)
            except OSError as exc:
                print(f"  ⚠ Systemd backup cleanup failed at {backup_dir}: {exc}")


def replace_units(units: dict[str, str], *, activate: tuple[str, ...], unit_dir: str = "/etc/systemd/system",
                  deactivate: tuple[str, ...] = (), prepare: Callable[[], None] | None = None,
                  preserve_unchanged: bool = False, verify: Callable[[], None] | None = None,
                  start_once: tuple[str, ...] = ()) -> None:
    """Validate, replace, enable and restart units, restoring on any failure.

    Only ``activate``, ``deactivate`` and ``start_once`` units change running/enabled state. This lets
    timer/path replacements leave an executing oneshot service alone. Recovery
    backups and a blocking marker survive failed rollback or process death.
    ``deactivate`` units stop and disable before publication, in the supplied
    order (activators before their mount/service), and stay stopped unless also
    in ``activate``. Rollback restores their previous state in reverse order,
    except that an automount is restored before its matching mount.

    ``start_once`` starts idle oneshot services after publication and before
    arming their timers. All activation timers must also be in ``deactivate``.
    Jobs already running before or after timer quiescence are left alone, even
    during rollback; they may finish naturally. Jobs started by this operation
    require checked quiescence before restoring files on failure. Completed
    job output is outside unit rollback. Dry runs skip starts.

    ``prepare`` runs once after validation and grouped deactivation, before
    publication, under the same lock and rollback boundary. It never runs in
    dry-run mode. Its failures restore unit files and activation state, but its
    filesystem, credential or other side effects are not rolled back.

    ``verify`` runs after activation and before completion under the same lock.
    A readiness failure triggers restoration of prior units and activation
    state. Dry runs skip it; it cannot accompany ``preserve_unchanged``. Callback
    side effects beyond the managed units are outside rollback.

    ``preserve_unchanged`` is an opt-in for timer/path activation without
    deactivation or preparation callbacks. Under the recovery lock it requires
    matching file content/metadata, loaded definitions with no pending daemon
    reload, and active, persistently enabled activators. A match performs no
    writes, reload or restart; drift uses the ordinary recoverable transaction.
    Service activation keeps its explicit restart behavior because external
    configuration or executables may have changed independently of unit text.
    """
    _reconcile_units(units, activate=activate, remove=(), unit_dir=unit_dir,
                     deactivate=deactivate, prepare=prepare, preserve_unchanged=preserve_unchanged,
                     verify=verify, start_once=start_once)


def remove_units(names: tuple[str, ...], *, unit_dir: str | None = None,
                 prepare: Callable[[], None] | None = None, owner: UnitOwnership | None = None) -> None:
    """Remove managed units together, restoring files and states on failure.

    Pass activators before their service. Missing managed files are skipped;
    vendor units are not stopped. Removal shares replacement's recovery lock.

    ``prepare`` runs once after grouped deactivation, before unlinking files,
    under the same lock and rollback boundary. Dry runs and removals with no
    managed files skip it. Its failures restore unit files and activation
    state, but its own side effects are not rolled back.

    An explicit ``unit_ownership`` owner lets a nested removal borrow that
    scope's lock and directory. Completion or failure retains the outer lock.
    A retained inner marker still blocks further removals. Without an owner,
    the default directory remains ``/etc/systemd/system``.
    """
    if unit_dir is None:
        unit_dir = owner.unit_dir if owner is not None else "/etc/systemd/system"
    _reconcile_units({}, activate=(), remove=names, unit_dir=unit_dir, prepare=prepare, owner=owner)
