"""Target-side activation for current Basaltwater installations."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess

from lib.state_read import read_state_object
from lib.validation import validate_filesystem_path
from lib.validators import validate_username


def _safe(path: Path) -> None:
    """Validate privileged state paths without following parent symlinks."""
    validate_filesystem_path(str(path))
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError(f"State path must be absolute and normalized: {path}")
    for parent in path.parents:
        if parent.is_symlink():
            raise ValueError(f"Refusing symlinked state parent: {parent}")


def _check_journal(root: Path) -> None:
    """Preserve incomplete historical cutovers for the intermediate release."""
    directory = root / "var/lib/basaltwater-migration"
    if not os.path.lexists(directory):
        return
    journal = directory / "journal.json"
    _safe(journal)
    for path in (directory, journal):
        info = path.lstat()
        if path.is_symlink() or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise ValueError(f"Unsafe migration journal: {path}")
    plan = read_state_object(str(journal), versioned=False)
    if plan is None or plan.get("status") != "complete":
        raise ValueError(
            f"Interrupted migration requires recovery with the intermediate version "
            f"in docs/BASALTWATER_MIGRATION.md before setup: {journal}"
        )


def _check_unit_operation_markers(root: Path) -> None:
    """Do not overwrite runtime used by unfinished systemd unit recovery."""
    for brand in ("infra-tools", "basaltwater"):
        marker = root / "etc/systemd/system" / f".{brand}-unit-operation.json"
        _safe(marker)
        if os.path.lexists(marker):
            raise ValueError(f"Unfinished systemd unit replacement; recover before setup: {marker}")


def _traversal_acl(content: str, uid: int) -> str:
    """Grant traversal without unmasking another account's latent ACL rights."""
    entries = [line.split("#", 1)[0].strip() for line in content.splitlines()]
    entries = [line for line in entries if line]
    access = [line.split(":") for line in entries if not line.startswith("default:")]
    if any(len(entry) != 3 or not re.fullmatch(r"[r-][w-][x-]", entry[2]) for entry in access):
        raise ValueError("Invalid state directory ACL")
    if not {("user", ""), ("group", ""), ("other", "")}.issubset({tuple(entry[:2]) for entry in access}):
        raise ValueError("Incomplete state directory ACL")
    mask = next((entry[2] for entry in access if entry[0] == "mask"), "rwx")
    updated = []
    found = False
    effective_mask = set("x")
    for kind, qualifier, permissions in access:
        if kind == "mask":
            continue
        if kind == "group" or (kind == "user" and qualifier):
            permissions = "".join(c if c in mask else "-" for c in permissions)
            if kind == "user" and qualifier == str(uid):
                permissions = permissions[:2] + "x"
                found = True
            effective_mask.update(permissions)
        updated.append(f"{kind}:{qualifier}:{permissions}")
    if not found:
        updated.append(f"user:{uid}:--x")
    updated.append("mask::" + "".join(c if c in effective_mask else "-" for c in "rwx"))
    updated.extend(line for line in entries if line.startswith("default:"))
    return "\n".join(updated) + "\n"


def _ensure_acl_tools() -> None:
    if not all(shutil.which(command) for command in ("getfacl", "setfacl")):
        from lib.remote_utils import install_package

        if not install_package("ACL tools", "acl", ["apt-get", "-o", "DPkg::Lock::Timeout=60", "install", "-y", "-qq", "acl"]):
            raise ValueError("ACL tools are required to preserve Syncthing state access")


def repair_syncthing_state_access(root: Path) -> None:
    """Restore traversal after runtime staging masks the private parent's ACL."""
    parent = root / "var/lib/basaltwater"
    home = parent / "syncthing"
    _safe(home)
    if not os.path.lexists(home):
        return
    if home.is_symlink() or not home.is_dir():
        raise ValueError(f"Unsafe Syncthing state directory: {home}")
    uid = home.stat().st_uid
    if uid == os.geteuid():
        return
    _ensure_acl_tools()
    before = subprocess.run(
        ["getfacl", "--omit-header", "--numeric", "--", str(parent)],
        check=True, capture_output=True, text=True,
    ).stdout
    subprocess.run(
        ["setfacl", "--set-file=-", "--", str(parent)],
        input=_traversal_acl(before, uid), text=True, check=True,
    )


def prepare_target_runtime(source: str, username: str) -> None:
    """Activate current runtime; leave retired installations untouched."""
    from lib import setup_common

    validate_filesystem_path(source, must_exist=True)
    if not validate_username(username):
        raise ValueError("Invalid setup username")
    runtime = Path(setup_common.REMOTE_INSTALL_DIR)
    root = runtime.parent.parent
    _check_unit_operation_markers(root)
    _check_journal(root)
    for parent in ("opt", "var/lib"):
        for name in ("infra_tools", "infra-tools"):
            legacy = root / parent / name
            if os.path.lexists(legacy):
                raise ValueError(
                    f"Retired infra-tools installation at {legacy}; use the intermediate "
                    "version in docs/BASALTWATER_MIGRATION.md before setup"
                )
    setup_common._activate_local_runtime(source)
    repair_syncthing_state_access(root)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", required=True)
    args = parser.parse_args()
    try:
        if os.geteuid() != 0:
            raise ValueError("Target runtime activation requires root")
        prepare_target_runtime(str(Path(__file__).resolve().parents[1]), args.username)
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"Setup stopped before running setup steps: {exc}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
