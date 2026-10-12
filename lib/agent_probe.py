"""Bounded local diagnostic processes; raw errors never enter support records."""

from __future__ import annotations

from dataclasses import dataclass
import subprocess

from lib.streamed_process import CommandOutputLimitError, run_captured


@dataclass(frozen=True)
class ProbeResult:
    output: str = ""
    issue: str | None = None


def run_probe(argv: list[str], *, timeout: float = 10, max_bytes: int = 256 * 1024,
              env: dict[str, str] | None = None, cwd: str = "/",
              input_data: bytes | None = None) -> ProbeResult:
    """Cap stdout and wall time, discard stderr, and terminate the probe group."""
    try:
        result = run_captured(argv, timeout=timeout, max_bytes=max_bytes, env=env, cwd=cwd,
                              input_data=input_data, capture_stderr=False)
    except CommandOutputLimitError:
        return ProbeResult(issue="probe_output_limit")
    except subprocess.TimeoutExpired:
        return ProbeResult(issue="probe_timeout")
    except OSError:
        return ProbeResult(issue="probe_unavailable")
    return ProbeResult(issue="probe_failed") if result.returncode else ProbeResult(output=result.stdout)
