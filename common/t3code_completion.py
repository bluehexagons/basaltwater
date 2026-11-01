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

_ISSUES = {
    "setup_running": "Setup has not exited; T3 completion was not applied",
    "setup_unfinished": "Unfinished setup prevents T3 completion",
    "setup_busy": "Setup is still busy; final T3 reload was not applied",
    "restart_failed": "Final T3 start/reload failed; inspect the user service",
    "readiness_failed": "Final T3 readiness check failed; inspect the user service",
}


class CompletionError(RuntimeError):
    """A safe diagnostic code without application logs or credential values."""

    def __init__(self, issue: str) -> None:
        super().__init__(_ISSUES[issue])
        self.issue = issue


def _receipt_path(request: str) -> str:
    return str(Path(REQUEST_FILE).with_name(f"t3-completion-{request}.json"))


def _receipt(request: str, status: str, issue: str | None = None) -> None:
    write_json_atomic(_receipt_path(request), {
        "schema_version": 1, "id": request, "status": status, "issue": issue,
        "updated_at": time.time(), "provider_turn_verified": False,
    }, mode=0o600)


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
            raise CompletionError("setup_running")
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
    _receipt(request, "queued")
    unit = "basaltwater-t3-finish-" + request
    try:
        result = run([
            "/usr/bin/systemd-run", "--quiet", "--unit=" + unit,
            "--on-active=5s", "--timer-property=AccuracySec=1s",
            "--property=Type=exec", "--property=RemainAfterExit=yes",
            "--property=UMask=0077", "--property=StandardInput=null",
            "--property=StandardOutput=null", "--property=StandardError=journal",
            "--working-directory=/", "--", sys.executable, "-I", WORKER,
            request, str(os.getpid()), started,
        ], check=False, capture_output=True, timeout=30)
    except Exception:
        # A timeout can lose the dispatch reply after systemd accepted the unit.
        _receipt(request, "dispatch_uncertain", "dispatch_uncertain")
        raise
    if result.returncode:
        _receipt(request, "failed", "dispatch_failed")
        raise RuntimeError("Could not queue the final T3 reload; inspect the service before retrying")
    print("T3 Code will start/reload after setup exits; active T3 terminals may disconnect.")
    print(f"Completion result: sudo systemctl status {unit}.service")
    print(f"Private completion receipt: {_receipt_path(request)}")


def complete(request: str, pid: int, started: str) -> None:
    """Finish outside the T3 cgroup, excluding setup and source replacement."""
    if (len(request) != 32 or any(c not in "0123456789abcdef" for c in request)
            or pid <= 0 or not started.isdecimal()):
        raise ValueError("Invalid T3 completion identity")
    try:
        _receipt(request, "waiting_for_setup")
        outcome = _complete(request, pid, started)
        _receipt(request, outcome)
    except Exception as exc:
        _receipt(request, "failed", exc.issue if isinstance(exc, CompletionError) else "completion_failed")
        raise


def _complete(request: str, pid: int, started: str) -> str:
    from common.t3code_steps import T3_SERVICE_NAME, _user_systemctl, _wait_for_t3_service

    _wait_for_setup(pid, started)
    # A refresh worker may still be closing its private argument file after
    # the target runner exits. Never restart across another setup/refresh.
    for attempt in range(60):
        with maintenance_lock() as acquired:
            if acquired:
                with OperationStateStore(os.path.join(STATE_DIR, "setup-operation.json")).ownership() as store:
                    if store.load() is not None:
                        raise CompletionError("setup_unfinished")
                    data = read_json_file(REQUEST_FILE)
                    if data.get("id") != request:
                        return "superseded"  # A newer successful setup owns completion.
                    username = data.get("username")
                    if not validate_username(username):
                        raise ValueError("Invalid T3 completion user")
                    # Exposure policy was validated before installation and
                    # queueing. This worker only validates its readiness target;
                    # it does not change the listener or its firewall rules.
                    host = validate_t3_bind_host(data.get("host"))
                    port = validate_network_port(data.get("port"), "T3 Code port")
                    account = pwd.getpwnam(username)
                    _receipt(request, "restarting")
                    result = _user_systemctl(username, account.pw_uid, "restart", T3_SERVICE_NAME)
                    if result.returncode:
                        raise CompletionError("restart_failed")
                    _receipt(request, "checking_readiness")
                    try:
                        _wait_for_t3_service(username, account.pw_uid, host, port, account.pw_dir)
                    except (RuntimeError, OSError) as exc:
                        raise CompletionError("readiness_failed") from exc
                    return "ready"
        if attempt < 59:
            time.sleep(1)
    raise CompletionError("setup_busy")
