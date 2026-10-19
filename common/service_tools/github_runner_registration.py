#!/usr/bin/env python3
"""One fixed root operation, activated per connection by systemd."""

from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
import os
import socket
import sys

sys.path.insert(0, "/opt/basaltwater")

from lib.github_runner_registration import peer_uid
from lib.validation import validate_runner_registration_token
from web.github_runner_steps import register_pending_runner


def handle_connection(connection: socket.socket, panel_uid: int) -> None:
    """Accept only the configured panel UID and a bounded token followed by EOF."""
    response = b"failed\n"
    try:
        connection.settimeout(10)
        if panel_uid <= 0 or peer_uid(connection) != panel_uid:
            raise ValueError("Unauthorized registration peer")
        payload = bytearray()
        while len(payload) <= 4096:
            chunk = connection.recv(4097 - len(payload))
            if not chunk:
                break
            payload.extend(chunk)
        token = validate_runner_registration_token(bytes(payload))
        # Setup helpers may print nonsecret diagnostics; the protocol returns
        # only a fixed result, never command output or exception text.
        with open(os.devnull, "w") as output, redirect_stdout(output), redirect_stderr(output):
            register_pending_runner(token)
        response = b"registered\n"
    except Exception:
        pass
    try:
        connection.sendall(response)
    except OSError:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--panel-uid", type=int, required=True)
    args = parser.parse_args()
    if os.geteuid() != 0 or args.panel_uid <= 0:
        return 1
    with socket.socket(fileno=os.dup(0)) as connection:
        handle_connection(connection, args.panel_uid)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
