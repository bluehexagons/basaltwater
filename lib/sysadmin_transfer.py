"""rsync push/pull convenience wrappers."""

from __future__ import annotations

import os
import shutil
import sys
from typing import Optional

from lib.sysadmin_process import run_command
from lib.cache import load_setup_command
from lib.ssh_utils import build_rsync_ssh_transport, ssh_batch_mode


def _resolve_credentials(
    host: str,
    username: Optional[str],
    ssh_key: Optional[str],
    port: Optional[int],
) -> tuple[str, Optional[str], Optional[int]]:
    config = load_setup_command(host)
    if config:
        if not username:
            username = config.username
        if not ssh_key:
            ssh_key = config.ssh_key
        if port is None:
            port = getattr(config, "port", None)
    return username or "root", ssh_key, port


def _parse_remote(remote: str) -> tuple[str, str]:
    if ":" not in remote:
        print(f"Error: remote must be host:path, got {remote!r}", file=sys.stderr)
        raise ValueError(remote)
    host, path = remote.split(":", 1)
    return host, path


def _build_rsync_cmd(
    src: str,
    dst: str,
    *,
    ssh_key: Optional[str],
    port: Optional[int],
    delete: bool = False,
    dry_run: bool = False,
) -> list[str]:
    if not shutil.which("rsync"):
        print("Error: rsync is not installed.", file=sys.stderr)
        raise RuntimeError("rsync not found")

    transport = build_rsync_ssh_transport(
        ssh_key=ssh_key, port=port, batch_mode=ssh_batch_mode()
    )
    cmd = ["rsync", "-avP", "-e", transport, src, dst]
    if delete:
        cmd.append("--delete")
    if dry_run:
        cmd.append("--dry-run")
    return cmd


def run_push(
    local_path: str,
    remote: str,
    username: Optional[str] = None,
    ssh_key: Optional[str] = None,
    port: Optional[int] = None,
    delete: bool = False,
    dry_run: bool = False,
) -> int:
    try:
        host, remote_path = _parse_remote(remote)
    except ValueError:
        return 1

    username, ssh_key, port = _resolve_credentials(host, username, ssh_key, port)
    dst = f"{username}@{host}:{remote_path}"

    if delete and not dry_run:
        print(
            "Warning: --delete is set. Files on the remote not present locally will be removed.",
            file=sys.stderr,
        )
        try:
            confirm = input("Continue? [y/N] ").strip().lower()
        except EOFError:
            confirm = ""
        if confirm != "y":
            print("Aborted.", file=sys.stderr)
            return 1

    try:
        cmd = _build_rsync_cmd(local_path, dst, ssh_key=ssh_key, port=port, delete=delete, dry_run=dry_run)
    except RuntimeError:
        return 1

    if dry_run:
        print("Dry run — no files will be transferred.")
    result = run_command(cmd)
    return result.returncode


def run_pull(
    remote: str,
    local_path: Optional[str] = None,
    username: Optional[str] = None,
    ssh_key: Optional[str] = None,
    port: Optional[int] = None,
    dry_run: bool = False,
) -> int:
    try:
        host, remote_path = _parse_remote(remote)
    except ValueError:
        return 1

    username, ssh_key, port = _resolve_credentials(host, username, ssh_key, port)

    if local_path is None:
        local_path = os.path.basename(remote_path.rstrip("/")) or host
        print(f"Destination: ./{local_path}")

    src = f"{username}@{host}:{remote_path}"

    try:
        cmd = _build_rsync_cmd(src, local_path, ssh_key=ssh_key, port=port, dry_run=dry_run)
    except RuntimeError:
        return 1

    if dry_run:
        print("Dry run — no files will be transferred.")
    result = run_command(cmd)
    return result.returncode
