"""Task-scoped CachyOS Wayland automation; never owns or restarts the desktop."""

from __future__ import annotations

from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import signal
import socket
import stat
import struct
import subprocess
import threading
import time

from desktop import accessibility, session_runtime
from lib.validation import validate_filesystem_path

MAX_MESSAGE = 65536
SESSION_SECONDS = 900


def runtime_directory(*, create=False):
    parent = Path(f"/run/user/{os.getuid()}")
    info = parent.lstat()
    if os.getuid() == 0 or parent.is_symlink() or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RuntimeError("A private runtime directory for the desktop user is required")
    path = parent / "basaltwater-wayland"
    if create:
        path.mkdir(mode=0o700, exist_ok=True)
    if path.exists() or path.is_symlink():
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise RuntimeError("Unsafe Wayland automation runtime directory")
    return path


def request(payload):
    path = runtime_directory() / "control.sock"
    info = path.lstat()
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RuntimeError("Unsafe Wayland control socket")
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(15)
        connection.connect(str(path))
        _, uid, _ = struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        if uid != os.getuid():
            raise PermissionError("Wayland helper belongs to another user")
        message = json.dumps({**payload, "deadline": time.monotonic() + 14}).encode() + b"\n"
        if len(message) > MAX_MESSAGE:
            raise ValueError("Wayland request exceeds its size limit")
        connection.sendall(message)
        result = session_runtime.receive(connection)
    if "error" in result:
        raise RuntimeError(result["error"])
    return result


def status():
    try:
        return request({"action": "status"})
    except (FileNotFoundError, ConnectionRefusedError):
        return {"state": "stopped", "desktop": "kde-wayland", "backend": "portal",
                "interactive_required": True, "detail": "Run desktop start and approve KDE's selected-monitor/input dialog"}


def check_session():
    from lib.cachyos import is_cachyos
    from lib.cachyos_doctor import _owned_socket

    uid = os.getuid()
    display = os.environ.get("WAYLAND_DISPLAY", "")
    kde = "KDE" in os.environ.get("XDG_CURRENT_DESKTOP", "").upper().split(":")
    if uid == 0 or os.geteuid() != uid:
        raise RuntimeError("Run native automation as the existing desktop user without sudo")
    if not is_cachyos() or not kde or os.environ.get("XDG_SESSION_TYPE") != "wayland" or not re.fullmatch(r"wayland-[0-9]{1,6}", display):
        raise RuntimeError("Run native automation in your CachyOS KDE Wayland session")
    parent = runtime_directory().parent
    if not _owned_socket(parent / display, uid) or not _owned_socket(parent / "bus", uid):
        raise RuntimeError("The invoking user's Wayland socket and session bus are required")
    if os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY"):
        raise RuntimeError("Start native automation locally from the graphical session")
    return display


def start():
    current = status()
    if current["state"] != "stopped":
        return current
    check_session()
    folder = runtime_directory(create=True)
    fd = os.open(folder / "start.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        current = status()
        if current["state"] != "stopped":
            return current
        env = dict(os.environ, DBUS_SESSION_BUS_ADDRESS=f"unix:path=/run/user/{os.getuid()}/bus",
                   XDG_RUNTIME_DIR=f"/run/user/{os.getuid()}")
        env.pop("AT_SPI_BUS_ADDRESS", None)
        log_fd = os.open(folder / "helper.log", os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        os.fchmod(log_fd, 0o600)
        with os.fdopen(log_fd, "w") as log:
            process = subprocess.Popen(["/usr/bin/python3", "-m", "desktop.native_session", "--serve"],
                cwd=str(Path(__file__).resolve().parents[1]), env=env, stdin=subprocess.DEVNULL,
                stdout=log, stderr=log, start_new_session=True)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            current = status()
            if current["state"] != "stopped":
                return current
            if process.poll() is not None:
                raise RuntimeError(f"Native helper failed; inspect {folder / 'helper.log'}")
            time.sleep(0.05)
        raise RuntimeError("Native helper start is pending; inspect desktop status before retrying")
    except BlockingIOError as exc:
        raise RuntimeError("A native start is already pending; inspect status") from exc
    finally:
        os.close(fd)


def doctor():
    try:
        check_session()
        current = status()
        result = subprocess.run(["/usr/bin/python3", "-c",
            "import gi; gi.require_version('Atspi','2.0'); gi.require_version('Gst','1.0'); "
            "gi.require_version('GstApp','1.0'); gi.require_version('GstVideo','1.0'); "
            "gi.require_version('Gtk','3.0'); "
            "from gi.repository import Atspi,Gst; import ctypes; ctypes.CDLL('libxkbcommon.so.0'); Gst.init(None); "
            "assert Gst.ElementFactory.find('pipewiresrc') and Gst.ElementFactory.find('videoconvert')"],
            capture_output=True, text=True, timeout=5, check=False)
        if result.returncode:
            raise RuntimeError("Install python-gobject, at-spi2-core, gstreamer, gst-plugins-base, gst-plugin-pipewire, gtk3 and libxkbcommon")
        return {"healthy": True, "checks": {"session": current},
                "unverified": ["portal consent", "capture/input", "application responsiveness"]}
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        return {"healthy": False, "suggestions": [str(exc)]}


def normalize_window_id(value):
    pid, reference = value.split("/", 1)
    accessibility.validate_query({"pid": int(pid)})
    accessibility.reference_path(reference)
    return value


def keysyms(chord):
    import ctypes
    library = ctypes.CDLL("libxkbcommon.so.0")
    library.xkb_keysym_from_name.argtypes = [ctypes.c_char_p, ctypes.c_int]
    library.xkb_keysym_from_name.restype = ctypes.c_uint32
    if not isinstance(chord, str) or not re.fullmatch(r"[A-Za-z0-9_+]{1,100}", chord):
        raise ValueError("Invalid key chord")
    aliases = {"ctrl": "Control_L", "alt": "Alt_L", "shift": "Shift_L", "super": "Super_L",
               "enter": "Return", "esc": "Escape", "space": "space"}
    keys = [library.xkb_keysym_from_name(aliases.get(part.lower(), part).encode(), 1) for part in chord.split("+")]
    if not 1 <= len(keys) <= 8 or not all(keys):
        raise ValueError("Unknown key symbol; use XKB names such as ctrl+s or Return")
    return keys


class NativeSession:
    def __init__(self, portal):
        self.portal = portal
        self.generation = secrets.token_hex(16)
        self.state = "awaiting-consent"
        self.detail = "Approve the KDE dialog for one monitor, keyboard and pointer"
        self.paused = False
        self.lease = None
        self.lease_until = 0
        self.until = time.monotonic() + SESSION_SECONDS
        self.geometry = None
        self.captured = 0
        self.elements = {}
        self.launches = {}
        self.lock = threading.RLock()
        self.stopping = threading.Event()

    def snapshot_status(self):
        return {"state": self.state, "generation": self.generation, "desktop": "kde-wayland",
                "backend": "portal", "origin": "portal", "paused": self.paused,
                "geometry": self.geometry, "interactive_required": self.state == "awaiting-consent",
                "expires_in": max(0, int(self.until - time.monotonic())),
                "control_active": self.lease is not None and time.monotonic() < self.lease_until,
                "detail": self.detail}

    def close(self):
        with self.lock:
            self.state = "stopped"
            self.paused = True
            self.lease = None
            self.elements.clear()
            self.stopping.set()

    def guard(self, payload):
        if time.monotonic() >= payload.get("deadline", float("inf")):
            raise RuntimeError("Native request expired; observe before retrying")
        if self.state != "running" or time.monotonic() >= self.until:
            raise RuntimeError("Native control is unavailable or expired; inspect desktop status")
        if payload.get("generation") != self.generation:
            raise ValueError("Native session changed; observe again")
        if self.paused or not self.lease or payload.get("lease") != self.lease or time.monotonic() >= self.lease_until:
            raise RuntimeError("Control is paused or its lease expired; observe before retrying")

    def handle(self, payload):
        with self.lock:
            action = payload.get("action")
            if action == "status":
                return self.snapshot_status()
            if action == "pause":
                self.paused = True
                self.lease = None
                self.elements.clear()
                return self.snapshot_status()
            if action == "resume":
                self.paused = False
                return self.snapshot_status()
            if action == "stop":
                self.close()
                return {"stopped": True, "desktop_preserved": True}
            if time.monotonic() >= payload.get("deadline", float("inf")):
                raise RuntimeError("Native request expired; observe before retrying")
            if payload.get("generation") != self.generation:
                raise ValueError("Native session changed; observe again")
            if self.state != "running" or time.monotonic() >= self.until:
                raise RuntimeError("Native control is not ready; inspect desktop status and the KDE permission dialog")
            if action == "release":
                if payload.get("lease") == self.lease:
                    self.lease = None
                return {"released": True}
            if action == "acquire":
                if self.paused:
                    raise RuntimeError("Human paused native control")
                if self.lease and time.monotonic() < self.lease_until:
                    raise RuntimeError("Native control is leased by another operation")
                self.lease = secrets.token_hex(16)
                self.lease_until = time.monotonic() + 30
                return {"generation": self.generation, "lease": self.lease, "expires_in": 30}
            if action == "inspect":
                pid = payload.get("pid")
                accessibility.validate_query(payload)
                if Path(f"/proc/{pid}").stat().st_uid != os.getuid():
                    raise ValueError("Inspect only this desktop user's application")
                result = accessibility.worker_request({**payload, "operation": "inspect"})
                for row in result["elements"]:
                    self.elements[row["ref"]] = {"pid": pid, "observed_at": time.monotonic()}
                while len(self.elements) > 512:
                    del self.elements[next(iter(self.elements))]
                return {**result, "generation": self.generation}
            if action == "windows":
                result = accessibility.worker_request({"pid": os.getpid(), "operation": "windows"})
                return {**result, "generation": self.generation}
            if action == "launch-status":
                process = self.launches.get(payload.get("launch"))
                if process is None:
                    raise ValueError("Unknown native launch")
                code = process.poll()
                return {"generation": self.generation, "pid": process.pid, "returncode": code,
                        "state": "running" if code is None else "exited"}
            if action == "screenshot":
                return self.capture(payload)
            self.guard(payload)
            self.elements = {ref: row for ref, row in self.elements.items() if time.monotonic() - row["observed_at"] <= 60}
            if action == "element":
                observed = self.elements.get(payload.get("ref"))
                if not observed:
                    raise ValueError("Element reference is stale; inspect again")
                try:
                    result = accessibility.worker_request({**payload, "pid": observed["pid"]})
                finally:
                    self.elements.clear()
                return {**result, "generation": self.generation}
            self.elements.clear()
            if action == "exec":
                argv, cwd = payload.get("argv"), payload.get("cwd")
                if not isinstance(argv, list) or not 1 <= len(argv) <= 100 or any(not isinstance(v, str) or len(v) > 4096 or "\0" in v for v in argv):
                    raise ValueError("Application argv must be a bounded nonempty string array")
                validate_filesystem_path(cwd, must_exist=True)
                if not Path(cwd).is_absolute() or not Path(cwd).is_dir():
                    raise ValueError("Application working directory must be an absolute directory")
                environment = dict(os.environ)
                # Agent harnesses commonly disable accessibility. Enable only
                # these task processes; never alter desktop-wide preferences.
                environment["NO_AT_BRIDGE"] = "0"
                modules = environment.get("GTK_MODULES", "").split(":")
                environment["GTK_MODULES"] = ":".join(dict.fromkeys([*(m for m in modules if m), "atk-bridge"]))
                environment["QT_LINUX_ACCESSIBILITY_ALWAYS_ON"] = "1"
                environment.pop("AT_SPI_BUS_ADDRESS", None)
                process = subprocess.Popen(argv, cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
                launch = secrets.token_hex(16)
                self.launches[launch] = process
                while len(self.launches) > 128:
                    del self.launches[next(iter(self.launches))]
                return {"generation": self.generation, "pid": process.pid, "launch": launch}
            if action == "input":
                return self.input(payload)
            raise ValueError("This operation is unavailable on KDE Wayland; use observed accessible controls or portal input")

    def capture(self, payload):
        if payload.get("window") or payload.get("active_window"):
            raise ValueError("Portal capture uses the consent-selected monitor; window capture is unavailable")
        output = payload.get("output")
        validate_filesystem_path(output)
        if not Path(output).is_absolute() or Path(output).suffix.lower() != ".png":
            raise ValueError("Capture requires an absolute new PNG path")
        fd = os.open(output, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            process = subprocess.run(["/usr/bin/python3", str(Path(__file__).with_name("portal_capture.py")),
                str(self.portal.fd), str(self.node), str(fd)], pass_fds=(self.portal.fd, fd),
                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=12, check=False)
            if len(process.stdout) > 4096:
                raise RuntimeError("Capture response exceeded its limit")
            result = json.loads(process.stdout)
            if process.returncode or "error" in result:
                raise RuntimeError(result.get("error", "Portal capture failed"))
            if self.stopping.is_set():
                raise RuntimeError("Portal control was revoked during capture")
            geometry = result.get("geometry")
            if not isinstance(geometry, list) or len(geometry) != 2 or any(type(n) is not int or not 0 < n <= 16384 for n in geometry) or geometry[0] * geometry[1] > 32 * 1024 * 1024:
                raise RuntimeError("Invalid capture geometry")
            header = os.pread(fd, 24, 0)
            if header[:16] != b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" or len(header) != 24 or list(struct.unpack(">II", header[16:])) != geometry:
                raise RuntimeError("Capture artifact does not match its reported PNG geometry")
            self.geometry = geometry
            self.captured = time.monotonic()
            return {**self.snapshot_status(), "output": output, "image_geometry": self.geometry,
                    "captured_at": datetime.now(timezone.utc).isoformat()}
        except Exception:
            os.unlink(output)
            raise
        finally:
            os.close(fd)

    def input(self, payload):
        if self.geometry is None or payload.get("geometry") != self.geometry or time.monotonic() - self.captured > 60:
            raise ValueError("Capture the selected monitor again before input; geometry must match a recent screenshot")
        kind = payload.get("kind")
        if kind in ("click", "move"):
            x, y = payload.get("x"), payload.get("y")
            if type(x) is not int or type(y) is not int or not (0 <= x < self.geometry[0] and 0 <= y < self.geometry[1]):
                raise ValueError("Pointer coordinates are outside the selected monitor")
            button = payload.get("button", 1)
            if kind == "click" and (type(button) is not int or button not in range(1, 8)):
                raise ValueError("Button must be 1 through 7")
            self.portal.notify("NotifyPointerMotionAbsolute", "udd", self.node,
                x * self.logical_size[0] / self.geometry[0], y * self.logical_size[1] / self.geometry[1])
            if kind == "click":
                if button >= 4:
                    axis, steps = {4:(0,-1), 5:(0,1), 6:(1,-1), 7:(1,1)}[button]
                    self.portal.notify("NotifyPointerAxisDiscrete", "ui", axis, steps)
                else:
                    code = {1:272, 2:274, 3:273}[button]
                    try:
                        self.portal.notify("NotifyPointerButton", "iu", code, 1)
                    finally:
                        try:
                            self.portal.notify("NotifyPointerButton", "iu", code, 0)
                        except Exception as exc:
                            self.close()
                            raise RuntimeError("Pointer release failed; native control stopped") from exc
        elif kind in ("key", "text"):
            if kind == "text":
                text = payload.get("text")
                if not isinstance(text, str) or len(text) > 1024 or "\0" in text:
                    raise ValueError("Text must contain at most 1024 characters without NUL")
                keys = [[{"\n":0xff0d, "\t":0xff09}.get(char, ord(char) if ord(char) < 256 else 0x01000000 | ord(char))] for char in text]
            else:
                keys = [keysyms(payload.get("key"))]
            for chord in keys:
                self.guard(payload)
                pressed = []
                try:
                    for key in chord:
                        pressed.append(key)
                        self.portal.notify("NotifyKeyboardKeysym", "iu", key, 1)
                finally:
                    release_error = None
                    for key in reversed(pressed):
                        try:
                            self.portal.notify("NotifyKeyboardKeysym", "iu", key, 0)
                        except Exception as exc:
                            release_error = exc
                    if release_error is not None:
                        self.close()
                        raise RuntimeError("Key release failed; native control stopped") from release_error
        else:
            raise ValueError("Unknown native input kind")
        return {"generation": self.generation, "geometry": self.geometry}


def serve():
    check_session()
    from desktop.portal import Portal
    from gi.repository import GLib

    folder = runtime_directory(create=True)
    lock_fd = os.open(folder / "session.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    session = NativeSession(None)
    portal = Portal(session.close)
    session.portal = portal
    path = folder / "control.sock"
    path.unlink(missing_ok=True)
    loop = GLib.MainLoop()

    def initialize():
        try:
            node, size = portal.start()
            with session.lock:
                if session.stopping.is_set():
                    return
                session.node, session.logical_size = node, size
                session.state, session.detail = "running", "User-approved selected-monitor capture and input"
        except Exception as exc:
            with session.lock:
                session.state, session.detail = "failed", str(exc)
            # Retain an actionable status briefly, then discard the session.
            session.stopping.wait(10)
            session.close()

    def connected(connection):
        with connection:
            connection.settimeout(15)
            try:
                _, uid, _ = struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                if uid != os.getuid():
                    raise PermissionError("Only the owning desktop user may control this session")
                payload = session_runtime.receive(connection)
                payload.setdefault("deadline", time.monotonic() + 14)
                result = session.handle(payload)
            except Exception as exc:
                result = {"error": str(exc)}
            try:
                connection.sendall(json.dumps(result).encode() + b"\n")
            except OSError:
                pass

    def accepting(server):
        workers = []
        while not session.stopping.is_set():
            workers = [worker for worker in workers if worker.is_alive()]
            try:
                connection, _ = server.accept()
            except TimeoutError:
                continue
            if len(workers) >= 8:
                connection.close()
                continue
            worker = threading.Thread(target=connected, args=(connection,), daemon=True)
            workers.append(worker)
            worker.start()

    def tick():
        from lib.cachyos_doctor import _owned_socket
        display = os.environ["WAYLAND_DISPLAY"]
        if time.monotonic() >= session.until or not _owned_socket(folder.parent / display, os.getuid()):
            session.close()
        if session.stopping.is_set():
            loop.quit()
            return False
        return True

    with socket.socket(socket.AF_UNIX) as server:
        server.bind(str(path))
        os.chmod(path, 0o600)
        server.listen(8)
        server.settimeout(.25)
        signal.signal(signal.SIGTERM, lambda *_: session.close())
        signal.signal(signal.SIGINT, lambda *_: session.close())
        threading.Thread(target=accepting, args=(server,), daemon=True).start()
        threading.Thread(target=initialize, daemon=True).start()
        GLib.timeout_add(250, tick)
        try:
            loop.run()
        finally:
            session.close()
            portal.close()
            path.unlink(missing_ok=True)
            os.close(lock_fd)
    return 0


if __name__ == "__main__":
    raise SystemExit(serve())
