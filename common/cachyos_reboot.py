"""Read-only, best-effort reboot advice for an existing CachyOS workstation."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import platform
import re
import stat
import time
from typing import TYPE_CHECKING

from lib.validation import validate_arch_package_name, validate_filesystem_path

if TYPE_CHECKING:
    from lib.config import SetupConfig


MODULES = Path("/usr/lib/modules")
LOCAL_DATABASE = Path("/var/lib/pacman/local")
PROC_STAT = Path("/proc/stat")
REBOOT_MARKER = Path("/run/reboot-required")
CORE_PACKAGES = ("glibc", "systemd")


@dataclass(frozen=True)
class RebootNotice:
    """Signals recommend a restart; missing evidence must not become an all-clear."""

    reasons: tuple[str, ...]
    unknown: tuple[str, ...]


def _read(path: Path, limit: int = 65536) -> str:
    validate_filesystem_path(str(path))
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("Reboot evidence must be a regular file")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            content = stream.read(limit + 1)
        if len(content) > limit:
            raise ValueError("Reboot evidence exceeds its read limit")
        return content.decode("utf-8")
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _install_time(package: str) -> int:
    """Read locale-independent ALPM metadata without a package-manager process."""
    validate_arch_package_name(package)
    matches = []
    for path in LOCAL_DATABASE.glob(package + "-*/desc"):
        if path.parent.is_symlink():
            raise ValueError("Symlinked package metadata")
        text = _read(path)
        names = re.findall(r"^%NAME%\n([^\n]+)$", text, re.M)
        if names != [package]:
            continue
        dates = re.findall(r"^%INSTALLDATE%\n([0-9]+)$", text, re.M)
        if len(dates) != 1:
            raise ValueError("Missing package installation time")
        matches.append(int(dates[0]))
    if len(matches) != 1:
        raise ValueError("Missing or ambiguous installed package metadata")
    return matches[0]


def collect_reboot_notice() -> RebootNotice:
    """Observe kernel availability and core packages installed since this boot."""
    reasons: list[str] = []
    unknown: list[str] = []
    try:
        info = REBOOT_MARKER.lstat()
        if stat.S_ISREG(info.st_mode):
            reasons.append("the system has a reboot-required marker")
        else:
            unknown.append("the reboot-required marker is not a regular file")
    except FileNotFoundError:
        pass
    except OSError:
        unknown.append("the system reboot marker could not be inspected")

    packages = list(CORE_PACKAGES)
    release = platform.release()
    if not re.fullmatch(r"[A-Za-z0-9_.+-]{1,128}", release):
        unknown.append("the running kernel release could not be validated")
    else:
        modules = MODULES / release
        try:
            info = modules.stat()
            if not stat.S_ISDIR(info.st_mode):
                raise ValueError("Invalid kernel module tree")
            # Match only the booted kernel's package, avoiding a reboot notice
            # solely because an unused alternative kernel was installed.
            package = _read(modules / "pkgbase", 256).strip()
            validate_arch_package_name(package)
            packages.append(package)
        except FileNotFoundError:
            if not modules.exists():
                reasons.append("the running kernel's module directory is absent")
            else:
                unknown.append("the booted kernel's package could not be identified")
        except (OSError, ValueError, UnicodeError):
            unknown.append("the running kernel's modules or package could not be inspected")

    try:
        boots = re.findall(r"^btime ([0-9]+)$", _read(PROC_STAT, 1024 * 1024), re.M)
        now = time.time()
        if len(boots) != 1 or not 0 < int(boots[0]) <= now:
            raise ValueError("Unknown boot time")
        boot = int(boots[0])
    except (OSError, ValueError, UnicodeError):
        unknown.append("boot time is unavailable for core package checks")
    else:
        for package in dict.fromkeys(packages):
            try:
                installed = _install_time(package)
                if not 0 < installed <= now:
                    raise ValueError("Invalid package installation time")
                if installed > boot:
                    reasons.append(f"{package} was installed or updated after this boot")
            except (OSError, ValueError, UnicodeError):
                unknown.append(f"{package} installation time could not be inspected")
    return RebootNotice(tuple(reasons), tuple(unknown))


def report_reboot_notice(config: SetupConfig) -> None:
    """Print advice at setup completion without rebooting or failing setup."""
    from lib.remote_utils import is_dry_run

    if config.dry_run or is_dry_run():
        print("  Would check CachyOS kernel/core-package restart signals at completion")
        return
    notice = collect_reboot_notice()
    if notice.reasons:
        print("  System restart recommended: " + "; ".join(notice.reasons) + ".")
        print("  Save your work and restart when convenient; Basaltwater does not reboot CachyOS.")
    elif notice.unknown:
        print("  System restart status unknown: some kernel/core-package evidence was unavailable.")
    else:
        print("  No kernel/core-package restart signal detected.")
    if notice.unknown:
        print("  Restart check incomplete: " + "; ".join(notice.unknown) + ".")
    print("  This is best-effort advice; follow CachyOS update/firmware restart notices too.")
