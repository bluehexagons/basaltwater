"""Run Debian refresh independently of terminals and services it replaces."""

from __future__ import annotations

import os
from pathlib import Path
import stat
import sys
import uuid

from lib.remote_utils import run
from lib.setup_directories import directory_fd
from lib.validation import validate_filesystem_path


WORKER_ENV = "BASALTWATER_REFRESH_WORKER"
LOG_DIRECTORY = "/var/log/basaltwater-refresh"


def queue_refresh(repository: str) -> int:
    """Queue a root-owned job with private output; acceptance is not completion."""
    entry = str(Path(repository) / "basaltwater.py")
    validate_filesystem_path(entry, must_exist=True)
    job = uuid.uuid4().hex
    unit = "basaltwater-refresh-" + job
    with directory_fd(LOG_DIRECTORY, create=True, mode=0o700) as parent:
        info = os.fstat(parent)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError("Refresh logs require a private directory owned by the invoking root account")
        descriptor = os.open(job + ".log", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=parent)
        os.close(descriptor)
    log = os.path.join(LOG_DIRECTORY, job + ".log")
    result = run([
        "/usr/bin/systemd-run", "--quiet", "--unit=" + unit,
        "--property=Type=exec", "--property=RemainAfterExit=yes",
        "--property=UMask=0077", "--property=StandardInput=null",
        "--property=StandardOutput=append:" + log,
        "--property=StandardError=inherit", "--working-directory=/",
        "--setenv=" + WORKER_ENV + "=1", "--setenv=HOME=/root",
        "--", sys.executable, "-I", entry, "refresh",
    ], capture_output=True, check=False, timeout=30)
    if result.returncode:
        raise RuntimeError("Could not queue independent refresh; no source or setup changes were started")
    print(f"Refresh queued as {unit}.service; closing this terminal will not stop it.")
    print(f"Private output: sudo tail -f {log}")
    print(f"Final result: sudo systemctl show {unit}.service -p ActiveState -p Result -p ExecMainStatus")
    print("Exit 0 here confirms dispatch only. Check the job result before retrying or assuming setup completed.")
    return 0
