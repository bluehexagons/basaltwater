"""Remote apt upgrade across one or more hosts."""

from __future__ import annotations

import re
import shlex
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from lib.maintenance_defaults import APT_LOCK_OPTIONS, APT_UPDATE_OPTIONS, APT_UPGRADE_SAFETY_OPTIONS
from lib.sysadmin_fan import _run_remote

_DEBIAN_VERSION = """if [ -r /etc/os-release ] && [ -r /etc/debian_version ]; then
    (
        . /etc/os-release
        if [ "${ID:-}" = debian ]; then
            printf 'BASALTWATER_DEBIAN_VERSION=%s\\n' "$(cat /etc/debian_version)"
        fi
    )
fi"""


def _upgrade_command(*, check_only: bool, with_new_pkgs: bool) -> str:
    options = [*APT_UPGRADE_SAFETY_OPTIONS]
    if with_new_pkgs:
        options.append("--with-new-pkgs")

    if check_only:
        simulate = shlex.join(["apt-get", "--simulate", "-q", *options, "upgrade"])
        script = f"{_DEBIAN_VERSION}\nLC_ALL=C {simulate}"
    else:
        update = shlex.join(["apt_as_root", "-qq", *APT_LOCK_OPTIONS, *APT_UPDATE_OPTIONS, "update"])
        upgrade = shlex.join(["apt_as_root", "-y", "-q", *APT_LOCK_OPTIONS, *options, "upgrade"])
        script = f"""apt_as_root() {{
    if [ "$(id -u)" = 0 ]; then apt-get "$@"; else sudo -n apt-get "$@"; fi
}}
{update} && {upgrade} || exit "$?"
{_DEBIAN_VERSION}
if [ -f /run/reboot-required ]; then echo REBOOT_REQUIRED; fi"""
    return shlex.join(["sh", "-c", script])


def run_upgrade(
    hosts: list[str],
    username: Optional[str] = None,
    ssh_key: Optional[str] = None,
    check_only: bool = False,
    max_workers: int = 10,
    *,
    with_new_pkgs: bool = False,
) -> int:
    command = _upgrade_command(check_only=check_only, with_new_pkgs=with_new_pkgs)
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

        lines = stdout.splitlines()
        version = re.search(r"^BASALTWATER_DEBIAN_VERSION=([0-9]+(?:\.[0-9]+)*)$", stdout, re.MULTILINE)
        release = f" (Debian {version.group(1)})" if version else ""
        if check_only:
            count = sum(line.startswith("Inst ") for line in lines)
            print(f"  {host}: {count} package(s) pending{release}")
        else:
            if "REBOOT_REQUIRED" in lines:
                reboot_needed.append(host)
                print(f"  [OK, REBOOT] {host}{release}")
            else:
                print(f"  [OK]         {host}{release}")

    if reboot_needed:
        print(f"\nReboot required: {', '.join(reboot_needed)}")

    return 1 if any_failed else 0
