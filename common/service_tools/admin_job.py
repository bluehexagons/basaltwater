#!/usr/bin/env python3
"""Execute one approved fixed maintenance action independently of the panel."""

from __future__ import annotations

import argparse
import fcntl
import math
import os
import secrets
import signal
import subprocess
import sys
import time

if __name__ == "__main__" and not __package__:
    sys.path.insert(0, "/opt/basaltwater")

from lib.admin_actions import ADMIN_ACTIONS, ADMIN_STATE, ADMIN_TIMEOUT, ADMIN_UNIT, action_spec
from lib.admin_status import FAILURE_LABELS, JOB_ID_ENV, STAGE_LABELS, setup_details, valid_job_id
from lib.atomic_io import read_json_file, write_json_atomic
from lib.machine_state import STATE_DIR, SETUP_CONFIG_FILE, can_manage_system_services, can_restart_system
from lib.operation_state import OperationStateStore
from lib.installation_info import INSTALLATION_METADATA_FILENAME, read_installation_metadata
from lib.privilege_policy import protected_path
from lib.refresh import SOURCE_ACTIVATION_MARKER_FILENAME
from lib.remote_utils import read_os_release
from lib.validation import validate_channel


ENVIRONMENT = {
    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "HOME": "/root",
    "DEBIAN_FRONTEND": "noninteractive", "NEEDRESTART_MODE": "a",
    "GIT_TERMINAL_PROMPT": "0",
}
SOURCE = "/opt/basaltwater"


def current_setup_context(job: dict) -> dict | None:
    """Read only a protected marker correlated with this refresh invocation."""
    if job.get("action") != "refresh" or not valid_job_id(job.get("job_id")) or job.get("stage") != "command":
        return None
    try:
        # The installed state directory can be a managed symlink. Validate its
        # resolved root-owned destination, while refusing a linked marker file.
        path = os.path.join(os.path.realpath(STATE_DIR), "setup-operation.json")
        protected_path(path)
        record = OperationStateStore(path).load()
        if record is None or record.operation_type != "target_setup" or record.context.get("maintenance_job_id") != job["job_id"]:
            return None
        return setup_details({"job_id": job["job_id"], "phase": record.phase,
                              **{key: record.context.get(key) for key in (
                                  "setup_stage", "step_index", "step_count", "error_type",
                              )}}, job["job_id"])
    except (OSError, ValueError, RuntimeError):
        return None


def _refresh_recovery_block_reason() -> str | None:
    """Gate new refresh dispatch on unresolved source or hard-killed setup state."""
    try:
        activation_marker = os.path.join(os.path.dirname(SOURCE), SOURCE_ACTIVATION_MARKER_FILENAME)
        for marker in (activation_marker, os.path.join(os.path.realpath(STATE_DIR), "setup-operation.json")):
            protected_path(os.path.dirname(marker), directory=True)
            for candidate in (marker, marker + ".lock"):
                if os.path.lexists(candidate):
                    protected_path(candidate)
        activation = OperationStateStore(activation_marker).load()
        if activation is not None:
            return "A Basaltwater source activation needs administrator recovery before refresh."
        setup = OperationStateStore(os.path.join(os.path.realpath(STATE_DIR), "setup-operation.json")).load()
        if setup is not None and (
            setup.operation_type != "target_setup" or setup.status == "in_progress"
        ):
            return "An interrupted or unknown target setup needs administrator inspection before refresh."
    except (OSError, ValueError, RuntimeError, TypeError):
        return "Refresh recovery state could not be verified; administrator inspection is required."
    return None


def availability() -> dict[str, str]:
    """Return a reason for each unavailable action without running commands."""
    blocked: dict[str, str] = {}
    try:
        if not can_manage_system_services():
            return {key: "This machine cannot manage system services." for key in ADMIN_ACTIONS}
        restart = can_restart_system()
    except (OSError, RuntimeError, ValueError):
        return {key: "Machine capabilities could not be read." for key in ADMIN_ACTIONS}
    if not restart:
        for key in ("reboot", "shutdown", "cancel-shutdown"):
            blocked[key] = "Power control is unavailable for this machine profile."
    debian = read_os_release().get("ID") == "debian"
    if not debian or not os.path.isfile("/usr/bin/apt-get"):
        blocked["update-packages"] = "Package updates currently require Debian with APT."
    try:
        if not debian:
            raise ValueError("Not Debian")
        # Inspect only protected metadata here. Full setup validation belongs to
        # the approved dry run and to refresh itself.
        protected_path(SOURCE, directory=True)
        channel_path = SOURCE + "/.basaltwater/channel.json"
        if os.path.isfile(channel_path):
            protected_path(channel_path)
            channel = read_json_file(channel_path, max_bytes=16384)
            if not isinstance(channel, dict) or not channel.get("channel"):
                raise ValueError("No managed channel")
            validate_channel(channel["channel"])
        else:
            protected_path(os.path.join(SOURCE, INSTALLATION_METADATA_FILENAME))
            if read_installation_metadata(SOURCE) is None:
                raise ValueError("No installed setup provenance")
        if not os.path.isfile(SETUP_CONFIG_FILE):
            raise ValueError("No saved setup")
    except (OSError, ValueError, RuntimeError, TypeError):
        reason = "The installed Basaltwater source or saved Debian setup is missing or unsafe."
        blocked.update({"refresh-preview": reason, "refresh": reason})
    recovery_reason = _refresh_recovery_block_reason() if debian else None
    if recovery_reason:
        blocked.update({"refresh-preview": recovery_reason, "refresh": recovery_reason})
    if not os.path.isfile("/usr/sbin/nginx"):
        blocked.update({key: "Nginx is not installed." for key in ("check-web", "reload-web")})
    return blocked


def unit_state() -> str:
    """Read only the fixed maintenance unit; unavailable is never idle."""
    try:
        result = subprocess.run(
            ["/usr/bin/systemctl", "show", ADMIN_UNIT, "--property=ActiveState", "--value"],
            capture_output=True, text=True, timeout=2, check=False, env=ENVIRONMENT,
        )
        value = result.stdout.strip()
        if result.returncode == 0 and value in {"active", "activating", "deactivating", "inactive", "failed"}:
            return value
    except (OSError, subprocess.TimeoutExpired):
        pass
    return "unavailable"


def _read_job_record() -> dict:
    """Read and validate the protected latest-job record, or return no history."""
    # Confirm the broker state directory is trusted before interpreting a
    # missing leaf. lstat preserves lookup errors (including EIO) and sees
    # dangling symlinks, unlike exists()/lexists().
    protected_path(os.path.dirname(ADMIN_STATE), directory=True)
    try:
        os.lstat(ADMIN_STATE)
    except FileNotFoundError:
        return {}

    protected_path(ADMIN_STATE)
    value = read_json_file(ADMIN_STATE, max_bytes=4096)
    if (not isinstance(value, dict) or value.get("action") not in ADMIN_ACTIONS
            or value.get("status") not in {"running", "succeeded", "failed", "interrupted"}
            or type(value.get("started")) not in {int, float} or not math.isfinite(value["started"])
            or value.get("finished") is not None and (type(value["finished"]) not in {int, float} or not math.isfinite(value["finished"]))
            or value.get("exit_code") is not None and type(value["exit_code"]) is not int
            or value.get("stage") is not None and value["stage"] not in STAGE_LABELS
            or value.get("failure") is not None and value["failure"] not in FAILURE_LABELS
            or value.get("job_id") is not None and not valid_job_id(value["job_id"])
            or value.get("command_index") is not None and (
                type(value["command_index"]) is not int or not 1 <= value["command_index"] <= 32)):
        raise ValueError("Invalid maintenance result")
    result = {key: value.get(key) for key in (
        "action", "status", "started", "finished", "exit_code", "stage", "command_index", "failure", "job_id",
    )}
    result["setup_context"] = setup_details(value.get("setup_context"), value.get("job_id"))
    if result["setup_context"] is None and result["status"] == "running":
        result["setup_context"] = current_setup_context(value)
    return result


def _same_job(first: dict, second: dict) -> bool:
    """Whether two protected snapshots identify the same job invocation."""
    if not second:
        return False
    if first.get("job_id") is not None:
        return first.get("job_id") == second.get("job_id")
    # Legacy records predate job IDs; their action and start time are the
    # available identity, and a new ID cannot be correlated to them safely.
    return (second.get("job_id") is None and first.get("action") == second.get("action")
            and first.get("started") == second.get("started"))


def job_status() -> dict:
    """Expose fixed result metadata, never privileged output or saved setup."""
    initial_read_succeeded = True
    try:
        result = _read_job_record()
    except (OSError, ValueError, RuntimeError, TypeError, OverflowError):
        result = {"status": "unavailable"}
        initial_read_succeeded = False

    # Observe the unit after the first record read. An inactive or failed
    # observation must be reconciled against a second protected snapshot even
    # when the first snapshot was empty or terminal: another job may have been
    # published while systemd was queried.
    active = unit_state()
    if active in {"inactive", "failed"}:
        try:
            latest = _read_job_record()
        except (OSError, ValueError, RuntimeError, TypeError, OverflowError):
            result = {"status": "unavailable"}
            active = "unavailable"
        else:
            if not initial_read_succeeded:
                result = {"status": "unavailable"}
                active = "unavailable"
            elif not result and not latest:
                # Two confirmed missing snapshots are stable absence.
                result = {}
            elif not _same_job(result, latest):
                # The old unit observation cannot classify a replacement job.
                result = {"status": "unavailable"}
                active = "unavailable"
            elif latest.get("status") == "running":
                latest["status"] = "interrupted"
                latest["failure"] = "job_interrupted"
                result = latest
            else:
                # A terminal record for this same invocation is newer evidence
                # than the unit state sampled during the write/exit window.
                result = latest
    return {"unit_state": active, "latest": result, "blocked": availability()}


def commands(action: str) -> list[list[str]]:
    action_spec(action)
    if action == "update-packages":
        return [["/usr/bin/apt-get", "-o", "DPkg::Lock::Timeout=60", "update"], [
            "/usr/bin/apt-get", "-o", "DPkg::Lock::Timeout=60",
            "-o", "Dpkg::Options::=--force-confold", "--assume-yes", "upgrade",
        ]]
    if action in {"refresh", "refresh-preview"}:
        return [["/usr/bin/python3", "-I", SOURCE + "/basaltwater.py", "refresh", *(
            ["--dry-run"] if action == "refresh-preview" else []
        )]]
    if action in {"check-web", "reload-web"}:
        return [["/usr/sbin/nginx", "-t"], *(
            [["/usr/bin/systemctl", "--no-ask-password", "reload", "nginx.service"]]
            if action == "reload-web" else []
        )]
    if action == "restart-panel":
        return [["/usr/bin/systemctl", "--no-ask-password", "restart", "basaltwater-web-panel.service"]]
    return [["/usr/sbin/shutdown", *({"reboot": ["-r", "+2"], "shutdown": ["-h", "+2"], "cancel-shutdown": ["-c"]}[action])]]


def trusted_refresh_source() -> None:
    """Root must not run setup or Git hooks from an agent-writable checkout."""
    protected_path(SOURCE, directory=True)
    for root, directories, files in os.walk(SOURCE):
        if root == SOURCE:
            directories[:] = [name for name in directories if name != "state"]
        protected_path(root, directory=True)
        for name in (*directories, *files):
            protected_path(os.path.join(root, name), directory=name in directories)


def run_action(action: str) -> int:
    """Persist the claim before effects and fail rather than retrying commands."""
    action_spec(action)
    if os.geteuid() != 0:
        raise PermissionError("Administration jobs require root")
    directory = os.path.dirname(ADMIN_STATE)
    protected_path(directory, directory=True)
    lock_path = ADMIN_STATE + ".lock"
    if os.path.lexists(lock_path):
        protected_path(lock_path)
    with open(lock_path, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if os.path.lexists(ADMIN_STATE):
            protected_path(ADMIN_STATE)
        value = {"action": action, "job_id": secrets.token_hex(16), "status": "running", "started": time.time(), "finished": None,
                 "exit_code": None, "stage": "preflight", "command_index": None, "failure": None}
        write_json_atomic(ADMIN_STATE, value, mode=0o600)
        try:
            if action in availability():
                value["failure"] = "action_unavailable"
                raise ValueError("Action is unavailable on this host")
            if action in {"refresh", "refresh-preview"}:
                value["stage"] = "source_verification"
                write_json_atomic(ADMIN_STATE, value, mode=0o600)
                trusted_refresh_source()
            deadline = time.monotonic() + ADMIN_TIMEOUT
            exit_code = 0
            for index, argv in enumerate(commands(action), 1):
                value.update(stage="command", command_index=index)
                write_json_atomic(ADMIN_STATE, value, mode=0o600)
                result = subprocess.run(
                    argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    cwd="/", env={**ENVIRONMENT, **({JOB_ID_ENV: value["job_id"]} if action == "refresh" else {})}, close_fds=True, check=False,
                    timeout=max(1, deadline - time.monotonic()),
                )
                exit_code = result.returncode
                if exit_code:
                    value["failure"] = "command_signal" if exit_code < 0 else "nonzero_exit"
                    break
            value.update(status="failed" if exit_code else "succeeded", exit_code=exit_code)
        except subprocess.TimeoutExpired:
            value.update(status="failed", failure="command_timeout")
        except (OSError, ValueError, RuntimeError) as exc:
            failure = value["failure"] or (
                "source_rejected" if value["stage"] == "source_verification" else
                "execution_unavailable" if isinstance(exc, OSError) else "execution_rejected"
            )
            value.update(status="failed", failure=failure)
        except BaseException:
            value.update(status="interrupted", failure="job_interrupted")
            raise
        finally:
            value["finished"] = time.time()
            value["setup_context"] = current_setup_context(value)
            write_json_atomic(ADMIN_STATE, value, mode=0o600)
        return 0 if value["status"] == "succeeded" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=tuple(ADMIN_ACTIONS))
    args = parser.parse_args()
    def interrupted(_signum, _frame):
        raise SystemExit(1)
    signal.signal(signal.SIGTERM, interrupted)
    os.umask(0o077)
    return run_action(args.action)


if __name__ == "__main__":
    raise SystemExit(main())
