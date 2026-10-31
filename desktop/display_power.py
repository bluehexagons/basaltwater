"""Read-only KDE display power observation; never wake or alter an output."""

from __future__ import annotations

from datetime import datetime, timezone
import os
import re
import subprocess

from lib.streamed_process import run_captured


def display_power(*, probe=None) -> dict:
    """Observe DPMS in the caller's established graphical environment."""
    observed_at = datetime.now(timezone.utc).isoformat()
    command = ["/usr/bin/kscreen-doctor", "--dpms", "show"]
    display = os.environ.get("WAYLAND_DISPLAY", "")
    if not re.fullmatch(r"wayland-[0-9]{1,6}", display):
        return {"state": "unknown", "outputs": [], "observed_at": observed_at,
                "reason": "DPMS observation needs the existing KDE Wayland session"}
    runtime = f"/run/user/{os.getuid()}"
    environment = {"XDG_RUNTIME_DIR": runtime, "DBUS_SESSION_BUS_ADDRESS": f"unix:path={runtime}/bus",
                   "WAYLAND_DISPLAY": display, "PATH": "/usr/bin:/bin", "LC_ALL": "C",
                   "QT_QPA_PLATFORM": "wayland"}
    try:
        if probe is None:
            result = run_captured(command, timeout=3, max_bytes=16384, env=environment)
            successful, output = result.returncode == 0, result.stdout
        else:
            state, output = probe(["/usr/bin/env", f"WAYLAND_DISPLAY={display}", "QT_QPA_PLATFORM=wayland", *command])
            successful = state == "ok"
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        rows = re.findall(r"(?m)^dpms mode for screen ([^\r\n:]{1,128}): (on|off)\s*$", output)
        if not successful or not rows:
            raise RuntimeError("Display power query unavailable or unrecognized")
        outputs = [{"name": name, "power": power} for name, power in rows]
        states = {power for _, power in rows}
        return {"state": next(iter(states)) if len(states) == 1 else "mixed",
                "outputs": outputs, "observed_at": observed_at,
                "scope": "outputs reported by kscreen-doctor; completeness unverified"}
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        return {"state": "unknown", "outputs": [], "observed_at": observed_at,
                "reason": "DPMS observation unavailable; inspect kscreen-doctor --dpms show in KDE"}
