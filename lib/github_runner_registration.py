"""Bounded, volatile registration handoff over a root-owned Unix socket."""

from __future__ import annotations

import socket
import struct

from lib.validation import validate_runner_registration_token


SOCKET = "/run/basaltwater-github-runner-registration.sock"
ENDPOINT = "/actions/github-runner/register"
MAX_REQUEST_BYTES = 8 * 1024


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
            raise RuntimeError("Runner registration did not complete")
