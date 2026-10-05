"""Short-lived native login sessions with allowlisted, secret-free views."""

from __future__ import annotations

import os
from pathlib import Path
import pty
import re
import selectors
import shutil
import signal
import subprocess
import termios
import threading
import time
import urllib.parse

from lib.publishing import Publishing, provider
from lib.publishing_store import file_lock, now, private_directory, private_file
from lib.validators import validate_steam_account_name


def environment(home: Path, *, login: bool = False) -> dict[str, str]:
    # Do not inherit API keys, proxy credentials, loader hooks, or shell config.
    env = {"HOME": str(home), "PATH": f"{home}/.local/bin:/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8",
           "XDG_CONFIG_HOME": str(home / ".config"), "XDG_DATA_HOME": str(home / ".local/share"),
           "TERM": "dumb", "BUTLER_MANUAL_OAUTH": "1"}
    if not login:
        env["CI"] = "1"
    return env


def executable(service: str, home: Path) -> str:
    binary = shutil.which(provider(service), path=environment(home)["PATH"])
    if not binary:
        raise RuntimeError("Publishing tool is not installed")
    # SteamCMD derives its installation root and native binary name from $0.
    # The managed PATH launcher is a symlink named steamcmd to steamcmd.sh.
    return str(Path(binary).resolve(strict=True))


def credential_paths(service: str, home: Path) -> Path:
    if service == "butler":
        directory = private_directory(home / ".config/itch")
        path = directory / "butler_creds"
        private_file(path)
        return path
    directory = home / ".local/share/basaltwater/steamcmd"
    if directory.exists():
        # Installed binaries used to be 0755. The owner may make this native
        # store private before login, after rejecting linked/foreign paths.
        for ancestor in (*reversed(directory.parents), directory):
            if ancestor.is_symlink():
                raise ValueError("SteamCMD installation cannot have linked ancestors")
        if directory.stat().st_uid != os.getuid():
            raise ValueError("SteamCMD installation belongs to another user")
        os.chmod(directory, 0o700)
    private_directory(directory)
    for name in ("config", "logs"):
        child = directory / name
        if child.exists() and not child.is_symlink() and child.stat().st_uid == os.getuid():
            os.chmod(child, 0o700)
        private_directory(child)
        for path in child.iterdir():
            private_file(path)
    return directory


def login_view(output: str, service: str) -> dict:
    """Never return arbitrary native output, even error messages."""
    if service == "butler":
        match = re.search(r"https://itch\.io/user/oauth\?[^\s\"<>]+", output)
        if match:
            return {"challenge": "redirect", "link": match[0], "message": "Open itch.io login, then paste the final redirect URL."}
    lower = output.lower()
    if "password:" in lower or "enter password" in lower:
        return {"challenge": "password", "message": "Enter your Steam account password."}
    if any(text in lower for text in ("steam guard code", "two-factor code", "authenticator code", "enter the current code")):
        return {"challenge": "guard", "message": "Enter the current Steam Guard code."}
    if "waiting for confirmation" in lower or "confirm the login" in lower:
        return {"challenge": "waiting", "message": "Approve the Steam login in your authenticator."}
    return {"challenge": "waiting", "message": "Login is running. Unexpected prompts require a VM terminal; this session expires in 15 minutes."}


class PublishingAuth:
    """Credentials go straight to the native PTY; only status is persisted."""

    def __init__(self, publishing: Publishing):
        self.publishing = publishing
        self._sessions: dict[str, dict] = {}
        self._lock = threading.Lock()

    def views(self) -> list[dict]:
        with self._lock:
            return [{key: value for key, value in session.items() if key in {"id", "provider", "state", "challenge", "message", "link", "started"}}
                    for session in self._sessions.values()]

    def begin(self, service: str, username: str = "") -> str:
        service = provider(service)
        if os.geteuid() == 0:
            raise RuntimeError("Logins require a non-root publishing account")
        if service == "steamcmd":
            if not validate_steam_account_name(username):
                raise ValueError("Invalid Steam account name")
        credential_paths(service, self.publishing.home)
        binary = executable(service, self.publishing.home)
        root = private_directory(self.publishing.store.root)
        lease = file_lock(root / (service + ".lock"))
        lease_fd = lease.__enter__()
        session_id = self.publishing.store.new_id()
        master = slave = None
        try:
            master, slave = pty.openpty()
            attributes = termios.tcgetattr(slave)
            attributes[3] &= ~(termios.ECHO | termios.ECHONL)
            termios.tcsetattr(slave, termios.TCSANOW, attributes)
            command = [binary, "-i", str(credential_paths(service, self.publishing.home)), "login"] if service == "butler" else [binary, "+login", username, "+quit"]
            process = subprocess.Popen(command, stdin=slave, stdout=slave, stderr=slave,
                                       start_new_session=True, cwd=str(self.publishing.home),
                                       env=environment(self.publishing.home, login=True), umask=0o077, pass_fds=(lease_fd,))
            os.close(slave)
        except BaseException:
            for descriptor in (master, slave):
                if descriptor is not None:
                    os.close(descriptor)
            lease.__exit__(None, None, None)
            raise
        session = {"id": session_id, "provider": service, "username": username, "state": "running", "started": now(),
                   "master": master, "process": process, "challenge": "waiting", "message": "Starting native login"}
        with self._lock:
            self._sessions = {key: value for key, value in self._sessions.items() if value["state"] == "running"}
            self._sessions[session_id] = session
        threading.Thread(target=self._run, args=(session, lease), daemon=True, name="publishing-login").start()
        return session_id

    def respond(self, session_id: str, value: str) -> None:
        if not isinstance(value, str) or not 1 <= len(value) <= 8192 or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("Login response must be a single line")
        with self._lock:
            session = self._sessions.get(session_id)
            if not session or session["state"] != "running" or session["challenge"] not in {"password", "guard", "redirect"}:
                raise ValueError("Login is not waiting for an input")
            if session["challenge"] == "redirect":
                url = urllib.parse.urlsplit(value)
                if url.scheme != "http" or url.hostname != "127.0.0.1" or url.port != 226 or url.path != "/oauth/callback" or not url.fragment:
                    raise ValueError("Paste the native itch.io localhost callback URL")
            os.write(session["master"], value.encode() + b"\n")
            session.update(challenge="waiting", message="Response submitted to native login")
            session.pop("link", None)

    def cancel(self, session_id: str) -> None:
        with self._lock:
            session = self._sessions.get(session_id)
            if not session or session["state"] != "running":
                raise ValueError("Login is not running")
            self._kill(session["process"])

    @staticmethod
    def _kill(process) -> None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def _run(self, session: dict, lease) -> None:
        output = ""
        started = time.monotonic()
        process = session["process"]
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(session["master"], selectors.EVENT_READ)
                while process.poll() is None and time.monotonic() - started < 900:
                    for key, _ in selector.select(timeout=0.25):
                        try:
                            chunk = os.read(key.fd, 4096)
                        except OSError:
                            chunk = b""
                        if not chunk:
                            continue
                        output = (output + chunk.decode("utf-8", errors="replace"))[-16384:]
                        with self._lock:
                            session.update(login_view(output, session["provider"]))
                        # Consume the recognized prompt rather than redisplaying
                        # the password challenge after its response was submitted.
                        if session["challenge"] != "waiting":
                            output = ""
            if process.poll() is None:
                self._kill(process)
            code = process.wait(timeout=5)
            success = code == 0
            credential_paths(session["provider"], self.publishing.home)
            if session["provider"] == "butler":
                success = success and credential_paths("butler", self.publishing.home).is_file()
            with self.publishing.store.transaction() as db:
                self.publishing.store.put(db, "accounts", {"id": session["provider"], "state": "locally-authenticated" if success else "needs-login",
                    "username": session["username"], "observed_at": now()})
            with self._lock:
                session.update(state="complete" if success else "failed", challenge="none",
                               message="Native login completed; future operations revalidate the session." if success else "Login failed or expired. Retry or use the VM terminal.")
                session.pop("link", None)
        except Exception:
            with self._lock:
                session.update(state="failed", challenge="none", message="Native login could not finish; check the VM terminal and file permissions.")
                session.pop("link", None)
        finally:
            try:
                self._kill(process)
                process.wait(timeout=5)
            finally:
                os.close(session["master"])
                output = ""
                lease.__exit__(None, None, None)

    def close(self) -> None:
        with self._lock:
            for session in self._sessions.values():
                if session["state"] == "running":
                    self._kill(session["process"])

    def logout(self, service: str) -> None:
        service = provider(service)
        root = private_directory(self.publishing.store.root)
        with file_lock(root / (service + ".lock")):
            native = credential_paths(service, self.publishing.home)
            if service == "butler":
                native.unlink(missing_ok=True)
            else:
                # Preserve installed binaries, remove the known native config.
                for path in (native / "config").iterdir():
                    private_file(path)
                    path.unlink()
                (root / "steam-api.key").unlink(missing_ok=True)
            with self.publishing.store.transaction() as db:
                self.publishing.store.put(db, "accounts", {"id": service, "state": "needs-login", "observed_at": now()})
                for job in self.publishing.store.records(db, "jobs"):
                    project = self.publishing.store.get(db, "projects", job["project"])
                    if project["provider"] == service:
                        job["state"] = "paused"
                        self.publishing.store.put(db, "jobs", job)
