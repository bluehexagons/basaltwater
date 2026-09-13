"""sshfs mount/umount convenience wrappers."""

from __future__ import annotations

import os
import shutil
import sys
from typing import Optional

from lib.sysadmin_process import run_command
from lib.cache import load_setup_command
from lib.ssh_utils import get_workspace_known_hosts_path, ssh_batch_mode


def _resolve_host_credentials(
    host: str,
    username: Optional[str],
    ssh_key: Optional[str],
    port: Optional[int],
) -> tuple[str, Optional[str], Optional[int]]:
    """Pull username/key/port from saved config if not provided."""
    config = load_setup_command(host)
    if config:
        if not username:
            username = config.username
        if not ssh_key:
            ssh_key = config.ssh_key
        if port is None and hasattr(config, "port"):
            port = getattr(config, "port", None)
    return username or "root", ssh_key, port


def _build_sshfs_options(
    username: str,
    ssh_key: Optional[str],
    port: Optional[int],
    read_only: bool,
) -> list[str]:
    known_hosts = get_workspace_known_hosts_path()
    ssh_opts = [
        f"UserKnownHostsFile={known_hosts}",
        "StrictHostKeyChecking=yes",
        f"BatchMode={'yes' if ssh_batch_mode() else 'no'}",
        "ServerAliveInterval=30",
        "ServerAliveCountMax=3",
    ]
    if ssh_key:
        ssh_opts.append(f"IdentityFile={ssh_key}")

    opts = [f"ssh_command=ssh -o {' -o '.join(ssh_opts)}"]
    if port:
        opts.append(f"port={port}")
    opts.append("reconnect")
    if read_only:
        opts.append("ro")

    return opts


def run_mount(
    remote: str,
    local_path: str,
    username: Optional[str] = None,
    ssh_key: Optional[str] = None,
    port: Optional[int] = None,
    read_only: bool = False,
) -> int:
    """Mount a remote path via sshfs.

    remote should be in the form host:path.
    """
    if not shutil.which("sshfs"):
        print("Error: sshfs is not installed. Install it with: apt install sshfs", file=sys.stderr)
        return 1

    if ":" not in remote:
        print(f"Error: remote must be in host:path format, got: {remote!r}", file=sys.stderr)
        return 1

    host, remote_path = remote.split(":", 1)
    username, ssh_key, port = _resolve_host_credentials(host, username, ssh_key, port)

    local_path = os.path.expanduser(local_path)
    if not os.path.exists(local_path):
        try:
            os.makedirs(local_path, mode=0o755)
            print(f"Created mountpoint: {local_path}")
        except OSError as exc:
            print(f"Error: could not create mountpoint {local_path}: {exc}", file=sys.stderr)
            return 1

    opts = _build_sshfs_options(username, ssh_key, port, read_only)
    cmd = ["sshfs", f"{username}@{host}:{remote_path}", local_path, "-o", ",".join(opts)]

    print(f"Mounting {username}@{host}:{remote_path} → {local_path}")
    result = run_command(cmd)
    if result.returncode == 0:
        print("Mounted successfully.")
    return result.returncode


def run_umount(target: str) -> int:
    """Unmount an sshfs mount by local path or host name."""
    target = os.path.expanduser(target)

    # If target is not a path, try to find a mount using the host name
    if not os.path.exists(target) and not target.startswith("/"):
        result = run_command(
            ["findmnt", "--source-regex", f".*{target}.*", "-n", "-o", "TARGET"],
            capture_output=True,
            text=True,
        )
        mounts = result.stdout.strip().splitlines()
        if not mounts:
            print(f"Error: no sshfs mount found for host {target!r}", file=sys.stderr)
            return 1
        if len(mounts) > 1:
            print(f"Multiple mounts found for {target!r}:")
            for m in mounts:
                print(f"  {m}")
            print("Please specify the local path directly.", file=sys.stderr)
            return 1
        target = mounts[0]

    # Try fusermount first (user-space), fall back to umount
    for cmd in [["fusermount", "-u", target], ["umount", target]]:
        if shutil.which(cmd[0]):
            result = run_command(cmd)
            if result.returncode == 0:
                print(f"Unmounted {target}")
                return 0

    print(f"Error: could not unmount {target}", file=sys.stderr)
    return 1
