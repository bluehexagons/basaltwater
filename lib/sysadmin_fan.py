"""Parallel SSH fan-out and multi-host df."""

from __future__ import annotations

import shlex
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from lib.sysadmin_process import run_command
from lib.cache import load_setup_command
from lib.ssh_utils import build_ssh_command, ssh_batch_mode


def _resolve_credentials(
    host: str,
    username: Optional[str],
    ssh_key: Optional[str],
) -> tuple[str, Optional[str]]:
    config = load_setup_command(host)
    if config:
        if not username:
            username = config.username
        if not ssh_key:
            ssh_key = config.ssh_key
    return username or "root", ssh_key


def _run_remote(
    host: str,
    command: str,
    username: Optional[str],
    ssh_key: Optional[str],
) -> tuple[str, int, str, str]:
    """Run a command on one host, return (host, returncode, stdout, stderr)."""
    resolved_user, resolved_key = _resolve_credentials(host, username, ssh_key)
    cmd = build_ssh_command(
        host,
        resolved_user,
        resolved_key,
        batch_mode=ssh_batch_mode(),
        connect_timeout=15,
        remote_command=command,
    )
    result = run_command(cmd, capture_output=True, text=True)
    return host, result.returncode, result.stdout, result.stderr


# ---------------------------------------------------------------------------
# fan
# ---------------------------------------------------------------------------

def run_fan(
    hosts: list[str],
    remote_command: list[str],
    username: Optional[str] = None,
    ssh_key: Optional[str] = None,
    max_workers: int = 20,
) -> int:
    """Run a shell command on multiple hosts in parallel and print results."""
    command_str = shlex.join(remote_command)
    print(f"Running on {len(hosts)} host(s): {command_str}\n")

    results: list[tuple[str, int, str, str]] = []

    with ThreadPoolExecutor(max_workers=min(max_workers, len(hosts))) as pool:
        futures = {
            pool.submit(_run_remote, host, command_str, username, ssh_key): host
            for host in hosts
        }
        for future in as_completed(futures):
            results.append(future.result())

    # Sort by host for stable output
    results.sort(key=lambda r: r[0])

    any_failed = False
    for host, rc, stdout, stderr in results:
        status = "OK" if rc == 0 else f"FAILED (exit {rc})"
        bar = "=" * 60
        print(f"{bar}\n  {host}  [{status}]\n{bar}")
        if stdout.strip():
            print(stdout.rstrip())
        if stderr.strip():
            print(stderr.rstrip(), file=sys.stderr)
        print()
        if rc != 0:
            any_failed = True

    failed = [r[0] for r in results if r[1] != 0]
    succeeded = [r[0] for r in results if r[1] == 0]
    print(f"Summary: {len(succeeded)}/{len(results)} succeeded", end="")
    if failed:
        print(f", failed: {', '.join(failed)}", end="")
    print()

    return 1 if any_failed else 0


# ---------------------------------------------------------------------------
# df (multi-host disk table)
# ---------------------------------------------------------------------------

_DF_COMMAND = "df -h --output=pcent,size,used,avail,target"
_WARN_PCT = 85


def _parse_df_lines(raw: str, host: str) -> list[tuple[str, str, str, str, str, str]]:
    """Parse df --output=pcent,size,used,avail,target into (host, pct, size, used, avail, target) rows."""
    rows = []
    for line in raw.splitlines():
        parts = line.split()
        # Skip header row and empty lines
        if not parts or parts[0] in ("Use%", "Capacity", "IUse%"):
            continue
        if len(parts) >= 5:
            pct_str = parts[0].rstrip("%")
            try:
                int(pct_str)
            except ValueError:
                continue
            rows.append((host, parts[0], parts[1], parts[2], parts[3], parts[4]))
    return rows


def run_df(
    hosts: list[str],
    username: Optional[str] = None,
    ssh_key: Optional[str] = None,
    max_workers: int = 20,
) -> int:
    raw_results: list[tuple[str, int, str, str]] = []

    with ThreadPoolExecutor(max_workers=min(max_workers, len(hosts))) as pool:
        futures = {
            pool.submit(_run_remote, host, _DF_COMMAND, username, ssh_key): host
            for host in hosts
        }
        for future in as_completed(futures):
            raw_results.append(future.result())

    raw_results.sort(key=lambda r: r[0])

    all_rows: list[tuple[str, str, str, str, str, str, int]] = []
    for host, rc, stdout, stderr in raw_results:
        if rc != 0:
            msg = stderr.strip() or "command failed"
            print(f"Warning: {host}: {msg}", file=sys.stderr)
            continue
        for row in _parse_df_lines(stdout, host):
            pct = int(row[1].rstrip("%"))
            all_rows.append((*row, pct))

    if not all_rows:
        print("No results.")
        return 1

    # Sort by percent used descending
    all_rows.sort(key=lambda r: r[6], reverse=True)

    # Column widths
    header = ("HOST", "USE%", "SIZE", "USED", "AVAIL", "MOUNT")
    col_w = [
        max(len(header[i]), max(len(r[i]) for r in all_rows))
        for i in range(6)
    ]
    fmt = "  ".join(f"{{:<{w}}}" for w in col_w)

    print(fmt.format(*header))
    print("  ".join("-" * w for w in col_w))
    for row in all_rows:
        line = fmt.format(*row[:6])
        if row[6] >= _WARN_PCT:
            line = f"[!] {line}"
        else:
            line = f"    {line}"
        print(line)

    return 0
