"""Gracefully reopen an existing package-owned T3 desktop, outside its cgroup."""

from __future__ import annotations

from dataclasses import dataclass
import fcntl
import os
from pathlib import Path
import select
import signal
import stat
import subprocess
import sys
import time

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lib.validation import validate_filesystem_path, validate_positive_integer


APP_EXECUTABLE = "/opt/t3code-bin/t3code"
PROC = Path("/proc")
MAX_PROCESS_DATA = 4 * 1024 * 1024
REOPENED_AT_ENV = "BASALTW_T3_DESKTOP_REOPENED_NS"


@dataclass
class RunningDesktop:
    """A stable process handle and private launch state, never written to logs."""

    descriptor: int
    arguments: list[str]
    environment: dict[str, str]
    directory: str


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


def _started_after(path: Path, queued_before_ns: int) -> bool:
    # /proc starttime and CLOCK_BOOTTIME include time spent suspended. The
    # process name can contain spaces and parentheses, so split after its end.
    with (path / "stat").open("rb") as stream:
        fields = stream.read(8192).rpartition(b") ")[2].split()
    try:
        start_ticks = int(fields[19])
    except (IndexError, ValueError) as error:
        raise RuntimeError("Cannot read T3 desktop start time") from error
    return start_ticks * 1_000_000_000 > queued_before_ns * os.sysconf("SC_CLK_TCK")


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
                if _started_after(path, queued_before_ns):
                    continue
                arguments = _process_data(path / "cmdline")
                if not arguments or any(arg == "--type" or arg.startswith("--type=")
                                        for arg in arguments[1:]):
                    continue
                environment = dict(item.split("=", 1) for item in _process_data(path / "environ")
                                   if "=" in item)
                if environment.get("ELECTRON_RUN_AS_NODE") == "1":
                    continue
                try:
                    reopened_at_ns = validate_positive_integer(
                        environment.get(REOPENED_AT_ENV, "0"), "desktop relaunch timestamp",
                    )
                except ValueError:
                    reopened_at_ns = 0
                if reopened_at_ns > queued_before_ns:
                    continue
                directory = os.readlink(path / "cwd")
                validate_filesystem_path(directory, must_exist=True)
                candidates.append(RunningDesktop(descriptor, arguments, environment, directory))
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
            os.close(app.descriptor)


def restart_running_desktop(queued_before_ns: int) -> bool:
    """SIGTERM the existing app, wait for shutdown, then replace this worker."""
    app = _running_desktop(queued_before_ns)
    if app is None:
        return False
    try:
        poller = select.poll()
        poller.register(app.descriptor, select.POLLIN)
        if poller.poll(0):
            return False
        validate_filesystem_path(APP_EXECUTABLE, must_exist=True)
        result = subprocess.run(
            ["/usr/bin/pacman", "-Qqo", "--", APP_EXECUTABLE],
            check=True, capture_output=True, text=True, timeout=15,
        )
        if result.stdout.strip() != "t3code-bin" or not os.access(APP_EXECUTABLE, os.X_OK):
            raise RuntimeError("T3 desktop executable must belong to t3code-bin")
        # Resolve a vanished working directory before disturbing the running app.
        os.chdir(app.directory)
        try:
            signal.pidfd_send_signal(app.descriptor, signal.SIGTERM)
        except ProcessLookupError:
            return False
        if not poller.poll(60_000):
            raise RuntimeError("T3 desktop did not quit within 60 seconds; no forced kill or duplicate launch")
        # Credentials/environment stay in memory. The worker becomes the app,
        # allowing systemd to track it without killing it when a helper exits.
        # exec preserves the worker's process start time. Record the later
        # relaunch too, so requests queued during shutdown also coalesce.
        environment = {**app.environment,
                       REOPENED_AT_ENV: str(time.clock_gettime_ns(time.CLOCK_BOOTTIME))}
        os.execve(APP_EXECUTABLE, [APP_EXECUTABLE, *app.arguments[1:]], environment)
        return True  # Reached only by tests that mock execve.
    finally:
        os.close(app.descriptor)


def main(queued_before_ns: int) -> int:
    """Serialize workers in the user's private runtime directory."""
    if os.getuid() == 0:
        raise RuntimeError("Run T3 desktop refresh as the desktop user, without sudo")
    runtime = Path(f"/run/user/{os.getuid()}")
    info = runtime.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RuntimeError("T3 refresh requires an owned private user runtime directory")
    descriptor = os.open(runtime / "basaltwater-t3-desktop-refresh.lock",
                         os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_nlink != 1 or info.st_mode & 0o077):
            raise RuntimeError("Unsafe T3 desktop refresh lock")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        restart_running_desktop(queued_before_ns)
        return 0
    finally:
        os.close(descriptor)


if __name__ == "__main__":
    try:
        if len(sys.argv) != 2:
            raise ValueError("T3 desktop refresh requires its queue timestamp")
        sys.exit(main(validate_positive_integer(sys.argv[1], "queue timestamp")))
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        detail = str(error) if isinstance(error, RuntimeError) else type(error).__name__
        # OS/validation errors can contain private process launch state.
        print(f"T3 desktop refresh failed: {detail}; inspect the app and rerun setup.",
              file=sys.stderr)
        sys.exit(1)
