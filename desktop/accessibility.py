"""Bounded AT-SPI observations and actions, isolated from the session supervisor."""

from __future__ import annotations

from collections import deque
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any

MAX_NODES = 512
MAX_RESULTS = 128
MAX_OUTPUT = 48000
STATES = ("enabled", "sensitive", "showing", "visible", "focused", "editable", "checked", "selected", "busy", "defunct")


def check_dependencies() -> dict[str, Any]:
    """Import the system bindings without connecting to a desktop or reading UI."""
    try:
        result = subprocess.run(
            ["/usr/bin/python3", "-c",
             "import gi; gi.require_version('Atspi', '2.0'); from gi.repository import Atspi"],
            capture_output=True, text=True, timeout=3, check=False,
        )
        if result.returncode == 0:
            return {"available": True}
    except (OSError, subprocess.SubprocessError):
        pass
    return {"available": False, "error": "System AT-SPI bindings unavailable; check python3-gi and gir1.2-atspi-2.0"}


def validate_query(payload: dict[str, Any]) -> None:
    if type(payload.get("pid")) is not int or not 0 < payload["pid"] <= 2147483647:
        raise ValueError("Accessibility PID must be a positive process ID from desktop windows")
    for field in ("name", "role"):
        value = payload.get(field)
        if value is not None and (not isinstance(value, str) or len(value) > 256 or "\0" in value):
            raise ValueError(f"Accessibility {field} must contain at most 256 characters")
    if payload.get("root") is not None:
        reference_path(payload["root"])


def reference_path(ref: Any) -> list[int]:
    """Decode a bounded location; its fingerprint is checked against the live tree."""
    if (not isinstance(ref, str) or len(ref) > 256
            or not re.fullmatch(r"(?:app|[0-9]+(?:\.[0-9]+){0,19}):[0-9a-f]{32}", ref)):
        raise ValueError("Invalid element reference; inspect again")
    location = ref.split(":", 1)[0]
    path = [] if location == "app" else [int(index) for index in location.split(".")]
    if any(index > 2147483647 for index in path):
        raise ValueError("Invalid element reference; inspect again")
    return path


def worker_request(payload: dict[str, Any]) -> dict[str, Any]:
    """Use the system interpreter and the supervisor's display/D-Bus environment."""
    validate_query(payload)
    try:
        result = subprocess.run(
            ["/usr/bin/python3", str(Path(__file__).resolve())],
            input=json.dumps(payload), capture_output=True, text=True, timeout=8, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Accessibility timed out; inspect the application before retrying an action") from exc
    if len(result.stdout.encode()) > MAX_OUTPUT:
        raise RuntimeError("Accessibility response exceeded its size limit")
    try:
        response = json.loads(result.stdout)
    except ValueError as exc:
        raise RuntimeError("Accessibility helper failed; check python3-gi and gir1.2-atspi-2.0") from exc
    if not isinstance(response, dict):
        raise RuntimeError("Invalid accessibility helper response")
    if result.returncode or "error" in response:
        raise RuntimeError(response.get("error", "Accessibility helper failed"))
    return response


def describe(node: Any, path: list[int], api: Any, parent_ref: str) -> dict[str, Any]:
    """Expose bounded text, excluding password controls and their descendants."""
    role = node.get_role_name()
    protected = node.get_role() == api.Role.PASSWORD_TEXT
    full_name = "" if protected else (node.get_name() or "")
    name = full_name[:256]
    states = node.get_state_set()
    present = [state for state in STATES if states.contains(getattr(api.StateType, state.upper()))]
    identity = [node.app.bus_name, node.path, path, role, full_name, parent_ref]
    location = ".".join(map(str, path)) or "app"
    ref = location + ":" + hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:32]
    actions = []
    if not protected and node.is_action():
        action = node.get_action_iface()
        actions = [action.get_action_name(i) for i in range(min(action.get_n_actions(), 16))]
    row = {"ref": ref, "path": path, "role": role, "name": name,
           "name_truncated": len(full_name) > 256,
           "states": present, "actions": actions, "protected": protected}
    if not protected and node.is_text():
        text = node.get_text_iface()
        count = text.get_character_count()
        row.update(text=api.Text.get_text(text, 0, min(count, 256)), text_truncated=count > 256)
    return row


def resolve_reference(application: Any, ref: str, api: Any, deadline: float) -> tuple[Any, list[int], str]:
    """Walk only the recorded ancestry and reject hidden, replaced or protected paths."""
    path = reference_path(ref)
    node, parent_ref = application, ""
    application.clear_cache()
    for depth in range(len(path) + 1):
        if time.monotonic() >= deadline:
            raise RuntimeError("Accessibility reference lookup timed out")
        if node is None or (depth and not node.get_state_set().contains(api.StateType.SHOWING)):
            raise ValueError("Element or root is unavailable; inspect again")
        row = describe(node, path[:depth], api, parent_ref)
        if depth == len(path):
            if row["ref"] != ref:
                raise ValueError("Element or root changed; inspect again")
            return node, path, parent_ref
        if row["protected"] or path[depth] >= node.get_child_count():
            raise ValueError("Element or root is unavailable; inspect again")
        node, parent_ref = node.get_child_at_index(path[depth]), row["ref"]
    raise ValueError("Invalid element reference")


def inspect_application(payload: dict[str, Any], api: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Scan showing controls; never treat a partial scan as proof of absence."""
    validate_query(payload)
    deadline = time.monotonic() + 5
    desktop = api.get_desktop(0)
    application = None
    for index in range(min(desktop.get_child_count(), 128)):
        if time.monotonic() >= deadline:
            raise RuntimeError("Accessibility application lookup timed out")
        try:
            candidate = desktop.get_child_at_index(index)
            if candidate and candidate.get_process_id() == payload["pid"]:
                application = candidate
                break
        except api.Error:
            continue  # A closing unrelated application must not block this one.
    if application is None:
        raise RuntimeError("Application is not available through AT-SPI; check PID and accessibility support")
    target_ref = payload.get("ref") if payload.get("operation", "inspect") != "inspect" else None
    root = target_ref or payload.get("root")
    start = resolve_reference(application, root, api, deadline) if root is not None else (application, [], "")
    pending = deque([start])
    elements: list[dict[str, Any]] = []
    targets = {}
    visited = 0
    size = 512
    truncated = False
    while pending and visited < MAX_NODES and time.monotonic() < deadline:
        node, path, parent_ref = pending.popleft()
        visited += 1
        if node is None:
            truncated = True
            continue
        try:
            if path and not node.get_state_set().contains(api.StateType.SHOWING):
                if visited == 1 and root is not None:
                    raise ValueError("Element or root is unavailable; inspect again")
                continue  # Closed menus and inactive tabs can dwarf the usable UI.
            row = describe(node, path, api, parent_ref)
            if visited == 1 and root is not None and row["ref"] != root:
                raise ValueError("Element or root changed; inspect again")
            matches = (row["ref"] == target_ref if target_ref is not None else
                       all(payload.get(field) is None or row[field] == payload[field] for field in ("name", "role"))
                       and (payload.get("name") is None or not row["name_truncated"]))
            if matches:
                cost = len(json.dumps(row).encode()) + 2
                if len(elements) >= MAX_RESULTS or size + cost > MAX_OUTPUT - 1024:
                    truncated = True
                    break
                elements.append(row)
                targets[row["ref"]] = (node, parent_ref)
                size += cost
                if target_ref is not None:
                    break  # Do not delay a known target behind unrelated UI queries.
            if not row["protected"]:
                count = node.get_child_count()
                room = MAX_NODES - visited - len(pending)
                if len(path) >= 20:
                    truncated |= count > 0
                else:
                    truncated |= count > room
                    for index in range(min(count, room)):
                        pending.append((node.get_child_at_index(index), [*path, index], row["ref"]))
        except api.Error:
            truncated = True  # Applications can remove nodes while being inspected.
    return {"pid": payload["pid"], "root": payload.get("root"), "elements": elements,
            "truncated": truncated or bool(pending)}, targets


def perform(payload: dict[str, Any], api: Any) -> dict[str, Any]:
    operation = payload.get("operation", "inspect")
    if operation == "windows":
        desktop = api.get_desktop(0)
        windows = []
        truncated = False
        deadline = time.monotonic() + 5
        count = desktop.get_child_count()
        for index in range(min(count, 64)):
            if time.monotonic() >= deadline:
                truncated = True
                break
            try:
                application = desktop.get_child_at_index(index)
                pid = application.get_process_id()
                # Return only showing top-level windows; custom canvases may
                # expose no AT-SPI window at all. Never claim complete coverage.
                for child in range(min(application.get_child_count(), 32)):
                    node = application.get_child_at_index(child)
                    row = describe(node, [child], api, describe(application, [], api, "")["ref"])
                    if row["protected"] or "showing" not in row["states"]:
                        continue
                    windows.append({"id": f"{pid}/{row['ref']}", "identity": row["ref"],
                        "pid": pid, "title": row["name"], "class": application.get_name()[:256],
                        "visible": True, "accessible_ref": row["ref"]})
                    if len(windows) >= 64:
                        truncated = True
                        break
            except api.Error:
                truncated = True
            if truncated or len(json.dumps(windows).encode()) > MAX_OUTPUT - 2048:
                truncated = True
                break
        return {"windows": windows, "active_window": None, "truncated": True,
                "coverage": "AT-SPI top-level windows only; custom applications may be absent"}
    if operation not in ("inspect", "invoke", "set-text", "focus"):
        raise ValueError("Unknown accessibility operation")
    if operation != "inspect":
        reference_path(payload.get("ref"))
    if operation == "set-text":
        text = payload.get("text")
        if not isinstance(text, str) or len(text) > 4096 or "\0" in text:
            raise ValueError("Replacement text must contain at most 4096 characters")
    result, targets = inspect_application(payload, api)
    if operation == "inspect":
        return result
    ref = payload.get("ref")
    if not isinstance(ref, str) or ref not in targets:
        raise ValueError("Element changed or is unavailable; inspect again")
    previous = next(item for item in result["elements"] if item["ref"] == ref)
    node, parent_ref = targets[ref]
    node.clear_cache()
    row = describe(node, previous["path"], api, parent_ref)
    if row["ref"] != ref:
        raise ValueError("Element changed before the action; inspect again")
    if row["protected"] or not {"enabled", "sensitive", "showing"}.issubset(row["states"]) or "defunct" in row["states"]:
        raise ValueError("Element is protected, hidden, disabled, or defunct; inspect again")
    if operation == "invoke":
        action = payload.get("action_name")
        if not isinstance(action, str) or row["actions"].count(action) != 1:
            raise ValueError("Choose one exact action name from the inspected element")
        accepted = node.get_action_iface().do_action(row["actions"].index(action))
    elif operation == "set-text":
        if "editable" not in row["states"] or not node.is_editable_text():
            raise ValueError("Element does not support editable text")
        accepted = node.get_editable_text_iface().set_text_contents(payload["text"])
    else:
        if not node.is_component():
            raise ValueError("Element does not support focus")
        accepted = node.get_component_iface().grab_focus()
    if not accepted:
        raise RuntimeError("Application rejected the accessibility action; inspect before retrying")
    return {"pid": payload["pid"], "ref": ref, "requested": operation}


def main() -> int:
    try:
        import gi
        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi, GLib

        Atspi.set_timeout(500, 500)
        # Keep the backend injectable for tests without importing GI on CLI hosts.
        Atspi.Error = GLib.Error
        payload = json.loads(sys.stdin.read(65537))
        if not isinstance(payload, dict):
            raise ValueError("Accessibility request must be an object")
        response = perform(payload, Atspi)
    except (ImportError, ValueError, RuntimeError) as exc:
        response = {"error": str(exc)}
    except Exception:
        response = {"error": "Accessibility query failed; inspect application support and responsiveness"}
    print(json.dumps(response))
    return int("error" in response)


if __name__ == "__main__":
    raise SystemExit(main())
