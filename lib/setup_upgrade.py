"""Target-side runtime activation and automatic recent-install cutover."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import pwd
import shutil
import stat
import subprocess

from lib import rename_migration
from lib.validation import validate_filesystem_path
from lib.validators import validate_username


def _check_journal(root: Path, *, system: bool) -> bool:
    directory = root / ("var/lib/basaltwater-migration" if system else ".local/state/basaltwater-migration")
    if not os.path.lexists(directory):
        return False
    journal = directory / "journal.json"
    rename_migration._safe(journal)
    for path in (directory, journal):
        info = path.lstat()
        if path.is_symlink() or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise ValueError(f"Unsafe migration journal: {path}")
    if json.loads(journal.read_text()).get("status") != "complete":
        raise ValueError(f"Interrupted migration requires recovery before setup: {journal}")
    return True


def _migrate_users(runtime: Path, username: str) -> None:
    """Run each existing login account's cutover with its own permissions."""
    accounts = [account for account in pwd.getpwall()
                if account.pw_uid == 0 or account.pw_uid >= 1000 or account.pw_name == username]
    for account in accounts:
        if not Path(account.pw_dir).is_dir():
            continue
        if not validate_username(account.pw_name):
            raise ValueError("Invalid migration account")
        validate_filesystem_path(account.pw_dir, must_exist=True)
        environment = [
            "env", "-i", f"HOME={account.pw_dir}", f"USER={account.pw_name}",
            f"LOGNAME={account.pw_name}", "PATH=/usr/local/bin:/usr/bin:/bin",
            "PYTHONDONTWRITEBYTECODE=1",
            f"XDG_RUNTIME_DIR=/run/user/{account.pw_uid}",
            f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{account.pw_uid}/bus",
            "python3", "-B", "-m", "lib.setup_upgrade", "--user-migration",
        ]
        print(f"Checking existing user data for {account.pw_name}", flush=True)
        subprocess.run(["runuser", "--user", account.pw_name, "--", *environment],
                       cwd=str(runtime), stdin=subprocess.DEVNULL, check=True)


def prepare_target_runtime(source: str, username: str) -> None:
    """Migrate before replacement; refuse to continue through partial cutover."""
    from lib import setup_common

    validate_filesystem_path(source, must_exist=True)
    if not validate_username(username):
        raise ValueError("Invalid setup username")
    runtime = Path(setup_common.REMOTE_INSTALL_DIR)
    # The managed target layout is /opt/basaltwater. Deriving the root also
    # permits isolated filesystem fixtures without touching host state.
    root = runtime.parent.parent
    completed = _check_journal(root, system=True)
    legacy = runtime.with_name("infra_tools")
    migrated = False
    if os.path.lexists(legacy):
        if not (legacy / "infra_tools.py").is_file():
            raise ValueError(f"Unrecognized legacy runtime; refusing replacement: {legacy}")
        print("Migrating recent infra-tools installation to Basaltwater", flush=True)
        plan = rename_migration.build_plan(root, system=True, runtime_source=Path(source))
        rename_migration.apply_plan(plan)
        migrated = True
    if not migrated:
        # A system cutover may have completed before a user pass failed. Keep
        # migrated deployment sources when retrying without replacement input.
        previous_deployments = runtime / "deployments"
        incoming_deployments = Path(source) / "deployments"
        if completed and previous_deployments.is_dir() and not incoming_deployments.exists():
            if previous_deployments.is_symlink():
                raise ValueError("Refusing symlinked deployment directory")
            shutil.copytree(previous_deployments, incoming_deployments, symlinks=True)
        setup_common._activate_local_runtime(source)
    else:
        # Migration already installed this source while retaining the previous
        # deployments. Explicitly supplied deployment sources take precedence.
        deployments = Path(source) / "deployments"
        if deployments.is_dir():
            destination = runtime / "deployments"
            if destination.is_symlink():
                raise ValueError("Refusing symlinked deployment directory")
            if destination.exists():
                shutil.rmtree(destination)
            shutil.copytree(deployments, destination, symlinks=True)
    if migrated or completed:
        _migrate_users(runtime, username)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username")
    parser.add_argument("--user-migration", action="store_true")
    args = parser.parse_args()
    try:
        if args.user_migration:
            _check_journal(Path.home(), system=False)
            return rename_migration.migrate(system=False, apply=True)
        if os.geteuid() != 0:
            raise ValueError("Target runtime activation requires root")
        prepare_target_runtime(str(Path(__file__).resolve().parents[1]), args.username)
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"Setup stopped before running setup steps: {exc}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
