#!/usr/bin/env python3
"""Preview or remove this repository's three allowlisted generated directories."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import stat
import subprocess
import sys

_REPO_ROOT = Path(os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))
sys.path.insert(0, str(_REPO_ROOT))

from lib import agent_storage_inventory, cleanup_paths


_TARGET_NAMES = ("build", "dist", "htmlcov")
_INVENTORY_MAX_ENTRIES = 50_000
_INVENTORY_TIMEOUT = 10
_GIT_TIMEOUT = 10


class CleanupRefused(RuntimeError):
    """The repository or a selected generated directory failed preflight."""


def repository_root() -> Path:
    """Return this script's lexical repository root without following links."""
    return _REPO_ROOT


def _reject_symlink_components(path: Path) -> None:
    """Reject a symlink at any existing component from / through path."""
    absolute = Path(os.path.abspath(path))
    current = Path(absolute.anchor)
    for component in absolute.parts[1:]:
        current /= component
        try:
            info = current.lstat()
        except FileNotFoundError:
            return
        if stat.S_ISLNK(info.st_mode):
            raise CleanupRefused(f"Refusing symlink path component: {current}")


def _target_paths(root: Path) -> list[Path]:
    return [root / name for name in _TARGET_NAMES]


def _identity(info: os.stat_result | None) -> tuple[int, int, int, int, int] | None:
    if info is None:
        return None
    return (info.st_dev, info.st_ino, info.st_uid, info.st_mode, info.st_mtime_ns)


def _require_private_repository(root: Path, uid: int) -> None:
    """Owner process checks require a checkout inaccessible to other accounts."""
    if root.lstat().st_uid != uid:
        raise CleanupRefused("Repository must belong to the current account")
    for ancestor in (root, *root.parents):
        info = ancestor.lstat()
        if info.st_uid == uid and not info.st_mode & 0o077:
            return
    raise CleanupRefused("Repository must be beneath an owner-only directory (for example a private home); shared checkouts require coordinated project cleanup")


def _in_use(path: Path, root: Path, uid: int, coverage: set[str]) -> bool:
    _require_private_repository(root, uid)

    def incomplete() -> None:
        message = "Open-file inspection is incomplete for non-dumpable account processes; stop builds and reviews before applying cleanup."
        if message not in coverage:
            coverage.add(message)
            print(message, file=sys.stderr)

    return cleanup_paths.process_uses_path(str(path), uid=uid, include_open_files=True,
                                          on_incomplete=incomplete)


def _git_untracked_by_index(root: Path) -> None:
    """Require Git to confirm that no allowlisted target contains tracked files."""
    env = os.environ.copy()
    env["GIT_OPTIONAL_LOCKS"] = "0"
    for name in _TARGET_NAMES:
        command = ["git", "-C", str(root), "--no-optional-locks", "ls-files",
                   "--error-unmatch", "--", f"{name}/"]
        try:
            result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    timeout=_GIT_TIMEOUT, check=False, env=env)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CleanupRefused(f"Git preflight failed: {exc}") from exc
        if result.returncode == 0:
            raise CleanupRefused(f"Refusing generated directory containing tracked files: {name}")
        if result.returncode != 1:
            raise CleanupRefused(f"Git preflight failed with status {result.returncode}")


def _inventory_by_path(root: Path, paths: list[Path]) -> dict[str, dict]:
    try:
        inventory = agent_storage_inventory.inspect_storage(
            home=str(root), paths=[str(path) for path in paths],
            max_entries=_INVENTORY_MAX_ENTRIES, timeout=_INVENTORY_TIMEOUT,
        )
    except (OSError, ValueError) as exc:
        raise CleanupRefused(f"Storage inventory failed: {exc}") from exc
    if inventory.get("issues"):
        raise CleanupRefused(f"Storage inventory reported issues: {inventory['issues']}")
    candidates = inventory.get("candidates")
    if not isinstance(candidates, list) or len(candidates) != len(paths):
        raise CleanupRefused("Storage inventory returned an incomplete target list")
    by_path = {row.get("path"): row for row in candidates if isinstance(row, dict)}
    if set(by_path) != {str(path) for path in paths}:
        raise CleanupRefused("Storage inventory returned unexpected target paths")
    for path in paths:
        row = by_path[str(path)]
        issues = row.get("issues")
        if issues:
            raise CleanupRefused(f"Storage inventory issues for {path}: {issues}")
        if row.get("is_mountpoint"):
            raise CleanupRefused(f"Target is a mountpoint: {path}")
        if row.get("status") not in ("measured", "absent"):
            raise CleanupRefused(f"Storage inventory is incomplete for {path}: {row.get('status')}")
    return by_path


def _preflight(root: Path) -> list[tuple[Path, os.stat_result | None, dict]]:
    _reject_symlink_components(root)
    if not root.is_dir():
        raise CleanupRefused(f"Repository root is unavailable: {root}")
    _require_private_repository(root, os.getuid())
    root_identity = root.lstat()
    paths = _target_paths(root)
    for path in paths:
        _reject_symlink_components(path)
    before_inventory = {}
    for path in paths:
        try:
            info = path.lstat()
        except FileNotFoundError:
            info = None
        if info is not None and not stat.S_ISDIR(info.st_mode):
            raise CleanupRefused(f"Target is not a real directory: {path}")
        before_inventory[str(path)] = info
    _git_untracked_by_index(root)
    inventory = _inventory_by_path(root, paths)
    _reject_symlink_components(root)
    if _identity(root.lstat()) != _identity(root_identity):
        raise CleanupRefused("Repository root changed during preflight")
    uid = os.getuid()
    result = []
    for path in paths:
        record = inventory[str(path)]
        try:
            info = path.lstat()
        except FileNotFoundError:
            info = None
        _reject_symlink_components(path)
        if _identity(info) != _identity(before_inventory[str(path)]):
            raise CleanupRefused(f"Target changed during inventory: {path}")
        if info is None:
            if record["status"] != "absent":
                raise CleanupRefused(f"Target changed during inventory: {path}")
            result.append((path, None, record))
            continue
        if not stat.S_ISDIR(info.st_mode):
            raise CleanupRefused(f"Target is not a real directory: {path}")
        if info.st_uid != uid or record.get("owner_uid") != uid or record.get("other_owner_entries") != 0:
            raise CleanupRefused(f"Target contains entries outside the current account: {path}")
        if record["status"] != "measured":
            raise CleanupRefused(f"Target changed during inventory: {path}")
        result.append((path, info, record))
    return result


def run(*, apply: bool = False) -> int:
    """Show the allowlisted output directories, optionally deleting after preflight."""
    root = repository_root()
    try:
        targets = _preflight(root)
        uid = os.getuid()
        coverage: set[str] = set()
        for path, expected, record in targets:
            byte_count = record.get("allocated_bytes", 0)
            status = record["status"]
            if expected is None:
                print(f"{path}: {byte_count} allocated bytes ({status})")
                continue
            if not cleanup_paths.remove_checked_path(
                    str(path), expected, uid=uid,
                    in_use=lambda selected=path: _in_use(selected, root, uid, coverage),
                    dry_run=True):
                raise CleanupRefused(f"Target is in use: {path}")
            print(f"{path}: {byte_count} allocated bytes ({status}; {'apply' if apply else 'dry-run'})")
        if not apply:
            return 0
        for path, expected, _record in targets:
            if expected is None:
                continue
            removed = cleanup_paths.remove_checked_path(
                str(path), expected, uid=uid,
                in_use=lambda selected=path: _in_use(selected, root, uid, coverage),
            )
            if not removed:
                raise CleanupRefused(f"Target became active before removal: {path}")
        return 0
    except (CleanupRefused, OSError, ValueError) as exc:
        print(f"clean_generated: {exc}", file=sys.stderr)
        if isinstance(exc, cleanup_paths.CleanupPathError):
            print(f"Retained for recovery: {exc.recovery_path}", file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="show selected outputs without deleting (default)")
    mode.add_argument("--apply", action="store_true", help="remove the allowlisted generated directories")
    args = parser.parse_args(argv)
    return run(apply=args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
