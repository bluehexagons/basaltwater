"""Bounded execution outside the broker's filesystem and device namespace."""

from __future__ import annotations

from dataclasses import dataclass
import secrets

COMMAND_HELPER = "/opt/basaltwater/common/service_tools/privilege_command.py"
COMMAND_TIMEOUT_SECONDS = 60
EXECUTOR_WAIT_SECONDS = COMMAND_TIMEOUT_SECONDS + 20
ENVIRONMENT = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "HOME": "/"}


@dataclass(frozen=True)
class ExecutionResult:
    """Safe outcome metadata; never includes privileged stdout or stderr."""
    state: str
    exit_code: int | None = None
    failure: str | None = None


def command_executor_argv() -> list[str]:
    """Only fixed helper arguments reach systemd; reviewed argv travels on stdin."""
    return [
        "/usr/bin/systemd-run", "--no-ask-password", "--quiet", "--wait", "--collect", "--pipe",
        "--unit=basaltwater-approved-command-" + secrets.token_hex(16),
        "--property=Type=exec", f"--property=RuntimeMaxSec={COMMAND_TIMEOUT_SECONDS + 5}",
        "--property=TimeoutStopSec=5", "--property=KillMode=control-group",
        "--property=Restart=no", "--property=NoNewPrivileges=yes", "--property=UMask=0077",
        "--property=MemoryMax=1G", "--property=TasksMax=128",
        "--working-directory=/", "--uid=root", "--",
        "/usr/bin/python3", "-I", COMMAND_HELPER,
    ]
