#!/usr/bin/env python3
"""Target-owned Codex login; only bounded, sanitized events cross SSH."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import pwd
import re
import select
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from typing import Callable


DEVICE_URL = "https://auth.openai.com/codex/device"
LOGIN_TIMEOUT = 900
MAX_BYTES = 4 * 1024 * 1024


class LoginError(RuntimeError):
    """Fixed error text safe for display and setup reports."""


def private_directory(path: str) -> None:
    current = os.path.abspath(path)
    while current != os.path.dirname(current):
        if os.path.islink(current):
            raise LoginError("unsafe_credential_directory")
        current = os.path.dirname(current)
    os.makedirs(path, mode=0o700, exist_ok=True)
    info = os.lstat(path)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise LoginError("unsafe_credential_directory")


def read_private(path: str, *, config: bool = False) -> bytes | None:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & (0o022 if config else 0o077):
            raise LoginError("unsafe_credential_file")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            value = stream.read(MAX_BYTES + 1)
        if len(value) > MAX_BYTES:
            raise LoginError("credential_too_large")
        return value
    finally:
        os.close(fd)


class Protocol:
    def __init__(self, process: subprocess.Popen, deadline: float) -> None:
        self.process = process
        self.deadline = deadline
        self.buffer = bytearray()
        self.pending: list[dict] = []

    def send(self, message: dict) -> None:
        self.process.stdin.write(json.dumps(message).encode() + b"\n")
        self.process.stdin.flush()

    def receive(self, match: Callable[[dict], bool]) -> dict:
        for index, message in enumerate(self.pending):
            if match(message):
                return self.pending.pop(index)
        for _ in range(256):
            while b"\n" not in self.buffer:
                remaining = self.deadline - time.monotonic()
                if remaining <= 0 or not select.select([self.process.stdout], [], [], remaining)[0]:
                    raise LoginError("authorization_timed_out")
                chunk = os.read(self.process.stdout.fileno(), 65536)
                if not chunk:
                    raise LoginError("codex_connection_closed")
                self.buffer.extend(chunk)
                if len(self.buffer) > MAX_BYTES:
                    raise LoginError("invalid_codex_response")
            line, _, rest = self.buffer.partition(b"\n")
            self.buffer[:] = rest
            try:
                message = json.loads(line)
            except (ValueError, RecursionError):
                raise LoginError("invalid_codex_response") from None
            if not isinstance(message, dict):
                raise LoginError("invalid_codex_response")
            if match(message):
                return message
            if message.get("method") == "account/login/completed":
                if len(self.pending) >= 8:
                    raise LoginError("invalid_codex_response")
                self.pending.append(message)
        raise LoginError("too_many_codex_messages")

    def request(self, identifier: int, method: str, params: dict) -> dict:
        self.send({"id": identifier, "method": method, "params": params})
        response = self.receive(lambda message: message.get("id") == identifier)
        if response.get("error") is not None or not isinstance(response.get("result"), dict):
            raise LoginError("codex_rejected_login_request")
        return response["result"]


def login(home: str, method: str, api_key: str | None, emit: Callable[[dict], None]) -> None:
    if method not in {"subscription", "api-key"}:
        raise LoginError("unsupported_authentication_method")
    if method == "api-key" and (not api_key or len(api_key) > 16384 or any(c.isspace() for c in api_key)):
        raise LoginError("invalid_api_key_input")
    codex = shutil.which("codex", path=os.pathsep.join((os.path.join(home, ".local/bin"), os.environ.get("PATH", ""))))
    if not codex:
        raise LoginError("codex_not_installed_on_target")
    auth_home = os.path.join(home, ".codex")
    private_directory(auth_home)
    destination = os.path.join(auth_home, "auth.json")
    previous = read_private(destination)
    config_path = os.path.join(auth_home, "config.toml")
    previous_config = read_private(config_path, config=True)
    lock = os.open(os.path.join(auth_home, ".basaltwater-login.lock"), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(lock)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise LoginError("unsafe_login_lock")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise LoginError("another_login_is_running") from None
        with tempfile.TemporaryDirectory(prefix=".login-", dir=auth_home) as staging:
            staged_config = os.path.join(staging, "config.toml")
            if previous_config is not None:
                fd = os.open(staged_config, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(previous_config)
            environment = os.environ.copy()
            for name in ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN"):
                environment.pop(name, None)
            environment.update(HOME=home, CODEX_HOME=staging)
            process = subprocess.Popen(
                [codex, "-c", 'cli_auth_credentials_store="file"', "app-server"],
                cwd=home, env=environment, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            )
            protocol = Protocol(process, time.monotonic() + LOGIN_TIMEOUT)
            try:
                protocol.request(1, "initialize", {"clientInfo": {"name": "basaltwater-auth", "version": "1"}})
                protocol.send({"method": "initialized"})
                # Let Codex edit TOML correctly, preserving unrelated settings.
                protocol.request(3, "config/value/write", {
                    "keyPath": "cli_auth_credentials_store", "value": "file", "mergeStrategy": "replace",
                })
                params = {"type": "chatgptDeviceCode"} if method == "subscription" else {"type": "apiKey", "apiKey": api_key}
                result = protocol.request(2, "account/login/start", params)
                if method == "subscription":
                    code = result.get("userCode")
                    identifier = result.get("loginId")
                    if (
                        result.get("type") != "chatgptDeviceCode"
                        or result.get("verificationUrl") != DEVICE_URL
                        or not isinstance(code, str) or not re.fullmatch(r"[A-Za-z0-9-]{4,32}", code)
                        or not isinstance(identifier, str) or not identifier
                    ):
                        raise LoginError("invalid_device_authorization_response")
                    emit({"event": "device", "url": DEVICE_URL, "code": code})
                    completed = protocol.receive(lambda message: (
                        message.get("method") == "account/login/completed"
                        and isinstance(message.get("params"), dict)
                        and message["params"].get("loginId") == identifier
                    ))
                    if completed["params"].get("success") is not True:
                        raise LoginError("authorization_failed")
                elif result.get("type") != "apiKey":
                    raise LoginError("invalid_api_key_login_response")
                payload = read_private(os.path.join(staging, "auth.json"))
                try:
                    credential = json.loads(payload or b"")
                except (ValueError, RecursionError):
                    raise LoginError("login_did_not_save_credentials") from None
                if not isinstance(credential, dict):
                    raise LoginError("login_did_not_save_credentials")
                tokens = credential.get("tokens")
                if method == "subscription":
                    if not isinstance(tokens, dict) or not all(isinstance(tokens.get(k), str) and tokens[k] for k in ("access_token", "refresh_token")):
                        raise LoginError("login_did_not_save_subscription_credentials")
                elif credential.get("OPENAI_API_KEY") != api_key:
                    raise LoginError("login_did_not_save_api_key")
                private_directory(auth_home)
                if read_private(destination) != previous or read_private(config_path, config=True) != previous_config:
                    raise LoginError("target_credentials_changed_during_login")
                if read_private(staged_config, config=True) is None:
                    raise LoginError("login_did_not_save_configuration")
                os.chmod(staged_config, 0o600)
                os.replace(staged_config, config_path)
                os.replace(os.path.join(staging, "auth.json"), destination)
                emit({"event": "complete", "method": method})
            finally:
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                process.stdin.close()
                process.stdout.close()
    finally:
        os.close(lock)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--setup", action="store_true")
    args = parser.parse_args()

    def emit(event: dict) -> None:
        if args.setup:
            if event["event"] == "device":
                print(f"Open {event['url']} on any device and enter code: {event['code']}", flush=True)
            elif event["event"] == "complete":
                print("Codex subscription login installed on this VM", flush=True)
            else:
                print(f"Codex authorization required: {event['reason']}", flush=True)
        else:
            print(json.dumps(event), flush=True)

    stopped = threading.Event()
    watcher = None
    try:
        request = {"method": "subscription"} if args.setup else json.loads(sys.stdin.buffer.readline(20000))
        if not isinstance(request, dict):
            raise LoginError("invalid_login_request")
        if not args.setup:
            descriptor = sys.stdin.fileno()

            def disconnected() -> None:
                # Never hold a Python buffered-stream lock across a blocking
                # read: the controller keeps stdin open until we have exited.
                while not stopped.is_set():
                    if select.select([descriptor], [], [], 0.1)[0]:
                        if not os.read(descriptor, 1):
                            if not stopped.is_set():
                                os.kill(os.getpid(), signal.SIGTERM)
                            return

            watcher = threading.Thread(target=disconnected)
            watcher.start()
        login(pwd.getpwuid(os.getuid()).pw_dir, request.get("method", "subscription"), request.get("api_key"), emit)
        return 0
    except LoginError as exc:
        emit({"event": "error", "reason": str(exc)})
    except KeyboardInterrupt:
        emit({"event": "error", "reason": "authorization_cancelled"})
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        emit({"event": "error", "reason": "login_process_failed"})
    finally:
        stopped.set()
        if watcher is not None:
            watcher.join()
    return 3


if __name__ == "__main__":
    def cancel(_signum, _frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, cancel)
    signal.signal(signal.SIGHUP, cancel)
    raise SystemExit(main())
