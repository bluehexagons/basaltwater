"""Check reachability of saved hosts."""

from __future__ import annotations

import fnmatch
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from lib.sysadmin_process import run_command
from lib.cache import load_setup_command
from lib.ssh_utils import build_ssh_command, ssh_batch_mode
from lib.workspace import get_setup_cache_dir
import os


def _list_saved_hosts() -> list[str]:
    """Return all host names from saved setup cache."""
    cache_dir = get_setup_cache_dir()
    if not os.path.exists(cache_dir):
        return []

    import json
    hosts = []
    for fname in os.listdir(cache_dir):
        if not fname.endswith(".json"):
            continue
        try:
            with open(os.path.join(cache_dir, fname)) as f:
                data = json.load(f)
            host = data.get("host", "")
            if host:
                hosts.append(host)
        except (OSError, ValueError):
            continue
    return sorted(set(hosts))


def _probe_host(host: str, username: Optional[str], ssh_key: Optional[str]) -> tuple[str, bool, float]:
    """Return (host, reachable, latency_ms)."""
    config = load_setup_command(host)
    resolved_user = username or (config.username if config else "root")
    resolved_key = ssh_key or (config.ssh_key if config else None)

    cmd = build_ssh_command(
        host,
        resolved_user,
        resolved_key,
        batch_mode=ssh_batch_mode(),
        connect_timeout=5,
        server_alive_interval=None,
        remote_command="true",
    )

    start = time.monotonic()
    result = run_command(cmd, capture_output=True, timeout=10)
    if result.returncode == 124:
        print(f"{host}: command timed out", file=sys.stderr)
    elapsed_ms = (time.monotonic() - start) * 1000
    return host, result.returncode == 0, elapsed_ms


def run_reachable(
    pattern: Optional[str] = None,
    hosts: Optional[list[str]] = None,
    username: Optional[str] = None,
    ssh_key: Optional[str] = None,
    max_workers: int = 30,
) -> int:
    if hosts:
        target_hosts = hosts
    else:
        target_hosts = _list_saved_hosts()
        if not target_hosts:
            print("No saved hosts found. Use 'infra-tools list' to see saved configurations.")
            return 0
        if pattern:
            target_hosts = [h for h in target_hosts if fnmatch.fnmatch(h, pattern)]
        if not target_hosts:
            print(f"No saved hosts match pattern {pattern!r}.")
            return 0

    print(f"Probing {len(target_hosts)} host(s)…\n")

    results: list[tuple[str, bool, float]] = []
    with ThreadPoolExecutor(max_workers=min(max_workers, len(target_hosts))) as pool:
        futures = {
            pool.submit(_probe_host, host, username, ssh_key): host
            for host in target_hosts
        }
        for future in as_completed(futures):
            results.append(future.result())

    results.sort(key=lambda r: r[0])

    reachable = [r for r in results if r[1]]
    unreachable = [r for r in results if not r[1]]

    host_w = max(len(r[0]) for r in results)
    for host, ok, ms in results:
        status = f"OK   {ms:6.0f}ms" if ok else "UNREACHABLE"
        print(f"  {host:<{host_w}}  {status}")

    print(f"\n{len(reachable)}/{len(results)} reachable", end="")
    if unreachable:
        print(f"  |  unreachable: {', '.join(r[0] for r in unreachable)}", end="")
    print()

    return 0 if not unreachable else 1
