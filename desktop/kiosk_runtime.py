"""Unprivileged local browser session, launched by greetd on a real seat."""

from __future__ import annotations

import argparse
import fcntl
import os
from pathlib import Path
import pwd
import signal
import stat
import subprocess
import sys
import threading
import time

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.atomic_io import read_json_file, write_text_atomic
from lib.kiosk_config import validate_kiosk_destinations, validate_kiosk_url


USER = "basaltwater-kiosk"
HOME = Path("/var/lib/basaltwater-kiosk")
MANAGER = "basaltwater-kiosk"
ACCOUNT_COMMENT = "Basaltwater local kiosk"
MAX_ATTEMPTS = 3
RETRY_SECONDS = 3
STABLE_SECONDS = 60
RUNTIME_ROOT = Path("/run/user")
CLEANUP_SECONDS = 45


def configuration(path: Path) -> dict:
    """Read only a root-owned, non-writable managed session declaration."""
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or info.st_uid != 0 or info.st_mode & 0o022):
        raise RuntimeError("Unsafe kiosk session configuration")
    value = read_json_file(str(path), max_bytes=512 * 1024)
    if (not isinstance(value, dict) or set(value) not in (
            {"manager", "version", "url"}, {"manager", "version", "url", "destinations"})
            or value["manager"] != MANAGER or type(value["version"]) is not int
            or value["version"] != 1):
        raise ValueError("Unsupported kiosk session configuration")
    validate_kiosk_url(value["url"])
    if "destinations" in value:
        if not isinstance(value["destinations"], list):
            raise ValueError("Kiosk session destinations must be a list")
        validate_kiosk_destinations(value["destinations"])
    return value


def browser_command(url: str, *, start_page: bool = False) -> list[str]:
    """Preserve browser chrome and pass the URL as one literal argument."""
    validate_kiosk_url(url)
    destination = (HOME / "start.html").as_uri() if start_page else url
    return ["/usr/bin/cage", "-s", "-m", "last", "--", "/usr/bin/firefox-esr",
            "--no-remote", "--profile", str(HOME / "profile"), "--new-window", destination]


def session_environment() -> dict[str, str]:
    """Use the logind seat, rather than inheriting another graphical session."""
    environment = dict(os.environ)
    for name in ("DISPLAY", "WAYLAND_DISPLAY", "SESSION_MANAGER", "GREETD_SOCK"):
        environment.pop(name, None)
    environment.update(HOME=str(HOME), MOZ_ENABLE_WAYLAND="1", LIBSEAT_BACKEND="logind",
                       WLR_BACKENDS="drm,libinput", XDG_SESSION_TYPE="wayland",
                       XDG_CURRENT_DESKTOP="cage")
    return environment


def _stop_group(process: subprocess.Popen) -> None:
    """Reap the compositor and any browser processes left in its process group."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    # A reaped compositor can still have a browser descendant in its group.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=5)


def supervise(url: str, *, start_page: bool = False) -> int:
    """Retry failed sessions with a bounded budget; closing Firefox ends it."""
    stopped = threading.Event()
    previous = {}

    def stop(_signum, _frame):
        stopped.set()

    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        previous[signum] = signal.signal(signum, stop)
    attempts = 0
    try:
        while not stopped.is_set():
            started = time.monotonic()
            attempts += 1
            process = subprocess.Popen(browser_command(url, start_page=start_page), env=session_environment(),
                                       cwd=HOME, start_new_session=True)
            try:
                while process.poll() is None and not stopped.wait(0.25):
                    pass
                result = process.poll()
            finally:
                _stop_group(process)
            if stopped.is_set() or result == 0:
                return 0
            if time.monotonic() - started >= STABLE_SECONDS:
                attempts = 0
            if attempts >= MAX_ATTEMPTS:
                print("Kiosk session failed repeatedly; use Ctrl+Alt+F1 for the host console "
                      "and inspect journalctl -t basaltwater-kiosk.", flush=True)
                return 1
            print("Kiosk session failed; retrying shortly.", flush=True)
            stopped.wait(RETRY_SECONDS)
        return 0
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def run_session(path: Path) -> int:
    """Require the dedicated account and PAM-provided private runtime directory."""
    account = pwd.getpwnam(USER)
    if os.getuid() == 0 or os.getuid() != account.pw_uid:
        raise RuntimeError("Kiosk browser must run as its dedicated unprivileged account")
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", "/nonexistent"))
    info = runtime.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != account.pw_uid
            or stat.S_IMODE(info.st_mode) != 0o700):
        raise RuntimeError("Kiosk requires a private logind XDG_RUNTIME_DIR")
    home_info = HOME.lstat()
    if (not stat.S_ISDIR(home_info.st_mode) or home_info.st_uid != account.pw_uid
            or stat.S_IMODE(home_info.st_mode) != 0o700):
        raise RuntimeError("Unsafe kiosk home directory")
    profile = HOME / "profile"
    profile.mkdir(mode=0o700, exist_ok=True)
    profile_info = profile.lstat()
    if (not stat.S_ISDIR(profile_info.st_mode) or profile_info.st_uid != account.pw_uid
            or stat.S_IMODE(profile_info.st_mode) != 0o700):
        raise RuntimeError("Unsafe kiosk browser profile")
    value = configuration(path)
    descriptor = os.open(runtime / "basaltwater-kiosk.lock",
                         os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if value.get("destinations"):
            from desktop.kiosk_home import render_home

            write_text_atomic(str(HOME / "start.html"), render_home(value["url"], value["destinations"]), mode=0o600)
            return supervise(value["url"], start_page=True)
        return supervise(value["url"])
    finally:
        os.close(descriptor)


def cleanup_sessions() -> int:
    """Stop PAM-created scopes too: they can outlive greetd's service cgroup."""
    if os.getuid() != 0:
        raise RuntimeError("Kiosk session cleanup requires root")
    try:
        account = pwd.getpwnam(USER)
    except KeyError as error:
        raise RuntimeError("Kiosk cleanup requires its dedicated account") from error
    if (account.pw_uid == 0 or account.pw_dir != str(HOME)
            or account.pw_gecos != ACCOUNT_COMMENT or account.pw_shell != "/usr/sbin/nologin"):
        raise RuntimeError("Refusing cleanup of a changed kiosk account")
    deadline = time.monotonic() + CLEANUP_SECONDS

    def remaining() -> float:
        seconds = deadline - time.monotonic()
        if seconds <= 0:
            raise RuntimeError("Timed out waiting for kiosk logind sessions and runtime teardown")
        return seconds

    def present() -> bool:
        users = subprocess.run(["/usr/bin/loginctl", "list-users", "--no-legend", "--no-pager"],
                               check=True, capture_output=True, text=True, timeout=min(10, remaining()))
        found = False
        for row in users.stdout.splitlines():
            if not row.strip():
                continue
            fields = row.split()
            if len(fields) < 2 or not fields[0].isascii() or not fields[0].isdigit():
                raise RuntimeError("Unable to inspect logind users during kiosk cleanup")
            if fields[1] == USER or int(fields[0]) == account.pw_uid:
                if fields[1] != USER or int(fields[0]) != account.pw_uid:
                    raise RuntimeError("Kiosk logind identity does not match its account")
                found = True
        return found

    if present():
        subprocess.run(["/usr/bin/loginctl", "terminate-user", USER], check=True, timeout=min(15, remaining()))
        # terminate-user only queues teardown. Do not race a new greetd login
        # against the old user manager, profile lock or runtime mount.
        while present():
            time.sleep(min(0.25, remaining()))
    runtime = RUNTIME_ROOT / str(account.pw_uid)
    while os.path.lexists(runtime):
        time.sleep(min(0.25, remaining()))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    session = commands.add_parser("session")
    session.add_argument("--config", required=True, type=Path)
    commands.add_parser("idle")
    commands.add_parser("cleanup")
    args = parser.parse_args(argv)
    if args.command == "idle":
        print("Browser session ended. Ctrl+Alt+F1 opens the host login console.\n"
              "An administrator can reopen it with: systemctl restart basaltwater-kiosk.service",
              flush=True)
        while True:
            signal.pause()
    try:
        if args.command == "cleanup":
            return cleanup_sessions()
        return run_session(args.config)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"Kiosk startup failed: {error}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
