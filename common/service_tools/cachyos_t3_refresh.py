"""Complete T3 setup outside the cgroup of any T3-owned setup terminal."""

from __future__ import annotations

from dataclasses import dataclass, field
import fcntl
import math
import os
from pathlib import Path
import re
import select
import signal
import stat
import subprocess
import sys
import time

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lib.atomic_io import read_json_file, write_json_atomic
from lib.validation import validate_filesystem_path, validate_positive_integer


APP_EXECUTABLE = "/opt/t3code-bin/t3code"
PROC = Path("/proc")
MAX_PROCESS_DATA = 4 * 1024 * 1024


@dataclass
class RunningDesktop:
    """A stable process handle and private launch state, never written to logs."""

    descriptor: int
    arguments: list[str]
    environment: dict[str, str]
    directory: str
    refresh_needed: bool = True
    child_descriptors: list[int] = field(default_factory=list)


def _process_data(path: Path) -> list[str]:
    with path.open("rb") as stream:
        content = stream.read(MAX_PROCESS_DATA + 1)
    if len(content) > MAX_PROCESS_DATA:
        raise RuntimeError("T3 desktop launch state exceeds the refresh limit")
    return [os.fsdecode(part) for part in content.rstrip(b"\0").split(b"\0") if part]


def _is_app(path: Path) -> bool:
    # A package update can unlink the old executable while it is still running.
    return (path.stat().st_uid == os.getuid()
            and os.readlink(path / "exe").removesuffix(" (deleted)") == APP_EXECUTABLE)


def _start_ticks(path: Path) -> int:
    # /proc starttime and CLOCK_BOOTTIME include time spent suspended. The
    # process name can contain spaces and parentheses, so split after its end.
    with (path / "stat").open("rb") as stream:
        fields = stream.read(8192).rpartition(b") ")[2].split()
    try:
        start_ticks = int(fields[19])
    except (IndexError, ValueError) as error:
        raise RuntimeError("Cannot read T3 desktop start time") from error
    if start_ticks <= 0:
        raise RuntimeError("Cannot read T3 desktop start time")
    return start_ticks


def _runtime_directory() -> Path:
    runtime = Path(f"/run/user/{os.getuid()}")
    info = runtime.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RuntimeError("T3 refresh requires an owned private user runtime directory")
    return runtime


def _relaunch_receipt_path() -> Path:
    path = _runtime_directory() / "basaltwater-t3-desktop-reopened.json"
    validate_filesystem_path(str(path))
    try:
        info = path.lstat()
    except FileNotFoundError:
        return path
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or info.st_nlink != 1 or info.st_mode & 0o077):
        raise RuntimeError("Unsafe T3 desktop relaunch receipt; inspect the user runtime directory")
    return path


def _reopened_after(path: Path, started_ticks: int, queued_before_ns: int) -> bool:
    receipt = _relaunch_receipt_path()
    try:
        data = read_json_file(str(receipt), max_bytes=4096)
    except FileNotFoundError:
        return False
    if (not isinstance(data, dict) or set(data) != {"pid", "started_ticks", "reopened_at_ns"}
            or any(type(value) is not int or value <= 0 for value in data.values())):
        raise RuntimeError("Invalid T3 desktop relaunch receipt; inspect the user runtime directory")
    return (data["pid"] == int(path.name) and data["started_ticks"] == started_ticks
            and data["reopened_at_ns"] > queued_before_ns)


def _record_relaunch() -> None:
    # exec preserves PID/starttime, while Electron can erase /proc/environ.
    # Bind the later timestamp to both identities so PID reuse cannot coalesce
    # a refresh for a different process. /run removes the receipt on reboot.
    data = {"pid": os.getpid(), "started_ticks": _start_ticks(PROC / str(os.getpid())),
            "reopened_at_ns": time.clock_gettime_ns(time.CLOCK_BOOTTIME)}
    write_json_atomic(str(_relaunch_receipt_path()), data, mode=0o600)


def _exec_desktop(arguments: list[str], environment: dict[str, str]) -> None:
    try:
        _record_relaunch()
    except (OSError, ValueError, RuntimeError):
        # A bookkeeping failure after graceful shutdown must not leave T3 shut.
        print("T3 relaunch receipt unavailable; overlapping refresh requests may reload again", file=sys.stderr, flush=True)
    os.execve(APP_EXECUTABLE, [APP_EXECUTABLE, *arguments], environment)


def _desktop_children(path: Path) -> list[int]:
    """Keep handles to packaged children before shutdown can orphan them."""
    descriptors: list[int] = []
    try:
        children = _process_data(path / "task" / path.name / "children")
        for pid in " ".join(children).split():
            child = PROC / pid
            descriptor = None
            try:
                if not _is_app(child):
                    continue
                descriptor = os.pidfd_open(int(pid))
                # Recheck executable and parent after acquiring the stable
                # handle. Never wait on an unrelated reused PID or provider.
                fields = (child / "stat").read_bytes().rpartition(b") ")[2].split()
                if _is_app(child) and int(fields[1]) == int(path.name):
                    descriptors.append(descriptor)
                    descriptor = None
            except (FileNotFoundError, ProcessLookupError, PermissionError):
                # Sandboxed Chromium children may be inaccessible. The
                # embedded server is an ordinary same-user child process.
                continue
            finally:
                if descriptor is not None:
                    os.close(descriptor)
        return descriptors
    except BaseException:
        for descriptor in descriptors:
            os.close(descriptor)
        raise


def _running_desktop(queued_before_ns: int) -> RunningDesktop | None:
    """Find only this user's Electron main process, excluding provider CLI hosts."""
    candidates: list[RunningDesktop] = []
    try:
        for path in PROC.iterdir():
            if not path.name.isdecimal():
                continue
            descriptor = None
            try:
                try:
                    is_app = _is_app(path)
                except PermissionError:
                    # Unrelated non-dumpable processes need not be inspectable.
                    continue
                if not is_app:
                    continue
                descriptor = os.pidfd_open(int(path.name))
                # Recheck identity after opening the handle; signals use this
                # descriptor so PID reuse cannot target another process.
                if not _is_app(path):
                    continue
                # Coalesce requests queued before a preceding worker reopened
                # T3. A newly opened app already loads the refreshed settings.
                started_ticks = _start_ticks(path)
                refresh_needed = started_ticks * 1_000_000_000 <= queued_before_ns * os.sysconf("SC_CLK_TCK")
                arguments = _process_data(path / "cmdline")
                # Chromium can rewrite its process title into argv[0], with
                # spaces instead of NUL separators. Exclude those children
                # before reading sandbox cwd/environment or counting profiles.
                if (not arguments or re.search(r"\s--type(?:=|\s|$)", arguments[0])
                        or any(arg == "--type" or arg.startswith("--type=")
                               for arg in arguments[1:])):
                    continue
                environment = dict(item.split("=", 1) for item in _process_data(path / "environ")
                                   if "=" in item)
                if environment.get("ELECTRON_RUN_AS_NODE") == "1":
                    continue
                if refresh_needed and _reopened_after(path, started_ticks, queued_before_ns):
                    refresh_needed = False
                directory = os.readlink(path / "cwd")
                validate_filesystem_path(directory, must_exist=True)
                children = _desktop_children(path) if refresh_needed else []
                candidates.append(RunningDesktop(descriptor, arguments, environment, directory, refresh_needed, children))
                descriptor = None
            except (FileNotFoundError, ProcessLookupError):
                continue
            finally:
                if descriptor is not None:
                    os.close(descriptor)
        if len(candidates) > 1:
            raise RuntimeError("Multiple T3 desktops found; close extra profiles and rerun refresh")
        return candidates.pop() if candidates else None
    finally:
        for app in candidates:
            for descriptor in [app.descriptor, *app.child_descriptors]:
                os.close(descriptor)


def _validate_executable() -> None:
    validate_filesystem_path(APP_EXECUTABLE, must_exist=True)
    result = subprocess.run(
        ["/usr/bin/pacman", "-Qqo", "--", APP_EXECUTABLE],
        check=True, capture_output=True, text=True, timeout=15,
    )
    if result.stdout.strip() != "t3code-bin" or not os.access(APP_EXECUTABLE, os.X_OK):
        raise RuntimeError("T3 desktop executable must belong to t3code-bin")


def _launch_environment(previous: dict[str, str] | None = None) -> dict[str, str]:
    # Electron can clear the environment visible through /proc. The detached
    # worker inherits KDE's environment from the user manager; retain it as a
    # fallback while preserving readable application-specific overrides.
    environment = {**os.environ, **(previous or {})}
    environment.pop("ELECTRON_RUN_AS_NODE", None)
    if not (environment.get("WAYLAND_DISPLAY") or environment.get("DISPLAY")):
        raise RuntimeError("T3 relaunch has no graphical environment; rerun refresh from the existing KDE session")
    return environment


def _start_stopped_desktop(home: Path) -> bool:
    # Transient user services inherit the systemd user manager's environment,
    # rather than an SSH terminal's forwarded display or provider environment.
    graphical = subprocess.run(
        ["/usr/bin/systemctl", "--user", "is-active", "--quiet", "graphical-session.target"],
        check=False, capture_output=True, timeout=15,
    )
    if graphical.returncode or not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")):
        print("T3 desktop launch deferred: no active graphical session; KDE login startup remains enabled", flush=True)
        return False
    _validate_executable()
    validate_filesystem_path(str(home), must_exist=True)
    os.chdir(home)
    environment = _launch_environment()
    _exec_desktop([], environment)
    return True


def restart_running_desktop(queued_before_ns: int, home: Path | None = None) -> bool:
    """Start a stopped app or gracefully replace one that needs refreshed state."""
    app = _running_desktop(queued_before_ns)
    if app is None:
        return _start_stopped_desktop(home or Path.home())
    try:
        if not app.refresh_needed:
            return False
        poller = select.poll()
        poller.register(app.descriptor, select.POLLIN)
        if poller.poll(0):
            return False
        _validate_executable()
        environment = _launch_environment(app.environment)
        # Resolve a vanished working directory before disturbing the running app.
        os.chdir(app.directory)
        for descriptor in app.child_descriptors:
            poller.register(descriptor, select.POLLIN)
        try:
            signal.pidfd_send_signal(app.descriptor, signal.SIGTERM)
        except ProcessLookupError:
            return False
        # Electron's main process can exit before its embedded server releases
        # the T3 home ownership lock. Keep waiting on the captured child handles
        # even after reparenting; reopening on main-process exit alone races it.
        pending = {app.descriptor, *app.child_descriptors}
        deadline = time.monotonic() + 60
        while pending:
            remaining_ms = math.ceil((deadline - time.monotonic()) * 1000)
            events = poller.poll(max(0, remaining_ms)) if remaining_ms > 0 else []
            if not events:
                raise RuntimeError("T3 desktop or its backend did not quit within 60 seconds; no forced kill or duplicate launch")
            for descriptor, event in events:
                if not event & (select.POLLIN | select.POLLHUP):
                    raise RuntimeError("Cannot confirm T3 shutdown; no forced kill or duplicate launch")
                poller.unregister(descriptor)
                pending.remove(descriptor)
        # Credentials/environment stay in memory. The worker becomes the app,
        # allowing systemd to track it without killing it when a helper exits.
        _exec_desktop(app.arguments[1:], environment)
        return True  # Reached only by tests that mock execve.
    finally:
        for descriptor in [app.descriptor, *app.child_descriptors]:
            os.close(descriptor)


def main(queued_before_ns: int, mode: str, home: Path, config_root: Path, request: str) -> int:
    """Serialize workers in the user's private runtime directory."""
    if os.getuid() == 0:
        raise RuntimeError("Run T3 desktop refresh as the desktop user, without sudo")
    runtime = _runtime_directory()
    descriptor = os.open(runtime / "basaltwater-t3-desktop-refresh.lock",
                         os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_nlink != 1 or info.st_mode & 0o077):
            raise RuntimeError("Unsafe T3 desktop refresh lock")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        from common.cachyos_t3 import complete_setup

        validate_filesystem_path(str(home), must_exist=True)
        validate_filesystem_path(str(config_root))
        if not home.is_absolute() or not config_root.is_absolute() or ".." in home.parts or ".." in config_root.parts:
            raise ValueError("T3 completion requires absolute home/configuration paths")
        os.environ["XDG_CONFIG_HOME"] = str(config_root)
        if complete_setup(home, mode, request) and mode == "desktop":
            restart_running_desktop(queued_before_ns, home)
        return 0
    finally:
        os.close(descriptor)


if __name__ == "__main__":
    try:
        if len(sys.argv) != 6 or sys.argv[2] not in {"web", "desktop"}:
            raise ValueError("T3 completion requires timestamp, mode, home, configuration root and request")
        sys.exit(main(validate_positive_integer(sys.argv[1], "queue timestamp"), sys.argv[2],
                      Path(sys.argv[3]), Path(sys.argv[4]), sys.argv[5]))
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        detail = str(error) if isinstance(error, RuntimeError) else type(error).__name__
        # OS/validation errors can contain private process launch state.
        print(f"T3 desktop refresh failed: {detail}; inspect the app and rerun setup.",
              file=sys.stderr)
        sys.exit(1)
