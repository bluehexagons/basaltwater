#!/usr/bin/env python3
"""Execute an already-approved argument vector in a supervised root unit."""

from __future__ import annotations

import json
import os
import subprocess
import sys

if __name__ == "__main__" and not __package__:
    sys.path.insert(0, "/opt/basaltwater")

from lib.privilege_execution import COMMAND_TIMEOUT_SECONDS, ENVIRONMENT
from lib.privilege_policy import MAX_MESSAGE, validate_command_argv


def execute_command(payload: bytes) -> int:
    """Reject malformed input before effects and keep all privileged output private."""
    if len(payload) > MAX_MESSAGE:
        return 125
    try:
        argv = validate_command_argv(json.loads(payload))
        result = subprocess.run(
            argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, cwd="/", env=ENVIRONMENT, close_fds=True,
            timeout=COMMAND_TIMEOUT_SECONDS, check=False,
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
