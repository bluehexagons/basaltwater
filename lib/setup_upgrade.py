"""Target-side runtime activation and automatic recent-install cutover."""

from __future__ import annotations

import argparse
from contextlib import ExitStack
import fcntl
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import stat
import subprocess
import uuid

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
    plan = json.loads(journal.read_text())
    if system and _recover_lock_retirement(root, directory, plan):
        return False
    if plan.get("status") != "complete":
        raise ValueError(f"Interrupted migration requires recovery before setup: {journal}")
    return True


def _recover_lock_retirement(root: Path, directory: Path, plan: dict) -> bool:
    """Recover only the recognizable pre-unlink provisioning-lock failure."""
    if plan.get("root") != str(root) or plan.get("recovery") != str(directory) or plan.get("system") is not True:
        return False
    recovered = plan.get("status") == "recovered" and plan.get("automatic_lock_recovery") is True
    if not recovered:
        index = plan.get("pending")
        actions = plan.get("actions", [])
        if plan.get("status") != "planned" or type(index) is not int or not 0 <= index < len(actions) or plan.get("completed") != index:
            return False
        action = actions[index]
        old = Path(action.get("old", ""))
        canonical = Path(action.get("preserve_lock", ""))
        if action.get("kind") != "retire" or old.parent not in {root / "run/lock/infra-tools", root / "run/lock/infra_tools"} or canonical.parent != root / "run/lock/basaltwater" or old.name != canonical.name:
            return False
        if not re.fullmatch(r"provision-[0-9a-f]{64}\.lock", old.name):
            return False
        if not old.exists() or not canonical.exists() or os.path.lexists(directory / f"retired-{index}"):
            return False
        # All node-local lock inodes stay held while earlier journaled moves
        # are reversed. The running setup's separate outer lock is untouched.
        with ExitStack() as stack:
            for brand in ("infra-tools", "infra_tools", "basaltwater"):
                parent = root / "run/lock" / brand
                rename_migration._safe(parent / "placeholder")
                if not parent.exists():
                    continue
                for path in sorted(parent.rglob("*")):
                    rename_migration._safe(path)
                    info = path.lstat()
                    if stat.S_ISDIR(info.st_mode):
                        continue
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid():
                        raise ValueError(f"Unsafe migration recovery lock: {path}")
                    if path in (old, canonical) and info.st_size:
                        raise ValueError(f"Nonempty provisioning lock during recovery: {path}")
                    handle = stack.enter_context(os.fdopen(os.open(path, os.O_RDWR | os.O_NOFOLLOW), "r+"))
                    opened = os.fstat(handle.fileno())
                    if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                        raise ValueError(f"Migration recovery lock changed: {path}")
                    try:
                        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except OSError as exc:
                        raise ValueError(f"Active operation holds {path}; wait for it to finish") from exc
            print("Recovering interrupted provisioning-lock migration", flush=True)
            plan["automatic_lock_recovery"] = True
            rename_migration._save(plan)
            rename_migration.recover(root, system=True)
    archive = directory.with_name(directory.name + "-recovered-" + uuid.uuid4().hex)
    directory.rename(archive)
    print(f"Preserved recovered migration journal: {archive}", flush=True)
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
    rename_migration.check_unit_operation_markers(root)
    completed = _check_journal(root, system=True)
    if completed:
        rename_migration.repair_systemd_settings(root)
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
        # Staging sets the shared state parent to 0700. Preserve access even
        # when this setup selects no Syncthing steps or is retrying a cutover.
        rename_migration.repair_syncthing_state_access(root)
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
        rename_migration.repair_managed_markers(root)
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
