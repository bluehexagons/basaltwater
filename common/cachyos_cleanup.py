"""Conservative setup-time cleanup for the native CachyOS package cache."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import re
import stat
import sys
import time

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.config import SetupConfig
from lib.maintenance_defaults import CLEANUP_COMMAND_TIMEOUT_SECONDS
from lib.remote_utils import is_dry_run, run
from lib.validation import validate_arch_package_name, validate_filesystem_path


PACKAGE_CACHE = Path("/var/cache/pacman/pkg")
PACMAN_LOCK = Path("/var/lib/pacman/db.lck")
PACCACHE = "/usr/bin/paccache"
KEEP_VERSIONS = 3
MIN_AGE_DAYS = 30
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


def prune_package_cache(*, dry_run: bool) -> int:
    """Use paccache's version ordering, then prune only old top-level files."""
    if not dry_run and os.geteuid() != 0:
        raise RuntimeError("Package-cache removal must run as root through the setup step")
    if not _cache_available():
        print("  Pacman package cache absent; nothing to prune")
        return 0
    if _package_transaction_active():
        print("  Pacman cache cleanup deferred: a package transaction lock is present; retry on a later setup run")
        return 0
    # Age options make paccache recurse into download-* directories. Select
    # surplus top-level archives without them and apply our own age checks.
    preview = run([
        PACCACHE, "--dryrun", "--verbose", "--verbose", "--null", "--nocolor",
        "--cachedir", str(PACKAGE_CACHE), "--keep", str(KEEP_VERSIONS),
    ], capture_output=True, check=False, env=_ENVIRONMENT,
        timeout=CLEANUP_COMMAND_TIMEOUT_SECONDS)
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
    query = run(["/usr/bin/pacman", "-Q"], capture_output=True, check=False,
                env=_ENVIRONMENT, timeout=30)
    if query.returncode or (query.stderr or "").strip():
        raise RuntimeError("Cannot inspect installed package versions; no archives were removed")
    installed = {}
    for line in (query.stdout or "").splitlines():
        fields = line.split()
        if len(fields) != 2:
            raise RuntimeError("Unrecognized pacman package inventory; no archives were removed")
        installed[validate_arch_package_name(fields[0])] = fields[1].split(":", 1)[-1]
    cutoff = time.time() - MIN_AGE_DAYS * 86400
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
          f"old archive/signature files; keeping {KEEP_VERSIONS} versions and {MIN_AGE_DAYS} days")
    return len(eligible) if dry_run else removed


def cleanup_cachyos_packages(config: SetupConfig) -> None:
    """Preview without sudo; run the same bounded policy as root only if needed."""
    if config.dry_run or is_dry_run():
        print(f"  [DRY-RUN] Would prune pacman downloads: keep {KEEP_VERSIONS} versions and {MIN_AGE_DAYS} days")
        return
    if not prune_package_cache(dry_run=True):
        return
    command = ["/usr/bin/python3", str(Path(__file__).resolve()), "--apply"]
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
    args = parser.parse_args()
    try:
        prune_package_cache(dry_run=not args.apply)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Error: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
