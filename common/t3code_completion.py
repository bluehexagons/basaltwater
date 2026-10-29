"""Reload Debian T3 only after the successful setup process has exited."""

from __future__ import annotations

import os
from pathlib import Path
import pwd
import select
import sys
import time
import uuid

from lib.atomic_io import read_json_file, write_json_atomic
from lib.config import SetupConfig
from lib.machine_state import STATE_DIR, can_manage_system_services
from lib.maintenance_lock import maintenance_lock
from lib.operation_state import OperationStateStore
from lib.remote_utils import is_dry_run, run
from lib.validation import validate_network_port, validate_t3_bind_host, validate_web_interface_settings
from lib.validators import validate_username


REQUEST_FILE = os.path.join(STATE_DIR, "t3-completion.json")
WORKER = str(Path(__file__).parent / "service_tools" / "t3code_finish.py")


def _process_start(pid: int) -> str | None:
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except FileNotFoundError:
        return None


def _wait_for_setup(pid: int, started: str) -> None:
    """Use a process handle and birth identity so PID reuse cannot delay us."""
    if _process_start(pid) != started:
        return
    try:
        descriptor = os.pidfd_open(pid)
    except ProcessLookupError:
        return
    try:
        if _process_start(pid) != started:
            return
        if not select.select([descriptor], [], [], 600)[0]:
            raise RuntimeError("Setup has not exited; T3 completion was not applied")
    finally:
        os.close(descriptor)


def queue_completion(config: SetupConfig) -> None:
    """Queue a final reload after provider, skill, state and notification work."""
    if (is_dry_run() or "t3code" not in (config.web_interfaces or [])
            or not can_manage_system_services(config.machine_type)):
        return
    if not validate_username(config.username):
        raise ValueError("Invalid T3 completion user")
    validate_web_interface_settings(config)
    request = uuid.uuid4().hex
    host = config.web_interface_host or "127.0.0.1"
    write_json_atomic(REQUEST_FILE, {
        "id": request, "username": config.username, "host": host,
        "port": config.web_interface_port,
    }, mode=0o600)
    started = _process_start(os.getpid())
    if started is None:
        raise RuntimeError("Cannot identify the setup process for safe T3 completion")
    unit = "basaltwater-t3-finish-" + request
    result = run([
        "/usr/bin/systemd-run", "--quiet", "--unit=" + unit,
        "--on-active=5s", "--timer-property=AccuracySec=1s",
        "--property=Type=exec", "--property=RemainAfterExit=yes",
        "--property=UMask=0077", "--property=StandardInput=null",
        "--property=StandardOutput=null", "--property=StandardError=journal",
        "--working-directory=/", "--", sys.executable, "-I", WORKER,
        request, str(os.getpid()), started,
    ], check=False, capture_output=True, timeout=30)
    if result.returncode:
        raise RuntimeError("Could not queue the final T3 reload; inspect the service before retrying")
    print("T3 Code will start/reload after setup exits; active T3 terminals may disconnect.")
    print(f"Completion result: sudo systemctl status {unit}.service")


def complete(request: str, pid: int, started: str) -> None:
    """Finish outside the T3 cgroup, excluding setup and source replacement."""
    from common.t3code_steps import T3_SERVICE_NAME, _user_systemctl, _wait_for_t3_service

    if (len(request) != 32 or any(c not in "0123456789abcdef" for c in request)
            or pid <= 0 or not started.isdecimal()):
        raise ValueError("Invalid T3 completion identity")
    _wait_for_setup(pid, started)
    # A refresh worker may still be closing its private argument file after
    # the target runner exits. Never restart across another setup/refresh.
    for attempt in range(60):
        with maintenance_lock() as acquired:
            if acquired:
                with OperationStateStore(os.path.join(STATE_DIR, "setup-operation.json")).ownership() as store:
                    if store.load() is not None:
                        raise RuntimeError("Unfinished setup prevents T3 completion")
                    data = read_json_file(REQUEST_FILE)
                    if data.get("id") != request:
                        return  # A newer successful setup owns completion.
                    username = data.get("username")
                    if not validate_username(username):
                        raise ValueError("Invalid T3 completion user")
                    # Exposure policy was validated before installation and
                    # queueing. This worker only validates its readiness target;
                    # it does not change the listener or its firewall rules.
                    host = validate_t3_bind_host(data.get("host"))
                    port = validate_network_port(data.get("port"), "T3 Code port")
                    account = pwd.getpwnam(username)
                    result = _user_systemctl(username, account.pw_uid, "restart", T3_SERVICE_NAME)
                    if result.returncode:
                        raise RuntimeError("Final T3 start/reload failed; inspect the user service")
                    _wait_for_t3_service(username, account.pw_uid, host, port, account.pw_dir)
                    return
        if attempt < 59:
            time.sleep(1)
    raise RuntimeError("Setup is still busy; final T3 reload was not applied")
