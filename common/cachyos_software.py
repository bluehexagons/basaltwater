"""Opt-in publishing/material software, using the desktop's existing AUR helper."""

from __future__ import annotations

import os
from pathlib import Path
import shutil

from common import cachyos_aur as aur
from common.cachyos_steps import _home, _tool_path
from lib.config import SetupConfig
from lib.remote_utils import is_dry_run, run


# Preferred package first; retain the source-built Material Maker alternative.
AUR_SOFTWARE = (
    ("install_material_maker", "material-maker", ("material-maker-bin", "material-maker")),
    ("install_butler", "butler", ("butler",)),
    ("install_steamcmd", "steamcmd", ("steamcmd",)),
)


def selected_software(config: SetupConfig) -> list[tuple[str, tuple[str, ...]]]:
    return [(command, packages) for field, command, packages in AUR_SOFTWARE if getattr(config, field)]


def installed_package(packages: tuple[str, ...]) -> str | None:
    return next((package for package in packages if aur.package_version(package) is not None), None)


def _verify_executable(command: str, package: str, home: Path) -> None:
    executable = shutil.which(command, path=_tool_path(home))
    if not executable:
        raise RuntimeError(f"Selected {command} executable missing; repair its package before setup")
    owner = run(["pacman", "-Qqo", "--", executable], capture_output=True, check=False, timeout=15)
    if owner.returncode or owner.stdout.strip() != package:
        raise RuntimeError(f"{command} executable is not owned by {package}; inspect its installation and PATH")


def preflight_software(config: SetupConfig) -> None:
    selected = selected_software(config)
    if not selected or config.dry_run or is_dry_run():
        return
    if os.geteuid() == 0:
        raise ValueError("Run AUR setup as the existing desktop user, without sudo")
    home = _home(config)
    for command, packages in selected:
        package = installed_package(packages)
        if package:
            _verify_executable(command, package, home)
            continue
        if shutil.which(command, path=_tool_path(home)):
            raise ValueError(f"Existing {command} is not a supported pacman package; keep its original manager "
                             f"and omit --{command}, or migrate it explicitly before setup")
        helper = aur.install_command(home, packages[0])
        if Path(helper[0]).name == "shelly":
            aur.prepare_cache(home)
    if config.install_steamcmd:
        # These are in core on current Arch/CachyOS. Query enabled repositories
        # rather than editing pacman.conf or assuming multilib is required.
        for package in ("lib32-glibc", "lib32-gcc-libs"):
            if aur.package_version(package) is None:
                result = run(["pacman", "-Si", "--", package], capture_output=True, check=False, timeout=15)
                if result.returncode:
                    raise RuntimeError(f"SteamCMD requires {package} in the enabled repositories; "
                                       "resolve repository/full-system update issues before setup")


def install_software(config: SetupConfig) -> None:
    if config.dry_run or is_dry_run():
        print("  Would install selected AUR software with interactive source/build review")
        return
    preflight_software(config)
    home = _home(config)
    for command, packages in selected_software(config):
        package = installed_package(packages)
        if package is None:
            aur.install_package(aur.install_command(home, packages[0]), home)
            package = installed_package(packages)
            if package is None:
                raise RuntimeError(f"AUR installation finished without {packages[0]}; cancellation is not success")
        print(f"  {command}: retained package {package}; updates stay with its package manager")


def report_software_readiness(config: SetupConfig) -> None:
    """Check package ownership only: never launch a GUI, updater, or publisher."""
    for command, packages in selected_software(config):
        package = installed_package(packages)
        if package is None:
            raise RuntimeError(f"Selected {command} package/executable missing; rerun setup or repair its package")
        _verify_executable(command, package, _home(config))
        print(f"  {command}: package/executable verified; application use and publishing require a user test")
    if config.install_steamcmd:
        print("  SteamCMD has not been launched. Its first user launch downloads updates; authenticate locally.")
    if config.install_butler:
        print("  butler authentication and uploads are manual; no publishing credentials were read or changed")
