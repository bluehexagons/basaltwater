#!/usr/bin/env python3
"""Execute an already-approved argument vector in a supervised root unit."""

from __future__ import annotations

import json
import os
import subprocess
import sys

if __name__ == "__main__" and not __package__:
    sys.path.insert(0, "/opt/basaltwater")

from lib.privilege_execution import ENVIRONMENT, validate_runtime
from lib.privilege_policy import MAX_MESSAGE, validate_command_argv


def execute_command(payload: bytes) -> int:
    """Reject malformed input before effects and keep all privileged output private."""
    if len(payload) > MAX_MESSAGE:
        return 125
    try:
        message = json.loads(payload)
        if isinstance(message, list):
            argv, runtime = validate_command_argv(message), 60  # Previously reviewed wire format.
        elif isinstance(message, dict) and set(message) == {"argv", "runtime_seconds"}:
            argv, runtime = validate_command_argv(message["argv"]), validate_runtime(message["runtime_seconds"])
        else:
            return 125
        result = subprocess.run(
            argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, cwd="/", env=ENVIRONMENT, close_fds=True,
            timeout=runtime, check=False,
        )
    except subprocess.TimeoutExpired:
        return 124
    except (OSError, ValueError, TypeError, RecursionError):
        return 125
    return result.returncode if result.returncode >= 0 else 128 - result.returncode


def main() -> int:
    if os.geteuid() != 0:
        return 125
    return execute_command(sys.stdin.buffer.read(MAX_MESSAGE + 1))


if __name__ == "__main__":
    raise SystemExit(main())
