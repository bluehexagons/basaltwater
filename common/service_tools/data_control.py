#!/usr/bin/env python3
"""Socket-activated dispatcher for reviewed scrub recovery."""

from __future__ import annotations

import argparse
import json
import os
import socket
import struct
import subprocess
import sys

if __name__ == "__main__" and not __package__:
    sys.path.insert(0, "/opt/basaltwater")

from common.service_tools.data_job import job_status, stage_data_job
from lib.data_actions import DATA_HELPER, data_dispatch_argv, read_data_message, validate_data_action
from lib.privilege_execution import ENVIRONMENT
from lib.privilege_policy import protected_path
from sync.routine_job import _unit_state as routine_unit_state


def snapshot() -> dict:
    result = job_status()
    result["other_active"] = routine_unit_state() in {"active", "activating", "deactivating"}
    try:
        protected_path(DATA_HELPER)
    except (OSError, ValueError):
        result["available"] = False
    return result


def dispatch(message: object) -> dict:
    if message == {"action": "data-status"}:
        return snapshot()
    if not isinstance(message, dict) or set(message) != {"action", "parameters"} or message["action"] != "data-submit":
        raise ValueError("Unsupported recovery request")
    parameters = validate_data_action(message["parameters"])
    status = snapshot()
    if not status["available"] or status["blocked"] or status["other_active"] or status["unit_state"] not in {"inactive", "failed"}:
        raise ValueError("The recovery worker is busy or unavailable")
    stage_data_job(parameters)
    subprocess.run(data_dispatch_argv(parameters), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, cwd="/", env=ENVIRONMENT, timeout=3, check=True)
    return {"job_id": parameters["job_id"]}


def handle(connection: socket.socket, allowed_uid: int) -> None:
    connection.settimeout(2)
    try:
        _, uid, _ = struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        if uid != allowed_uid:
            raise PermissionError("Unauthorized recovery peer")
        with connection.makefile("rb") as stream:
            result = dispatch(read_data_message(stream))
        response = {"ok": True, "result": result}
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, RecursionError):
        response = {"ok": False, "error": "Recovery service could not confirm the request. Check recovery activity and the service journal before retrying."}
        print("Recovery service could not confirm a request", file=sys.stderr)
    try:
        connection.sendall((json.dumps(response) + "\n").encode())
    except OSError:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uid", type=int, required=True)
    args = parser.parse_args()
    if (os.geteuid() != 0 or args.uid <= 0 or os.environ.get("LISTEN_PID") != str(os.getpid())
            or os.environ.get("LISTEN_FDS") != "1"):
        raise SystemExit("Recovery control requires a root service with one systemd listener and a non-root panel UID")
    os.umask(0o077)
    with socket.socket(fileno=3) as listener:
        while True:
            connection, _ = listener.accept()
            with connection:
                handle(connection, args.uid)


if __name__ == "__main__":
    main()
