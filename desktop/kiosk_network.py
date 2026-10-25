"""Best-effort startup connectivity, with DNS isolated in a cancellable worker."""

from __future__ import annotations

import argparse
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.kiosk_config import validate_kiosk_url
from lib.validation import validate_network_ip, validate_network_port
from lib.validators import validate_host


STARTUP_SECONDS = 30
PROBE_SECONDS = 2
RETRY_SECONDS = 1


def _attempt(host: str, port: int, stopped: threading.Event, seconds: float) -> bool:
    """Bound hostname resolution too, and reap the worker before returning."""
    deadline = time.monotonic() + seconds
    process = subprocess.Popen(
        [sys.executable, "-I", str(Path(__file__).resolve()), "--host", host, "--port", str(port)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        while not stopped.is_set():
            result = process.poll()
            if result is not None:
                return result == 0
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            stopped.wait(min(0.1, remaining))
        return False
    finally:
        if process.poll() is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        process.wait(timeout=1)


def wait_for_endpoint(url: str, stopped: threading.Event) -> bool:
    """Wait once at startup; callers still open the browser after the deadline."""
    validate_kiosk_url(url)
    parsed = urlsplit(url)
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    deadline = time.monotonic() + STARTUP_SECONDS
    while not stopped.is_set():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            if _attempt(host, port, stopped, min(PROBE_SECONDS, remaining)):
                return True
        except OSError:
            # A missing worker/interpreter must not keep the local browser closed.
            return False
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        stopped.wait(min(RETRY_SECONDS, remaining))
    return False


def main(argv: list[str] | None = None) -> int:
    """Connect without HTTP, TLS, proxy settings, URL paths or authentication."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", required=True, type=int)
    args = parser.parse_args(argv)
    try:
        if ":" in args.host:
            if "%" in args.host:
                raise ValueError("Scoped kiosk endpoint addresses are unsupported")
            validate_network_ip(args.host, "Kiosk endpoint address")
        elif not validate_host(args.host):
            raise ValueError("Invalid kiosk endpoint hostname")
        validate_network_port(args.port, "Kiosk endpoint port")
        with socket.create_connection((args.host, args.port), timeout=1):
            return 0
    except (OSError, ValueError):
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
