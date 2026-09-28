#!/usr/bin/env python3
"""Local, owner-scoped control socket for managed HTTPS forwards and previews."""

from __future__ import annotations

import json
import os
import pwd
import socket
import stat
import struct
import subprocess
import sys
import tempfile


SOURCE_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
if SOURCE_ROOT not in sys.path:
    sys.path.insert(0, SOURCE_ROOT)

from common.service_tools import basaltwater_web


SOCKET_PATH = "/run/basaltwater-web/control.sock"
UTILITY_PATH = "/opt/basaltwater/common/service_tools/basaltwater_web.py"
MAX_REQUEST_BYTES = 65536
MAX_OUTPUT_BYTES = 1048576
COMMAND_TIMEOUT_SECONDS = 3600
ALLOWED_MUTATIONS = {
    ("forward", "add"),
    ("forward", "remove"),
    ("forward", "prune"),
    ("preview", "start"),
    ("preview", "stop"),
    ("preview", "prune"),
}


def is_delegated_mutation(args: list[str]) -> bool:
    return len(args) >= 2 and tuple(args[:2]) in ALLOWED_MUTATIONS


def _validate_argv(value: object) -> list[str]:
    if (
        not isinstance(value, list)
        or not 2 <= len(value) <= 128
        or any(
            not isinstance(arg, str)
            or not arg
            or len(arg) > 8192
            or any(ord(char) < 32 or ord(char) == 127 for char in arg)
            for arg in value
        )
        or not is_delegated_mutation(value)
    ):
        raise ValueError("Unsupported HTTPS gateway mutation")
    return value


def _read_message(connection: socket.socket, limit: int = MAX_REQUEST_BYTES) -> object:
    data = bytearray()
    while len(data) <= limit:
        chunk = connection.recv(min(4096, limit + 1 - len(data)))
        if not chunk:
            break
        data.extend(chunk)
        if b"\n" in chunk:
            break
    if len(data) > limit or not data.endswith(b"\n"):
        raise ValueError("Invalid HTTPS gateway request size")
    try:
        return json.loads(data)
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError("Invalid HTTPS gateway request") from exc


def _peer_username(connection: socket.socket) -> str:
    credentials = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
    _pid, uid, _gid = struct.unpack("3i", credentials)
    username = pwd.getpwuid(uid).pw_name
    policy = basaltwater_web._load_policy()
    if username not in policy["users"]:
        raise PermissionError(f"User is not allowed to manage HTTPS forwards: {username}")
    return username


def _read_output(stream) -> str:
    stream.seek(0)
    data = stream.read(MAX_OUTPUT_BYTES + 1)
    if len(data) > MAX_OUTPUT_BYTES:
        return data[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace") + "\n[output truncated]\n"
    return data.decode("utf-8", errors="replace")


def _execute(argv: list[str], username: str) -> dict[str, object]:
    environment = {
        "HOME": "/",
        "LANG": "C.UTF-8",
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "SUDO_USER": username,
    }
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        try:
            result = subprocess.run(
                [sys.executable, UTILITY_PATH, *argv],
                cwd="/",
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                timeout=COMMAND_TIMEOUT_SECONDS,
                check=False,
            )
            return {
                "status": result.returncode,
                "stdout": _read_output(stdout),
                "stderr": _read_output(stderr),
            }
        except subprocess.TimeoutExpired:
            return {"status": 1, "stdout": "", "stderr": "HTTPS gateway command timed out\n"}


def _handle(connection: socket.socket) -> None:
    try:
        username = _peer_username(connection)
        request = _read_message(connection)
        if not isinstance(request, dict) or set(request) != {"argv"}:
            raise ValueError("Invalid HTTPS gateway request")
        argv = _validate_argv(request["argv"])
        response = _execute(argv, username)
    except (OSError, KeyError, PermissionError, RuntimeError, ValueError) as exc:
        response = {"status": 1, "stdout": "", "stderr": f"Error: {exc}\n"}
    try:
        connection.sendall((json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8"))
    except OSError:
        pass  # The caller disconnected after its request was already handled.


def serve() -> None:
    if os.geteuid() != 0:
        raise RuntimeError("HTTPS gateway control service must run as root")
    parent = os.path.dirname(SOCKET_PATH)
    parent_stat = os.stat(parent, follow_symlinks=False)
    if (
        not stat.S_ISDIR(parent_stat.st_mode)
        or parent_stat.st_uid != 0
        or parent_stat.st_mode & 0o022
    ):
        raise RuntimeError("Unsafe HTTPS gateway runtime directory")
    if os.path.lexists(SOCKET_PATH):
        current = os.stat(SOCKET_PATH, follow_symlinks=False)
        if not stat.S_ISSOCK(current.st_mode) or current.st_uid != 0:
            raise RuntimeError("Refusing unsafe HTTPS gateway control socket")
        os.unlink(SOCKET_PATH)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind(SOCKET_PATH)
        os.chmod(SOCKET_PATH, 0o666)
        listener.listen(8)
        while True:
            connection, _ = listener.accept()
            with connection:
                connection.settimeout(30)
                _handle(connection)


def request(argv: list[str]) -> int:
    _validate_argv(argv)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(COMMAND_TIMEOUT_SECONDS + 30)
            connection.connect(SOCKET_PATH)
            connection.sendall((json.dumps({"argv": argv}) + "\n").encode("utf-8"))
            response = _read_message(connection, 8 * MAX_OUTPUT_BYTES + 4096)
    except OSError as exc:
        raise RuntimeError(
            "HTTPS gateway control service is unavailable; rerun the saved VM setup"
        ) from exc
    if (
        not isinstance(response, dict)
        or not isinstance(response.get("status"), int)
        or not isinstance(response.get("stdout"), str)
        or not isinstance(response.get("stderr"), str)
    ):
        raise RuntimeError("Invalid HTTPS gateway control response")
    sys.stdout.write(response["stdout"])
    sys.stderr.write(response["stderr"])
    return response["status"]


if __name__ == "__main__":
    if sys.argv[1:] != ["serve"]:
        raise SystemExit("Usage: basaltwater_web_control.py serve")
    serve()
