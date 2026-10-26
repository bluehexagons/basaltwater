"""Debian systemd execution adapter for an admitted, unprivileged build.

The job owner must retain its worker lease and running record until this adapter
confirms cleanup. An exception is an unconfirmed outcome, never permission to
replay commands or to release durable capacity ownership.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Callable

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.build_protocol import MAX_REQUEST_BYTES, decode_document, parse_build_request, request_digest
from lib.build_git import prepare_history
from lib.cicd_deadline import run_command
from lib.setup_directories import directory_fd
from lib.validation import validate_filesystem_path


_ID = re.compile(r"[0-9a-f]{32}")
_FIELDS = {"LoadState", "ActiveState", "SubState", "Result", "ExecMainCode", "ExecMainStatus"}
_ENVIRONMENT = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "HOME": "/"}
WORK_ROOT = "/run/basaltwater-build-work"
WORKSPACE = WORK_ROOT + "/source/checkout"
BUILD_HOME = WORK_ROOT + "/home"
INPUT_PATH = WORK_ROOT + "/request.json"
HISTORY_PATH = WORK_ROOT + "/source/history.bundle"


@dataclass(frozen=True)
class BuildExecutionResult:
    state: str
    exit_code: int | None
    reason: str


def unit_name(request_id: str) -> str:
    if not isinstance(request_id, str) or _ID.fullmatch(request_id) is None:
        raise ValueError("Invalid build request ID")
    return "basaltwater-build-" + request_id + ".service"


def _path(value: str) -> str:
    validate_filesystem_path(value, must_exist=False)
    if (not os.path.isabs(value) or os.path.normpath(value) != value
            or any(char.isspace() for char in value) or "%" in value or ":" in value):
        raise ValueError("Build execution paths must be canonical managed absolute paths")
    return value


def execution_argv(request: object, *, uid: int, gid: int, scratch_path: str,
                   log_path: str) -> list[str]:
    """Use fixed executables; literal commands travel only in a checked file."""
    job = parse_build_request(request)
    if any(type(value) is not int or value <= 0 for value in (uid, gid)):
        raise ValueError("Build identity must have an unprivileged UID and GID")
    scratch_path, log_path = map(_path, (scratch_path, log_path))
    helper = _path(os.path.abspath(__file__))
    limits = job["limits"]
    properties = [
        "Type=exec", "ExitType=cgroup", "RemainAfterExit=yes", "Restart=no",
        "Group=root", "NoNewPrivileges=yes", "UMask=0077",
        "CapabilityBoundingSet=CAP_SETUID CAP_SETGID CAP_SETPCAP", "AmbientCapabilities=",
        "KillMode=control-group", "TimeoutStopSec=15", "SendSIGKILL=yes",
        f"RuntimeMaxSec={limits['timeout_seconds']}", f"MemoryMax={limits['memory_bytes']}",
        "MemorySwapMax=0", f"CPUQuota={limits['cpu_percent']}%", "TasksMax=256",
        "LimitCORE=0", "ProtectSystem=strict", "ProtectHome=yes",
        "TemporaryFileSystem=/tmp:ro /var/tmp:ro",
        "PrivateDevices=yes", "ProtectKernelTunables=yes", "ProtectKernelModules=yes",
        "ProtectKernelLogs=yes", "ProtectControlGroups=yes", "RestrictSUIDSGID=yes",
        "LockPersonality=yes", "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6",
        f"BindPaths={scratch_path}:{WORK_ROOT}",
        f"ReadWritePaths={WORKSPACE} {BUILD_HOME}", "StandardInput=null",
        f"StandardOutput=append:{log_path}", "StandardError=inherit",
    ]
    # Omit explicit User=root: systemd's explicit-user seccomp setup can drop
    # CAP_SETUID before setpriv. Only this fixed executable runs privileged.
    return [
        "/usr/bin/systemd-run", "--no-ask-password", "--quiet", "--expand-environment=no",
        "--unit=" + unit_name(job["request_id"]),
        *("--property=" + item for item in properties), "--working-directory=" + WORKSPACE,
        "--", "/usr/bin/setpriv", f"--reuid={uid}", f"--regid={gid}", "--clear-groups",
        "--inh-caps=-all", "--ambient-caps=-all", "--bounding-set=-all", "--no-new-privs",
        "--", "/usr/bin/env", "-i", "PATH=/usr/local/bin:/usr/bin:/bin",
        "LANG=C.UTF-8", "HOME=" + BUILD_HOME, "/usr/bin/python3", "-I", helper,
        "--execute", INPUT_PATH, request_digest(job), WORKSPACE, BUILD_HOME, HISTORY_PATH,
    ]


def _observation(unit: str) -> dict[str, str]:
    result = run_command([
        "/usr/bin/systemctl", "show", unit, "--no-pager",
        "--property=" + ",".join(sorted(_FIELDS)),
    ], timeout=15, capture_output=True, env=_ENVIRONMENT)
    raw = result.stdout
    if not isinstance(raw, str) or len(raw) > 4096:
        raise RuntimeError("Build service status is unavailable")
    fields = {}
    for line in raw.splitlines():
        key, separator, value = line.partition("=")
        if not separator or key not in _FIELDS or key in fields:
            raise RuntimeError("Build service returned an invalid status")
        fields[key] = value
    if set(fields) != _FIELDS or (result.returncode != 0 and fields["LoadState"] != "not-found"):
        raise RuntimeError("Build service status is unavailable")
    if fields["LoadState"] not in {"loaded", "not-found"}:
        raise RuntimeError("Build service load state is unavailable")
    return fields


def _empty_cgroup(unit: str) -> bool:
    """Only confirmed absence/zero population proves descendants have ended."""
    # Require the unified hierarchy even when this particular group is gone.
    with open("/sys/fs/cgroup/cgroup.controllers", "rb") as controllers:
        controllers.read(4096)
    try:
        with directory_fd("/sys/fs/cgroup/system.slice/" + unit) as parent:
            descriptor = os.open("cgroup.events", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                 dir_fd=parent)
            with os.fdopen(descriptor, "rb") as source:
                raw = source.read(4097)
    except FileNotFoundError:
        return True
    if len(raw) > 4096:
        raise RuntimeError("Build cgroup status is invalid")
    values = [line for line in raw.splitlines() if line.startswith(b"populated ")]
    if values not in ([b"populated 0"], [b"populated 1"]):
        raise RuntimeError("Build cgroup population is unavailable")
    return values == [b"populated 0"]


def _settled(fields: dict[str, str], unit: str) -> bool:
    terminal = (fields["ActiveState"] in {"inactive", "failed"}
                or (fields["ActiveState"] == "active" and fields["SubState"] == "exited"))
    return terminal and _empty_cgroup(unit)


def _stop(unit: str) -> None:
    result = run_command(["/usr/bin/systemctl", "--no-block", "stop", unit],
                         timeout=15, capture_output=True, env=_ENVIRONMENT)
    if result.returncode != 0:
        raise RuntimeError("Build service stop is unconfirmed; retain worker ownership")


def run_systemd_build(request: object, *, uid: int, gid: int, scratch_path: str, log_path: str,
                      cancelled: Callable[[], bool],
                      clock: Callable[[], float] = time.monotonic,
                      pause: Callable[[float], None] = time.sleep) -> BuildExecutionResult:
    """Wait for the cgroup, including cleanup beyond the normal work deadline.

    The caller holds durable capacity and supplies a provisioned scratch
    filesystem with the fixed input/workspace/home layout and a new empty
    private log. Start/probe/stop errors escape
    with the job still running; callers must not finish it or start another job.
    """
    job = parse_build_request(request)
    argv = execution_argv(job, uid=uid, gid=gid, scratch_path=scratch_path, log_path=log_path)
    unit = unit_name(job["request_id"])
    if _observation(unit)["LoadState"] != "not-found" or not _empty_cgroup(unit):
        raise RuntimeError("Build service already exists; do not replay this attempt")
    if cancelled():
        return BuildExecutionResult("cancelled", None, "cancelled-before-launch")
    deadline = clock() + job["limits"]["timeout_seconds"]
    launched = run_command(argv, timeout=30, capture_output=True, env=_ENVIRONMENT)
    if launched.returncode != 0:
        raise RuntimeError("Build service launch is unconfirmed; retain worker ownership")
    stop_reason = ""
    failed_exit_code = None
    while True:
        fields = _observation(unit)
        if fields["LoadState"] != "loaded":
            raise RuntimeError("Build service disappeared; retain worker ownership")
        if not stop_reason:
            if cancelled():
                stop_reason = "cancelled"
            elif clock() >= deadline:
                stop_reason = "timeout"
            elif fields["ExecMainCode"] != "0" and (
                    fields["ExecMainCode"] != "1" or fields["ExecMainStatus"] != "0"):
                try:
                    failed_exit_code = int(fields["ExecMainStatus"])
                except ValueError as exc:
                    raise RuntimeError("Build service exit status is unavailable") from exc
                stop_reason = "command-failed"
            if stop_reason:
                _stop(unit)
        if _settled(fields, unit):
            if not stop_reason:
                # Preserve the exit observation before removing RemainAfterExit.
                _stop(unit)
            # A successful main process is insufficient while cgroup descendants
            # remain; _settled checked the recursive population first.
            if stop_reason:
                return BuildExecutionResult("cancelled" if stop_reason == "cancelled" else "failed",
                                            failed_exit_code, stop_reason)
            try:
                exit_code = int(fields["ExecMainStatus"])
            except ValueError as exc:
                raise RuntimeError("Build service exit status is unavailable") from exc
            success = (fields["Result"] == "success" and fields["ExecMainCode"] == "1"
                       and exit_code == 0)
            return BuildExecutionResult("succeeded" if success else "failed", exit_code,
                                        "completed" if success else "command-failed")
        pause(0.2)


def execute_request(input_path: str, expected_digest: str, workspace: str, home: str,
                    history_path: str) -> int:
    """Fixed unprivileged service helper; never import project Python code."""
    if os.getuid() == 0 or os.getgid() == 0 or os.getgroups():
        raise RuntimeError("Build helper requires a separate identity without supplementary groups")
    with open(input_path, "rb") as source:
        raw = source.read(MAX_REQUEST_BYTES + 1)
    job = parse_build_request(decode_document(raw))
    if request_digest(job) != expected_digest:
        raise ValueError("Build input changed before launch")
    _path(workspace)
    _path(home)
    _path(history_path)
    prepare_history(history_path, job["source"]["revision"], workspace, home)
    base = {"PATH": "/usr/local/go/bin:/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8",
            "HOME": home, "TMPDIR": os.path.join(home, "tmp"),
            "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null"}
    remaining = job["limits"]["log_bytes"]
    for component in job["components"]:
        for command in component["commands"]:
            process = subprocess.Popen(
                ["/bin/bash", "--noprofile", "--norc", "-c", command], cwd=workspace,
                env={**base, **component["env"], "HOME": home,
                     "TMPDIR": os.path.join(home, "tmp")}, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            )
            assert process.stdout is not None
            with process.stdout:
                while chunk := process.stdout.read1(65536):
                    kept = chunk[:remaining]
                    if kept:
                        sys.stdout.buffer.write(kept)
                        sys.stdout.buffer.flush()
                    remaining -= len(kept)
                    if len(chunk) > len(kept):
                        # The coordinator sees a failed main process and stops
                        # its entire cgroup before packaging or releasing capacity.
                        return 1
            if process.wait() != 0:
                return 1
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 7 or sys.argv[1] != "--execute":
        raise SystemExit("Use the managed build worker")
    try:
        raise SystemExit(execute_request(*sys.argv[2:]))
    except (OSError, ValueError, RuntimeError):
        # The coordinator records the failure. Do not append diagnostics beyond
        # the admitted output bound or disclose private request fields.
        raise SystemExit(1)
