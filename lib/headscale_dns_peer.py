"""Explicit root SSH authority and bounded, fixed DNS service protocol."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import selectors
import signal
import stat
import subprocess
import tempfile
import time

from lib.atomic_io import parse_json
from lib.setup_directories import check_directory_path
from lib.validation import validate_filesystem_path
from lib.validators import validate_host, validate_username

LIMIT = 16384
COMMAND = "/usr/bin/python3 /opt/basaltwater/common/service_tools/headscale_dns.py"


@dataclass(frozen=True)
class DNSRemote:
    host: str
    key: str
    known_hosts: str
    user: str = "root"

    def __post_init__(self) -> None:
        if not validate_host(self.host) or not validate_username(self.user) or self.user != "root":
            raise ValueError("Explicit root SSH authority required")
        for path in (self.key, self.known_hosts):
            validate_filesystem_path(path)
            if (not Path(path).is_absolute() or ".." in Path(path).parts
                    or any(c.isspace() for c in path) or any(c in path for c in "%$~\"'\\")):
                raise ValueError("SSH bindings require absolute paths")

    def validate_files(self) -> None:
        for path in (self.key, self.known_hosts):
            check_directory_path(str(Path(path).parent))
            info = os.lstat(path)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != 0
                    or info.st_mode & 0o077 or not 1 <= info.st_size <= 1024 * 1024):
                raise ValueError("SSH authority files must be private, root owned regular files")

    def call(self, action: str, **parameters) -> dict:
        self.validate_files()
        payload = json.dumps(dict(action=action, parameters=parameters)).encode()
        if len(payload) > LIMIT:
            raise ValueError("DNS request too large")
        args = ["/usr/bin/ssh", "-F", "/dev/null", "-T", "-i", self.key,
                "-o", "IdentitiesOnly=yes", "-o", "IdentityAgent=none", "-o", "BatchMode=yes",
                "-o", "StrictHostKeyChecking=yes", "-o", "UserKnownHostsFile=" + self.known_hosts,
                "-o", "GlobalKnownHostsFile=/dev/null", "-o", "ConnectTimeout=10",
                "-o", "ConnectionAttempts=1", "-o", "ClearAllForwardings=yes",
                "-o", "PermitLocalCommand=no", "-o", "UpdateHostKeys=no",
                "--", self.user + "@" + self.host, COMMAND]
        output = bytearray()
        try:
            with tempfile.TemporaryFile() as request:
                request.write(payload)
                request.seek(0)
                with subprocess.Popen(args, stdin=request, stdout=subprocess.PIPE,
                                      stderr=subprocess.DEVNULL, start_new_session=True) as proc:
                    try:
                        deadline = time.monotonic() + 300
                        with selectors.DefaultSelector() as selector:
                            selector.register(proc.stdout, selectors.EVENT_READ)
                            while selector.get_map():
                                if time.monotonic() >= deadline:
                                    raise ValueError("DNS SSH acknowledgement uncertain; reconcile")
                                for key, _ in selector.select(1):
                                    chunk = os.read(key.fd, LIMIT + 1)
                                    if not chunk:
                                        selector.unregister(key.fileobj)
                                    output.extend(chunk)
                                    if len(output) > LIMIT:
                                        raise ValueError("DNS SSH response exceeds bound; reconcile")
                        if proc.wait(timeout=max(.1, deadline - time.monotonic())):
                            raise ValueError("DNS SSH operation unconfirmed; reconcile")
                    finally:
                        if proc.poll() is None:
                            os.killpg(proc.pid, signal.SIGKILL)
                            proc.wait()
            result = parse_json(output.decode())
            if type(result) is not dict or set(result) != {"ok", "result"} or result["ok"] is not True:
                raise ValueError()
            if type(result["result"]) is not dict:
                raise ValueError()
            return result["result"]
        except (OSError, ValueError, UnicodeError, subprocess.SubprocessError):
            raise ValueError("DNS SSH operation unconfirmed; inspect before reconciliation") from None
