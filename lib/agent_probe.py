"""Bounded local diagnostic processes; raw errors never enter support records."""

from __future__ import annotations

from dataclasses import dataclass
import os
import selectors
import signal
import subprocess
import time


@dataclass(frozen=True)
class ProbeResult:
    output: str = ""
    issue: str | None = None


def run_probe(argv: list[str], *, timeout: float = 10, max_bytes: int = 256 * 1024,
              env: dict[str, str] | None = None) -> ProbeResult:
    """Cap stdout and wall time, discard stderr, and terminate the probe group."""
    deadline = time.monotonic() + timeout
    try:
        process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   stdin=subprocess.DEVNULL, start_new_session=True, env=env, cwd="/")
    except OSError:
        return ProbeResult(issue="probe_unavailable")
    output = bytearray()
    issue = None
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    issue = "probe_timeout"
                    break
                for key, _events in selector.select(remaining):
                    chunk = os.read(key.fd, min(65536, max_bytes + 1 - len(output)))
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    output.extend(chunk)
                    if len(output) > max_bytes:
                        issue = "probe_output_limit"
                        break
                if issue:
                    break
        if not issue:
            try:
                if process.wait(timeout=max(0.001, deadline - time.monotonic())) != 0:
                    issue = "probe_failed"
            except subprocess.TimeoutExpired:
                issue = "probe_timeout"
    except OSError:
        issue = "probe_failed"
    finally:
        # Also stop descendants holding stdout after the original process exits.
        if issue or process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
        if process.stdout is not None:
            process.stdout.close()
    return ProbeResult(issue=issue) if issue else ProbeResult(output.decode("utf-8", errors="replace"))
