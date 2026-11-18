#!/usr/bin/env python3
"""
Auto-update APT Packages

This script refreshes and upgrades from Debian security independently of the
guarded distribution upgrade across all configured APT repositories.

Logs to: /var/log/basaltwater/security/auto_update_apt.log
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from logging import ERROR, WARNING
from pathlib import Path

# Add lib directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '../..'))

from lib.logging_utils import get_service_logger
from lib.logging_utils import log_event
from lib.apt_sources import debian_security_source, ensure_debian_package_sources
from lib.maintenance_defaults import (
    APT_DPKG_OPTIONS,
    APT_LOCK_OPTIONS,
    APT_UPDATE_OPTIONS,
    APT_UPGRADE_SAFETY_OPTIONS,
)
from lib.notifications import load_notification_configs_from_state, send_notification_safe
from lib.maintenance_lock import maintenance_lock
from lib.proxmox_preflight import (
    check_proxmox_installation,
    check_proxmox_update_safety,
    check_proxmox_upgrade_candidate,
    is_proxmox_host,
)
from web.cloudflared_runtime import refresh_cloudflared_service


# Initialize centralized logger
logger = get_service_logger('auto_update_apt', 'security', use_syslog=True)

def run_apt_command(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run an apt-get command with non-interactive settings."""
    env = os.environ.copy()
    env['DEBIAN_FRONTEND'] = 'noninteractive'
    cmd = ['apt-get'] + args
    try:
        return subprocess.run(cmd, capture_output=True, text=True, env=env)
    except OSError as exc:
        # Feed launch failures through the same logging/notification path as
        # an APT nonzero exit. The systemd unit bounds the whole transaction.
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr=str(exc))


def update_package_lists(*, repair_sources: bool = True) -> bool:
    """Run apt-get update to refresh package lists."""
    try:
        if repair_sources:
            ensure_debian_package_sources()
    except (OSError, RuntimeError, ValueError) as exc:
        log_event(
            logger,
            "Debian APT source preflight failed",
            level=ERROR,
            stderr=str(exc),
        )
        return False

    result = run_apt_command(['update', '-qq'] + APT_LOCK_OPTIONS + APT_UPDATE_OPTIONS)
    if result.returncode != 0:
        log_event(logger, "apt-get update failed", level=ERROR, stderr=result.stderr.strip())
        return False
    log_event(logger, "Package lists updated")
    return True


def upgrade_packages() -> tuple[bool, str]:
    """Run apt-get dist-upgrade to upgrade all packages without removals.

    Returns:
        Tuple of (success, output_summary).
    """
    result = run_apt_command(
        ['dist-upgrade', '-y', '-qq'] + APT_UPGRADE_SAFETY_OPTIONS + APT_DPKG_OPTIONS + APT_LOCK_OPTIONS
    )
    output = result.stdout.strip()
    if result.returncode != 0:
        log_event(logger, "apt-get dist-upgrade failed", level=ERROR, stderr=result.stderr.strip())
        return False, result.stderr.strip()
    log_event(logger, "Packages upgraded successfully")
    return True, output


def upgrade_debian_security_packages() -> tuple[bool, str]:
    """Apply signed Debian security updates using fresh, isolated indexes."""
    try:
        source = debian_security_source()
        if source is None:
            return True, ""
        with tempfile.TemporaryDirectory(prefix="basaltwater-apt-security-") as directory:
            root = Path(directory)
            root.chmod(0o755)  # Let the APT download sandbox traverse the directory.
            indexes = root / "lists"
            indexes.mkdir()
            indexes.chmod(0o755)
            source_parts = root / "sourceparts"
            source_parts.mkdir()
            sources = root / "security.sources"
            sources.write_text(source, encoding="utf-8")
            sources.chmod(0o644)
            config = root / "security.conf"
            # Normal update hooks can regenerate host caches using this
            # temporary package universe. Dpkg install hooks still run.
            config.write_text(
                "#clear APT::Update::Pre-Invoke;\n"
                "#clear APT::Update::Post-Invoke;\n"
                "#clear APT::Update::Post-Invoke-Success;\n",
                encoding="utf-8",
            )
            options = [
                "-c", str(config),
                "-o", f"Dir::Etc::sourcelist={sources}",
                "-o", f"Dir::Etc::sourceparts={source_parts}",
                "-o", f"Dir::State::lists={indexes}",
                "-o", "Dir::Cache::pkgcache=",
                "-o", "Dir::Cache::srcpkgcache=",
                "-o", "APT::Default-Release=",
                "-o", "Acquire::AllowInsecureRepositories=false",
                "-o", "Acquire::AllowDowngradeToInsecureRepositories=false",
                "-o", "APT::Get::AllowUnauthenticated=false",
                *APT_LOCK_OPTIONS,
            ]
            result = run_apt_command(["update", "-qq", *options, *APT_UPDATE_OPTIONS])
            if result.returncode:
                return False, "Security index refresh failed: " + result.stderr.strip()
            result = run_apt_command([
                "upgrade", "-y", "-qq", "--with-new-pkgs", *options,
                *APT_UPGRADE_SAFETY_OPTIONS, *APT_DPKG_OPTIONS,
            ])
            if result.returncode:
                return False, "Security upgrade failed: " + result.stderr.strip()
    except (OSError, RuntimeError, ValueError) as exc:
        return False, str(exc)
    log_event(logger, "Debian security upgrade completed")
    return True, ""


def main() -> int:
    """Main function to update APT packages."""
    log_event(logger, "Starting APT package update")
    notification_configs = load_notification_configs_from_state(logger)
    try:
        with maintenance_lock() as acquired:
            if not acquired:
                log_event(logger, "APT update deferred while setup or maintenance is running")
                return 0
            return _update_packages(notification_configs)
    except (OSError, TimeoutError, RuntimeError, ValueError) as exc:
        log_event(logger, "APT maintenance stopped", level=ERROR, error=str(exc))
        send_notification_safe(
            notification_configs, subject="Error: APT maintenance stopped",
            job="auto_update_apt", status="error", message=str(exc), logger=logger,
        )
        return 1


def _update_packages(notification_configs) -> int:
    """Run one transaction while setup and other local maintenance are excluded."""
    proxmox = is_proxmox_host()
    security_success = True
    if proxmox:
        check_proxmox_installation()
        report = check_proxmox_update_safety(allow_inactive_storage=True)
        for warning in report.warnings:
            log_event(logger, "Proxmox maintenance warning", level=WARNING, warning=warning)
    else:
        security_success, details = upgrade_debian_security_packages()
        if not security_success:
            log_event(logger, "Debian security upgrade failed", level=ERROR, error=details)
            send_notification_safe(
                notification_configs, subject="Error: Debian security upgrade failed",
                job="auto_update_apt", status="error",
                message="Independent Debian security upgrade failed", details=details, logger=logger,
            )

    refreshed = (
        update_package_lists(repair_sources=False) if proxmox else update_package_lists()
    )
    if not refreshed:
        send_notification_safe(
            notification_configs,
            subject="Error: APT update failed",
            job="auto_update_apt",
            status="error",
            message="Failed to update package lists (apt-get update)",
            logger=logger,
        )
        return 1

    if proxmox:
        # Recheck after downloading indexes: a backup or migration may have begun.
        check_proxmox_upgrade_candidate()
        check_proxmox_update_safety(allow_inactive_storage=True)
    success, output = upgrade_packages()
    if not success:
        send_notification_safe(
            notification_configs,
            subject="Error: APT upgrade failed",
            job="auto_update_apt",
            status="error",
            message="Failed to upgrade packages (apt-get dist-upgrade)",
            details=output,
            logger=logger,
        )
        return 1

    if proxmox:
        check_proxmox_installation()
        check_proxmox_upgrade_candidate(require_current=True)
        check_proxmox_update_safety(allow_inactive_storage=True)
    if refresh_cloudflared_service():
        log_event(logger, "Restarted cloudflared to load the updated package")
    if not security_success:
        return 1
    log_event(logger, "APT package update completed successfully")
    return 0


if __name__ == "__main__":
    sys.exit(main())
