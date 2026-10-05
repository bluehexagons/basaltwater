"""Independent publishing tool setup and coordinated root maintenance."""

from __future__ import annotations

from contextlib import contextmanager, ExitStack
import fcntl
import os
from pathlib import Path
import pwd
import shlex
import stat

from common.common_steps import _run_as_login_user
from lib.config import SetupConfig
from lib.maintenance_systemd import configure_maintenance_timer
from lib.publishing_config import validate_publishing_tools
from lib.release_management import detect_release_arch, load_json_state, write_json_state
from lib.remote_utils import get_user_home, is_dry_run, run
from lib.validation import validate_filesystem_path
from lib.validators import validate_username

PUBLISHING_TOOL_STATE = "/opt/basaltwater/state/publishing-tools.json"


def selections() -> dict[str, list[str]]:
    state = load_json_state(PUBLISHING_TOOL_STATE, read_error_label="Publishing tools", invalid_state_message="Invalid publishing tool registry")
    values = state.get("users", {})
    if not isinstance(values, dict):
        raise ValueError("Invalid publishing users")
    result = {}
    for user, tools in values.items():
        if user == "root" or not validate_username(user):
            raise ValueError("Invalid publishing user")
        result[user] = validate_publishing_tools(tools)
    return result


@contextmanager
def maintenance_lock(username: str, service: str):
    """Root maintenance takes the same user/provider lease as native sessions."""
    if not validate_username(username) or username == "root":
        raise ValueError("Publishing maintenance requires a non-root user")
    validate_publishing_tools([service])
    account = pwd.getpwnam(username)
    home = get_user_home(username)
    validate_filesystem_path(home, must_exist=True)
    root = Path(home) / ".local/share/basaltwater/publishing"
    for path in (*reversed(root.parents), root):
        if path.is_symlink():
            raise ValueError("Publishing maintenance cannot follow linked state")
    result = _run_as_login_user(username, home, "install -d -m 0700 " + shlex.quote(str(root)), check=False, capture_output=True)
    if result.returncode:
        raise RuntimeError("Cannot prepare publishing maintenance lock")
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != account.pw_uid or info.st_mode & 0o077:
        raise ValueError("Publishing state must be private and owned by its account")
    path = root / (service + ".lock")
    # Open ancestors by descriptor so a user cannot swap one for a symlink
    # between validation and the root-owned updater's file creation.
    directory_fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in root.parts[1:]:
            child_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = child_fd
        try:
            fd = os.open(path.name, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=directory_fd)
        except FileExistsError:
            fd = os.open(path.name, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
        else:
            try:
                os.fchown(fd, account.pw_uid, account.pw_gid)
            except BaseException:
                os.close(fd)
                raise
    finally:
        os.close(directory_fd)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != account.pw_uid or info.st_nlink != 1 or info.st_mode & 0o077:
            raise ValueError("Invalid publishing maintenance lock")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def install_selected_tools(users: dict[str, list[str]], *, busy_skip: bool = False) -> bool:
    from common.godot_steps import install_or_update_butler_release, install_or_update_steamcmd

    changed = False
    butler_users = [user for user, tools in users.items() if "butler" in tools]
    if butler_users:
        # Butler is shared system-wide. A setup for a new account must also
        # respect sessions belonging to every previously registered owner.
        from common.godot_steps import _validated_registered_bundles

        registered = selections()
        bundles, legacy_users, _ = _validated_registered_bundles()
        butler_users = sorted(set(butler_users) | {user for user, tools in registered.items() if "butler" in tools}
                              | (set(legacy_users) if "publishing" in bundles else set()))
        try:
            with ExitStack() as stack:
                for user in sorted(butler_users):
                    stack.enter_context(maintenance_lock(user, "butler"))
                _, updated, _ = install_or_update_butler_release()
                changed |= updated
        except BlockingIOError:
            if not busy_skip:
                raise RuntimeError("Butler account is busy; retry setup after the operation finishes")
            print("  ✓ Butler update deferred while publishing is active")
    steam_users = [user for user, tools in users.items() if "steamcmd" in tools]
    if steam_users and detect_release_arch() == "amd64":
        run("apt-get -o DPkg::Lock::Timeout=60 install -y -qq lib32gcc-s1 lib32stdc++6", check=True)
    for user in steam_users:
        try:
            with maintenance_lock(user, "steamcmd"):
                changed |= install_or_update_steamcmd(user)
        except BlockingIOError:
            if not busy_skip:
                raise RuntimeError("Steam account is busy; retry setup after the operation finishes")
            print("  ✓ SteamCMD update deferred while publishing is active")
    return changed


def install_publishing_tools(config: SetupConfig) -> None:
    tools = validate_publishing_tools(config.publishing_tools)
    if not tools:
        return
    if is_dry_run():
        print("  [DRY-RUN] Would install publishing tools independently of Godot: " + ", ".join(tools))
        return
    run("apt-get -o DPkg::Lock::Timeout=60 install -y -qq curl ca-certificates", check=True)
    install_selected_tools({config.username: tools})
    saved = selections()
    saved[config.username] = list(dict.fromkeys([*saved.get(config.username, []), *tools]))
    write_json_state(PUBLISHING_TOOL_STATE, {"version": 1, "users": saved}, mode=0o600)


def update_registered_publishing_tools() -> bool:
    from common.godot_steps import _validated_registered_bundles

    users = selections()
    bundles, legacy_users, _ = _validated_registered_bundles()
    if "publishing" in bundles:
        for user in legacy_users:
            users[user] = list(dict.fromkeys([*users.get(user, []), "butler", "steamcmd"]))
    return install_selected_tools(users, busy_skip=True) if users else False


def configure_auto_update_publishing(config: SetupConfig) -> None:
    del config
    if not configure_maintenance_timer(service_name="auto-update-godot", service_desc="Auto-update Godot and publishing tools",
                                       timer_desc="Auto-update game tools weekly", script_path="/opt/basaltwater/common/service_tools/auto_update_godot.py",
                                       schedule="Sun *-*-* 06:30:00", check_path=PUBLISHING_TOOL_STATE, check_name="Publishing tools", purpose="auto-update"):
        raise RuntimeError("Publishing tool maintenance timer failed verification")
