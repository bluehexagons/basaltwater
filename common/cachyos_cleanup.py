"""Conservative setup-time cleanup for the native CachyOS package cache."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.config import SetupConfig
from lib.maintenance_defaults import CLEANUP_COMMAND_TIMEOUT_SECONDS
from lib.remote_utils import is_dry_run, run
from lib.streamed_process import run_captured
from lib.validation import validate_arch_package_name, validate_filesystem_path


PACKAGE_CACHE = Path("/var/cache/pacman/pkg")
PACMAN_LOCK = Path("/var/lib/pacman/db.lck")
PACCACHE = "/usr/bin/paccache"
KEEP_VERSIONS = 3
MIN_AGE_DAYS = 30
PRESSURE_KEEP_VERSIONS = 1
PRESSURE_MIN_AGE_DAYS = 7
PRESSURE_TARGET_BYTES = 2 * 1024**3
PRESSURE_MIN_INODES = 10000
_ARCHIVE = re.compile(r"([A-Za-z0-9@_+.-]+)\.pkg\.tar(?:\.(?:zst|xz|gz|bz2|lrz|lzo|lz4|lz|Z))?")
_ENVIRONMENT = {"LC_ALL": "C", "PATH": "/usr/bin:/bin"}


@dataclass(frozen=True)
class Archive:
    path: Path
    info: os.stat_result


def _cache_available() -> bool:
    """Never follow a redirected or user-writable system cache directory."""
    validate_filesystem_path(str(PACKAGE_CACHE))
    for path in reversed((PACKAGE_CACHE, *PACKAGE_CACHE.parents)):
        if path.is_symlink():
            raise RuntimeError("Pacman cache cleanup refuses symlinked cache paths; manage that cache manually")
        if not path.exists():
            return False
        metadata = path.stat()
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != 0 or metadata.st_mode & 0o022:
            raise RuntimeError("Pacman cache cleanup requires a root-owned directory without group/other write access")
    return True


def _package_transaction_active() -> bool:
    return PACMAN_LOCK.exists() or PACMAN_LOCK.is_symlink()


def _under_pressure() -> bool:
    capacity = os.statvfs(PACKAGE_CACHE)
    return (capacity.f_bavail * capacity.f_frsize < PRESSURE_TARGET_BYTES
            or (capacity.f_files > 0 and capacity.f_favail < PRESSURE_MIN_INODES))


def prune_package_cache(*, dry_run: bool, pressure: bool = False) -> int:
    """Use paccache's version ordering, then prune only old top-level files."""
    if not dry_run and os.geteuid() != 0:
        raise RuntimeError("Package-cache removal must run as root through the setup step")
    if not _cache_available():
        print("  Pacman package cache absent; nothing to prune")
        return 0
    if _package_transaction_active():
        print("  Pacman cache cleanup deferred: a package transaction lock is present; retry on a later setup run")
        return 0
    if pressure and not _under_pressure():
        print("  Pacman cache: sufficient capacity; pressure cleanup skipped")
        return 0
    keep = PRESSURE_KEEP_VERSIONS if pressure else KEEP_VERSIONS
    age = PRESSURE_MIN_AGE_DAYS if pressure else MIN_AGE_DAYS
    # Age options make paccache recurse into download-* directories. Select
    # surplus top-level archives without them and apply our own age checks.
    try:
        preview = run_captured([
            PACCACHE, "--dryrun", "--verbose", "--verbose", "--null", "--nocolor",
            "--cachedir", str(PACKAGE_CACHE), "--keep", str(keep),
        ], env=_ENVIRONMENT, max_bytes=1024 * 1024, timeout=CLEANUP_COMMAND_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("Pacman cache inventory failed or exceeded its limits; no archives were removed") from exc
    if preview.returncode or (preview.stderr or "").strip():
        raise RuntimeError("Pacman cache inventory failed; inspect paccache locally and rerun setup")
    output = preview.stdout or ""
    if output.strip() == "==> no candidate packages found for pruning":
        print("  Pacman cache: no old surplus downloads to prune")
        return 0
    header, separator, body = output.partition("\n")
    paths, delimiter, summary = body.rpartition("\0")
    if (header != "==> Candidate packages:" or not separator or not delimiter
            or not re.fullmatch(r"\s*==> finished dry run: [0-9]+ candidates \(disk space saved: [^\n]+\)\s*", summary)):
        raise RuntimeError("Unrecognized paccache inventory; no package archives were removed")
    try:
        query = run_captured(["/usr/bin/pacman", "-Q"], env=_ENVIRONMENT, timeout=30, max_bytes=1024 * 1024)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("Installed package inventory failed or exceeded its limits; no archives were removed") from exc
    if query.returncode or (query.stderr or "").strip():
        raise RuntimeError("Cannot inspect installed package versions; no archives were removed")
    installed = {}
    for line in (query.stdout or "").splitlines():
        fields = line.split()
        if len(fields) != 2:
            raise RuntimeError("Unrecognized pacman package inventory; no archives were removed")
        installed[validate_arch_package_name(fields[0])] = fields[1].split(":", 1)[-1]
    cutoff = time.time() - age * 86400
    eligible: dict[Path, Archive] = {}
    for raw in paths.split("\0"):
        path = Path(raw)
        validate_filesystem_path(str(path))
        if path.parent != PACKAGE_CACHE:
            raise RuntimeError("paccache returned a path outside the selected cache; nothing removed")
        match = _ARCHIVE.fullmatch(path.name.removesuffix(".sig"))
        if match is None:
            continue
        parts = match[1].rsplit("-", 3)
        if len(parts) != 4 or installed.get(parts[0]) == f"{parts[1]}-{parts[2]}":
            continue  # Retain the installed version even after a downgrade.
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        if (stat.S_ISREG(info.st_mode) and info.st_uid == 0
                and max(info.st_atime, info.st_mtime) < cutoff):
            eligible[path] = Archive(path, info)
    eligible = {path: archive for path, archive in eligible.items()
                if not path.name.endswith(".sig") or path.with_suffix("") in eligible}
    removed = 0
    removed_archives: set[Path] = set()
    for archive in sorted(eligible.values(), key=lambda entry: (entry.path.name.endswith(".sig"), str(entry.path))):
        if dry_run:
            continue
        if archive.path.name.endswith(".sig") and archive.path.with_suffix("") not in removed_archives:
            continue
        # Finish signatures for removed archives, but stop selecting archives
        # as soon as the cache filesystem has enough bytes and inodes again.
        if pressure and not archive.path.name.endswith(".sig") and not _under_pressure():
            continue
        if not _cache_available() or _package_transaction_active():
            print("  Pacman cache cleanup deferred: cache or transaction state changed")
            break
        try:
            current = archive.path.lstat()
        except FileNotFoundError:
            continue
        if (not stat.S_ISREG(current.st_mode) or current.st_uid != 0
                or (current.st_dev, current.st_ino, current.st_atime_ns, current.st_mtime_ns)
                != (archive.info.st_dev, archive.info.st_ino, archive.info.st_atime_ns, archive.info.st_mtime_ns)):
            continue
        archive.path.unlink()
        removed_archives.add(archive.path)
        removed += 1
    print(f"  Pacman cache: {'would remove ' + str(len(eligible)) if dry_run else 'removed ' + str(removed)} "
          f"old archive/signature files; keeping {keep} versions and {age} days (plus installed versions)")
    return len(eligible) if dry_run else removed


def cleanup_cachyos_packages(config: SetupConfig) -> None:
    """Preview without sudo; run the same bounded policy as root only if needed."""
    _cleanup_packages(config, pressure=False)


def report_pending_package_configs(config: SetupConfig) -> None:
    """Report pacman configuration backups without editing them."""
    if config.dry_run or is_dry_run():
        return
    try:
        result = run_captured(["/usr/bin/pacdiff", "--output", "--pacmandb", "--nocolor"],
                              env=_ENVIRONMENT, timeout=30, max_bytes=128 * 1024)
    except (OSError, subprocess.SubprocessError):
        print("  WARNING: Could not check pending package configuration. Inspect with `sudo pacdiff --output`.")
        return
    if result.returncode or (result.stderr or "").strip():
        print("  WARNING: Package configuration check was incomplete. Inspect with `sudo pacdiff --output`.")
        return
    paths = (result.stdout or "").splitlines()
    if paths:
        print(f"  WARNING: {len(paths)} package configuration files need review. Inspect with `sudo pacdiff --output`.")
        for path in paths[:10]:
            print(f"    {path!r}")


def recover_cachyos_package_space(config: SetupConfig) -> None:
    """Recover old downloads before installing tools on a low-capacity host."""
    if not config.dry_run and not is_dry_run() and not Path(PACCACHE).is_file():
        print("  Pacman pressure cleanup unavailable until pacman-contrib is installed")
        return
    _cleanup_packages(config, pressure=True)


def _cleanup_packages(config: SetupConfig, *, pressure: bool) -> None:
    if config.dry_run or is_dry_run():
        print("  [DRY-RUN] Would check pacman downloads for " + ("low-capacity recovery" if pressure else "old surplus archives"))
        return
    if not prune_package_cache(dry_run=True, pressure=pressure):
        return
    command = ["/usr/bin/python3", str(Path(__file__).resolve()), "--apply"]
    if pressure:
        command.append("--pressure")
    if os.geteuid() != 0:
        command.insert(0, "sudo")
    result = run(command, check=False, interactive=os.geteuid() != 0,
                 timeout=CLEANUP_COMMAND_TIMEOUT_SECONDS)
    if result.returncode:
        raise RuntimeError("Pacman cache cleanup failed; installed packages are retained. Resolve the error and rerun setup")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    parser.add_argument("--pressure", action="store_true", help="Below 2 GiB or 10,000 free inodes, keep one version and seven days")
    args = parser.parse_args()
    try:
        prune_package_cache(dry_run=not args.apply, pressure=args.pressure)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Error: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
