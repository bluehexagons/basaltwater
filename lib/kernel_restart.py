"""Kernel restart markers and conservative Debian-family update advisories."""

from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess

from lib.atomic_io import write_text_atomic
from lib.validation import validate_filesystem_path

KERNEL_HOOK = "/etc/kernel/postinst.d/basaltwater-reboot-required"
_KERNEL_RELEASE = re.compile(
    r"([0-9]+(?:[.+~-][0-9a-z]+)*?)-([a-z][a-z0-9]*(?:-[a-z][a-z0-9]*)*)"
)

# Keep this hook independent of /opt, Python imports, and unattended-upgrades:
# package configuration must also work while basaltwater is being replaced.
KERNEL_HOOK_CONTENT = """#!/bin/sh
# Managed by basaltwater. Kernel package post-install hook.
set -eu
case "${1:-}" in
    ''|*[!a-zA-Z0-9.+_-]*) exit 0 ;;
esac
[ "$1" != "$(uname -r)" ] || exit 0
case "${DPKG_MAINTSCRIPT_PACKAGE:-}" in
    ''|*[!a-zA-Z0-9.+:-]*) package="kernel-$1" ;;
    *) package="$DPKG_MAINTSCRIPT_PACKAGE" ;;
esac
touch /run/reboot-required
if ! grep -Fqx -- "$package" /run/reboot-required.pkgs 2>/dev/null; then
    printf '%s\\n' "$package" >> /run/reboot-required.pkgs
fi
"""


def install_kernel_restart_hook() -> None:
    """Install the executable marker hook, preserving other packages' hooks."""
    validate_filesystem_path(KERNEL_HOOK, must_exist=False)
    write_text_atomic(KERNEL_HOOK, KERNEL_HOOK_CONTENT, mode=0o755)


def newer_installed_kernel() -> str | None:
    """Find a newer configured kernel of the running flavour with a boot image.

    This is advisory evidence only: package presence does not establish what
    GRUB or proxmox-boot-tool will boot, especially with a deliberate pin.
    Query failures are raised so callers cannot report a false all-clear.
    """
    running_release = os.uname().release
    running = _KERNEL_RELEASE.fullmatch(running_release)
    if running is None:
        return None
    result = subprocess.run(
        ["dpkg-query", "-W", "-f=${db:Status-Status} ${Package}\\n"],
        check=True, capture_output=True, text=True, timeout=30,
    )
    newest = running_release
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) != 2 or fields[0] != "installed":
            continue
        package = fields[1]
        for prefix in ("proxmox-kernel-", "pve-kernel-", "linux-image-unsigned-", "linux-image-"):
            if not package.startswith(prefix):
                continue
            release = package[len(prefix):].removesuffix("-signed")
            match = _KERNEL_RELEASE.fullmatch(release)
            if match is None or match.group(2) != running.group(2):
                continue
            image = f"/boot/vmlinuz-{release}"
            validate_filesystem_path(image, must_exist=False)
            if not Path(image).is_file():
                continue
            comparison = subprocess.run(
                ["dpkg", "--compare-versions", release, "gt", newest],
                check=False, capture_output=True, text=True, timeout=30,
            )
            if comparison.returncode not in (0, 1):
                raise RuntimeError("Could not compare installed kernel versions")
            if comparison.returncode == 0:
                newest = release
    return newest if newest != running_release else None
