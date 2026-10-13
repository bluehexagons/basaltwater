#!/usr/bin/env python3
"""Print a read-only Guacamole support report as JSON; run as the desktop owner."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import pwd
import re
import shutil
import subprocess

OUTPUT_LIMIT = 65536
SOURCE_ROOT = Path("/opt/basaltwater")
DESKTOP_CONFIG = Path("/etc/basaltwater/desktop.json")


def redact(text: str) -> str:
    """Remove common credential forms without collecting credential files."""
    text = re.sub(r"(?i)(https?://)[^\s/@]+:[^\s/@]+@", r"\1[redacted]@", text)
    text = re.sub(r"(?im)\b(authorization\s*[:=]\s*).+$", r"\1[redacted]", text)
    text = re.sub(r"(?im)\b((?:password|passwd|token|secret)\s*[:=]\s*).+$",
                  r"\1[redacted]", text)
    return re.sub(r"(?i)([?&](?:attachment|token|password)=)[^&\s]+",
                  r"\1[redacted]", text)


def capture(command: list[str]) -> dict:
    try:
        result = subprocess.run(command, check=False, capture_output=True,
                                text=True, errors="replace", timeout=20)
        return dict(command=command, returncode=result.returncode,
                    stdout=redact(result.stdout[-OUTPUT_LIMIT:]),
                    stderr=redact(result.stderr[-OUTPUT_LIMIT:]),
                    truncated=len(result.stdout) > OUTPUT_LIMIT or len(result.stderr) > OUTPUT_LIMIT)
    except (OSError, subprocess.TimeoutExpired) as error:
        return dict(command=command, error=f"{type(error).__name__}: command unavailable or timed out")


def source_info(path: Path) -> dict:
    try:
        info = path.lstat()
        if path.is_symlink() or not path.is_file() or info.st_size > 2 * 1024 * 1024:
            return dict(path=str(path), error="Not a bounded regular file")
        content = path.read_bytes()
        return dict(path=str(path), sha256=hashlib.sha256(content).hexdigest(),
                    bytes=len(content), uid=info.st_uid, mode=oct(info.st_mode & 0o7777))
    except OSError as error:
        return dict(path=str(path), error=type(error).__name__)


def desktop_status() -> dict:
    """Use the installed runtime directly when the orchestration CLI is absent."""
    executable = shutil.which("basaltw") or shutil.which("basaltwater")
    if os.getuid() != 0 and executable:
        return capture([executable, "desktop", "status", "--json"])
    command = ["/usr/bin/python3", "-c",
               "import sys,json; sys.path.insert(0, '/opt/basaltwater'); "
               "from desktop.session_runtime import status; print(json.dumps(status()))"]
    if os.getuid() == 0:
        try:
            info = DESKTOP_CONFIG.lstat()
            if (DESKTOP_CONFIG.is_symlink() or not DESKTOP_CONFIG.is_file()
                    or info.st_uid != 0 or info.st_mode & 0o022 or info.st_size > 16384):
                raise ValueError("Unsafe desktop configuration")
            owner = json.loads(DESKTOP_CONFIG.read_text())["username"]
            if not isinstance(owner, str) or pwd.getpwnam(owner).pw_uid == 0:
                raise ValueError("A non-root desktop owner is required")
        except (OSError, ValueError, KeyError, TypeError) as error:
            return dict(error=f"{type(error).__name__}: could not determine the managed desktop owner")
        command = ["/usr/sbin/runuser", "-u", owner, "--", *command]
    return capture(command)


def collect() -> dict:
    elevate = [] if os.getuid() == 0 else ["sudo", "-n"]
    desktop = desktop_status()
    if desktop.get("returncode") == 0:
        try:
            value = json.loads(desktop["stdout"])
            # Exclude application/window titles and arbitrary desktop payloads.
            allowed = {"state", "generation", "geometry", "paused", "desktop",
                       "username", "display", "window_manager_ready"}
            desktop["stdout"] = json.dumps({key: val for key, val in value.items() if key in allowed})
        except (ValueError, AttributeError):
            desktop["stdout"] = "[desktop status was not valid JSON]"
    report = dict(
        schema="basaltwater-guacamole-diagnostics-v1",
        collected_at=datetime.now(timezone.utc).isoformat(),
        architecture=platform.machine(), collector_uid=os.getuid(),
        desktop=desktop,
        services=capture(["systemctl", "show", "--no-pager",
                          "--property=Id,LoadState,ActiveState,SubState,Result,ExecMainStatus,NRestarts,MainPID",
                          "basaltwater-guacamole.service", "basaltwater-guacamole-rdp.service",
                          "xrdp.service", "xrdp-sesman.service"]),
        journal=capture([*elevate, "journalctl", "--no-pager", "--since=-30min",
                         "--output=short-iso", "-n", "200",
                         "-u", "basaltwater-guacamole.service",
                         "-u", "basaltwater-guacamole-rdp.service"]),
        xrdp_logs=capture([*elevate, "tail", "-n", "120",
                           "/var/log/xrdp.log", "/var/log/xrdp-sesman.log"]),
        versions=capture(["dpkg-query", "--show",
                          "--showformat=${binary:Package}\t${Version}\n",
                          "basaltwater-guacamole", "xrdp", "xorgxrdp",
                          "libfreerdp3-3", "python3-aiohttp"]),
        source_commit=capture(["git", "-C", str(SOURCE_ROOT), "rev-parse", "HEAD"]),
        source_files=[source_info(SOURCE_ROOT / name) for name in (
            "desktop/config/guacamole.js", "common/service_tools/guacamole_service.py",
            "common/service_tools/guacamole_protocol.py", "lib/guacamole_config.py")],
        notes=["Read-only collection: no service restarts, desktop input, screenshots, "
               "password files, environment dumps, or configuration secrets.",
               "If elevated commands fail, run sudo -v as the desktop owner and retry.",
               "Review the report before sharing; credential redaction is best effort."],
    )
    return report


if __name__ == "__main__":
    print(json.dumps(collect(), indent=2, sort_keys=True))
