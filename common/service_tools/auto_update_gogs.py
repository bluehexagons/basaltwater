#!/usr/bin/env python3
"""
Auto-update Gogs.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timezone
from logging import ERROR

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '../..'))

from lib.atomic_io import write_json_atomic
from lib.logging_utils import get_service_logger, log_event
from lib.notifications import load_notification_configs_from_state, send_notification_safe
from web.gogs_steps import (
    GOGS_BINARY_LINK,
    GOGS_CURRENT_DIR,
    GOGS_RELEASES_DIR,
    GOGS_SERVICE,
    build_gogs_admin_command,
    install_or_update_gogs_release,
    read_gogs_state,
    write_gogs_state,
)


logger = get_service_logger('auto_update_gogs', 'common', use_syslog=True)
GOGS_UPDATE_STATE_FILE = "/opt/basaltwater/state/gogs_update.json"


def _run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True)


def _run_shell_command(command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["/bin/bash", "-lc", command], capture_output=True, text=True)


def _derive_data_path(config_path: str) -> str | None:
    suffix = "/custom/conf/app.ini"
    if config_path.endswith(suffix):
        return config_path[:-len(suffix)]
    return None


def _current_release_path() -> str | None:
    """Return the active versioned release directory when one is installed."""
    current_binary = os.path.join(GOGS_CURRENT_DIR, "gogs")
    if not os.path.exists(current_binary):
        return None
    release_path = os.path.realpath(GOGS_CURRENT_DIR)
    releases_root = os.path.realpath(GOGS_RELEASES_DIR)
    try:
        if os.path.commonpath((release_path, releases_root)) != releases_root:
            return None
    except ValueError:
        return None
    return release_path


def _rollback_gogs_release(previous_release: str | None) -> bool:
    """Restore the previous release symlink and restart Gogs."""
    if not previous_release or not os.path.exists(os.path.join(previous_release, "gogs")):
        log_event(logger, "Gogs rollback unavailable", level=ERROR)
        return False

    link_result = _run_command(["ln", "-sfn", previous_release, GOGS_CURRENT_DIR])
    if link_result.returncode != 0:
        details = link_result.stderr.strip() or link_result.stdout.strip() or "ln failed"
        log_event(logger, "Gogs rollback symlink failed", level=ERROR, stderr=details)
        return False

    restart_result = _run_command(["systemctl", "restart", GOGS_SERVICE])
    if restart_result.returncode != 0:
        details = restart_result.stderr.strip() or restart_result.stdout.strip() or "systemctl restart failed"
        log_event(logger, "Gogs rollback restart failed", level=ERROR, stderr=details)
        return False

    log_event(logger, "Rolled Gogs back to previous release", release_path=previous_release)
    return True


def _details_with_rollback(details: str, rolled_back: bool) -> str:
    rollback_status = "Previous release restored." if rolled_back else "Previous release could not be restored."
    return f"{details}\n{rollback_status}"


def _run_update() -> int:
    """Update Gogs to the preferred upstream release when installed."""
    notification_configs = load_notification_configs_from_state(logger)
    state = read_gogs_state()
    config_path = state.get("config_path")
    installed_tag = state.get("tag_name")

    if not isinstance(config_path, str) or not config_path or not os.path.exists(config_path):
        log_event(logger, "Gogs config not found, skipping update")
        return 0

    if not os.path.exists(GOGS_BINARY_LINK):
        log_event(logger, "Gogs binary not found, skipping update")
        return 0

    previous_release = _current_release_path()
    log_event(logger, "Starting Gogs update check", current_version=installed_tag or "unknown")
    try:
        target_tag, changed, archive_sha256 = install_or_update_gogs_release()
    except Exception as exc:
        details = str(exc)
        log_event(logger, "Gogs release update failed", level=ERROR, stderr=details)
        send_notification_safe(
            notification_configs,
            subject="Error: Gogs update failed",
            job="auto_update_gogs",
            status="error",
            message="Failed to install the preferred Gogs release",
            details=details,
            logger=logger,
        )
        return 1

    if not changed:
        log_event(
            logger,
            "Gogs already up to date",
            current_version=installed_tag or target_tag,
            target_version=target_tag,
        )
        return 0

    data_path = state.get("data_path")
    if not isinstance(data_path, str) or not data_path:
        data_path = _derive_data_path(config_path)
    if not data_path:
        details = f"Missing Gogs data path for config {config_path}"
        rolled_back = _rollback_gogs_release(previous_release)
        log_event(logger, "Gogs state update failed", level=ERROR, stderr=details)
        send_notification_safe(
            notification_configs,
            subject="Error: Gogs update failed",
            job="auto_update_gogs",
            status="error",
            message="Updated Gogs but could not persist updated install state",
            details=_details_with_rollback(details, rolled_back),
            logger=logger,
        )
        return 1
    for admin_args, label in (
        (["admin", "rewrite-authorized-keys"], "authorized_keys"),
        (["admin", "resync-hooks"], "repository hooks"),
    ):
        result = _run_shell_command(build_gogs_admin_command(admin_args, config_path))
        if result.returncode != 0:
            details = result.stderr.strip() or result.stdout.strip() or f"Failed to refresh {label}"
            rolled_back = _rollback_gogs_release(previous_release)
            log_event(logger, "Gogs post-update command failed", level=ERROR, step=label, stderr=details)
            send_notification_safe(
                notification_configs,
                subject="Error: Gogs update failed",
                job="auto_update_gogs",
                status="error",
                message=f"Failed to refresh Gogs {label} after updating",
                details=_details_with_rollback(details, rolled_back),
                logger=logger,
            )
            return 1

    restart_result = _run_command(["systemctl", "restart", GOGS_SERVICE])
    if restart_result.returncode != 0:
        details = restart_result.stderr.strip() or restart_result.stdout.strip() or "systemctl restart failed"
        rolled_back = _rollback_gogs_release(previous_release)
        log_event(logger, "Gogs service restart failed", level=ERROR, stderr=details)
        send_notification_safe(
            notification_configs,
            subject="Error: Gogs update failed",
            job="auto_update_gogs",
            status="error",
            message="Updated Gogs but failed to restart the service",
            details=_details_with_rollback(details, rolled_back),
            logger=logger,
        )
        return 1

    try:
        write_gogs_state(target_tag, data_path, config_path, archive_sha256)
    except (OSError, ValueError) as exc:
        details = str(exc)
        rolled_back = _rollback_gogs_release(previous_release)
        log_event(logger, "Gogs state update failed", level=ERROR, stderr=details)
        send_notification_safe(
            notification_configs,
            subject="Error: Gogs update failed",
            job="auto_update_gogs",
            status="error",
            message="Updated Gogs but could not persist updated install state",
            details=_details_with_rollback(details, rolled_back),
            logger=logger,
        )
        return 1

    log_event(
        logger,
        "Gogs updated successfully",
        current_version=installed_tag or "unknown",
        target_version=target_tag,
    )
    send_notification_safe(
        notification_configs,
        subject="Success: Gogs updated",
        job="auto_update_gogs",
        status="good",
        message=f"Gogs updated to {target_tag}",
        logger=logger,
    )
    return 0


def _record_update_result(exit_code: int) -> None:
    """Persist the completion of every scheduled update check for health reporting."""

    write_json_atomic(
        GOGS_UPDATE_STATE_FILE,
        {
            "schema_version": 1,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "exit_code": exit_code,
            "successful": exit_code == 0,
        },
        mode=0o600,
        sort_keys=True,
    )


def main() -> int:
    """Run and record one Gogs update check."""

    result = 1
    try:
        result = _run_update()
        return result
    finally:
        _record_update_result(result)


if __name__ == "__main__":
    sys.exit(main())
