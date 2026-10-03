"""Local safety checks for the supported stable Proxmox installation."""

from __future__ import annotations

import os
import re
import shutil
from urllib.parse import urlsplit

from lib.apt_sources import inspect_apt_sources
from lib.proxmox_maintenance import ProxmoxMaintenanceReport, collect_local_maintenance_report
from lib.remote_utils import read_os_release, run
from lib.validation import validate_filesystem_path


SUPPORTED_PVE_RELEASE = "9.2"
SUPPORTED_DEBIAN_CODENAME = "trixie"


def is_proxmox_host() -> bool:
    """Identify a hypervisor independently of its saved setup profile."""
    return os.path.isdir("/etc/pve") or shutil.which("pveversion") is not None


def check_proxmox_installation() -> None:
    """Reject unsupported releases and unstable or incomplete repositories.

    Repository access and signatures must also pass a strict APT refresh before
    any package installation. This check never changes repository selections.
    """
    release = read_os_release()
    if (release.get("ID"), release.get("VERSION_CODENAME")) != (
        "debian", SUPPORTED_DEBIAN_CODENAME,
    ):
        raise RuntimeError("Proxmox setup requires Debian 13 (trixie)")
    version = run("pveversion", check=False, capture_output=True, timeout=60)
    if version.returncode != 0 or not re.match(
        rf"^pve-manager/{re.escape(SUPPORTED_PVE_RELEASE)}(?:[.-]|/)",
        (version.stdout or "").strip(),
    ):
        raise RuntimeError(
            f"Only stable Proxmox VE {SUPPORTED_PVE_RELEASE} is supported; "
            "upgrade the host through Proxmox's documented procedure first"
        )

    sources = inspect_apt_sources(SUPPORTED_DEBIAN_CODENAME)
    if sources.cdrom_sources or not (
        sources.has_official_base and sources.has_official_security
    ):
        raise RuntimeError(
            "Proxmox requires enabled trixie Debian base and security repositories "
            "and no active CD-ROM sources; repair APT sources before setup"
        )
    has_pve = False
    for entry in sources.entries:
        uri = urlsplit(entry.uri)
        hostname = (uri.hostname or "").lower()
        path = uri.path.rstrip("/")
        if hostname in {"enterprise.proxmox.com", "download.proxmox.com"}:
            if path == "/debian/pve":
                expected = (
                    "pve-enterprise" if hostname == "enterprise.proxmox.com"
                    else "pve-no-subscription"
                )
                if entry.suite != SUPPORTED_DEBIAN_CODENAME or entry.components != (expected,):
                    raise RuntimeError(
                        f"Unsupported Proxmox repository in {entry.path}: "
                        "use the trixie enterprise or no-subscription channel"
                    )
                has_pve = True
            elif path.startswith("/debian/ceph-"):
                if entry.suite != SUPPORTED_DEBIAN_CODENAME or any(
                    component not in {"enterprise", "no-subscription"}
                    for component in entry.components
                ) or not entry.components:
                    raise RuntimeError(f"Unsupported Ceph repository in {entry.path}")
        elif hostname == "debian.org" or hostname.endswith(".debian.org"):
            if entry.suite not in {
                "trixie", "trixie-updates", "trixie-security", "trixie-backports",
            }:
                raise RuntimeError(f"Unsupported Debian suite in {entry.path}: {entry.suite}")
    if not has_pve:
        raise RuntimeError("No supported stable Proxmox repository is enabled")


def _read_config(path: str, *, required: bool = False) -> str:
    validate_filesystem_path(path, must_exist=False)
    try:
        with open(path, encoding="utf-8") as config:
            content = config.read(1024 * 1024 + 1)
    except FileNotFoundError:
        if required:
            raise RuntimeError(f"Required Proxmox configuration is missing: {path}")
        return ""
    if len(content) > 1024 * 1024:
        raise RuntimeError(f"Proxmox configuration is unexpectedly large: {path}")
    return "\n".join(line for line in content.splitlines() if not line.lstrip().startswith("#"))


def check_proxmox_upgrade_candidate() -> None:
    """Refuse a release transition discovered after refreshing package indexes."""
    result = run("LC_ALL=C apt-cache policy pve-manager", check=False, capture_output=True, timeout=60)
    match = re.search(r"^\s*Candidate:\s*(\S+)\s*$", result.stdout or "", re.MULTILINE)
    candidate = match.group(1).split(":")[-1] if match else ""
    if result.returncode != 0 or not re.match(
        rf"^{re.escape(SUPPORTED_PVE_RELEASE)}(?:[.-]|$)", candidate,
    ):
        raise RuntimeError(
            f"APT pve-manager candidate is not supported Proxmox VE {SUPPORTED_PVE_RELEASE}; "
            "review repository selection and perform release transitions manually"
        )


def check_proxmox_package_state() -> None:
    """Reject incomplete transactions and held core packages before mutations."""
    audit = run("dpkg --audit", check=False, capture_output=True, timeout=60)
    if audit.returncode != 0 or (audit.stdout or "").strip() or (audit.stderr or "").strip():
        raise RuntimeError("dpkg reports an incomplete package transaction; repair it first")
    holds = run("apt-mark showhold", check=False, capture_output=True, timeout=60)
    if holds.returncode != 0 or (holds.stderr or "").strip():
        raise RuntimeError("Could not inspect held packages")
    held_core = [name for name in (holds.stdout or "").split() if name.startswith(
        (
            "proxmox-", "pve-", "libpve-", "libproxmox-", "qemu-server",
            "corosync", "libcorosync", "libknet", "libqb", "ceph",
            "zfs", "libzfs", "libzpool", "lxc", "liblxc",
        )
    )]
    if held_core:
        raise RuntimeError("Held Proxmox packages require review: " + ", ".join(held_core))


def check_proxmox_update_safety(*, require_evacuated: bool = False) -> ProxmoxMaintenanceReport:
    """Gate node maintenance and return its audit without coordinating other nodes."""
    if _read_config("/etc/pve/ha/resources.cfg").strip():
        raise RuntimeError("HA resources require operator-managed Proxmox maintenance")
    storage = _read_config("/etc/pve/storage.cfg", required=True)
    if re.search(r"^\s*(rbd|cephfs)\s*:", storage, re.MULTILINE) or any(
        _read_config(path).strip()
        for path in ("/etc/pve/ceph.conf", "/etc/ceph/ceph.conf")
    ):
        raise RuntimeError("Ceph requires operator-managed Proxmox maintenance")
    check_proxmox_package_state()
    report = collect_local_maintenance_report()
    if not report.healthy:
        raise RuntimeError("Proxmox maintenance checks failed: " + "; ".join(report.errors))
    if require_evacuated and not report.reboot_safe:
        raise RuntimeError("Proxmox reboot blocked: " + "; ".join(report.reboot_blockers()))
    return report
