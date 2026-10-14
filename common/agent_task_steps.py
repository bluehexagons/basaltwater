"""Provision the independent non-root prompt-task worker on managed hosts."""

from __future__ import annotations

import fcntl
import hashlib
import os
import pwd
import stat
from contextlib import ExitStack, contextmanager
from pathlib import Path

from lib.agent_task_api import WORKER_MARKER, worker_unit
from lib.atomic_io import parse_json
from lib.config import SetupConfig
from lib.machine_state import can_manage_system_services
from lib.remote_utils import is_dry_run, run
from lib.unit_transaction import replace_units, snapshot_unit_file
from lib.validation import validate_filesystem_path, validate_no_control_characters
from lib.validators import validate_username


UNIT_DIR = "/etc/systemd/system"
SOURCE_ROOT = Path(__file__).resolve().parent.parent
PANEL_UNIT = "basaltwater-web-panel.service"
PANEL_MARKER = "# Managed by basaltwater web panel"


class _LegacyWorkerHeld(RuntimeError):
    """A schema-1 scheduler must stop before setup can block new claims."""

    def __init__(self) -> None:
        super().__init__("Stop the legacy panel/task worker before migrating its scheduler; it cannot block new claims")


def _quote(value: str) -> str:
    validate_no_control_characters(value, "Task worker systemd value")
    return '"' + value.replace("%", "%%").replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_task_worker(account: pwd.struct_passwd, *, revision: str) -> str:
    if account.pw_uid == 0 or not validate_username(account.pw_name):
        raise ValueError("Task workers require a valid non-root account")
    validate_filesystem_path(account.pw_dir, must_exist=False)
    if not os.path.isabs(account.pw_dir):
        raise ValueError("Task worker home must be absolute")
    # WorkingDirectory is a literal path; quoting it makes it non-absolute.
    # A final slash preserves trailing spaces and prevents line continuation.
    working_directory = account.pw_dir.replace("%", "%%").rstrip("/") + "/"
    return f"""{WORKER_MARKER}
# Runtime revision: {revision}
[Unit]
Description=Basaltwater persistent agent tasks for {account.pw_name}
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User={account.pw_name}
Group={account.pw_gid}
WorkingDirectory={working_directory}
Environment={_quote('HOME=' + account.pw_dir)}
Environment={_quote('CODEX_HOME=' + account.pw_dir + '/.codex')}
Environment=XDG_RUNTIME_DIR=/run/user/{account.pw_uid}
Environment=DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{account.pw_uid}/bus
ExecStart=/usr/bin/python3 /opt/basaltwater/basaltwater.py agent tasks worker
UMask=0077
Restart=on-failure
RestartSec=15
KillMode=control-group
TimeoutStopSec=15
NoNewPrivileges=true
PrivateDevices=true
PrivateTmp=true
ProtectSystem=full
ProtectControlGroups=true
ProtectKernelModules=true
ProtectKernelTunables=true
RestrictSUIDSGID=true
StandardOutput=null
StandardError=journal

[Install]
WantedBy=multi-user.target
"""


def _active(unit: str) -> bool:
    return run(["systemctl", "is-active", "--quiet", unit], check=False, capture_output=True).returncode == 0


@contextmanager
def _refresh_guard(account: pwd.struct_passwd):
    """Block account task claims while setup checks and refreshes an idle worker."""
    validate_filesystem_path(account.pw_dir, must_exist=True)
    with ExitStack() as resources:
        parent = os.open(account.pw_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        resources.callback(os.close, parent)
        home_info = os.fstat(parent)
        if home_info.st_uid != account.pw_uid or home_info.st_mode & 0o022:
            raise RuntimeError("Unsafe task account home; inspect it before setup")
        for name in (".local", "state", "basaltwater", "prompt-tasks"):
            created = False
            try:
                os.mkdir(name, mode=0o700, dir_fd=parent)
                created = True
            except FileExistsError:
                pass
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            resources.callback(os.close, child)
            info = os.fstat(child)
            if created and info.st_uid != account.pw_uid:
                os.fchown(child, account.pw_uid, account.pw_gid)
                info = os.fstat(child)
            forbidden = 0o077 if name == "prompt-tasks" else 0o022
            if info.st_uid != account.pw_uid or info.st_mode & forbidden:
                raise RuntimeError("Unsafe task state directory; inspect it before setup")
            parent = child
        created = False
        try:
            descriptor = os.open("state.lock", os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=parent)
            created = True
        except FileExistsError:
            descriptor = os.open("state.lock", os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        resources.callback(os.close, descriptor)
        info = os.fstat(descriptor)
        if created and info.st_uid != account.pw_uid:
            os.fchown(descriptor, account.pw_uid, account.pw_gid)
            info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != account.pw_uid
                or info.st_mode & 0o077 or info.st_nlink != 1):
            raise RuntimeError("Unsafe task state lock; inspect it before setup")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Task state is busy; retry setup after its current transaction") from exc
        yield parent


def _worker_busy(account: pwd.struct_passwd, parent: int) -> bool:
    try:
        descriptor = os.open("worker.lock", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    except FileNotFoundError:
        return False
    try:
        metadata = os.fstat(descriptor)
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != account.pw_uid
                or metadata.st_mode & 0o077 or metadata.st_nlink != 1):
            raise RuntimeError("Unsafe task worker lease; inspect it before setup")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return False
        except BlockingIOError:
            state = os.open("tasks.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            with os.fdopen(state, "rb") as stream:
                info = os.fstat(stream.fileno())
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != account.pw_uid
                        or info.st_mode & 0o077 or info.st_nlink != 1 or info.st_size > 1024 * 1024):
                    raise RuntimeError("Unsafe task state; inspect it before setup")
                payload = stream.read(1024 * 1024 + 1)
            if len(payload) > 1024 * 1024:
                raise RuntimeError("Task state exceeds its storage limit")
            try:
                value = parse_json(payload)
            except (ValueError, UnicodeError, RecursionError) as exc:
                raise RuntimeError("Invalid task state; inspect it before setup") from exc
            if not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] not in (1, 2):
                raise RuntimeError("Unsupported task state; inspect it before setup")
            if value["version"] == 1:
                raise _LegacyWorkerHeld()
            if (not isinstance(value.get("runs"), list) or len(value["runs"]) > 40
                    or any(not isinstance(run, dict) or not isinstance(run.get("status"), str) or run["status"] not in
                           {"running", "completed", "failed", "cancelled", "interrupted"} for run in value["runs"])):
                raise RuntimeError("Invalid task state; inspect it before setup")
            return any(isinstance(run, dict) and run.get("status") == "running" for run in value["runs"])
    finally:
        os.close(descriptor)


@contextmanager
def _panel_handoff(account: pwd.struct_passwd, *, legacy: bool = False):
    """Stop only this account's managed panel and restore it after handoff."""
    previous = snapshot_unit_file(os.path.join(UNIT_DIR, PANEL_UNIT))
    panel_active = (previous is not None
                    and previous["content"].startswith(PANEL_MARKER + "\n")
                    and _active(PANEL_UNIT))
    if panel_active:
        owner = run(["systemctl", "show", PANEL_UNIT, "--property=User", "--value"],
                    check=False, capture_output=True)
        panel_active = owner.returncode == 0 and owner.stdout.strip() in {account.pw_name, str(account.pw_uid)}
    if not panel_active:
        if legacy:
            raise RuntimeError("Legacy worker is not this account's managed panel; stop its scheduler before retrying setup")
        yield
        return
    if legacy:
        print("  ⚠ Stopping the legacy panel/task worker for migration; active work may be interrupted")
    # A legacy scheduler ignores state.lock. Stop the service before rechecking
    # its lease, rather than treating a momentarily idle snapshot as safe.
    # A failed stop may still have acted, so restoration covers that call too.
    try:
        run(["systemctl", "stop", PANEL_UNIT], timeout=120)
        yield
    finally:
        run(["systemctl", "start", PANEL_UNIT], timeout=120)


def configure_agent_task_worker(config: SetupConfig) -> None:
    """Install a worker, stopping a managed legacy scheduler when necessary."""
    if not can_manage_system_services(config.machine_type):
        print("  ✓ Skipping task worker (persistent services unavailable)")
        return
    if config.username == "root":
        print("  ✓ Skipping task worker (a non-root task account is required)")
        return
    if not validate_username(config.username):
        raise ValueError("Invalid task worker username")
    if is_dry_run():
        print("  [DRY-RUN] Would configure the independent agent task worker")
        return
    account = pwd.getpwnam(config.username)
    validate_filesystem_path(account.pw_dir, must_exist=True)
    if os.stat(account.pw_dir).st_uid != account.pw_uid:
        raise RuntimeError("Task account must own its home directory")
    unit = worker_unit(account.pw_dir)
    path = os.path.join(UNIT_DIR, unit)
    previous = snapshot_unit_file(path)
    if previous is not None and not previous["content"].startswith(WORKER_MARKER + "\n"):
        raise RuntimeError(f"Refusing an unmanaged task worker: {path}")
    digest = hashlib.sha256()
    for name in ("agent_tasks.py", "agent_task_context.py", "agent_task_worker.py"):
        digest.update((SOURCE_ROOT / "lib" / name).read_bytes())
    content = render_task_worker(account, revision=digest.hexdigest()[:16])
    if previous is not None and previous["content"] == content and _active(unit):
        print(f"  ✓ Independent task worker ready: {unit}")
        return
    with _refresh_guard(account) as parent, ExitStack() as handoff:
        migrated = False
        try:
            busy = _worker_busy(account, parent)
        except _LegacyWorkerHeld:
            handoff.enter_context(_panel_handoff(account, legacy=True))
            migrated = True
            busy = _worker_busy(account, parent)
        if busy:
            raise RuntimeError("Finish or cancel the active prompt task before refreshing its worker")
        if not migrated:
            handoff.enter_context(_panel_handoff(account))
        replace_units({unit: content}, activate=(unit,), unit_dir=UNIT_DIR)
    print(f"  ✓ Independent task worker: {unit}")
