"""Request normal logout as the desktop owner before setup changes packages."""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
import sys
import time
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from desktop import session_runtime as runtime


def _control_disconnected(exc: Exception) -> bool:
    """Recognize transport loss without hiding configuration or protocol errors."""
    return (isinstance(exc, (ConnectionError, FileNotFoundError))
            or (type(exc) is ValueError and str(exc) == "Incomplete desktop request"))


def _status_for_logout(deadline: float) -> dict[str, Any]:
    """Reobserve after teardown disconnects; disconnection alone is not stopped."""
    while True:
        try:
            return runtime.status()
        except (ConnectionError, FileNotFoundError, ValueError) as exc:
            if not _control_disconnected(exc):
                raise
            if time.monotonic() >= deadline:
                raise RuntimeError("Desktop logout did not finish within 60 seconds; control channel remains disconnected. Inspect desktop status and rerun setup") from exc
            time.sleep(0.25)


def logout_for_setup() -> bool:
    """Handle existing supervisors without requiring a protocol upgrade first."""
    runtime.configuration()
    # A never-started owner may not yet have a logind runtime directory.
    if not Path(f"/run/user/{os.getuid()}").exists():
        return False
    with runtime.session_lock("start.lock"):
        deadline = time.monotonic() + 60
        current = _status_for_logout(deadline)
        if current["state"] == "stopped":
            return False
        generation = current["generation"]
        if current["state"] == "running":
            lease = None
            try:
                # Setup is an explicit maintenance handoff, including when paused.
                runtime.request({"action": "pause"})
                runtime.request({"action": "resume"})
                lease = runtime.request({"action": "acquire", "generation": generation})
                runtime.request({"action": "logout", **lease})
            except (ConnectionError, FileNotFoundError, ValueError) as exc:
                if not _control_disconnected(exc):
                    raise
                # Logout may have completed before its reply (or during handoff).
                # Never resend logout; the status wait must confirm shutdown.
            finally:
                if lease is not None:
                    with contextlib.suppress(OSError, RuntimeError, ValueError):
                        runtime.request({"action": "release", **lease})
                # Keep automation paused during logout or a canceled dialog.
                with contextlib.suppress(OSError, RuntimeError, ValueError):
                    runtime.request({"action": "pause"})
        elif current["state"] != "stopping":
            raise RuntimeError("Desktop is starting; wait for readiness and rerun setup")
        while time.monotonic() < deadline:
            current = _status_for_logout(deadline)
            if current["state"] == "stopped":
                return True
            if current.get("generation") != generation:
                raise RuntimeError("Desktop restarted during setup logout; disconnect automatic RDP reconnect and rerun setup")
            time.sleep(0.25)
        raise RuntimeError("Desktop logout did not finish within 60 seconds; dismiss any logout dialog and rerun setup. Agent input remains paused")


if __name__ == "__main__":
    try:
        print(json.dumps({"logged_out": logout_for_setup()}))
    except (OSError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
