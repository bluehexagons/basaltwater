"""Bounded desktop workflows performed between short supervisor requests."""

from __future__ import annotations

import importlib.util
import json
import math
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
from desktop.observations import validate_capture_max_age, validate_observation_option
from lib.validation import validate_filesystem_path
from desktop.receipts import DesktopRequestError, input_failure


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
    poll_interval = 0.25
    stable_since = None
    stable_identity = None
    inventory_detail = None
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
            if time.monotonic() >= deadline:
                break
        result = backend.request({"action": "windows", "generation": generation})
        if result["generation"] != generation:
            raise RuntimeError("Desktop session changed while waiting")
        inventory_detail = result.get("detail") if result.get("coverage") == "unavailable" else None
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
        if ready and now <= deadline:
            return {"generation": generation, "condition": condition, "windows": matches,
                    "launch_status": launch_status}
        if now >= deadline:
            break
        # Back off expensive inventories, but keep checking an observed window
        # frequently while establishing its requested stability interval.
        interval = 0.25 if stable_since is not None else poll_interval
        time.sleep(min(interval, deadline - now))
        poll_interval = 0.25 if stable_since is not None else min(1, poll_interval * 2)
        if time.monotonic() >= deadline:
            break
    detail = f"; {inventory_detail}" if inventory_detail else ""
    raise RuntimeError(f"Timed out waiting for window condition '{condition}'; inspect desktop status and windows{detail}")


def wait_for_element(generation: str, *, pid: int, name: str | None = None,
                     role: str | None = None, state: str = "present", text: str | None = None,
                     timeout: float = 15, root: str | None = None, backend=runtime) -> dict[str, Any]:
    """Poll semantic observations; require an unambiguous match."""
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
    poll_interval = 0.25
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
        now = time.monotonic()
        if ready and now <= deadline:
            return {**result, "elements": matches, "condition": state}
        if now >= deadline:
            break
        time.sleep(min(poll_interval, deadline - now))
        poll_interval = min(1, poll_interval * 2)
        if time.monotonic() >= deadline:
            break
    return {**result, "error": f"Timed out waiting for element state '{state}'; inspect matches and truncation"}


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
    """Reject invalid paced input before sending characters."""
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


def validate_keycodes(payload: dict[str, Any]) -> list[int] | None:
    """Validate a complete physical chord before any key is pressed."""
    codes = payload.get("codes")
    if payload.get("kind") != "keycode":
        if codes is not None:
            raise ValueError("Physical keycodes apply only to keycode input")
        return None
    if (payload.get("action") != "input" or not isinstance(codes, list)
            or not 1 <= len(codes) <= 8
            or any(type(code) is not int or not 1 <= code <= 255 for code in codes)
            or len(set(codes)) != len(codes)):
        raise ValueError("Physical input requires 1–8 distinct Linux evdev keyboard codes from 1 through 255")
    if payload.get("key") is not None or payload.get("text") is not None:
        raise ValueError("Physical keycodes cannot be combined with key symbols or text")
    return codes


def validate_hold(payload: dict[str, Any]) -> int:
    """Bound native button/chord holds separately from character pacing."""
    hold = payload.get("hold_ms")
    if hold is None:
        return 0
    if (payload.get("action") != "input" or payload.get("kind") not in ("key", "keycode", "click")
            or (payload.get("kind") == "click" and payload.get("button", 1) not in (1, 2, 3))):
        raise ValueError("Hold duration applies only to key chords and pointer buttons 1–3")
    if type(hold) is not int or not 0 <= hold <= 5000:
        raise ValueError("Hold duration must be an integer from 0 to 5000 milliseconds")
    return hold


def validate_drag(payload: dict[str, Any]) -> int:
    """Validate an entire bounded straight-line gesture before any pointer event."""
    if payload.get("kind") != "drag":
        if any(payload.get(key) is not None for key in ("to_x", "to_y", "duration_ms")):
            raise ValueError("Drag endpoints/duration apply only to drag input")
        return 0
    if payload.get("action") != "input":
        raise ValueError("Drag requires an input action")
    duration = payload.get("duration_ms")
    duration = 300 if duration is None else duration
    if type(duration) is not int or not 1 <= duration <= 5000:
        raise ValueError("Drag duration must be an integer from 1 to 5000 milliseconds")
    button = payload.get("button", 1)
    if type(button) is not int or button not in (1, 2, 3):
        raise ValueError("Drag button must be 1–3")
    geometry = payload.get("geometry")
    if not isinstance(geometry, list) or len(geometry) != 2 or any(type(n) is not int or not 1 <= n <= 16384 for n in geometry):
        raise ValueError("Drag requires observed full-monitor geometry")
    for x_key, y_key in (("x", "y"), ("to_x", "to_y")):
        x, y = payload.get(x_key), payload.get(y_key)
        if type(x) is not int or type(y) is not int or not (0 <= x < geometry[0] and 0 <= y < geometry[1]):
            raise ValueError("Drag endpoints must lie inside the selected monitor")
    return duration


def validate_capture_crop(payload: dict[str, Any], *, backend=runtime) -> list | None:
    crop = payload.get("crop")
    if crop is not None:
        if backend is runtime or payload.get("action") != "screenshot":
            raise ValueError("Cropped capture requires a native screenshot action")
        from desktop.portal_capture import validate_crop
        validate_crop(crop)
    return crop


def bounded_deadline(payload: dict, *, deadline: float | None = None, seconds: float = 14) -> float:
    """Keep the earliest finite absolute monotonic budget across local hops."""
    limits = [time.monotonic() + seconds]
    for value in (payload.get("deadline"), deadline):
        if value is not None:
            try:
                valid = type(value) in (int, float) and math.isfinite(value)
            except OverflowError:
                valid = False
            if not valid:
                raise ValueError("Deadline must be a finite monotonic timestamp")
            limits.append(value)
    return min(limits)


def remaining_seconds(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise RuntimeError("Desktop request expired; observe before retrying")
    return remaining


def send_action(payload: dict[str, Any], *, deadline: float | None = None, backend=runtime) -> dict[str, Any]:
    """Pace text with short revocable requests, including on existing supervisors."""
    if (backend is runtime and payload.get("action") == "input" and payload.get("kind") == "text"
            and payload.get("delay_ms") is None and isinstance(payload.get("text"), str)
            and not payload["text"].isascii()):
        # Rapid xdotool batches can lose newly mapped Unicode characters in
        # native clients. Separate revocable requests let keymap updates settle.
        payload = {**payload, "delay_ms": 10}
    validate_text_delay(payload)
    validate_keycodes(payload)
    validate_hold(payload)
    validate_drag(payload)
    validate_observation_option(payload)
    if payload.get("action") == "screenshot":
        validate_capture_max_age(payload.get("max_age_seconds", 60))
    crop = validate_capture_crop(payload, backend=backend)
    if payload.get("kind") == "keycode" and backend is runtime:
        raise ValueError("Physical keycodes require the native desktop backend")
    if payload.get("kind") == "drag" and backend is runtime:
        raise ValueError("Drag requires the native desktop backend")
    if payload.get("hold_ms") is not None and backend is runtime:
        raise ValueError("Hold duration requires the native desktop backend")
    delay = payload.get("delay_ms")
    request = {name: value for name, value in payload.items() if name != "delay_ms"}
    deadline = bounded_deadline(payload, deadline=deadline, seconds=20 if delay is not None else 14)
    request["deadline"] = deadline
    if delay is None:
        remaining_seconds(deadline)
        try:
            result = backend.request(request)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            if payload.get("action") != "input":
                raise
            if isinstance(exc, DesktopRequestError) and exc.receipt.get("input_delivery") in ("not-sent", "completed", "unknown"):
                raise
            detail = "\n".join([str(exc), *getattr(exc, "__notes__", [])])
            receipt = input_failure(exc, payload, stage="transport")
            receipt["reason"] = "transport-failed"
            receipt["error"] = f"{detail}; input outcome may be partial or unknown; inspect before further input; do not replay"
            raise DesktopRequestError(receipt) from exc
        if crop is not None and (result.get("image_origin") != crop[:2] or result.get("image_geometry") != crop[2:]):
            return {**result, "error": "Backend did not confirm requested crop mapping; restart the native helper and recapture"}
        if payload.get("action") == "screenshot" and payload.get("max_age_seconds", 60) != 60 and not result.get("error"):
            if (not result.get("observation_id")
                    or result.get("capture_freshness", {}).get("max_age_seconds") != payload["max_age_seconds"]):
                return {**result, "error": "Backend did not confirm observation lifetime; update and restart the helper at a safe checkpoint"}
        return result
    submitted = 0
    server_attempted = False
    result: dict[str, Any] = {"generation": payload["generation"], "geometry": payload["geometry"]}
    try:
        if backend is not runtime:
            capabilities = backend.status(deadline=deadline).get("input_capabilities", [])
            if isinstance(capabilities, list) and "server-paced-text" in capabilities:
                remaining_seconds(deadline)
                server_attempted = True
                return backend.request({**request, "delay_ms": delay})
        if payload.get("observation_id") is not None:
            raise ValueError("Observation-bound paced text requires server-paced-text; on Debian use verified "
                             "element set-text, or capture and use paced text without --observation")
        for character in payload["text"]:
            if submitted:
                time.sleep(min(delay / 1000, remaining_seconds(deadline)))
            if time.monotonic() >= deadline:
                raise RuntimeError("Paced typing exceeded its 20-second budget")
            # Every character rechecks generation, geometry and the human's control revision.
            result = backend.request({**request, "text": character})
            submitted += 1
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        detail = "\n".join([str(exc), *getattr(exc, "__notes__", [])])
        if server_attempted and isinstance(exc, DesktopRequestError) and "input_delivery" in exc.receipt:
            return {**result, **exc.receipt, "submitted_characters": None}
        return {**result, "error": f"{detail}; input may be partial; inspect input before further actions; do not replay",
                "submitted_characters": None if server_attempted else submitted,
                "completion_unknown": server_attempted}
    return {**result, "submitted_characters": submitted}


def observe_after_input(result: dict, payload: dict, output: str, *, settle_ms: int, backend) -> dict:
    """Retain completed input evidence even when its subsequent capture fails."""
    from desktop.observations import compact_capture
    time.sleep(settle_ms / 1000)
    try:
        observed = backend.status()
        if (observed.get("generation") != payload["generation"] or
                observed.get("control_revision") != payload["control_revision"]):
            raise RuntimeError("Desktop control changed after input; observe before further input")
        capture = backend.request({"action": "screenshot", "generation": payload["generation"],
                                   "control_revision": payload["control_revision"],
                                   "output": output, **({"next_frame": True} if backend is not runtime else {})})
        if backend is not runtime and capture.get("initial_frame_skipped") is not True:
            raise RuntimeError("Backend did not confirm subsequent-frame capture; restart the native helper")
        if (capture.get("generation") != payload["generation"] or
                capture.get("control_revision") != payload["control_revision"]):
            raise RuntimeError("Desktop control changed during capture; observe before further input")
        return {**result, "observation": compact_capture(capture), "settle_ms": settle_ms}
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        return {**result, "error": f"Input completed; observation failed: {exc}. Do not replay input.",
                "reason": "observation-failed", "dispatch_stage": "post-input-observation",
                "input_delivery": "completed", "observation_output": output,
                "observation_failure": getattr(exc, "receipt", {"error": str(exc)})}


def run_sequence(path: str, generation: str, *, backend=runtime) -> dict[str, Any]:
    validate_filesystem_path(path, must_exist=True)
    if Path(path).stat().st_size > backend.MAX_MESSAGE:
        raise ValueError("Desktop sequence is too large")
    steps = json.loads(Path(path).read_text())
    if not isinstance(steps, list) or not 1 <= len(steps) <= 20:
        raise ValueError("Desktop sequences require 1 to 20 actions")
    for step in steps:
        if (not isinstance(step, dict) or step.get("action") not in ("input", "window", "screenshot", "windows")
                or any(key in step for key in ("generation", "control_revision", "lease"))):
            raise ValueError("Sequences accept input, window, screenshot, and windows actions without embedded control metadata")
        validate_text_delay(step)
        validate_keycodes(step)
        validate_hold(step)
        validate_drag(step)
        validate_observation_option(step)
        if step.get("action") == "screenshot":
            validate_capture_max_age(step.get("max_age_seconds", 60))
        validate_capture_crop(step, backend=backend)
        if step.get("kind") == "keycode" and backend is runtime:
            raise ValueError("Physical keycodes require the native desktop backend")
        if step.get("kind") == "drag" and backend is runtime:
            raise ValueError("Drag requires the native desktop backend")
        if step.get("hold_ms") is not None and backend is runtime:
            raise ValueError("Hold duration requires the native desktop backend")
    current = backend.status()
    if current.get("generation") != generation:
        raise ValueError("Desktop session changed; observe again")
    revision = current["control_revision"]
    if any(step.get("observation_id") is not None for step in steps) and "observation-input" not in current.get("input_capabilities", []):
        raise ValueError("Helper does not support observation-bound input; update and restart it at a safe checkpoint")
    results = []
    deadline = time.monotonic() + 20
    try:
        for step in steps:
            if time.monotonic() >= deadline:
                raise RuntimeError("Desktop sequence exceeded its 20-second budget")
            observed = backend.status()
            if observed.get("generation") != generation or observed.get("control_revision") != revision:
                raise RuntimeError("Desktop control changed during the sequence; observe before retrying")
            if observed.get("agent_control") != "enabled":
                raise RuntimeError("Agent desktop control is " + str(observed.get("agent_control")))
            result = send_action({**step, "generation": generation, "control_revision": revision}, deadline=deadline, backend=backend)
            if "error" in result:
                return {"generation": generation, "error": result["error"], "completed": len(results),
                        "results": results, "failed_action": result}
            results.append(result)
        return {"generation": generation, "completed": len(results), "results": results}
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        return {"generation": generation, "error": str(exc), "completed": len(results), "results": results}
