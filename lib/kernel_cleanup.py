"""Plan obsolete kernel cleanup while preserving boot and APT retention."""

from __future__ import annotations

import argparse
from functools import cmp_to_key
import os
from pathlib import Path
import re
import subprocess

from lib.machine_state import can_modify_kernel, is_container
from lib.validation import validate_filesystem_path, validate_package_name

_RELEASE = re.compile(r"([0-9]+(?:[.+~-][0-9a-z]+)*?)-([a-z][a-z0-9]*(?:-[a-z][a-z0-9]*)*)")
_SERIES = re.compile(r"(?:proxmox|pve)-kernel-([0-9]+\.[0-9]+)(?:-signed)?")


def _query(command: list[str]) -> str:
    """Read package state with a bounded, checked command."""
    result = subprocess.run(
        command, check=True, capture_output=True, text=True, timeout=30,
    )
    if result.stderr.strip():
        raise RuntimeError(f"Kernel retention query reported an error: {command[0]}")
    return result.stdout


def _compare(left: str, right: str) -> int:
    """Compare recognized kernel versions using Debian's version rules."""
    if left == right:
        return 0
    result = subprocess.run(
        ["dpkg", "--compare-versions", left, "lt", right],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode not in (0, 1):
        raise RuntimeError("Could not compare kernel versions")
    return -1 if result.returncode == 0 else 1


def _has_image(release: str) -> bool:
    path = f"/boot/vmlinuz-{release}"
    validate_filesystem_path(path, must_exist=False)
    return Path(path).is_file()


def obsolete_manual_kernels() -> list[str]:
    """Plan metadata changes only; failures prevent the subsequent autoremove.

    Restrict images to the running flavour and versions older than the running
    kernel. Keep the newest older image as an additional fallback. Never touch
    default-kernel, helper, or unversioned Debian/Ubuntu tracking metapackages.
    APT still owns dependency resolution and removal, including boot-tool pins.
    """
    return _obsolete_kernels(manual_only=True)


def obsolete_kernel_packages() -> list[str]:
    """Plan explicit old-kernel removals, including automatically installed ones."""
    return _obsolete_kernels(manual_only=False)


def _proxmox_boot_selections(running: str) -> set[str]:
    """Read current native selections, including pins not yet refreshed in APT."""
    headers = {
        "Manually selected kernels:", "Automatically selected kernels:",
        "Pinned kernel:", "Kernel pinned on next-boot:",
    }
    seen = set()
    selected = set()
    section = ""
    for line in _query(["proxmox-boot-tool", "kernel", "list"]).splitlines():
        value = line.strip()
        if not value:
            continue
        if value in headers:
            if value in seen:
                raise ValueError("Duplicate Proxmox boot-selection section")
            section = value
            seen.add(value)
            continue
        if section == "Manually selected kernels:" and value == "None.":
            continue
        match = _RELEASE.fullmatch(value)
        if not section or not match or match.group(2) != "pve":
            raise ValueError("Invalid Proxmox boot-selection response")
        selected.add(value)
    if not {"Manually selected kernels:", "Automatically selected kernels:"}.issubset(seen) or running not in selected:
        raise RuntimeError("Cannot verify native Proxmox boot selections")
    return selected


def _obsolete_kernels(*, manual_only: bool) -> list[str]:
    if is_container() or not can_modify_kernel():
        return []
    running = os.uname().release
    match = _RELEASE.fullmatch(running)
    if match is None:
        return []
    flavour = match.group(2)
    inventory = _query(["dpkg-query", "-W", "-f=${db:Status-Status} ${Package}\\n"])
    installed: set[str] = set()
    for line in inventory.splitlines():
        fields = line.split()
        if len(fields) != 2:
            raise ValueError("Invalid kernel package inventory")
        if fields[0] == "installed":
            validate_package_name(fields[1])
            installed.add(fields[1])

    images: dict[str, str] = {}
    for package in installed:
        for prefix in ("proxmox-kernel-", "pve-kernel-", "linux-image-unsigned-", "linux-image-"):
            if package.startswith(prefix):
                release = package[len(prefix):].removesuffix("-signed")
                parsed = _RELEASE.fullmatch(release)
                if parsed and parsed.group(2) == flavour:
                    images[package] = release
                break
    if running not in images.values() or not _has_image(running):
        raise RuntimeError("Cannot verify the installed running kernel; skipping kernel management")

    boot_selected = _proxmox_boot_selections(running) if flavour == "pve" and not manual_only else set()
    selected = set(_query(["apt-mark", "showmanual"]).split()) if manual_only else installed
    held = set(_query(["apt-mark", "showhold"]).split())
    config = _query(["apt-config", "dump"])
    protections = []
    for line in config.splitlines():
        if line.startswith("APT::NeverAutoRemove::"):
            entry = re.fullmatch(r'APT::NeverAutoRemove:: "([^"\n]+)";', line)
            if entry is None:
                raise ValueError("Cannot parse APT kernel retention rules")
            try:
                protections.append(re.compile(entry.group(1)))
            except re.error as exc:
                raise ValueError("Invalid APT retention expression") from exc
    if not protections:
        raise RuntimeError("Missing APT retention rules; skipping kernel management")

    older = {
        release for release in images.values()
        if _compare(release, running) < 0 and _has_image(release)
    }
    fallback = max(older, key=cmp_to_key(_compare), default="")
    running_series = re.match(r"[0-9]+\.[0-9]+", running)
    candidates: list[str] = []
    for package in sorted((installed & selected) - held):
        if any(rule.search(package) for rule in protections):
            continue
        release = images.get(package)
        if release in boot_selected:
            continue
        if release and release in older and release != fallback:
            candidates.append(package)
            continue
        series = _SERIES.fullmatch(package)
        if (
            flavour == "pve" and series and running_series
            and _compare(series.group(1), running_series.group(0)) < 0
            and not any(version.startswith(series.group(1) + ".") for version in boot_selected)
        ):
            # Preserve the extra fallback's tracking package as well.
            if not fallback.startswith(series.group(1) + "."):
                candidates.append(package)
    return candidates


def validate_kernel_removal_actions(payload: str, allowed_packages: list[str]) -> None:
    """Abort an APT version-2 hook unless it removes exactly the planned kernels.

    This runs under APT's own locks, checking the real dependency solution as
    well as the earlier simulation. No install, upgrade, or configure is allowed.
    """
    if not allowed_packages:
        raise ValueError("No kernel removals were authorized")
    for package in allowed_packages:
        validate_package_name(package)
        if not package.startswith(("proxmox-kernel-", "pve-kernel-")):
            raise ValueError("Only versioned Proxmox kernels may be removed")
        release = package.split("kernel-", 1)[1].removesuffix("-signed")
        parsed = _RELEASE.fullmatch(release)
        if not _SERIES.fullmatch(package) and not (parsed and parsed.group(2) == "pve"):
            raise ValueError("Only versioned Proxmox kernels may be removed")
    lines = payload.splitlines()
    if not lines or lines[0] != "VERSION 2":
        raise ValueError("Missing APT version-2 removal protocol")
    try:
        separator = lines.index("", 1)
    except ValueError as exc:
        raise ValueError("Incomplete APT removal protocol") from exc
    if any("=" not in line for line in lines[1:separator]):
        raise ValueError("Invalid APT configuration header")
    removed = set()
    for line in lines[separator + 1:]:
        fields = line.split()
        if len(fields) != 5:
            raise ValueError("Invalid APT package action")
        name, old, direction, new, action = fields
        if name not in allowed_packages or old == "-" or (direction, new, action) != (">", "-", "**REMOVE**"):
            raise ValueError(f"Unapproved APT package action: {name}")
        removed.add(name)
    if removed != set(allowed_packages):
        raise ValueError("APT removals differ from the authorized kernel plan")


if __name__ == "__main__":
    # Read-only preview; does not change marks or run autoremove.
    parser = argparse.ArgumentParser(description="Preview obsolete kernel packages without changing the host")
    parser.add_argument("--all", action="store_true", help="include automatically installed kernels")
    args = parser.parse_args()
    for package in obsolete_kernel_packages() if args.all else obsolete_manual_kernels():
        print(package)
