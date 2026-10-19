"""Bounded, volatile registration handoff over a root-owned Unix socket."""

from __future__ import annotations

import socket
import struct

from lib.validation import validate_runner_registration_token


SOCKET = "/run/basaltwater-github-runner-registration.sock"
ENDPOINT = "/actions/github-runner/register"
MAX_REQUEST_BYTES = 8 * 1024
_FAILURES = {
    "failed": "Registration did not complete. Refresh status before retrying. If still pending, use a fresh token for the configured scope. If recovery is required, rerun setup.",
    "start": "Runner registration could not start. Rerun setup to repair the runner prerequisites, then retry registration.",
    "configure": "GitHub runner configuration failed. Check the token's repository or organization and the VM's GitHub connectivity, then retry with a fresh token.",
    "timeout": "Runner registration timed out. Refresh status before retrying and check the VM's GitHub connectivity.",
    "credentials": "Runner registration did not produce complete credentials. Rerun setup, then retry registration.",
    "activate": "Runner registration completed, but service activation failed. Rerun setup without a token to repair the service.",
    "installation": "Runner installation needs repair. Rerun setup before retrying registration.",
}


class RunnerRegistrationError(RuntimeError):
    """A fixed failure category safe for the setup log and panel protocol."""

    def __init__(self, code: str) -> None:
        self.code = code if code in _FAILURES else "failed"
        super().__init__(_FAILURES[self.code])


def registration_error_message(error: Exception) -> str:
    """Never expose arbitrary exception text, command output or credentials."""
    return _FAILURES.get(error.code, _FAILURES["failed"]) if isinstance(error, RunnerRegistrationError) else _FAILURES["failed"]


def peer_uid(connection: socket.socket) -> int:
    """Read the kernel-authenticated identity of the Unix peer."""
    credentials = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    return struct.unpack("3i", credentials)[1]


def submit_registration(token: bytes) -> None:
    """Send one token; the helper chooses every path and registration setting."""
    validate_runner_registration_token(token)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(245)
        connection.connect(SOCKET)
        if peer_uid(connection) != 0:
            raise RuntimeError("Runner registration service is unavailable")
        connection.sendall(token)
        connection.shutdown(socket.SHUT_WR)
        response = bytearray()
        while len(response) <= 32:
            chunk = connection.recv(33 - len(response))
            if not chunk:
                break
            response.extend(chunk)
        if response != b"registered\n":
            code = next((code for code in _FAILURES if response == f"failed {code}\n".encode("ascii")), "failed")
            raise RunnerRegistrationError(code)
