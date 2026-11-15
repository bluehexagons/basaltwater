#!/usr/bin/env python3
"""Root private stdin adapter for enrollment and read-only native observation."""

from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path
import re
import shlex
import stat
import sys
import tempfile

if __name__ == "__main__" and not __package__:
    sys.path.insert(0, "/opt/basaltwater")

from lib import headscale_client as client
from lib.atomic_io import parse_json
from lib.headscale_admin import approved_tags
from lib.headscale_config import validate_headscale_url
from lib.setup_directories import check_directory_path


def _privacy() -> None:
    fields = dict(line.split("=", 1) for line in client.native([
        "/usr/bin/systemctl", "show", "tailscaled", "--property=ActiveState,MainPID,Environment"
    ]).splitlines() if "=" in line)
    if (fields.get("ActiveState") != "active" or not re.fullmatch(r"[1-9][0-9]{0,9}", fields.get("MainPID", ""))
            or "TS_NO_LOGS_NO_SUPPORT=true" not in shlex.split(fields.get("Environment", ""))):
        raise ValueError("Active native privacy setting required")
    # Observe the running process too; a newly edited drop-in alone is no proof.
    with open("/proc/" + fields["MainPID"] + "/environ", "rb") as source:
        environment = source.read(65537)
    if len(environment) > 65536 or b"TS_NO_LOGS_NO_SUPPORT=true" not in environment.split(b"\0"):
        raise ValueError("Running native privacy setting required")
    check_directory_path(os.path.dirname(client.NATIVE_STATE))
    info = os.lstat(client.NATIVE_STATE)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1 or info.st_mode & 0o077):
        raise ValueError("Unsafe retained native client identity")


def inspect_client(url: str, tags: list[str], *, enrolled_only: bool = False) -> dict:
    with client.client_lock():
        client.reject_control_host(url)
        binding = client.owned(url)
        if binding["phase"] == "leaving":
            raise ValueError("Reconcile retained logout intent locally first")
        if enrolled_only and binding["phase"] != "enrolled":
            raise ValueError("Owned enrolled binding required")
        if enrolled_only:
            client.check_native_state()
        _privacy()
        status, prefs = client.snapshot()
        client.check_network(status, prefs, url, binding["phase"])
        if status["BackendState"] == "Running":
            identity = client._identity(status, prefs, url, True)
            if set(identity.tags) != set(tags) or binding["node_id"] != identity.node_id:
                raise ValueError("Existing native identity requires reconciliation")
            return {"phase": "enrolled", "identity": asdict(identity)}
        if (binding["phase"] not in {"prepared", "left"} or status["BackendState"] != "NeedsLogin"
                or status.get("HaveNodeKey", False) or binding["node_id"]):
            raise ValueError("Reconcile previous native enrollment first")
        return {"phase": "prepared", "identity": None}


def execute_request(payload: bytes) -> dict:
    try:
        if os.geteuid() != 0 or len(payload) > 16384:
            raise ValueError()
        request = parse_json(payload.decode())
        if type(request) is not dict:
            raise ValueError()
        fields = {"action", "url", "tags"}
        if request.get("action") == "enroll":
            fields.add("key")
        if set(request) != fields:
            raise ValueError()
        if request["action"] not in {"inspect", "enroll", "observe"}:
            raise ValueError()
        url = validate_headscale_url(request["url"])
        tags = ([] if request["action"] == "observe" and request["tags"] == []
                else approved_tags(request["tags"]))
        if request["action"] == "observe":
            observed = inspect_client(url, tags, enrolled_only=True)
        else:
            observed = inspect_client(url, tags)
        if request["action"] in {"inspect", "observe"}:
            result = observed
        else:
            secret = request["key"]
            if (observed["phase"] != "prepared" or not isinstance(secret, str)
                    or not re.fullmatch(r"[A-Za-z0-9_-]{32,4096}", secret)):
                raise ValueError()
            # /run is volatile. The native client consumes/removes this private
            # file; TemporaryDirectory also removes it on a rejected attempt.
            with tempfile.TemporaryDirectory(prefix="basaltwater-enroll-", dir="/run") as temporary:
                path = Path(temporary) / "key"
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, "w") as stream:
                    stream.write(secret)
                    stream.flush()
                    os.fsync(stream.fileno())
                result = {"identity": asdict(client.enroll_client(url, key_file=str(path), expected_tags=tuple(tags)))}
        return {"ok": True, "result": result}
    except Exception:
        return {"ok": False, "error": "Native enrollment unconfirmed; reconcile before retry"}


def main() -> int:
    result = execute_request(sys.stdin.buffer.read(16385))
    print(json.dumps(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
