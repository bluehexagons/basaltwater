#!/usr/bin/env python3
"""Maintain Godot and independently selected game publishing tools."""

from __future__ import annotations

import os
import sys
from logging import ERROR

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../.."))

from common.godot_steps import (
    GODOT_BINARY_LINK,
    install_or_update_godot_release,
    update_registered_godot_bundles,
)
from lib.logging_utils import get_service_logger, log_event
from lib.notifications import load_notification_configs_from_state, send_notification_safe
from common.publishing_steps import PUBLISHING_TOOL_STATE, update_registered_publishing_tools


logger = get_service_logger("auto_update_godot", "common", use_syslog=True)


def main() -> int:
    """Maintain installed game tools without requiring the Godot engine."""
    notification_configs = load_notification_configs_from_state(logger)
    if not os.path.exists(GODOT_BINARY_LINK) and not os.path.exists(PUBLISHING_TOOL_STATE):
        log_event(logger, "Game tooling not registered, skipping update")
        return 0

    try:
        tag_name, engine_changed = "publishing tools", False
        bundle_changed = False
        if os.path.exists(GODOT_BINARY_LINK):
            tag_name, engine_changed, _archive_sha256 = install_or_update_godot_release()
            bundle_changed = update_registered_godot_bundles(include_publishing=False)
        bundle_changed = update_registered_publishing_tools() or bundle_changed
    except Exception as exc:
        details = str(exc)
        log_event(logger, "Game tooling update failed", level=ERROR, stderr=details)
        send_notification_safe(
            notification_configs,
            subject="Error: Game tooling update failed",
            job="auto_update_godot",
            status="error",
            message="Failed to maintain registered game tools",
            details=details,
            logger=logger,
        )
        return 1

    if not engine_changed and not bundle_changed:
        log_event(logger, "Game tooling already up to date", target_version=tag_name)
        return 0

    log_event(logger, "Game tooling updated successfully", target_version=tag_name)
    send_notification_safe(
        notification_configs,
        subject="Success: Game tooling updated",
        job="auto_update_godot",
        status="good",
        message=f"Registered game tools updated ({tag_name})",
        logger=logger,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
