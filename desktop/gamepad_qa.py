"""Linux virtual controller: optional evdev, private socket, bounded input.

Use ``basaltw desktop [--native] gamepad --help``. Device events are delivered
through uinput; the selected desktop supervisor supplies observation and control
guards. Receipts do not claim portal-delivered input or physical hardware tests.
"""

from __future__ import annotations

import argparse
import fcntl
import importlib.util
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

from desktop import control_state, native_session, session_runtime, team_lease
from desktop.qa_artifacts import new_bundle, stamp
from lib.atomic_io import read_json_file, write_json_atomic
from lib.validation import validate_filesystem_path

BUTTONS = {
    # The Xbox mapping follows xpad's historical BTN_X/BTN_Y aliases. Linux's
    # BTN_NORTH/BTN_WEST symbol names do not describe these physical positions.
    "south": 304, "east": 305, "north": 308, "west": 307,
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
MAX_LIFETIME_SECONDS = 3 * 60 * 60


class InputRejected(ValueError):
    """Invalid input/capture detected before any device event was dispatched."""


class LifetimeExpired(RuntimeError):
    """Normal idle expiry; an interrupted input still has uncertain delivery."""


def backend_name(native: bool) -> str:
    return "cachyos-native" if native else "debian-desktop"


def helper_status(native: bool = True) -> dict:
    backend = native_session if native else session_runtime
    return backend.request({"action": "status", "deadline": time.monotonic() + .25})


def account_guard() -> None:
    if os.getuid() == 0 or os.geteuid() != os.getuid():
        raise RuntimeError("Run the gamepad as the existing desktop account, without sudo")


def enabled(current: dict, native: bool = True) -> None:
    account_guard()
    if current.get("state") != "running" or (native and current.get("helper_running") is not True):
        raise RuntimeError(f"A running {backend_name(native)} supervisor is required")
    if current.get("agent_control") != "enabled":
        raise RuntimeError("Agent control is blocked")
    if current.get("browser_pause"):
        raise RuntimeError("Desktop input is reserved by a browser controller")
    control_state.require_enabled(current)


def doctor(native: bool) -> dict:
    """Observe prerequisites without opening uinput or changing the machine."""
    dependency = importlib.util.find_spec("evdev") is not None
    path = Path("/dev/uinput")
    try:
        character_device = stat.S_ISCHR(path.stat().st_mode)
    except OSError:
        character_device = False
    access = character_device and os.access(path, os.R_OK | os.W_OK)
    try:
        current = helper_status(native)
        enabled(current, native)
        session = {"ready": True, "generation": current.get("generation"),
                   "control_revision": current.get("control_revision")}
    except (FileNotFoundError, ConnectionRefusedError):
        command = "basaltw desktop --native status" if native else "basaltw desktop status"
        session = {"ready": False, "error": f"Desktop supervisor unavailable. Check {command} before starting a gamepad."}
    except (OSError, ValueError, RuntimeError) as exc:
        session = {"ready": False, "error": str(exc)}
    return {"ok": dependency and access and session["ready"], "backend": backend_name(native),
            "prerequisites_only": True, "evdev_available": dependency,
            "uinput": {"path": str(path), "character_device": character_device, "read_write": access},
            "session": session, "device_creation": "unverified", "application_mapping": "unverified",
            "setup_guide": "docs/workstations/gamepad-qa.md",
            "dependency_package": "python-evdev" if native else "python3-evdev"}


def validate_action(payload: dict) -> list[tuple[int, int, int]]:
    if type(payload.get("hold_ms")) is not int or not 0 <= payload["hold_ms"] <= 5000:
        raise InputRejected("hold_ms must be an integer from 0 to 5000")
    buttons, axes = payload.get("buttons", []), payload.get("axes", {})
    if (not isinstance(buttons, list) or len(buttons) > len(BUTTONS)
            or any(not isinstance(name, str) or name not in BUTTONS for name in buttons)
            or len(set(buttons)) != len(buttons)):
        raise InputRejected("Unknown or repeated button")
    if not isinstance(axes, dict) or any(name not in AXES for name in axes):
        raise InputRejected("Unknown axis")
    if not buttons and not axes:
        raise InputRejected("At least one button or axis is required")
    events = [(1, BUTTONS[name], 1) for name in buttons]
    for name, value in axes.items():
        code, low, high = AXES[name]
        if type(value) is not int or not low <= value <= high:
            raise InputRejected(f"{name} must be an integer from {low} to {high}")
        events.append((3, code, value))
    return events


def validate_observation(observation: dict, current: dict, used: set[str], native: bool = True) -> str:
    enabled(current, native)
    for key in ("generation", "control_revision", "observation_id", "geometry"):
        if not current.get(key) or observation.get(key) != current[key]:
            raise InputRejected(f"Observation has stale or missing {key}")
    identity = current["observation_id"]
    if not current.get("capture_freshness", {}).get("fresh") or identity in used:
        raise InputRejected("A fresh unused inspected capture is required")
    return identity


class Pad:
    def __init__(self) -> None:
        # Optional system package; importing this module and offline tests do not
        # need evdev, uinput access or a graphical session.
        try:
            from evdev import AbsInfo, UInput
        except ImportError as exc:
            raise RuntimeError("evdev is missing in this Python interpreter; run gamepad doctor and follow its setup guide") from exc

        capabilities = {1: list(BUTTONS.values()), 3: [
            (code, AbsInfo(0, low, high, 0, 4096 if low == -32768 else 0, 0))
            for code, low, high in AXES.values()
        ]}
        self.device = UInput(capabilities, name="Basaltwater QA Virtual Gamepad",
                             vendor=0x045e, product=0x028e, version=0x0114, bustype=3)
        if self.device.device is None:
            self.device.close()
            raise RuntimeError("Created gamepad event device is unreadable; check the named-device udev rule and account groups")

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
    def __init__(self, pad: Pad, folder: Path, lifetime: int, initial: dict, native: bool = True) -> None:
        self.pad, self.folder = pad, folder
        self.until = time.monotonic() + lifetime
        self.initial = initial
        self.native = native
        self.used: set[str] = set()
        self.stopping = False
        self.failed = False
        self.stop_reason = "stop-requested"
        self.checked = 0.0

    def validate_session(self, current: dict) -> None:
        enabled(current, self.native)
        if any(current.get(key) != self.initial.get(key)
               for key in ("generation", "control_revision", "geometry")):
            raise RuntimeError("Desktop session changed; start a new bridge")

    def guard(self) -> None:
        if self.stopping:
            raise RuntimeError("Gamepad bridge stopped")
        if time.monotonic() >= self.until:
            self.stop_reason = "lifetime-expired"
            raise LifetimeExpired("Gamepad bridge expired")
        control_state.require_enabled(self.initial)
        if time.monotonic() - self.checked >= .2:
            current = helper_status(self.native)
            self.validate_session(current)
            self.checked = time.monotonic()

    def press(self, payload: dict, connection: socket.socket) -> dict:
        events = validate_action(payload)
        observation = payload.get("observation")
        if not isinstance(observation, dict):
            raise InputRejected("An inspected screenshot receipt is required")
        self.guard()
        current = helper_status(self.native)
        self.validate_session(current)
        identity = validate_observation(observation, current, self.used, self.native)
        self.used.add(identity)  # Never replay an uncertain action on this capture.
        receipt = {"schema_version": 1, "kind": "linux-uinput-qa", "id": uuid.uuid4().hex,
                   "backend": backend_name(self.native),
                   "requested": stamp(), "observation": observation, "events": events,
                   "buttons": payload.get("buttons", []), "axes": payload.get("axes", {}),
                   "hold_ms": payload["hold_ms"], "delivery": "not-sent", "neutralized": False}
        if "_team_lease_context" in payload:
            receipt["team_lease"] = payload["_team_lease_context"]
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
            self.failed = True
            if self.stop_reason != "lifetime-expired":
                self.stop_reason = "input-failed"
            receipt["error"] = str(exc)
        finally:
            try:
                self.pad.neutral()
                receipt["neutralized"] = True
                receipt["released"] = stamp()
            except Exception as exc:
                self.stopping = True
                self.failed = True
                self.stop_reason = "release-failed"
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
    # One owner across both desktop backends: switching a CLI flag must never
    # create a second controller or bypass a capture's single-use check.
    return session_runtime.runtime_directory() / "gamepad-qa.sock"


def serve(output: str, lifetime: int, native: bool = True) -> dict:
    if type(lifetime) is not int or not 60 <= lifetime <= MAX_LIFETIME_SECONDS:
        raise ValueError(f"Lifetime must be 60–{MAX_LIFETIME_SECONDS} seconds")
    current = helper_status(native)
    enabled(current, native)
    folder = new_bundle(output)
    path = socket_path()
    provenance = native_session.capture_worker_provenance()
    descriptor = os.open(path.with_suffix(".lock"), os.O_CREAT | os.O_NOFOLLOW | os.O_RDWR, 0o600)
    pad = None
    close_reason = "startup-failed"
    cleanup = {}
    succeeded = False
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if path.exists():
            info = path.lstat()
            if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
                raise RuntimeError("Unsafe stale pad socket")
            path.unlink()
        lease_folder = team_lease.resource_directory(native)
        startup = team_lease.wire_request({"action": "pad-start"})
        with team_lease.admission(lease_folder, startup, channel="pad", generation=current["generation"]):
            pad = Pad()
            pad.neutral()
        bridge = Bridge(pad, folder, lifetime, current, native)
        previous = {}
        def stop(_signum, _frame):
            bridge.stop_reason = signal.Signals(_signum).name.lower()
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
                    "name": pad.device.name, "mapping": "Linux Xbox 360 USB", "backend": backend_name(native),
                    "lifetime_seconds": lifetime, "generation": current["generation"],
                    "team_lease": team_lease.CAPABILITY,
                    "worker_provenance": provenance,
                }, mode=0o600)
                close_reason = "bridge-failed"
                while not bridge.stopping:
                    try:
                        bridge.guard()
                    except LifetimeExpired:
                        break
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
                            if payload.get("backend", "cachyos-native") != backend_name(native):
                                raise InputRejected("Gamepad belongs to the other desktop backend; inspect status with its original flag")
                            if action == "stop":
                                bridge.stopping = True
                                result = {"stopping": True}
                            elif action == "status":
                                result = {"running": True, "pid": os.getpid(), "output": str(folder),
                                          "backend": backend_name(native),
                                          "team_lease": team_lease.CAPABILITY,
                                          "worker_provenance": provenance,
                                          "remaining_seconds": max(0, bridge.until - time.monotonic())}
                            elif action == "press":
                                try:
                                    result = team_lease.dispatch(
                                        lease_folder, payload, current["generation"],
                                        lambda request: bridge.press(request, connection), channel="pad",
                                        rejection=(InputRejected,))
                                except (team_lease.LeaseRejected, team_lease.TeamBusy) as exc:
                                    raise InputRejected(str(exc)) from exc
                            else:
                                raise ValueError("Unknown gamepad action")
                        except InputRejected as exc:
                            # Rejection precedes device writes. Keep the neutral
                            # device connected so recapturing can recover without
                            # changing the game's controller/camera mode.
                            result = {"error": str(exc), "delivery": "not-sent", "rejected": True,
                                      "requested": stamp(), "observation": payload.get("observation")}
                            artifact = folder / f"{uuid.uuid4().hex}.json"
                            try:
                                write_json_atomic(str(artifact), result, mode=0o600)
                                result["artifact"] = str(artifact)
                            except OSError:
                                bridge.stopping = True
                                raise
                        except Exception as exc:
                            # Persistence/release failure after dispatch must
                            # never be mislabeled as a rejected input.
                            result = {"error": str(exc), "delivery": "unknown" if action == "press" else "not-sent"}
                            if action == "press":
                                bridge.failed = True
                                if not bridge.stopping and bridge.stop_reason != "lifetime-expired":
                                    bridge.stop_reason = "input-failed"
                                bridge.stopping = True
                        try:
                            connection.sendall(json.dumps(result).encode() + b"\n")
                        except OSError:
                            if not bridge.stopping:
                                bridge.failed = True
                                bridge.stop_reason = "client-disconnected"
                            bridge.stopping = True
                close_reason = bridge.stop_reason
                succeeded = not bridge.failed
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)
    finally:
        # Closing uinput also removes the owned device after process death. No
        # other input device, user control setting or mapping is changed.
        try:
            if pad is not None:
                try:
                    pad.close()
                    cleanup["neutralized"] = True
                except Exception as exc:
                    cleanup["cleanup_error"] = str(exc)
                    raise
                finally:
                    path.unlink(missing_ok=True)
        finally:
            os.close(descriptor)
            write_json_atomic(str(folder / "closed.json"), {
                "closed": stamp(), "reason": close_reason,
                "ok": succeeded and cleanup.get("neutralized") is True, **cleanup,
            }, mode=0o600)
    return {"closed": True, "ok": succeeded, "reason": close_reason, "output": str(folder)}


def call(payload: dict) -> dict:
    path = socket_path()
    try:
        info = path.lstat()
    except FileNotFoundError:
        if payload.get("action") in ("status", "stop"):
            return {"running": False, "backend": payload.get("backend"),
                    "already_stopped": payload["action"] == "stop"}
        raise
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RuntimeError("Unsafe pad socket")
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(7)
        connection.connect(str(path))
        _, uid, _ = struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        if uid != os.getuid():
            raise PermissionError("Pad server belongs to another account")
        connection.sendall(json.dumps(team_lease.wire_request(payload)).encode() + b"\n")
        return receive(connection)


def add_actions(parser: argparse.ArgumentParser) -> None:
    commands = parser.add_subparsers(dest="gamepad_command", required=True)
    server = commands.add_parser("serve")
    server.add_argument("--output", required=True, help="New private receipt directory outside Git")
    server.add_argument("--lifetime", type=int, default=900,
                        help=f"Foreground lifetime, 60–{MAX_LIFETIME_SECONDS} seconds; supervise with desktop develop exec")
    for name in ("status", "stop", "doctor"):
        commands.add_parser(name)
    press = commands.add_parser("press")
    press.add_argument("--observation", required=True, help="Inspected screenshot JSON receipt from the selected desktop backend")
    press.add_argument("--button", action="append", choices=BUTTONS, default=[],
                       help="Physical control. Use select for View/Back, start for Menu. Guide is the system button.")
    press.add_argument("--axis", action="append", default=[], metavar="NAME=INTEGER")
    press.add_argument("--hold-ms", type=int, default=120,
                       help="Hold all requested controls for 0–5000 ms before release (default: 120 ms)")
    for action in commands.choices.values():
        action.add_argument("--json", action="store_true")


def add_gamepad_parser(commands: argparse._SubParsersAction) -> None:
    add_actions(commands.add_parser("gamepad", help="Bounded Linux virtual controller for the selected desktop"))


def run_command(args: argparse.Namespace, *, native: bool) -> dict:
    action = args.gamepad_command
    if action == "doctor":
        return doctor(native)
    if action == "serve":
        return serve(args.output, args.lifetime, native)
    payload = {"action": action, "backend": backend_name(native)}
    if action == "press":
        validate_filesystem_path(args.observation, must_exist=True)
        axes = {}
        for item in args.axis:
            name, separator, value = item.partition("=")
            if not separator or name in axes:
                raise ValueError("Axes require unique NAME=INTEGER arguments")
            axes[name] = int(value)
        observation = read_json_file(args.observation, max_bytes=MAX_MESSAGE)
        if not isinstance(observation, dict):
            raise ValueError("Expected a screenshot JSON object")
        payload.update(hold_ms=args.hold_ms, buttons=args.button, axes=axes, observation=observation)
        validate_action(payload)
    result = call(payload)
    if result.get("delivery") == "unknown" or result.get("release_error") is not None:
        result.setdefault("error", "Gamepad delivery or release is uncertain; inspect before further input, never replay")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--debian", action="store_true", help="Source entry point only: use the Debian desktop supervisor")
    add_actions(parser)
    args = parser.parse_args()
    try:
        result = run_command(args, native=not args.debian)
        print(json.dumps(result, indent=2))
        return int("error" in result or result.get("ok") is False)
    except (OSError, ValueError, RuntimeError) as exc:
        print(json.dumps({"error": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
