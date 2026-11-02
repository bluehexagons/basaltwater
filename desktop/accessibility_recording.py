"""Foreground, application-scoped AT-SPI timelines in private evidence bundles."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from desktop.accessibility import STATES
from desktop import native_session
from desktop.qa_artifacts import new_bundle
from lib.atomic_io import write_json_atomic
from lib.remote_utils import CommandTimeoutError
from lib.streamed_process import run_streamed

EVENT_TYPES = (
    "object:property-change:accessible-name", "object:property-change:accessible-value",
    "object:state-changed", "object:announcement", "focus",
)
LIMITATIONS = [
    "AT-SPI exposure only; absent events do not establish accessibility or speech success.",
    "No keystroke listeners, text-change subscriptions, text interface reads or audio capture.",
    "Names and announcements may contain application data; review before sharing.",
    "Protected, defunct, foreign and unverifiable ancestry events are omitted.",
    "Timestamps mark callback observation, not application emission; worker startup is included.",
]


def stamp() -> dict[str, Any]:
    return {"at": datetime.now(timezone.utc).isoformat(), "monotonic_ns": time.monotonic_ns()}


def process_identity(pid: int) -> dict[str, Any]:
    """Use Linux start ticks, UID and boot identity; never store command lines."""
    if type(pid) is not int or not 0 < pid <= 2147483647:
        raise ValueError("PID must be a positive Linux process ID")
    root = Path(f"/proc/{pid}")
    before = (root / "stat").read_text()
    fields = before[before.rfind(")") + 2:].split()
    if len(fields) < 20 or fields[0] in ("Z", "X"):
        raise ValueError("Target process is dead or unavailable")
    uid = root.stat().st_uid
    if uid != os.getuid():
        raise ValueError("Target process must belong to the current desktop user")
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    if not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", boot):
        raise ValueError("Linux boot identity is unavailable")
    return {"pid": pid, "start_ticks": int(fields[19]), "uid": uid, "boot_id": boot}


def require_identity(identity: dict[str, Any]) -> None:
    if process_identity(identity["pid"]) != identity:
        raise ValueError("Target process exited or PID was reused")


def validate_options(options: dict[str, Any]) -> None:
    for key, low, high in (("pid", 1, 2147483647), ("seconds", 1, 120),
                           ("max_events", 1, 5000), ("max_output_bytes", 16384, 4 * 1024 * 1024),
                           ("max_label_chars", 1, 512)):
        if type(options.get(key)) is not int or not low <= options[key] <= high:
            raise ValueError(f"{key} must be an integer from {low} to {high}")


def add_recording_parser(commands: argparse._SubParsersAction) -> None:
    parser = commands.add_parser("accessibility-record", help="Native: record a bounded AT-SPI timeline for one PID")
    parser.add_argument("--pid", required=True, type=int)
    parser.add_argument("--process-start", type=int, help="Expected Linux /proc start ticks; reject stale PID selection")
    parser.add_argument("--seconds", type=int, default=15, help="Foreground duration, 1–120 seconds")
    parser.add_argument("--max-events", type=int, default=500)
    parser.add_argument("--max-output-bytes", type=int, default=262144)
    parser.add_argument("--max-label-chars", type=int, default=256)
    parser.add_argument("--output", required=True, help="New private bundle directory under an existing parent")
    parser.add_argument("--json", action="store_true")


class RecordingLimit(RuntimeError):
    """Stop collection while retaining already accepted evidence."""


def run_recording_command(args: argparse.Namespace) -> dict[str, Any]:
    """Run independently of portal/control services; return a bounded summary."""
    if not getattr(args, "native", False):
        raise ValueError("Accessibility recording requires --native in the existing Linux desktop session")
    options = {key: getattr(args, key) for key in
               ("pid", "seconds", "max_events", "max_output_bytes", "max_label_chars")}
    validate_options(options)
    native_session.check_session()
    identity = process_identity(options["pid"])
    expected = getattr(args, "process_start", None)
    if expected is not None and (type(expected) is not int or expected != identity["start_ticks"]):
        raise ValueError("Target process start identity changed")
    output = new_bundle(args.output)
    artifact = output / "manifest.json"
    start = stamp()
    data: dict[str, Any] = {
        "schema_version": 1, "kind": "accessibility-recording",
        "started_at": start["at"], "started_monotonic_ns": start["monotonic_ns"],
        "finished_at": None, "finished_monotonic_ns": None,
        "clock": {"boot_id": identity["boot_id"], "monotonic_scope": "linux-boot"},
        "source": {"backend": "AT-SPI2", "process": identity}, "limits": options,
        "events": [], "truncated": False, "truncation_reasons": [], "errors": [],
        "limitations": LIMITATIONS, "status": "recording",
    }
    write_json_atomic(str(artifact), data, mode=0o600, indent=None)
    used = 8192  # Reserve bounded manifest metadata and final diagnostics.
    terminal = False
    wire_bytes = 0

    def receive(line: str) -> None:
        nonlocal used, terminal, wire_bytes
        wire_bytes += len(line.encode())
        if wire_bytes > options["max_output_bytes"] + 8192:
            raise RecordingLimit("worker-output-bytes")
        message = json.loads(line)
        if not isinstance(message, dict) or terminal:
            raise ValueError("Invalid AT-SPI worker protocol")
        if message.get("kind") == "done":
            terminal = True
            data["truncation_reasons"] = message["truncation_reasons"]
            data["errors"] = message["errors"]
            return
        if message.get("kind") != "event":
            raise ValueError("Invalid AT-SPI worker protocol")
        row = message["event"]
        require_identity(identity)
        if row.get("pid") != identity["pid"]:
            raise ValueError("AT-SPI worker returned a foreign PID")
        cost = len(json.dumps(row, ensure_ascii=True).encode()) + 2
        if used + cost > options["max_output_bytes"]:
            raise RecordingLimit("output-bytes")
        if len(data["events"]) >= options["max_events"]:
            raise RecordingLimit("max-events")
        used += cost
        data["events"].append(row)

    try:
        environment = dict(os.environ)
        for key in ("AT_SPI_BUS_ADDRESS", "GI_TYPELIB_PATH", "PYTHONPATH", "PYTHONHOME"):
            environment.pop(key, None)
        environment.update(DBUS_SESSION_BUS_ADDRESS=f"unix:path=/run/user/{os.getuid()}/bus", NO_AT_BRIDGE="0")
        code = run_streamed(
            ["/usr/bin/python3", str(Path(__file__).resolve())], timeout=options["seconds"] + 3,
            input_data=json.dumps({**options, "identity": identity}).encode(),
            on_output=receive, discard_stderr=True,
            env=environment,
        )
        if code or not terminal:
            data["errors"].append("AT-SPI worker exited without successful completion")
    except RecordingLimit as exc:
        data["truncation_reasons"].append(str(exc))
    except (subprocess.TimeoutExpired, CommandTimeoutError):
        data["errors"].append("AT-SPI worker timed out and was terminated")
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        data["errors"].append("AT-SPI worker failed; inspect dependencies, target identity and responsiveness")
    except KeyboardInterrupt:
        data["errors"].append("Recording interrupted; worker terminated")
    finally:
        finish = stamp()
        data.update(finished_at=finish["at"], finished_monotonic_ns=finish["monotonic_ns"],
                    truncated=bool(data["truncation_reasons"]),
                    status="failed" if data["errors"] else "finished")
        write_json_atomic(str(artifact), data, mode=0o600, indent=None)
    result = {"artifact": str(artifact), "event_count": len(data["events"]),
              "truncated": data["truncated"], "status": data["status"]}
    if data["errors"]:
        result["error"] = "; ".join(data["errors"])
    return result


def event_row(event: Any, application: Any, identity: dict[str, Any], api: Any,
              label_limit: int) -> dict[str, Any] | None:
    """Fail closed before fetching labels; ignore raw event payloads except announcements."""
    kind = event.type
    if not (kind in EVENT_TYPES or kind == "focus:" or kind.startswith("object:state-changed:")):
        return None
    require_identity(identity)
    node = event.source
    owner = node.get_application()
    if owner is None or owner != application or owner.get_process_id() != identity["pid"]:
        return None
    current = node
    for _ in range(32):
        if current is None:
            return None
        current.clear_cache()
        states = current.get_state_set()
        if states.contains(api.StateType.DEFUNCT) or current.get_role() == api.Role.PASSWORD_TEXT:
            return None
        if current == application:
            break
        current = current.get_parent()
    else:
        return None
    observed = stamp()
    row = {**observed, "pid": identity["pid"], "type": kind[:128]}
    clipped = False
    for key, value in (("name", node.get_name()), ("role", node.get_role_name())):
        value = value or ""
        row[key] = value[:label_limit]
        clipped |= len(value) > label_limit
    states = node.get_state_set()
    row["states"] = [name for name in STATES if states.contains(getattr(api.StateType, name.upper()))]
    if "defunct" in row["states"]:
        return None
    if kind == "object:announcement" and isinstance(event.any_data, str):
        row["announcement"] = event.any_data[:label_limit]
        clipped |= len(event.any_data) > label_limit
    if kind == "object:property-change:accessible-value" and node.is_value():
        value = node.get_value_iface().get_current_value()
        if isinstance(value, (int, float)) and math.isfinite(value):
            row["value"] = value
    row["labels_truncated"] = clipped
    require_identity(identity)
    return row


def record_worker(options: dict[str, Any], api: Any, glib: Any, emit: Any) -> None:
    validate_options(options)
    identity = options["identity"]
    require_identity(identity)
    deadline = time.monotonic() + options["seconds"]
    desktop = api.get_desktop(0)
    application = None
    for index in range(min(desktop.get_child_count(), 128)):
        if time.monotonic() >= deadline:
            raise RuntimeError("Application lookup timed out")
        try:
            candidate = desktop.get_child_at_index(index)
            if candidate and candidate.get_process_id() == identity["pid"]:
                application = candidate
                break
        except api.Error:
            continue
    if application is None:
        raise RuntimeError("Target application unavailable through AT-SPI")
    errors: list[str] = []
    reasons: list[str] = []
    count, used = 0, 8192
    loop = glib.MainLoop()

    def callback(event: Any, *_unused: Any) -> None:
        nonlocal count, used
        if errors or reasons:
            return
        if time.monotonic() >= deadline:
            loop.quit()
            return
        try:
            row = event_row(event, application, identity, api, options["max_label_chars"])
            if row is None:
                return
            cost = len(json.dumps(row).encode()) + 2
            if used + cost > options["max_output_bytes"]:
                reasons.append("output-bytes")
                loop.quit()
                return
            emit({"kind": "event", "event": row})
            count += 1
            used += cost
            if count >= options["max_events"]:
                reasons.append("max-events")
                loop.quit()
        except Exception:
            errors.append("Event query failed or target identity changed")
            loop.quit()

    def tick() -> bool:
        try:
            require_identity(identity)
        except (OSError, ValueError):
            errors.append("Target process exited or PID was reused")
            loop.quit()
            return False
        if time.monotonic() >= deadline:
            loop.quit()
            return False
        return True

    listener = api.EventListener.new(callback, None)
    registered = []
    timer = None
    try:
        for kind in EVENT_TYPES:
            if not listener.register_with_app(kind, None, application):
                raise RuntimeError("Application-scoped event registration failed")
            registered.append(kind)
        timer = glib.timeout_add(100, tick)
        loop.run()
    finally:
        if timer is not None:
            # The timer may already have removed itself on normal completion.
            source = glib.MainContext.default().find_source_by_id(timer)
            if source is not None:
                source.destroy()
        for kind in registered:
            listener.deregister(kind)
    emit({"kind": "done", "errors": errors, "truncation_reasons": reasons})


def main() -> int:
    def emit(message: dict[str, Any]) -> None:
        print(json.dumps(message), flush=True)
    try:
        import gi
        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi, GLib
        Atspi.set_timeout(500, 500)
        Atspi.Error = GLib.Error
        options = json.loads(sys.stdin.read(8193))
        record_worker(options, Atspi, GLib, emit)
    except Exception:
        emit({"kind": "done", "errors": ["AT-SPI recording failed; check system python-gobject, at-spi2-core and target responsiveness"],
              "truncation_reasons": []})
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
