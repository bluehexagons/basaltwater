#!/usr/bin/env python3
"""Execute one approved fixed maintenance action independently of the panel."""

from __future__ import annotations

import argparse
import fcntl
import math
import os
import signal
import subprocess
import sys
import time

if __name__ == "__main__" and not __package__:
    sys.path.insert(0, "/opt/basaltwater")

from lib.admin_actions import ADMIN_ACTIONS, ADMIN_STATE, ADMIN_TIMEOUT, ADMIN_UNIT, action_spec
from lib.atomic_io import read_json_file, write_json_atomic
from lib.machine_state import SETUP_CONFIG_FILE, can_manage_system_services, can_restart_system
from lib.privilege_policy import protected_path
from lib.remote_utils import read_os_release
from lib.validation import validate_channel


ENVIRONMENT = {
    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "HOME": "/root",
    "DEBIAN_FRONTEND": "noninteractive", "NEEDRESTART_MODE": "a",
    "GIT_TERMINAL_PROMPT": "0",
}
SOURCE = "/opt/basaltwater"


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
        protected_path(channel_path)
        channel = read_json_file(channel_path, max_bytes=16384)
        if not isinstance(channel, dict) or not channel.get("channel"):
            raise ValueError("No managed channel")
        validate_channel(channel["channel"])
        if not os.path.isfile(SETUP_CONFIG_FILE):
            raise ValueError("No saved setup")
    except (OSError, ValueError, RuntimeError, TypeError):
        reason = "Requires a root-owned managed source channel and a saved local Debian setup. Controller-installed snapshots must be updated from their controller."
        blocked.update({"refresh-preview": reason, "refresh": reason})
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


def job_status() -> dict:
    """Expose fixed result metadata, never privileged output or saved setup."""
    active = unit_state()
    try:
        protected_path(ADMIN_STATE)
        value = read_json_file(ADMIN_STATE, max_bytes=4096)
        if (not isinstance(value, dict) or value.get("action") not in ADMIN_ACTIONS
                or value.get("status") not in {"running", "succeeded", "failed", "interrupted"}
                or type(value.get("started")) not in {int, float} or not math.isfinite(value["started"])
                or value.get("finished") is not None and (type(value["finished"]) not in {int, float} or not math.isfinite(value["finished"]))
                or value.get("exit_code") is not None and type(value["exit_code"]) is not int):
            raise ValueError("Invalid maintenance result")
        result = {key: value.get(key) for key in ("action", "status", "started", "finished", "exit_code")}
        if result["status"] == "running" and active in {"inactive", "failed"}:
            result["status"] = "interrupted"
    except FileNotFoundError:
        result = {}
    except (OSError, ValueError, RuntimeError, TypeError, OverflowError):
        result = {"status": "unavailable"}
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
        value = {"action": action, "status": "running", "started": time.time(), "finished": None, "exit_code": None}
        write_json_atomic(ADMIN_STATE, value, mode=0o600)
        try:
            if action in availability():
                raise ValueError("Action is unavailable on this host")
            if action in {"refresh", "refresh-preview"}:
                trusted_refresh_source()
            deadline = time.monotonic() + ADMIN_TIMEOUT
            exit_code = 0
            for argv in commands(action):
                result = subprocess.run(
                    argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    cwd="/", env=ENVIRONMENT, close_fds=True, check=False,
                    timeout=max(1, deadline - time.monotonic()),
                )
                exit_code = result.returncode
                if exit_code:
                    break
            value.update(status="failed" if exit_code else "succeeded", exit_code=exit_code)
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
            value.update(status="failed")
        except BaseException:
            value.update(status="interrupted")
            raise
        finally:
            value["finished"] = time.time()
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
