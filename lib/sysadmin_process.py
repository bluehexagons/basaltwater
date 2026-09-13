"""Bound non-interactive sysadmin commands and expose timeout status 124."""

from __future__ import annotations

import subprocess
import sys

from lib.remote_utils import CommandTimeoutError, run


def run_command(
    command: list[str], *, capture_output: bool = False,
    text: bool = True, timeout: float = 3600,
) -> subprocess.CompletedProcess[str]:
    try:
        return run(command, check=False, capture_output=capture_output, text=text, timeout=timeout)
    except CommandTimeoutError as exc:
        message = str(exc)
        if not capture_output:
            print(message, file=sys.stderr)
        return subprocess.CompletedProcess(command, 124, "", message)
