"""Optional native game tooling and project Node versions on CachyOS."""

from __future__ import annotations

import os
from pathlib import Path
import pwd
import shutil
import stat

from lib.config import SetupConfig
from lib.validation import validate_filesystem_path


# Arch packages include their development headers. Keep these aligned with
# Antistatic's pacman bootstrap; project scripts own version requirements.
GAME_DEV_MODULES = (
    "sdl3", "sdl3-image", "freetype2", "vorbisfile", "libusb-1.0", "glew", "openal",
)
GAME_DEV_PACKAGES = (
    "cmake", "ninja", "python", "pkgconf", "sdl3", "sdl3_image", "freetype2",
    "libvorbis", "libusb", "glew", "openal", "gdb", "ccache",
    "xorg-server-xvfb", "xorg-xauth", "mesa-utils",
    # npm's project-pinned Electron supplies the executable, not these host
    # libraries. Do not replace it with a distro Electron or disable its sandbox.
    "gtk3", "nss", "alsa-lib", "libnotify", "libxss", "libxtst", "libdrm",
    "libxkbcommon", "xdg-utils",
)
GAME_DEV_COMMANDS = (
    "cc", "cmake", "ninja", "python", "pkg-config", "gdb", "ccache", "Xvfb",
    "xvfb-run", "xauth", "glxinfo",
)


def nvm_script(home: Path) -> Path:
    """Validate the opt-in user installation before any setup changes."""
    root = home / ".nvm"
    configured = os.environ.get("NVM_DIR")
    if configured:
        validate_filesystem_path(configured)
        if Path(configured).resolve() != root.resolve():
            raise ValueError("--node-versions manages ~/.nvm; omit it to keep a custom NVM_DIR")
    validate_filesystem_path(str(root))
    for directory in (root, *root.parents):
        if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
            raise ValueError(f"Unsafe NVM directory: {directory}")
        if directory.is_relative_to(home) and directory.exists():
            info = directory.stat()
            if info.st_uid != os.getuid() or info.st_mode & 0o002:
                raise ValueError("NVM directories must be owned by you and not world-writable")
    script = root / "nvm.sh"
    if script.exists() or script.is_symlink():
        info = script.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o002:
            raise ValueError("NVM must use a regular nvm.sh owned by you and not world-writable")
    return script


def verify_node_versions(home: Path) -> None:
    from common.cachyos_steps import _user_run

    script = nvm_script(home)
    if not script.is_file():
        raise RuntimeError("User-local NVM missing; rerun setup with --node-versions")
    result = _user_run([
        "env", "NVM_DIR=" + str(script.parent), "/bin/bash", "--noprofile", "--norc", "-c",
        '. "$1" --no-use && nvm --version', "basaltwater-cachyos-nvm", str(script),
    ], home, cwd="/", capture_output=True, check=False, timeout=30)
    if result.returncode:
        raise RuntimeError("User-local NVM could not be loaded; repair ~/.nvm before rerunning setup")


def install_cachyos_node_versions(config: SetupConfig) -> None:
    """Install only NVM; Node versions and shell defaults remain explicit choices."""
    from common.cachyos_steps import _directory, _home, _tool_path, install_vendor_tool
    from lib.remote_utils import is_dry_run

    if config.dry_run or is_dry_run():
        print("  Would prepare user-local NVM without selecting Node or changing shell defaults")
        return
    home = _home(config)
    script = nvm_script(home)
    if not script.is_file():
        if script.parent.exists() and any(script.parent.iterdir()):
            raise ValueError("Existing ~/.nvm has no nvm.sh; repair it or move it aside explicitly")
        _directory(script.parent)
        # Do not inherit NODE_VERSION, NVM_SOURCE, alternate repositories, or
        # profile settings that can turn preparation into a runtime migration.
        environment = {
            "HOME": str(home), "PATH": _tool_path(home), "NVM_DIR": str(script.parent),
            "PROFILE": "/dev/null",
        }
        if install_vendor_tool("nvm", accept_vendor_channel=True, non_interactive=True,
                               environment=environment) != 0:
            raise RuntimeError("User-local NVM installation failed; rerun --node-versions after repair")
    verify_node_versions(home)
    print("  NVM ready; use basaltw node install from each project to install its pin")


def prepare_project_node_versions() -> Path:
    """Prepare NVM for an explicit project install without replaying host setup."""
    from lib.cachyos import preflight_cachyos

    account = pwd.getpwuid(os.getuid())
    config = SetupConfig(host="localhost", username=account.pw_name,
                         system_type="agent_cachyos", install_node_versions=True)
    preflight_cachyos(config)
    print("Preparing user-local NVM for this project install")
    install_cachyos_node_versions(config)
    return nvm_script(Path(account.pw_dir))


def report_game_dev_readiness(config: SetupConfig) -> None:
    """Check commands and headers without launching graphics or repository code."""
    from common.cachyos_steps import _home, _tool_path, _user_run

    home = _home(config)
    for command in GAME_DEV_COMMANDS:
        if not shutil.which(command, path=_tool_path(home)):
            raise RuntimeError(f"Game development command missing: {command}; rerun --game-dev")
    for module in GAME_DEV_MODULES:
        result = _user_run(["pkg-config", "--modversion", module], home,
                           cwd="/", capture_output=True, check=False, timeout=15)
        if result.returncode:
            raise RuntimeError(f"Game development pkg-config module missing: {module}; "
                               "inspect PKG_CONFIG_PATH and rerun --game-dev")
    print("  Game development: commands and native modules available; run the project's doctor/build/tests")
    print("  Graphics and Animator: verify game captures and project-pinned Electron in the KDE session")
