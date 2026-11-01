"""Bounded commands using shared process cleanup."""

from __future__ import annotations

import math
import subprocess

from lib.remote_utils import CommandTimeoutError, run


def command_timeout(limit: float) -> float:
    """Require a finite positive command timeout."""
    if isinstance(limit, bool) or not isinstance(limit, (int, float)) or not math.isfinite(limit) or limit <= 0:
        raise ValueError("Command timeout must be finite and positive")
    return limit


def run_command(command, *, timeout: float, input=None, **kwargs):
    """Run with shared descendant cleanup and subprocess timeout reporting."""
    timeout = command_timeout(timeout)
    try:
        return run(command, check=False, input_data=input, timeout=timeout, **kwargs)
    except CommandTimeoutError as exc:
        raise subprocess.TimeoutExpired(exc.command, timeout) from exc
