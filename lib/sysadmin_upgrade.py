"""Remote apt upgrade across one or more hosts."""

from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from lib.sysadmin_fan import _resolve_credentials, _run_remote

_UPGRADE_COMMAND = (
    "sudo apt-get update -qq"
    " && sudo apt-get -y -q upgrade"
    " && ([ -f /run/reboot-required ] && echo 'REBOOT_REQUIRED' || echo 'no reboot needed')"
)

_CHECK_COMMAND = "apt-get -qq --just-print upgrade"


def run_upgrade(
    hosts: list[str],
    username: Optional[str] = None,
    ssh_key: Optional[str] = None,
    check_only: bool = False,
    max_workers: int = 10,
) -> int:
    command = _CHECK_COMMAND if check_only else _UPGRADE_COMMAND
    label = "Checking" if check_only else "Upgrading"
    print(f"{label} {len(hosts)} host(s)…\n")

    raw: list[tuple[str, int, str, str]] = []
    with ThreadPoolExecutor(max_workers=min(max_workers, len(hosts))) as pool:
        futures = {
            pool.submit(_run_remote, host, command, username, ssh_key): host
            for host in hosts
        }
        for future in as_completed(futures):
            raw.append(future.result())

    raw.sort(key=lambda r: r[0])

    any_failed = False
    reboot_needed: list[str] = []

    for host, rc, stdout, stderr in raw:
        if rc != 0:
            err = stderr.strip() or "command failed"
            print(f"  [FAIL] {host}: {err}", file=sys.stderr)
            any_failed = True
            continue

        out = stdout.strip()
        if check_only:
            count = sum(line.startswith("Inst ") for line in out.splitlines())
            print(f"  {host}: {count} package(s) pending")
        else:
            if "REBOOT_REQUIRED" in out:
                reboot_needed.append(host)
                print(f"  [OK, REBOOT] {host}")
            else:
                print(f"  [OK]         {host}")

    if reboot_needed:
        print(f"\nReboot required: {', '.join(reboot_needed)}")

    return 1 if any_failed else 0
