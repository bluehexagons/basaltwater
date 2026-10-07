"""Bounded desktop workflows performed between short supervisor requests."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
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


def validate_window_wait_options(*, timeout: float, exclude_titles: list[str] | tuple[str, ...] | None = None,
                                 stable_seconds: float = 0) -> tuple[str, ...]:
    """Validate optional readiness filters before launching or polling."""
    if type(timeout) not in (int, float) or not 0 < timeout <= 120:
        raise ValueError("Wait timeout must be greater than zero and at most 120 seconds")
    if type(stable_seconds) not in (int, float) or not 0 <= stable_seconds <= min(5, timeout):
        raise ValueError("Window stability must be 0–5 seconds and no longer than the wait timeout")
    if exclude_titles is None:
        return ()
    if not isinstance(exclude_titles, (list, tuple)) or len(exclude_titles) > 16:
        raise ValueError("Exclude at most 16 literal window title substrings")
    for title in exclude_titles:
        if not isinstance(title, str) or not title or len(title) > 512:
            raise ValueError("Excluded title must be a nonempty string of at most 512 characters")
    return tuple(dict.fromkeys(exclude_titles))


def wait_for_window(generation: str, *, window: str | None = None,
                    title: str | None = None, pid: int | None = None,
                    condition: str = "visible", timeout: float = 15,
                    launch: str | None = None, backend=runtime,
                    exclude_titles: list[str] | tuple[str, ...] | None = None,
                    stable_seconds: float = 0) -> dict[str, Any]:
    """Poll outside the supervisor so human pause stays available while waiting."""
    excluded = validate_window_wait_options(timeout=timeout, exclude_titles=exclude_titles,
                                           stable_seconds=stable_seconds)
    if condition not in ("present", "visible", "absent", "active"):
        raise ValueError("Unknown window wait condition")
    if window is not None:
        window = backend.normalize_window_id(window)
    if title is not None and (not isinstance(title, str) or not title or len(title) > 512):
        raise ValueError("Window title must be a nonempty string of at most 512 characters")
    if pid is not None and (type(pid) is not int or pid <= 0):
        raise ValueError("Window PID must be positive")
    if window is None and title is None and pid is None:
        raise ValueError("Select a window ID, title substring, or PID to wait for")
    deadline = time.monotonic() + timeout
    stable_since = None
    stable_identity = None
    while True:
        launch_status = None
        if launch:
            launch_status = backend.request({"action": "launch-status", "generation": generation, "launch": launch})
            if launch_status["returncode"] not in (None, 0):
                code = launch_status["returncode"]
                if code < 0:
                    try:
                        reason = f"signal {signal.Signals(-code).name} ({-code})"
                    except ValueError:
                        reason = f"signal {-code}"
                else:
                    reason = f"code {code}"
                raise RuntimeError(f"Application exited with {reason} before the requested window condition")
        result = backend.request({"action": "windows", "generation": generation})
        if result["generation"] != generation:
            raise RuntimeError("Desktop session changed while waiting")
        matches = [item for item in result["windows"]
                   if (window is None or item["id"] == window)
                   and (title is None or title in item["title"])
                   and not any(text in item["title"] for text in excluded)
                   and (pid is None or item.get("pid") == pid)]
        if condition == "absent":
            ready = not matches and not result.get("truncated", False)
        else:
            if condition == "visible":
                matches = [item for item in matches if item["visible"]]
            if condition == "active":
                matches = [item for item in matches if item["id"] == result.get("active_window")]
            ready = bool(matches)
        now = time.monotonic()
        if not ready:
            stable_since = None
            stable_identity = None
        elif stable_seconds:
            # A title/identity change, disappearance, or incomplete inventory
            # restarts the stability interval. Never retain control while polling.
            identity = tuple(sorted((item["id"], item.get("identity"), item["title"], item.get("pid"))
                                    for item in matches))
            if result.get("truncated", False):
                ready = False
                stable_since = None
                stable_identity = None
            elif identity != stable_identity or stable_since is None:
                stable_since = now
                stable_identity = identity
            if stable_since is not None:
                ready = now - stable_since >= stable_seconds
        if ready and (not stable_seconds or now <= deadline):
            return {"generation": generation, "condition": condition, "windows": matches,
                    "launch_status": launch_status}
        if now >= deadline:
            raise RuntimeError(f"Timed out waiting for window condition '{condition}'; inspect desktop status and windows")
        time.sleep(min(0.25, max(0, deadline - now)))


def wait_for_element(generation: str, *, pid: int, name: str | None = None,
                     role: str | None = None, state: str = "present", text: str | None = None,
                     timeout: float = 15, root: str | None = None, backend=runtime) -> dict[str, Any]:
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
        result = backend.request(query)
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


def validate_text_delay(payload: dict[str, Any]) -> None:
    """Reject invalid paced input before acquiring control or sending characters."""
    delay = payload.get("delay_ms")
    if delay is None:
        return
    if payload.get("action") != "input" or payload.get("kind") != "text":
        raise ValueError("Typing delay applies only to text input")
    text = payload.get("text")
    if not isinstance(text, str) or len(text) > 1024 or "\0" in text:
        raise ValueError("Text must contain at most 1024 characters without NUL")
    if type(delay) is not int or not 1 <= delay <= 100:
        raise ValueError("Typing delay must be an integer from 1 to 100 milliseconds")
    if len(text) * delay > 20000:
        raise ValueError("Paced text exceeds 20 seconds; use shorter verified commands")


def validate_hold(payload: dict[str, Any]) -> int:
    """Bound native button/chord holds separately from character pacing."""
    hold = payload.get("hold_ms")
    if hold is None:
        return 0
    if (payload.get("action") != "input" or payload.get("kind") not in ("key", "click")
            or (payload.get("kind") == "click" and payload.get("button", 1) not in (1, 2, 3))):
        raise ValueError("Hold duration applies only to key chords and pointer buttons 1–3")
    if type(hold) is not int or not 0 <= hold <= 5000:
        raise ValueError("Hold duration must be an integer from 0 to 5000 milliseconds")
    return hold


def send_action(payload: dict[str, Any], *, deadline: float | None = None, backend=runtime) -> dict[str, Any]:
    """Pace text with short revocable requests, including on existing supervisors."""
    validate_text_delay(payload)
    validate_hold(payload)
    if payload.get("hold_ms") is not None and backend is runtime:
        raise ValueError("Hold duration requires the native desktop backend")
    delay = payload.get("delay_ms")
    request = {name: value for name, value in payload.items() if name != "delay_ms"}
    if delay is None:
        return backend.request(request)
    deadline = time.monotonic() + 20 if deadline is None else deadline
    submitted = 0
    result: dict[str, Any] = {"generation": payload["generation"], "geometry": payload["geometry"]}
    try:
        for character in payload["text"]:
            if submitted:
                time.sleep(delay / 1000)
            if time.monotonic() >= deadline:
                raise RuntimeError("Paced typing exceeded its 20-second budget")
            # Every character rechecks generation, geometry, pause and the same lease.
            result = backend.request({**request, "text": character})
            submitted += 1
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        return {**result, "error": f"{exc}; inspect input before retrying; the last character may have arrived",
                "submitted_characters": submitted}
    return {**result, "submitted_characters": submitted}


def run_sequence(path: str, generation: str, *, backend=runtime) -> dict[str, Any]:
    validate_filesystem_path(path, must_exist=True)
    if Path(path).stat().st_size > backend.MAX_MESSAGE:
        raise ValueError("Desktop sequence is too large")
    steps = json.loads(Path(path).read_text())
    if not isinstance(steps, list) or not 1 <= len(steps) <= 20:
        raise ValueError("Desktop sequences require 1 to 20 actions")
    for step in steps:
        if (not isinstance(step, dict) or step.get("action") not in ("input", "window", "screenshot", "windows")
                or "generation" in step or "lease" in step):
            raise ValueError("Sequences accept input, window, screenshot, and windows actions without embedded leases or generations")
        validate_text_delay(step)
        validate_hold(step)
        if step.get("hold_ms") is not None and backend is runtime:
            raise ValueError("Hold duration requires the native desktop backend")
    lease = backend.request({"action": "acquire", "generation": generation})
    results = []
    deadline = time.monotonic() + 20
    try:
        for step in steps:
            if time.monotonic() >= deadline:
                raise RuntimeError("Desktop sequence exceeded its 20-second budget")
            result = send_action({**step, "generation": generation, "lease": lease["lease"]}, deadline=deadline, backend=backend)
            if "error" in result:
                return {"generation": generation, "error": result["error"], "completed": len(results),
                        "results": results, "failed_action": result}
            results.append(result)
        return {"generation": generation, "completed": len(results), "results": results}
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        return {"generation": generation, "error": str(exc), "completed": len(results), "results": results}
    finally:
        try:
            backend.request({"action": "release", "generation": generation, "lease": lease["lease"]})
        except (OSError, RuntimeError, ValueError):
            pass
