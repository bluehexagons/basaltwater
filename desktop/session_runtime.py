"""Private control channel for the one desktop owned by the setup account.

Sesman owns display creation and authentication. This process is its window
manager: it owns the desktop process group and accepts local same-UID requests.
It is deliberately not a boot service and never restarts a logged-out desktop.
"""

from __future__ import annotations

import contextlib
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import signal
import socket
import struct
import subprocess
import time
from typing import Any, Iterator

from desktop import accessibility, control_state
from lib.validation import validate_filesystem_path
from lib.validators import validate_username

CONFIG_PATH = Path("/etc/basaltwater/desktop.json")
SESSION_COMMANDS = {
    "xfce": ["xfce4-session"],
    "i3": ["i3"],
    "cinnamon": ["cinnamon-session"],
    "lxqt": ["startlxqt"],
}
MAX_MESSAGE = 65536
START_REQUEST_TIMEOUT = 30
START_READY_TIMEOUT = 30
BROWSER_CONTROL_TIMEOUT = 45


def configuration() -> dict[str, Any]:
    """Read the root-owned machine desktop declaration."""
    try:
        info = CONFIG_PATH.lstat()
    except FileNotFoundError as exc:
        raise RuntimeError("Shared desktop is not configured; rerun VM setup with --desktop xfce") from exc
    if not CONFIG_PATH.is_file() or CONFIG_PATH.is_symlink() or info.st_uid != 0 or info.st_mode & 0o022:
        raise RuntimeError("Unsafe desktop configuration")
    config = json.loads(CONFIG_PATH.read_text())
    if (not isinstance(config, dict) or config.get("version") != 1
            or not isinstance(config.get("desktop"), str)
            or config["desktop"] not in SESSION_COMMANDS):
        raise ValueError("Unsupported desktop configuration")
    owner = config.get("username", "")
    if not isinstance(owner, str) or not validate_username(owner) or pwd.getpwnam(owner).pw_uid != os.getuid() or os.getuid() == 0:
        raise PermissionError("Run desktop commands as the configured non-root desktop account")
    return config


def runtime_directory() -> Path:
    """Use logind's private runtime directory, never a caller-selected path."""
    parent = Path(f"/run/user/{os.getuid()}")
    info = parent.lstat()
    if parent.is_symlink() or not parent.is_dir() or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RuntimeError("A private logind runtime directory is required")
    path = parent / "basaltwater-desktop"
    path.mkdir(mode=0o700, exist_ok=True)
    info = path.lstat()
    if path.is_symlink() or not path.is_dir() or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RuntimeError("Unsafe desktop runtime directory")
    return path


@contextlib.contextmanager
def session_lock(name: str) -> Iterator[None]:
    fd = os.open(runtime_directory() / name, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        deadline = time.monotonic() + START_REQUEST_TIMEOUT + START_READY_TIMEOUT + 30
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Desktop operation is busy; retry after it finishes")
                time.sleep(0.1)
        yield
    finally:
        os.close(fd)


def receive(connection: socket.socket) -> dict[str, Any]:
    data = bytearray()
    while not data.endswith(b"\n"):
        part = connection.recv(4096)
        if not part:
            raise ValueError("Incomplete desktop request")
        data.extend(part)
        if len(data) > MAX_MESSAGE:
            raise ValueError("Desktop request is too large")
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError("Desktop request must be an object")
    return value


def request(payload: dict[str, Any]) -> dict[str, Any]:
    configuration()
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(30)
        connection.connect(str(runtime_directory() / "control.sock"))
        connection.sendall(json.dumps(payload).encode() + b"\n")
        result = receive(connection)
    if "error" in result:
        raise RuntimeError(result["error"])
    return result


def status(*, include_control: bool = True) -> dict[str, Any]:
    config = configuration()
    try:
        return request({"action": "status"})
    except (FileNotFoundError, ConnectionRefusedError):
        return {"state": "stopped", "desktop": config["desktop"], "username": config["username"],
                **(control_state.status() if include_control else {})}


def set_control(mode, *, seconds=control_state.DEFAULT_OVERRIDE_SECONDS, human_approved=False):
    configuration()
    try:
        return request({"action": "control", "mode": mode, "seconds": seconds, "human_approved": human_approved})
    except (FileNotFoundError, ConnectionRefusedError):
        return {"state": "stopped", **control_state.change(mode, seconds=seconds, human_approved=human_approved)}


def start() -> dict[str, Any]:
    """Authenticate to sesman with Unix peer credentials, never a saved password."""
    configuration()
    with session_lock("start.lock"):
        # The confined browser gateway has no writable home. A stopped
        # display needs no local permission read to start its supervisor.
        current = status(include_control=False)
        if current["state"] == "running":
            return current
        if current["state"] not in {"stopped", "starting"}:
            raise RuntimeError("Desktop is changing state; inspect status before retrying")
        startup_failed = False
        if current["state"] == "stopped":
            try:
                result = subprocess.run(
                    # Native media editors can require more than 720px height.
                    ["xrdp-sesrun", "-t", "Xorg", "-g", "1600x900", "-b", "32"],
                    capture_output=True, text=True, timeout=START_REQUEST_TIMEOUT, check=False,
                )
                startup_failed = result.returncode != 0
            except subprocess.TimeoutExpired:
                startup_failed = True
            # A human can win the login race before our control socket appears.
            # Even a failed/timed-out sesrun request may have created a session.
        deadline = time.monotonic() + START_READY_TIMEOUT
        while time.monotonic() < deadline:
            current = status(include_control=False)
            if current["state"] == "running":
                return current
            time.sleep(0.2)
        reason = "Desktop startup request failed" if startup_failed else "Desktop did not become ready"
        raise RuntimeError(f"{reason}; run 'basaltwater desktop status' and inspect xrdp-sesman and the per-session Xorg log")


def run_tool(argv: list[str], *, timeout: float = 8) -> str:
    result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode:
        raise RuntimeError(f"{argv[0]} failed: {result.stderr.strip()[:500]}")
    return result.stdout.strip()


def geometry() -> list[int]:
    output = run_tool(["xdotool", "getdisplaygeometry"], timeout=3)
    parts = output.split()
    if len(parts) != 2 or not all(part.isdecimal() for part in parts):
        raise RuntimeError("Could not determine desktop geometry")
    return [int(part) for part in parts]


def window_manager_ready() -> bool:
    """Require an EWMH window manager, not merely an accepting X server."""
    try:
        value = run_tool(["xprop", "-root", "_NET_SUPPORTING_WM_CHECK"], timeout=3)
    except (RuntimeError, OSError, subprocess.SubprocessError):
        return False
    return re.search(r"window id # 0x[1-9a-fA-F][0-9a-fA-F]*", value) is not None


def window_ids(property_name: str = "_NET_CLIENT_LIST_STACKING") -> list[str]:
    value = run_tool(["xprop", "-root", property_name], timeout=3)
    return [hex(int(value, 16)) for value in re.findall(r"0x[0-9a-fA-F]+", value)
            if int(value, 16)]


def window_details(window_id: str) -> dict[str, Any]:
    value = run_tool(["env", "LC_ALL=C.UTF-8", "xwininfo", "-id", window_id], timeout=3)
    fields = {}
    for name in ("Absolute upper-left X", "Absolute upper-left Y", "Width", "Height"):
        match = re.search(rf"^\s*{name}:\s*(-?\d+)\s*$", value, re.MULTILINE)
        if match is None:
            raise RuntimeError("Could not inspect application window geometry")
        fields[name] = int(match[1])
    title = re.search(r'^xwininfo: Window id: \S+ "(.*)"$', value, re.MULTILINE)
    properties = run_tool(["xprop", "-id", window_id, "_NET_WM_PID", "WM_CLASS", "_NET_WM_WINDOW_TYPE"], timeout=3)
    pid = re.search(r"_NET_WM_PID\(CARDINAL\) = (\d+)", properties)
    wm_class = re.search(r'^WM_CLASS\(STRING\) = (.*)$', properties, re.MULTILINE)
    name = title[1] if title else ""
    pid_value = int(pid[1]) if pid else None
    class_value = wm_class[1] if wm_class else ""
    identity = hashlib.sha256(json.dumps([window_id, pid_value, class_value, name]).encode()).hexdigest()[:24]
    return {"id": window_id, "title": name, "pid": pid_value, "class": class_value,
            "identity": identity,
            "system_window": any(kind in properties for kind in ("_NET_WM_WINDOW_TYPE_DESKTOP", "_NET_WM_WINDOW_TYPE_DOCK")),
            "origin": [fields["Absolute upper-left X"], fields["Absolute upper-left Y"]],
            "geometry": [fields["Width"], fields["Height"]],
            "visible": re.search(r"Map State:\s*IsViewable", value) is not None}


def normalize_window_id(selected: Any) -> str:
    if not isinstance(selected, str) or not re.fullmatch(r"(?:0x[0-9a-fA-F]+|[0-9]+)", selected):
        raise ValueError("Window ID must be decimal or hexadecimal")
    return hex(int(selected, 16 if selected.startswith("0x") else 10))


def change_window(payload: dict[str, Any]) -> dict[str, Any]:
    """Request normal WM operations, never destroy an X client or its process."""
    selected = normalize_window_id(payload.get("window"))
    if selected not in window_ids():
        raise ValueError("Window closed; list windows again")
    window = window_details(selected)
    if window["system_window"]:
        raise ValueError("Desktop and panel windows cannot be controlled as applications")
    if payload.get("identity") != window["identity"]:
        raise ValueError("Window identity changed; list windows again")
    operation = payload.get("operation")
    if operation == "focus":
        argv = ["wmctrl", "-ia", selected]
    elif operation == "close":
        argv = ["wmctrl", "-ic", selected]
    elif operation == "minimize":
        argv = ["xdotool", "windowminimize", selected]
    elif operation in ("maximize", "restore"):
        mode = "add" if operation == "maximize" else "remove"
        argv = ["wmctrl", "-ir", selected, "-b", f"{mode},maximized_vert,maximized_horz"]
    elif operation in ("move", "resize"):
        width, height = geometry()
        names = ("x", "y") if operation == "move" else ("width", "height")
        first, second = (payload.get(name) for name in names)
        lower = 0 if operation == "move" else 1
        if (type(first) is not int or type(second) is not int
                or not lower <= first <= width or not lower <= second <= height
                or (operation == "move" and (first == width or second == height))):
            raise ValueError("Window coordinates or dimensions are outside the desktop")
        placement = f"0,{first},{second},-1,-1" if operation == "move" else f"0,-1,-1,{first},{second}"
        argv = ["wmctrl", "-ir", selected, "-e", placement]
    else:
        raise ValueError("Unknown window operation")
    run_tool(argv)
    if operation == "restore":
        run_tool(["wmctrl", "-ia", selected])
    return {"requested": operation, "window": selected, "identity": window["identity"]}


def capture_window(payload: dict[str, Any]) -> dict[str, Any] | None:
    selected = payload.get("window")
    active = payload.get("active_window", False)
    if type(active) is not bool or (active and selected is not None):
        raise ValueError("Choose either a window ID or the active window")
    if active:
        ids = window_ids("_NET_ACTIVE_WINDOW")
        if not ids:
            raise ValueError("No active application window")
        selected = ids[0]
    if selected is None:
        return None
    selected = normalize_window_id(selected)
    if selected not in window_ids():
        raise ValueError("Window is no longer managed by this desktop; list windows again")
    window = window_details(selected)
    if not window["visible"]:
        raise ValueError("Window is minimized or hidden; make it visible before capture")
    return window


class DesktopSession:
    """Serialize bounded GUI operations and keep launches in the desktop group."""

    def __init__(self, config: dict[str, Any], process: subprocess.Popen[Any]):
        self.config = config
        self.process = process
        self.generation = secrets.token_hex(16)
        self.browser_pause: str | None = None
        self.browser_pause_until = 0.0
        self.browser_control_revision: str | None = None
        self.maintenance = False
        self.children: list[subprocess.Popen[Any]] = []
        self.launches: dict[str, subprocess.Popen[Any]] = {}
        self.elements: dict[str, dict[str, Any]] = {}
        self.control_revision = control_state.status()["control_revision"]

    def sync_control(self):
        current = control_state.status()
        if self.control_revision != current["control_revision"]:
            self.elements.clear()
            self.control_revision = current["control_revision"]
        return current

    def snapshot_status(self) -> dict[str, Any]:
        self.expire_browser_control()
        state = "stopping" if self.process.poll() is not None else "starting"
        size = None
        detail = None
        if state != "stopping":
            ready = window_manager_ready()
            try:
                size = geometry()
            except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
                detail = str(exc)
            if ready and size is not None:
                state = "running"
        result = {
            "state": state,
            "generation": self.generation, "desktop": self.config["desktop"],
            "username": self.config["username"], "display": os.environ["DISPLAY"],
            "geometry": size, **self.sync_control(),
            "browser_pause": self.browser_pause,
            "pid": self.process.pid,
            "control_active": False,
        }
        if detail:
            result["detail"] = detail
        return result

    def expire_browser_control(self) -> None:
        """Expire a browser connection without changing the human's durable choice."""
        if self.browser_pause is not None:
            current = control_state.status()
            if (time.monotonic() >= self.browser_pause_until
                    or current["agent_control"] != "human-override"
                    or current["control_revision"] != self.browser_control_revision):
                self.release_browser_control()

    def release_browser_control(self) -> None:
        self.browser_pause = None
        self.browser_pause_until = 0.0
        self.browser_control_revision = None

    def logout(self) -> dict[str, Any]:
        """Request normal logout, allowing applications to ask about unsaved work."""
        commands = {
            "xfce": ["xfce4-session-logout", "--logout"],
            "i3": ["i3-msg", "exit"],
            "cinnamon": ["cinnamon-session-quit", "--logout"],
            "lxqt": ["lxqt-leave", "--logout"],
        }
        self.children.append(subprocess.Popen(commands[self.config["desktop"]], process_group=self.process.pid))
        return {"logout_requested": True, "generation": self.generation}

    def handle(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.sync_control()
        self.expire_browser_control()
        action = payload.get("action")
        if action == "status":
            return self.snapshot_status()
        browser_actions = ("browser-take", "browser-refresh", "browser-release", "browser-logout")
        if action in browser_actions or (action == "control" and "generation" in payload):
            # Remote human handoffs must not act on a replacement desktop.
            # The local human control UI can still target the current one.
            if payload.get("generation") != self.generation:
                raise ValueError("Desktop session changed; inspect it again")
            # Cleanup is conditional on ownership and generation, even after
            # resizing or while the window manager is shutting down.
            if action == "browser-release":
                if self.browser_pause is not None and payload.get("pause_token") == self.browser_pause:
                    self.release_browser_control()
                return self.snapshot_status()
            snapshot = self.snapshot_status()
            if snapshot["state"] != "running":
                raise RuntimeError("Desktop is not ready for control handoff")
            if (action in browser_actions or "geometry" in payload) and payload.get("geometry") != snapshot["geometry"]:
                raise ValueError("Desktop geometry changed; inspect it again")
        if action in browser_actions:
            token = payload.get("pause_token")
            if action == "browser-take" and self.browser_pause is None and token is None:
                current = control_state.change("human-override", seconds=payload.get("override_seconds", 900),
                                               human_approved=payload.get("human_approved", False))
                self.browser_pause = secrets.token_hex(16)
                self.browser_control_revision = current["control_revision"]
                self.elements.clear()
            elif self.browser_pause is None or token != self.browser_pause:
                raise ValueError("Browser control expired or belongs to another connection")
            self.browser_pause_until = time.monotonic() + BROWSER_CONTROL_TIMEOUT
            if action == "browser-logout":
                return self.logout()
            return self.snapshot_status()
        if action == "control":
            control_state.change(payload.get("mode"), seconds=payload.get("seconds", 900),
                                 human_approved=payload.get("human_approved", False))
            self.release_browser_control()
            self.elements.clear()
            return self.snapshot_status()
        if payload.get("generation") != self.generation:
            raise ValueError("Desktop session changed; inspect it again")
        if self.process.poll() is not None:
            raise RuntimeError("Desktop is stopping")
        if action == "maintenance-logout":
            # Setup may request normal logout without changing durable permission.
            self.maintenance = True
            self.release_browser_control()
            self.elements.clear()
            return self.logout()
        if action == "inspect":
            result = accessibility.worker_request({**payload, "operation": "inspect"})
            for row in result["elements"]:
                self.elements[row["ref"]] = {"pid": result["pid"], "name": row["name"],
                    "role": row["role"], "observed_at": time.monotonic()}
            while len(self.elements) > 512:
                del self.elements[next(iter(self.elements))]
            return {**result, "generation": self.generation}
        if action == "windows":
            windows = []
            ids = window_ids()
            deadline = time.monotonic() + 10
            inspected = 0
            incomplete = False
            for window_id in ids[:64]:
                if time.monotonic() >= deadline:
                    break
                inspected += 1
                try:
                    windows.append(window_details(window_id))
                except (RuntimeError, OSError, subprocess.SubprocessError):
                    incomplete = True
                    continue  # A window may close while we enumerate it.
            active = window_ids("_NET_ACTIVE_WINDOW")
            return {"generation": self.generation, "windows": windows, "truncated": incomplete or len(ids) > inspected,
                    "active_window": active[0] if active else None}
        if action == "launch-status":
            launch = payload.get("launch")
            if not isinstance(launch, str) or launch not in self.launches:
                raise ValueError("Unknown application launch in this session")
            process = self.launches[launch]
            code = process.poll()
            return {"generation": self.generation, "launch": launch, "pid": process.pid,
                    "state": "running" if code is None else "exited", "returncode": code}
        if action == "screenshot":
            window = capture_window(payload)
            path = payload.get("output")
            if not isinstance(path, str):
                raise ValueError("Screenshot output path is required")
            validate_filesystem_path(path)
            if any(char in path for char in ("%", "$", "\\")):
                raise ValueError("Screenshot paths cannot contain scrot format characters: %, $, or backslash")
            output = Path(path)
            if not output.is_absolute() or output.suffix.lower() != ".png":
                raise ValueError("Screenshot output must be an absolute PNG path")
            desktop_geometry = geometry()
            if window:
                x, y = window["origin"]
                width, height = window["geometry"]
                if (x < 0 or y < 0 or x + width > desktop_geometry[0]
                        or y + height > desktop_geometry[1]):
                    raise ValueError(
                        "Window extends beyond the desktop; move or resize it fully "
                        "on screen before capture. If its minimum size is too large, "
                        "hide editor panels or use a larger desktop resolution."
                    )
            # Refuse overwrite and symlinks; create a private file before capture.
            fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            os.close(fd)
            try:
                options = ["--window", window["id"]] if window else []
                run_tool(["scrot", "--overwrite", *options, str(output)])
                with output.open("rb") as captured:
                    header = captured.read(24)
                if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
                    raise RuntimeError("Desktop capture did not produce a PNG")
                captured_geometry = list(struct.unpack(">II", header[16:24]))
                expected = desktop_geometry
                if window:
                    after = window_details(window["id"])
                    if after != window:
                        raise RuntimeError("Window changed during capture; take a new screenshot")
                    expected = window["geometry"]
                if captured_geometry != expected:
                    raise RuntimeError("Desktop or window resized during capture; take a new screenshot")
                snapshot = self.snapshot_status()
                if snapshot["geometry"] != desktop_geometry:
                    raise RuntimeError("Desktop resized during capture; take a new screenshot")
            except Exception:
                output.unlink(missing_ok=True)
                raise
            return {**snapshot, "image_geometry": captured_geometry,
                    "window": window, "output": str(output), "captured_at": datetime.now(timezone.utc).isoformat()}
        control_state.require_enabled(payload)
        if self.maintenance:
            raise RuntimeError("Desktop is logging out for setup; finish logout and restart the session")
        if action == "element":
            ref = payload.get("ref")
            observed = self.elements.get(ref) if isinstance(ref, str) else None
            if observed is None or time.monotonic() - observed["observed_at"] > 60:
                raise ValueError("Element reference expired; inspect again")
            self.elements.clear()  # Actions can change the entire accessible tree.
            result = accessibility.worker_request({**payload, **observed})
            return {**result, "generation": self.generation}
        self.elements.clear()
        if action == "window":
            return {**change_window(payload), "generation": self.generation}
        if action == "exec":
            argv = payload.get("argv")
            if not isinstance(argv, list) or not argv or not all(isinstance(v, str) and "\0" not in v for v in argv):
                raise ValueError("Application argv must be a nonempty string array")
            cwd = payload.get("cwd")
            if cwd is not None:
                if not isinstance(cwd, str) or not Path(cwd).is_absolute():
                    raise ValueError("Application working directory must be an absolute directory")
                validate_filesystem_path(cwd, must_exist=True)
                if not Path(cwd).is_dir():
                    raise ValueError("Application working directory must be a directory")
            process = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, process_group=self.process.pid)
            self.children.append(process)
            launch = secrets.token_hex(16)
            self.launches[launch] = process
            while len(self.launches) > 128:
                del self.launches[next(iter(self.launches))]
            return {"pid": process.pid, "generation": self.generation, "launch": launch}
        if action == "logout":
            return self.logout()
        if action == "input":
            if payload.get("geometry") != geometry():
                raise ValueError("Desktop geometry changed; capture a new screenshot")
            kind = payload.get("kind")
            if not isinstance(kind, str):
                raise ValueError("Unknown desktop input kind")
            if kind == "text":
                text = payload.get("text")
                if not isinstance(text, str) or len(text) > 1024 or "\0" in text:
                    raise ValueError("Text must contain at most 1024 characters")
                run_tool(["xdotool", "type", "--clearmodifiers", "--delay", "1", "--", text])
            elif kind == "key":
                key = payload.get("key")
                if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_+]{1,100}", key):
                    raise ValueError("Invalid key chord")
                run_tool(["xdotool", "key", "--clearmodifiers", key])
            elif kind in {"click", "move"}:
                width, height = geometry()
                x, y = payload.get("x"), payload.get("y")
                if type(x) is not int or type(y) is not int or not (0 <= x < width and 0 <= y < height):
                    raise ValueError("Pointer coordinates are outside the desktop")
                button = payload.get("button", 1)
                if kind == "click" and (type(button) is not int or button not in range(1, 8)):
                    raise ValueError("Button must be 1 through 7 (4–7 scroll)")
                # --sync waits for a position change and hangs on a no-op move.
                # Query on the same X connection to flush the move and verify
                # its destination before any click, without waiting for motion.
                location = run_tool(["xdotool", "mousemove", str(x), str(y),
                                     "getmouselocation", "--shell"])
                actual_x = re.search(r"^X=(\d+)$", location, re.MULTILINE)
                actual_y = re.search(r"^Y=(\d+)$", location, re.MULTILINE)
                if actual_x is None or actual_y is None or (int(actual_x[1]), int(actual_y[1])) != (x, y):
                    raise RuntimeError("Pointer did not reach the requested coordinates; inspect the desktop before retrying")
                if kind == "click":
                    control_state.require_enabled(payload)
                    run_tool(["xdotool", "click", str(button)])
            else:
                raise ValueError("Unknown desktop input kind")
            return {"generation": self.generation, "geometry": geometry()}
        raise ValueError("Unknown desktop action")


def serve() -> int:
    config = configuration()
    if not re.fullmatch(r":[0-9]+(?:\.0)?", os.environ.get("DISPLAY", "")):
        raise RuntimeError("The session supervisor must be started by sesman")
    os.chdir(pwd.getpwnam(config["username"]).pw_dir)
    # Xlib defaults to ~/.Xauthority, but clients such as PulseAudio pass an
    # explicitly empty authority filename unless the session declares it.
    os.environ["XAUTHORITY"] = os.environ.get("XAUTHORITY") or str(Path.home() / ".Xauthority")
    with session_lock("session.lock"):
        path = runtime_directory() / "control.sock"
        path.unlink(missing_ok=True)
        with socket.socket(socket.AF_UNIX) as server, contextlib.ExitStack() as cleanup:
            server.bind(str(path))
            cleanup.callback(path.unlink, missing_ok=True)
            os.chmod(path, 0o600)
            server.listen(8)
            server.settimeout(0.25)
            process = subprocess.Popen(SESSION_COMMANDS[config["desktop"]], process_group=0)
            session = DesktopSession(config, process)
            def stop(_signum: int, _frame: Any) -> None:
                raise KeyboardInterrupt
            signal.signal(signal.SIGTERM, stop)
            try:
                while process.poll() is None:
                    session.expire_browser_control()
                    session.children = [child for child in session.children if child.poll() is None]
                    try:
                        connection, _ = server.accept()
                    except TimeoutError:
                        continue
                    with connection:
                        connection.settimeout(10)
                        try:
                            _, uid, _ = struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                            if uid != os.getuid():
                                raise PermissionError("Desktop control requires the session owner's UID")
                            result = session.handle(receive(connection))
                        except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
                            result = {"error": str(exc)}
                        try:
                            connection.sendall(json.dumps(result).encode() + b"\n")
                        except OSError:
                            pass
            except KeyboardInterrupt:
                pass
            finally:
                path.unlink(missing_ok=True)
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            return 0


if __name__ == "__main__":
    raise SystemExit(serve())
