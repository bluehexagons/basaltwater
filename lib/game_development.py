"""Debian host prerequisites and read-only native game development inventory."""

from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import stat
import subprocess

from lib.atomic_io import read_json_file


SELECTION_PATH = Path("/var/lib/basaltwater/game-development.json")
DEBIAN_GAME_PACKAGES = (
    "build-essential", "cmake", "ninja-build", "python3", "pkg-config",
    "gdb", "strace", "valgrind", "ccache", "clang-format", "clang-tidy", "glslang-tools",
    "xvfb", "xauth", "mesa-utils", "xdg-utils",
    "libfreetype-dev", "libvorbis-dev", "libusb-1.0-0-dev", "libglew-dev", "libopenal-dev",
    # Headers also supply the correct runtime variants across Debian releases,
    # including t64 transitions. SDL's actual versions remain project-owned.
    "libasound2-dev", "libdecor-0-dev", "libdrm-dev", "libegl1-mesa-dev",
    "libglib2.0-dev", "libjpeg-dev", "libpng-dev", "libpulse-dev", "libtiff-dev",
    "libudev-dev", "libwayland-dev", "libwebp-dev", "libx11-dev", "libxcursor-dev",
    "libxext-dev", "libxfixes-dev", "libxi-dev", "libxinerama-dev",
    "libxkbcommon-dev", "libxrandr-dev", "libxrender-dev",
    "libgtk-3-dev", "libnss3", "libnotify4", "libxss1", "libxtst6", "libgbm-dev",
)
NATIVE_COMMANDS = (
    "cc", "cmake", "ninja", "python3", "pkg-config", "gdb", "ccache",
    "Xvfb", "xvfb-run", "xauth", "glxinfo", "clang-format", "clang-tidy", "glslangValidator",
)
NATIVE_MODULES = (
    "sdl3", "sdl3-image", "freetype2", "vorbisfile", "libusb-1.0", "glew", "openal",
)
SOURCE_MODULES = {"sdl3", "sdl3-image"}
_VERSION = re.compile(r"[0-9][A-Za-z0-9.+_~-]{0,63}", re.ASCII)


def game_development_selected() -> bool | None:
    """None indicates unsafe/incomplete selection, never an inferred opt-in."""
    try:
        info = SELECTION_PATH.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            return None
        value = read_json_file(str(SELECTION_PATH), max_bytes=1024)
        if (not isinstance(value, dict) or set(value) != {"schema_version", "selected"}
                or type(value["schema_version"]) is not int or value["schema_version"] != 1
                or type(value["selected"]) is not bool):
            return None
        return value["selected"]
    except FileNotFoundError:
        return False
    except (OSError, ValueError, UnicodeError):
        return None


def inspect_native_development(*, selected: bool | None = None) -> dict[str, object]:
    """Inventory commands/modules; never launch graphics or repository code."""
    selection = game_development_selected() if selected is None else selected
    commands = {name: shutil.which(name, path="/usr/local/bin:/usr/bin:/bin") is not None
                for name in NATIVE_COMMANDS}
    modules: dict[str, str | None] = dict.fromkeys(NATIVE_MODULES)
    pkg_config = shutil.which("pkg-config", path="/usr/local/bin:/usr/bin:/bin")
    if pkg_config:
        environment = {
            "PATH": "/usr/local/bin:/usr/bin:/bin", "LC_ALL": "C",
            "PKG_CONFIG_PATH": "/usr/local/lib/pkgconfig:/usr/local/lib/x86_64-linux-gnu/pkgconfig",
        }
        for module in NATIVE_MODULES:
            try:
                result = subprocess.run([pkg_config, "--modversion", module], cwd="/", env=environment,
                                        capture_output=True, text=True, timeout=5, check=False)
                version = result.stdout.strip()
                if result.returncode == 0 and _VERSION.fullmatch(version):
                    modules[module] = version
            except (OSError, subprocess.SubprocessError):
                pass
    issues = ["native_selection_unsafe"] if selection is None else []
    if selection:
        if not all(commands.values()):
            issues.append("native_command_missing")
        if any(version is None for name, version in modules.items() if name not in SOURCE_MODULES):
            issues.append("native_module_missing")
    return {
        "installed": selection is True, "selected": selection, "healthy": not issues,
        "commands": commands, "modules": modules, "issues": issues,
        "project_bootstrap_required": [name for name in NATIVE_MODULES if name in SOURCE_MODULES and modules[name] is None],
    }
