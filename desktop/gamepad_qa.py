"""Experimental Linux QA pad: optional evdev, private socket, bounded input.

Run with ``python -m desktop.gamepad_qa --help``. This is deliberately separate
from the portal helper: its receipts do not claim portal-delivered input.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import select
import signal
import socket
import stat
import struct
import time
import uuid

from desktop import control_state, native_session
from desktop.qa_artifacts import new_bundle, stamp
from lib.atomic_io import read_json_file, write_json_atomic
from lib.validation import validate_filesystem_path

BUTTONS = {
    "south": 304, "east": 305, "north": 307, "west": 308,
    "left-shoulder": 310, "right-shoulder": 311,
    "select": 314, "start": 315, "guide": 316,
    "left-stick": 317, "right-stick": 318,
}
AXES = {
    "left-x": (0, -32768, 32767), "left-y": (1, -32768, 32767),
    "left-trigger": (2, 0, 255), "right-x": (3, -32768, 32767),
    "right-y": (4, -32768, 32767), "right-trigger": (5, 0, 255),
    "dpad-x": (16, -1, 1), "dpad-y": (17, -1, 1),
}
MAX_MESSAGE = 16384


def helper_status() -> dict:
    return native_session.request({"action": "status", "deadline": time.monotonic() + .25})


def enabled(current: dict) -> None:
    if current.get("state") != "running" or current.get("helper_running") is not True:
        raise RuntimeError("A running native helper is required")
    if current.get("agent_control") != "enabled":
        raise RuntimeError("Agent control is blocked")
    control_state.require_enabled(current)


def validate_action(payload: dict) -> list[tuple[int, int, int]]:
    if type(payload.get("hold_ms")) is not int or not 0 <= payload["hold_ms"] <= 5000:
        raise ValueError("hold_ms must be an integer from 0 to 5000")
    buttons, axes = payload.get("buttons", []), payload.get("axes", {})
    if (not isinstance(buttons, list) or len(buttons) > len(BUTTONS)
            or any(not isinstance(name, str) or name not in BUTTONS for name in buttons)
            or len(set(buttons)) != len(buttons)):
        raise ValueError("Unknown or repeated button")
    if not isinstance(axes, dict) or any(name not in AXES for name in axes):
        raise ValueError("Unknown axis")
    if not buttons and not axes:
        raise ValueError("At least one button or axis is required")
    events = [(1, BUTTONS[name], 1) for name in buttons]
    for name, value in axes.items():
        code, low, high = AXES[name]
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f"{name} must be an integer from {low} to {high}")
        events.append((3, code, value))
    return events


def validate_observation(observation: dict, current: dict, used: set[str]) -> str:
    enabled(current)
    for key in ("generation", "control_revision", "observation_id", "geometry"):
        if not current.get(key) or observation.get(key) != current[key]:
            raise ValueError(f"Observation has stale or missing {key}")
    identity = current["observation_id"]
    if not current.get("capture_freshness", {}).get("fresh") or identity in used:
        raise ValueError("A fresh unused inspected capture is required")
    return identity


class Pad:
    def __init__(self) -> None:
        # Optional system package; importing this module and offline tests do not
        # need evdev, uinput access or a graphical session.
        from evdev import AbsInfo, UInput

        capabilities = {1: list(BUTTONS.values()), 3: [
            (code, AbsInfo(0, low, high, 0, 4096 if low == -32768 else 0, 0))
            for code, low, high in AXES.values()
        ]}
        self.device = UInput(capabilities, name="Basaltwater QA Virtual Gamepad",
                             vendor=0x045e, product=0x028e, version=0x0114, bustype=3)

    def write(self, events: list[tuple[int, int, int]]) -> None:
        for kind, code, value in events:
            self.device.write(kind, code, value)
        self.device.syn()

    def neutral(self) -> None:
        self.write([(1, code, 0) for code in BUTTONS.values()]
                   + [(3, axis[0], 0) for axis in AXES.values()])

    def close(self) -> None:
        try:
            self.neutral()
        finally:
            self.device.close()


class Bridge:
    def __init__(self, pad: Pad, folder: Path, lifetime: int, initial: dict) -> None:
        self.pad, self.folder = pad, folder
        self.until = time.monotonic() + lifetime
        self.initial = initial
        self.used: set[str] = set()
        self.stopping = False
        self.checked = 0.0

    def guard(self) -> None:
        if self.stopping or time.monotonic() >= self.until:
            raise RuntimeError("Gamepad bridge stopped or expired")
        control_state.require_enabled(self.initial)
        if time.monotonic() - self.checked >= .2:
            current = helper_status()
            enabled(current)
            if any(current.get(key) != self.initial.get(key)
                   for key in ("generation", "control_revision", "geometry")):
                raise RuntimeError("Native session changed; start a new bridge")
            self.checked = time.monotonic()

    def press(self, payload: dict, connection: socket.socket) -> dict:
        events = validate_action(payload)
        observation = payload.get("observation")
        if not isinstance(observation, dict):
            raise ValueError("An inspected screenshot receipt is required")
        self.guard()
        identity = validate_observation(observation, helper_status(), self.used)
        self.used.add(identity)  # Never replay an uncertain action on this capture.
        receipt = {"schema_version": 1, "kind": "linux-uinput-qa", "id": uuid.uuid4().hex,
                   "requested": stamp(), "observation": observation, "events": events,
                   "hold_ms": payload["hold_ms"], "delivery": "not-sent", "neutralized": False}
        path = self.folder / f"{receipt['id']}.json"
        try:
            write_json_atomic(str(path), receipt, mode=0o600)
            self.guard()
            receipt["delivery"] = "unknown"  # A partial write must remain uncertain.
            write_json_atomic(str(path), receipt, mode=0o600)
            self.pad.write(events)
            receipt["dispatched"] = stamp()
            write_json_atomic(str(path), receipt, mode=0o600)
            deadline = time.monotonic() + payload["hold_ms"] / 1000
            while time.monotonic() < deadline:
                self.guard()
                if select.select([connection], [], [], 0)[0] and not connection.recv(1, socket.MSG_PEEK):
                    raise RuntimeError("Gamepad client disconnected")
                time.sleep(min(.02, max(0, deadline - time.monotonic())))
            receipt["delivery"] = "completed"
        except Exception as exc:
            self.stopping = True
            receipt["error"] = str(exc)
        finally:
            try:
                self.pad.neutral()
                receipt["neutralized"] = True
                receipt["released"] = stamp()
            except Exception as exc:
                self.stopping = True
                receipt["release_error"] = str(exc)
            write_json_atomic(str(path), receipt, mode=0o600)
        return {"artifact": str(path), **receipt}


def receive(connection: socket.socket) -> dict:
    data = bytearray()
    while not data.endswith(b"\n"):
        chunk = connection.recv(4096)
        if not chunk:
            raise ValueError("Incomplete request")
        data.extend(chunk)
        if len(data) > MAX_MESSAGE:
            raise ValueError("Request too large")
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError("Expected an object")
    return value


def socket_path() -> Path:
    return native_session.runtime_directory() / "gamepad-qa.sock"


def serve(output: str, lifetime: int) -> dict:
    if not 60 <= lifetime <= 1800:
        raise ValueError("Lifetime must be 60–1800 seconds")
    current = helper_status()
    enabled(current)
    folder = new_bundle(output)
    path = socket_path()
    descriptor = os.open(path.with_suffix(".lock"), os.O_CREAT | os.O_NOFOLLOW | os.O_RDWR, 0o600)
    pad = None
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if path.exists():
            info = path.lstat()
            if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
                raise RuntimeError("Unsafe stale pad socket")
            path.unlink()
        pad = Pad()
        pad.neutral()
        bridge = Bridge(pad, folder, lifetime, current)
        previous = {}
        def stop(_signum, _frame):
            bridge.stopping = True
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous[signum] = signal.signal(signum, stop)
        try:
            with socket.socket(socket.AF_UNIX) as server:
                server.bind(str(path))
                path.chmod(0o600)
                server.listen(1)
                server.settimeout(.05)
                write_json_atomic(str(folder / "device.json"), {
                    "created": stamp(), "pid": os.getpid(), "device": str(pad.device.device),
                    "name": pad.device.name, "mapping": "Linux Xbox 360 USB", "experimental": True,
                    "lifetime_seconds": lifetime, "generation": current["generation"],
                }, mode=0o600)
                while not bridge.stopping:
                    bridge.guard()
                    try:
                        connection, _ = server.accept()
                    except socket.timeout:
                        continue
                    with connection:
                        connection.settimeout(.25)
                        _, uid, _ = struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                        if uid != os.getuid():
                            continue
                        action = None
                        try:
                            payload = receive(connection)
                            action = payload.get("action")
                            if action == "stop":
                                bridge.stopping = True
                                result = {"stopping": True}
                            elif action == "status":
                                result = {"running": True, "pid": os.getpid(), "output": str(folder),
                                          "remaining_seconds": max(0, bridge.until - time.monotonic())}
                            elif action == "press":
                                result = bridge.press(payload, connection)
                            else:
                                raise ValueError("Unknown gamepad action")
                        except Exception as exc:
                            # Persistence/release failure after dispatch must
                            # never be mislabeled as a rejected input.
                            result = {"error": str(exc), "delivery": "unknown" if action == "press" else "not-sent"}
                            if action == "press":
                                bridge.stopping = True
                        try:
                            connection.sendall(json.dumps(result).encode() + b"\n")
                        except OSError:
                            bridge.stopping = True
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)
    finally:
        # Closing uinput also removes the owned device after process death. No
        # other input device, user control setting or mapping is changed.
        if pad is not None:
            try:
                pad.close()
            finally:
                path.unlink(missing_ok=True)
        os.close(descriptor)
        write_json_atomic(str(folder / "closed.json"), {"closed": stamp()}, mode=0o600)
    return {"closed": True, "output": str(folder)}


def call(payload: dict) -> dict:
    path = socket_path()
    info = path.lstat()
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RuntimeError("Unsafe pad socket")
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(7)
        connection.connect(str(path))
        _, uid, _ = struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        if uid != os.getuid():
            raise PermissionError("Pad server belongs to another account")
        connection.sendall(json.dumps(payload).encode() + b"\n")
        return receive(connection)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    server = commands.add_parser("serve")
    server.add_argument("--output", required=True)
    server.add_argument("--lifetime", type=int, default=900)
    commands.add_parser("status")
    commands.add_parser("stop")
    press = commands.add_parser("press")
    press.add_argument("--observation", required=True, help="Private native screenshot JSON receipt")
    press.add_argument("--button", action="append", choices=BUTTONS, default=[])
    press.add_argument("--axis", action="append", default=[], metavar="NAME=INTEGER")
    press.add_argument("--hold-ms", type=int, default=120)
    args = parser.parse_args()
    try:
        if args.command == "serve":
            result = serve(args.output, args.lifetime)
        elif args.command == "press":
            validate_filesystem_path(args.observation, must_exist=True)
            axes = dict(item.split("=", 1) for item in args.axis)
            payload = {"action": "press", "hold_ms": args.hold_ms, "buttons": args.button,
                       "axes": {name: int(value) for name, value in axes.items()},
                       "observation": read_json_file(args.observation, max_bytes=MAX_MESSAGE)}
            validate_action(payload)
            result = call(payload)
        else:
            result = call({"action": args.command})
        print(json.dumps(result, indent=2))
        return int("error" in result or result.get("delivery") == "unknown" or result.get("release_error") is not None)
    except (OSError, ValueError, RuntimeError) as exc:
        print(json.dumps({"error": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
