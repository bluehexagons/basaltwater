"""Bounded desktop workflows performed between short supervisor requests."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import time
from typing import Any

from desktop import session_runtime as runtime
from desktop.accessibility import check_dependencies, validate_query
from lib.validation import validate_filesystem_path


def artifact_path() -> str:
    """Allocate a private, persistent capture location outside the repository."""
    directory = Path.home() / "Pictures" / "basaltwater"
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    info = directory.lstat()
    if directory.is_symlink() or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RuntimeError("Screenshot artifact directory must be owned by you with mode 0700")
    return str(directory / f"desktop-{time.time_ns()}-{secrets.token_hex(4)}.png")


def wait_for_window(generation: str, *, window: str | None = None,
                    title: str | None = None, pid: int | None = None,
                    condition: str = "visible", timeout: float = 15,
                    launch: str | None = None) -> dict[str, Any]:
    """Poll outside the supervisor so human pause stays available while waiting."""
    if type(timeout) not in (int, float) or not 0 < timeout <= 120:
        raise ValueError("Wait timeout must be greater than zero and at most 120 seconds")
    if condition not in ("present", "visible", "absent", "active"):
        raise ValueError("Unknown window wait condition")
    if window is not None:
        window = runtime.normalize_window_id(window)
    if title is not None and (not isinstance(title, str) or not title or len(title) > 512):
        raise ValueError("Window title must be a nonempty string of at most 512 characters")
    if pid is not None and (type(pid) is not int or pid <= 0):
        raise ValueError("Window PID must be positive")
    if window is None and title is None and pid is None:
        raise ValueError("Select a window ID, title substring, or PID to wait for")
    deadline = time.monotonic() + timeout
    while True:
        launch_status = None
        if launch:
            launch_status = runtime.request({"action": "launch-status", "generation": generation, "launch": launch})
            if launch_status["returncode"] not in (None, 0):
                raise RuntimeError(f"Application exited with code {launch_status['returncode']} before becoming ready")
        result = runtime.request({"action": "windows", "generation": generation})
        if result["generation"] != generation:
            raise RuntimeError("Desktop session changed while waiting")
        matches = [item for item in result["windows"]
                   if (window is None or item["id"] == window)
                   and (title is None or title in item["title"])
                   and (pid is None or item.get("pid") == pid)]
        if condition == "absent":
            ready = not matches and not result.get("truncated", False)
        else:
            if condition == "visible":
                matches = [item for item in matches if item["visible"]]
            if condition == "active":
                matches = [item for item in matches if item["id"] == result.get("active_window")]
            ready = bool(matches)
        if ready:
            return {"generation": generation, "condition": condition, "windows": matches,
                    "launch_status": launch_status}
        if time.monotonic() >= deadline:
            raise RuntimeError(f"Timed out waiting for window condition '{condition}'; inspect desktop status and windows")
        time.sleep(min(0.25, max(0, deadline - time.monotonic())))


def wait_for_element(generation: str, *, pid: int, name: str | None = None,
                     role: str | None = None, state: str = "present", text: str | None = None,
                     timeout: float = 15, root: str | None = None) -> dict[str, Any]:
    """Poll semantic observations without a lease; require an unambiguous match."""
    query = {"action": "inspect", "generation": generation, "pid": pid, "name": name, "role": role}
    if root is not None:
        query["root"] = root
    validate_query(query)
    if name is None and role is None:
        raise ValueError("Select an exact accessible name or role")
    if type(timeout) not in (int, float) or not 0 < timeout <= 120:
        raise ValueError("Wait timeout must be greater than zero and at most 120 seconds")
    if state not in ("present", "absent", "enabled", "showing", "focused"):
        raise ValueError("Unknown element wait state")
    if text is not None and (not isinstance(text, str) or len(text) > 256 or state == "absent"):
        raise ValueError("Text wait requires at most 256 characters and a non-absent state")
    deadline = time.monotonic() + timeout
    while True:
        result = runtime.request(query)
        if result["generation"] != generation:
            raise RuntimeError("Desktop session changed while waiting")
        matches = result["elements"]
        complete = not result["truncated"]
        if state not in ("present", "absent"):
            matches = [row for row in matches if state in row["states"]]
        if text is not None:
            matches = [row for row in matches if not row.get("text_truncated", False) and row.get("text") == text]
        ready = complete and not matches if state == "absent" else complete and len(matches) == 1
        if ready:
            return {**result, "elements": matches, "condition": state}
        if time.monotonic() >= deadline:
            return {**result, "error": f"Timed out waiting for element state '{state}'; inspect matches and truncation"}
        time.sleep(min(0.25, max(0, deadline - time.monotonic())))


def doctor() -> dict[str, Any]:
    """Inspect without starting, stopping, repairing, or capturing user content."""
    checks: dict[str, Any] = {}
    try:
        checks["session"] = runtime.status()
        checks["runtime_directory"] = str(runtime.runtime_directory())
    except (OSError, ValueError, RuntimeError, KeyError, subprocess.SubprocessError) as exc:
        checks["session"] = {"error": str(exc)}
    required = ("xrdp-sesrun", "xdotool", "xprop", "xwininfo", "scrot", "wmctrl", "xdg-open")
    checks["tools"] = {name: shutil.which(name) is not None for name in required}
    checks["handoff_ui"] = importlib.util.find_spec("tkinter") is not None
    checks["accessibility"] = check_dependencies()
    for name in ("xrdp", "xrdp-sesman"):
        try:
            result = subprocess.run(["systemctl", "is-active", name], capture_output=True,
                                    text=True, timeout=3, check=False)
            checks[name] = result.stdout.strip() or "unknown"
        except (OSError, subprocess.SubprocessError) as exc:
            checks[name] = str(exc)
    problems = []
    if "error" in checks["session"]:
        problems.append(checks["session"]["error"])
    missing = [name for name, found in checks["tools"].items() if not found]
    if missing or not checks["handoff_ui"]:
        problems.append("Rerun desktop setup to install missing tools: " + ", ".join(missing + ([] if checks["handoff_ui"] else ["python3-tk"])))
    if not checks["accessibility"]["available"]:
        problems.append(checks["accessibility"]["error"])
    if any(checks[name] != "active" for name in ("xrdp", "xrdp-sesman")):
        problems.append("Inspect xrdp and xrdp-sesman service logs; no services were restarted")
    if checks["session"].get("state") in ("starting", "stopping"):
        problems.append("Desktop is transitioning; inspect status again before using applications")
    return {"healthy": not problems, "checks": checks, "suggestions": problems,
            "unverified": ["human RDP reconnect/resize", "clipboard transfer", "application responsiveness"]}


def run_sequence(path: str, generation: str) -> dict[str, Any]:
    validate_filesystem_path(path, must_exist=True)
    if Path(path).stat().st_size > runtime.MAX_MESSAGE:
        raise ValueError("Desktop sequence is too large")
    steps = json.loads(Path(path).read_text())
    if not isinstance(steps, list) or not 1 <= len(steps) <= 20:
        raise ValueError("Desktop sequences require 1 to 20 actions")
    for step in steps:
        if (not isinstance(step, dict) or step.get("action") not in ("input", "window", "screenshot", "windows")
                or "generation" in step or "lease" in step):
            raise ValueError("Sequences accept input, window, screenshot, and windows actions without embedded leases or generations")
    lease = runtime.request({"action": "acquire", "generation": generation})
    results = []
    try:
        for step in steps:
            results.append(runtime.request({**step, "generation": generation, "lease": lease["lease"]}))
        return {"generation": generation, "completed": len(results), "results": results}
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        return {"generation": generation, "error": str(exc), "completed": len(results), "results": results}
    finally:
        try:
            runtime.request({"action": "release", "generation": generation, "lease": lease["lease"]})
        except (OSError, RuntimeError, ValueError):
            pass
