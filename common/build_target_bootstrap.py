"""Minimal target enrollment without replacing the saved application profile."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from common.build_target_steps import configure_build_target
from lib.machine_state import STATE_DIR
from lib.operation_state import OperationStateStore
from lib.remote_utils import is_dry_run

if TYPE_CHECKING:
    from lib.config import SetupConfig


BOOTSTRAP_STEP = "bootstrap_build_target"
SETUP_OPERATION_FILE = os.path.join(STATE_DIR, "setup-operation.json")


def bootstrap_build_target(config: SetupConfig) -> None:
    """Enroll only the typed helper; keep successful setup state untouched.

    The setup transport owns the bootstrap/runtime locks. The installer owns
    its durable enrollment marker and maintenance lease; this path never saves
    a temporary custom-step configuration as the machine's application profile.
    """
    from lib.config import SetupConfig

    if (config.system_type != "custom_steps" or config.custom_steps != BOOTSTRAP_STEP
            or config.build_target_enrollment is None):
        raise ValueError("Target bootstrap requires only explicit target enrollment")
    baseline = SetupConfig(
        host=config.host, username=config.username, system_type="custom_steps",
        machine_type=config.machine_type, custom_steps=BOOTSTRAP_STEP,
        build_target_enrollment=config.build_target_enrollment, dry_run=config.dry_run,
    )
    # Compare executable target intent, allowing the parser's equivalent
    # None/False/default-list representations after an argv round trip.
    if (config.build_on is not None or config.clear_build_on
            or config.to_remote_args() != baseline.to_remote_args()):
        raise ValueError("Target bootstrap refuses deployment, disable and unrelated setup options")
    if config.dry_run or is_dry_run():
        configure_build_target(config)
        print("  [DRY-RUN] Would bootstrap typed target enrollment without saving an application profile")
        return
    if os.geteuid() != 0:
        raise PermissionError("Target bootstrap requires root")
    with OperationStateStore(SETUP_OPERATION_FILE).ownership() as setup:
        if setup.load() is not None:
            raise RuntimeError("Unfinished target setup must be reconciled before target bootstrap")
        configure_build_target(config)
        print("  Typed target enrollment ready; saved application profile retained")
