"""Provision Debian game headers and Animator runtime libraries explicitly."""

from __future__ import annotations

import os
import shlex

from lib.atomic_io import write_json_atomic
from lib.config import SetupConfig
from lib.game_development import DEBIAN_GAME_PACKAGES, SELECTION_PATH
from lib.remote_utils import is_dry_run, run
from lib.validation import validate_filesystem_path, validate_package_name


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
    validate_filesystem_path(str(SELECTION_PATH))
    for directory in (SELECTION_PATH.parent, *SELECTION_PATH.parent.parents):
        if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
            raise ValueError("Unsafe game development selection directory")
    if SELECTION_PATH.is_symlink() or (SELECTION_PATH.exists() and not SELECTION_PATH.is_file()):
        raise ValueError("Unsafe game development selection path")
    directory = SELECTION_PATH.parent
    directory.mkdir(mode=0o755, parents=True, exist_ok=True)
    if directory.stat().st_uid != os.geteuid():
        raise ValueError("Game development selection directory must belong to the setup user")
    # Only this dedicated directory contains public selection metadata. Keep
    # it traversable even when setup runs under a restrictive root umask.
    directory.chmod(0o755)
    write_json_atomic(str(SELECTION_PATH), {
        "schema_version": 1, "selected": bool(config.install_game_dev),
    }, mode=0o644)
