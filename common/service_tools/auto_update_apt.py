#!/usr/bin/env python3
"""
Auto-update APT Packages

This script updates all packages from all configured APT repositories using
dist-upgrade, while refusing automated package removals. It replaces the
traditional unattended-upgrades approach by:

- Not requiring any hardcoded origins or codenames
- Automatically handling all configured repositories
- Supporting dependency additions while refusing automated package removals

Logs to: /var/log/basaltwater/security/auto_update_apt.log
"""

from __future__ import annotations

import os
import subprocess
import sys
from logging import ERROR

# Add lib directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '../..'))

from lib.logging_utils import get_service_logger
from lib.logging_utils import log_event
from lib.apt_sources import ensure_debian_package_sources
from lib.maintenance_defaults import APT_LOCK_OPTIONS, APT_UPDATE_OPTIONS
from lib.notifications import load_notification_configs_from_state, send_notification_safe


# Initialize centralized logger
logger = get_service_logger('auto_update_apt', 'security', use_syslog=True)

# dpkg options to avoid interactive prompts during automated upgrades
DPKG_OPTIONS = [
    '-o', 'Dpkg::Options::=--force-confdef',
    '-o', 'Dpkg::Options::=--force-confold',
]
APT_UPGRADE_SAFETY_OPTIONS = [
    '--no-remove',
]

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


def update_package_lists() -> bool:
    """Run apt-get update to refresh package lists."""
    try:
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
        ['dist-upgrade', '-y', '-qq'] + APT_UPGRADE_SAFETY_OPTIONS + DPKG_OPTIONS + APT_LOCK_OPTIONS
    )
    output = result.stdout.strip()
    if result.returncode != 0:
        log_event(logger, "apt-get dist-upgrade failed", level=ERROR, stderr=result.stderr.strip())
        return False, result.stderr.strip()
    log_event(logger, "Packages upgraded successfully")
    return True, output


def main() -> int:
    """Main function to update APT packages."""
    log_event(logger, "Starting APT package update")
    notification_configs = load_notification_configs_from_state(logger)

    if not update_package_lists():
        send_notification_safe(
            notification_configs,
            subject="Error: APT update failed",
            job="auto_update_apt",
            status="error",
            message="Failed to update package lists (apt-get update)",
            logger=logger,
        )
        return 1

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

    log_event(logger, "APT package update completed successfully")
    return 0


if __name__ == "__main__":
    sys.exit(main())
