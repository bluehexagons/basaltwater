"""Provision the independent non-root prompt-task worker on managed hosts."""

from __future__ import annotations

import fcntl
import hashlib
import os
import pwd
import stat
from pathlib import Path

from lib.agent_task_api import WORKER_MARKER, worker_unit
from lib.atomic_io import read_json_file
from lib.config import SetupConfig
from lib.machine_state import can_manage_system_services
from lib.remote_utils import is_dry_run, run
from lib.unit_transaction import replace_units, snapshot_unit_file
from lib.validation import validate_filesystem_path
from lib.validators import validate_username


UNIT_DIR = "/etc/systemd/system"
SOURCE_ROOT = Path(__file__).resolve().parent.parent


def _quote(value: str) -> str:
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("Invalid systemd value")
    return '"' + value.replace("%", "%%").replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_task_worker(account: pwd.struct_passwd, *, revision: str) -> str:
    if account.pw_uid == 0 or not validate_username(account.pw_name):
        raise ValueError("Task workers require a valid non-root account")
    validate_filesystem_path(account.pw_dir, must_exist=False)
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
WorkingDirectory={_quote(account.pw_dir)}
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


def _worker_busy(account: pwd.struct_passwd) -> bool:
    parent = Path(account.pw_dir) / ".local/state/basaltwater/prompt-tasks"
    try:
        descriptor = os.open(parent / "worker.lock", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return False
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != account.pw_uid or metadata.st_mode & 0o077:
            raise RuntimeError("Unsafe task worker lease; inspect it before setup")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return False
        except BlockingIOError:
            value = read_json_file(str(parent / "tasks.json"))
            if not isinstance(value, dict) or not isinstance(value.get("runs"), list):
                raise RuntimeError("Invalid task state; inspect it before setup")
            return any(isinstance(run, dict) and run.get("status") == "running" for run in value["runs"])
    finally:
        os.close(descriptor)


def configure_agent_task_worker(config: SetupConfig) -> None:
    """Install an idle worker without creating tasks or borrowing credentials."""
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
    if _worker_busy(account):
        raise RuntimeError("Finish or cancel the active prompt task before refreshing its worker")
    panel = "basaltwater-web-panel.service"
    panel_active = _active(panel)
    if panel_active:
        owner = run(["systemctl", "show", panel, "--property=User", "--value"], check=False, capture_output=True)
        panel_active = owner.returncode == 0 and owner.stdout.strip() == account.pw_name
    if panel_active:
        run(["systemctl", "stop", panel])
    try:
        replace_units({unit: content}, activate=(unit,), unit_dir=UNIT_DIR)
    finally:
        if panel_active:
            run(["systemctl", "start", panel])
    print(f"  ✓ Independent task worker: {unit}")
