"""Provision Debian game headers and Animator runtime libraries explicitly."""

from __future__ import annotations

import os
import shlex

from lib.atomic_io import write_json_atomic
from lib.config import SetupConfig
from lib.game_development import DEBIAN_GAME_PACKAGES, SELECTION_PATH
from lib.remote_utils import is_dry_run, run
from lib.validation import validate_package_name


def install_game_development(config: SetupConfig) -> None:
    from common.common_steps import _APT_GET, is_package_installed

    if config.dry_run or is_dry_run():
        print("  [DRY-RUN] Would install native development headers, checks/captures and Animator libraries")
        return
    for package in DEBIAN_GAME_PACKAGES:
        validate_package_name(package)
    missing = [package for package in DEBIAN_GAME_PACKAGES if not is_package_installed(package)]
    if missing:
        result = run([*shlex.split(_APT_GET), "install", "-y", "-qq", *missing], check=False,
                     env={**os.environ, "DEBIAN_FRONTEND": "noninteractive"})
        remaining = [package for package in missing if not is_package_installed(package)]
        if result.returncode or remaining:
            raise RuntimeError("Game development packages failed to install: " + ", ".join(remaining or missing))
    record_game_development_selection(config)
    print("  Game development host prerequisites ready; run the project's SDL bootstrap and doctor")


def record_game_development_selection(config: SetupConfig) -> None:
    if config.dry_run or is_dry_run():
        return
    if not config.install_game_dev and not os.path.lexists(SELECTION_PATH):
        return
    if SELECTION_PATH.is_symlink() or SELECTION_PATH.parent.is_symlink():
        raise ValueError("Unsafe game development selection path")
    write_json_atomic(str(SELECTION_PATH), {
        "schema_version": 1, "selected": bool(config.install_game_dev),
    }, mode=0o644)
