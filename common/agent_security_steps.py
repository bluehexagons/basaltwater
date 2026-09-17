"""System policy for safer Codex sessions on coding-agent machines."""

from __future__ import annotations

import grp
import json
import os
import pwd
import stat
import tomllib

from lib.atomic_io import (
    remove_file_durable,
    write_json_atomic,
    write_text_atomic,
)
from lib.config import SetupConfig
from lib.machine_state import can_manage_system_services
from lib.remote_utils import is_dry_run, run
from lib.types import JSONDict
from lib.validation import validate_filesystem_path
from lib.validators import validate_username


CODEX_SYSTEM_CONFIG_DIR = "/etc/codex"
CODEX_SYSTEM_CONFIG_NAME = "config.toml"
CODEX_REQUIREMENTS_NAME = "requirements.toml"
AGENT_USER_SECURITY_STATE_DIR = "/var/lib/infra_tools/agent-user-security"
SYSTEMD_LINGER_DIR = "/var/lib/systemd/linger"
_MANAGED_MARKER = "# Managed by infra-tools coding-agent security policy."
_HARDENED_CODEX_PROFILE = "infra_tools_hardened_workspace"
_USER_SECURITY_STATE_VERSION = 1
_AGENT_DENIED_GROUPS = frozenset(
    {
        "admin",
        "disk",
        "docker",
        "incus",
        "incus-admin",
        "kvm",
        "kmem",
        "libvirt",
        "libvirt-qemu",
        "lxd",
        "microk8s",
        "podman",
        "root",
        "shadow",
        "staff",
        "sudo",
        "vboxusers",
        "wheel",
    }
)
_USER_DENIED_GROUPS = _AGENT_DENIED_GROUPS | {
    "adm",
    "audio",
    "backup",
    "bluetooth",
    "cdrom",
    "dialout",
    "dip",
    "fax",
    "floppy",
    "input",
    "lp",
    "lpadmin",
    "mail",
    "netdev",
    "operator",
    "pcap",
    "plugdev",
    "render",
    "sasl",
    "scanner",
    "src",
    "systemd-journal",
    "tape",
    "tty",
    "uucp",
    "utmp",
    "video",
    "voice",
    "wireshark",
}


def _codex_config_content(*, hardened: bool) -> str:
    approval_policy = "never" if hardened else "on-request"
    reviewer = "user" if hardened else "auto_review"
    hardening = "allow_login_shell = false\n" if hardened else ""
    permission_profile = (
        _HARDENED_CODEX_PROFILE if hardened else ":workspace"
    )
    environment_hardening = (
        "\n[shell_environment_policy]\n"
        "ignore_default_excludes = false\n"
        if hardened
        else ""
    )
    return (
        f"{_MANAGED_MARKER}\n"
        f"# Policy: {'hardened' if hardened else 'standard'}\n"
        f'approval_policy = "{approval_policy}"\n'
        f'approvals_reviewer = "{reviewer}"\n'
        'sandbox_mode = "workspace-write"\n'
        f'default_permissions = "{permission_profile}"\n'
        f"{hardening}"
        "\n[sandbox_workspace_write]\n"
        "network_access = false\n"
        f"{environment_hardening}"
    )


def _codex_requirements_content() -> str:
    hardening = (
        "allow_login_shell = false\n"
        "allow_managed_hooks_only = true\n"
        "allow_browser_and_computer_use = false\n"
        "allow_appshots = false\n"
        "allow_remote_control = false\n"
    )
    return (
        f"{_MANAGED_MARKER}\n"
        "# Policy: hardened\n"
        'allowed_approval_policies = ["never"]\n'
        'allowed_approvals_reviewers = ["user"]\n'
        "allowed_web_search_modes = []\n"
        'allowed_sandbox_modes = ["read-only", "workspace-write"]\n'
        f'default_permissions = "{_HARDENED_CODEX_PROFILE}"\n'
        f"{hardening}"
        "\n[allowed_permission_profiles]\n"
        '":read-only" = true\n'
        f'{_HARDENED_CODEX_PROFILE} = true\n'
        f"\n[permissions.{_HARDENED_CODEX_PROFILE}]\n"
        'description = "Workspace access with network disabled by policy."\n'
        'extends = ":workspace"\n'
        f"\n[permissions.{_HARDENED_CODEX_PROFILE}.network]\n"
        "enabled = false\n"
        "\n[permissions.filesystem]\n"
        "deny_read = [\n"
        '    "/**/*.env",\n'
        '    "/**/.env.*",\n'
        '    "/run/secrets",\n'
        '    "~/.aws",\n'
        '    "~/.azure",\n'
        '    "~/.cargo/credentials*",\n'
        '    "~/.claude/.credentials.json",\n'
        '    "~/.codex/auth.json",\n'
        '    "~/.config/containers/auth.json",\n'
        '    "~/.config/gcloud",\n'
        '    "~/.config/gh",\n'
        '    "~/.config/glab-cli",\n'
        '    "~/.config/huggingface",\n'
        '    "~/.config/pypoetry/auth.toml",\n'
        '    "~/.config/rclone",\n'
        '    "~/.config/sops",\n'
        '    "~/.docker/config.json",\n'
        '    "~/.gem/credentials",\n'
        '    "~/.git-credentials",\n'
        '    "~/.gnupg",\n'
        '    "~/.gradle/gradle.properties",\n'
        '    "~/.kube",\n'
        '    "~/.local/share/keyrings",\n'
        '    "~/.local/share/opencode/auth.json",\n'
        '    "~/.m2/settings.xml",\n'
        '    "~/.netrc",\n'
        '    "~/.npmrc",\n'
        '    "~/.oci",\n'
        '    "~/.password-store",\n'
        '    "~/.pypirc",\n'
        '    "~/.ssh",\n'
        '    "~/.terraform.d/credentials.tfrc.json",\n'
        '    "~/.vault-token",\n'
        "]\n"
        "\n[features]\n"
        "apps = false\n"
        "browser_use = false\n"
        "browser_use_external = false\n"
        "browser_use_full_cdp_access = false\n"
        "computer_use = false\n"
        "in_app_browser = false\n"
        "in_app_updates = false\n"
        "plugins = false\n"
        "\n[mcp_servers]\n"
    )


def _user_security_state_path(uid: int) -> str:
    return os.path.join(AGENT_USER_SECURITY_STATE_DIR, f"{uid}.json")


def _ensure_user_security_state_directory() -> None:
    if os.path.lexists(AGENT_USER_SECURITY_STATE_DIR):
        directory_stat = os.lstat(AGENT_USER_SECURITY_STATE_DIR)
        if (
            not stat.S_ISDIR(directory_stat.st_mode)
            or directory_stat.st_uid != os.geteuid()
            or stat.S_IMODE(directory_stat.st_mode) & 0o022
        ):
            raise RuntimeError(
                "Refusing unsafe agent user security state directory: "
                f"{AGENT_USER_SECURITY_STATE_DIR}"
            )
    else:
        os.makedirs(AGENT_USER_SECURITY_STATE_DIR, mode=0o700)
    os.chown(AGENT_USER_SECURITY_STATE_DIR, 0, 0)
    os.chmod(AGENT_USER_SECURITY_STATE_DIR, 0o700)


def _validate_user_security_state_target(path: str) -> None:
    if not os.path.lexists(path):
        return
    path_stat = os.lstat(path)
    if (
        not stat.S_ISREG(path_stat.st_mode)
        or path_stat.st_uid != os.geteuid()
        or stat.S_IMODE(path_stat.st_mode) != 0o600
    ):
        raise RuntimeError(f"Refusing unsafe agent user security state: {path}")


def _validate_user_controls(value: object) -> JSONDict | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise RuntimeError("Invalid agent user security state: user_controls")
    home_mode = value.get("home_mode")
    password_status = value.get("password_status")
    linger_enabled = value.get("linger_enabled")
    if (
        isinstance(home_mode, bool)
        or not isinstance(home_mode, int)
        or not 0 <= home_mode <= 0o7777
        or password_status not in {"L", "NP", "P"}
        or (
            linger_enabled is not None
            and not isinstance(linger_enabled, bool)
        )
    ):
        raise RuntimeError("Invalid agent user security state: user_controls")
    return {
        "home_mode": home_mode,
        "password_status": password_status,
        "linger_enabled": linger_enabled,
    }


def _load_user_security_state(uid: int) -> JSONDict:
    if os.path.lexists(AGENT_USER_SECURITY_STATE_DIR):
        _ensure_user_security_state_directory()
    path = _user_security_state_path(uid)
    if not os.path.lexists(path):
        return {
            "version": _USER_SECURITY_STATE_VERSION,
            "uid": uid,
            "removed_groups": [],
            "user_controls": None,
        }
    _validate_user_security_state_target(path)
    try:
        with open(path, "r", encoding="utf-8") as file_obj:
            value = json.load(file_obj)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Invalid agent user security state: {path}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"Invalid agent user security state: {path}")
    state_uid = value.get("uid")
    removed_groups = value.get("removed_groups")
    if (
        value.get("version") != _USER_SECURITY_STATE_VERSION
        or isinstance(state_uid, bool)
        or state_uid != uid
        or not isinstance(removed_groups, list)
        or any(
            not isinstance(group_name, str)
            or group_name not in _USER_DENIED_GROUPS
            for group_name in removed_groups
        )
        or len(removed_groups) != len(set(removed_groups))
    ):
        raise RuntimeError(f"Invalid agent user security state: {path}")
    return {
        "version": _USER_SECURITY_STATE_VERSION,
        "uid": uid,
        "removed_groups": list(removed_groups),
        "user_controls": _validate_user_controls(value.get("user_controls")),
    }


def _write_user_security_state(state: JSONDict) -> None:
    uid = state["uid"]
    if not isinstance(uid, int) or isinstance(uid, bool):
        raise RuntimeError("Invalid agent user security state UID")
    path = _user_security_state_path(uid)
    if os.path.lexists(AGENT_USER_SECURITY_STATE_DIR):
        _ensure_user_security_state_directory()
    removed_groups = state.get("removed_groups")
    user_controls = state.get("user_controls")
    if removed_groups or user_controls is not None:
        _ensure_user_security_state_directory()
        _validate_user_security_state_target(path)
        write_json_atomic(path, state, mode=0o600, sort_keys=True)
        os.chown(path, 0, 0)
        os.chmod(path, 0o600)
        return
    if os.path.lexists(path):
        _validate_user_security_state_target(path)
        remove_file_durable(path)


def _account_groups(username: str, primary_gid: int) -> set[str]:
    groups: set[str] = set()
    for group_id in os.getgrouplist(username, primary_gid):
        try:
            groups.add(grp.getgrgid(group_id).gr_name)
        except KeyError:
            continue
    return groups


def _group_exists(group_name: str) -> bool:
    try:
        grp.getgrnam(group_name)
    except KeyError:
        return False
    return True


def _password_status(username: str) -> str:
    result = run(
        ["passwd", "--status", username],
        capture_output=True,
    )
    fields = (result.stdout or "").split()
    if len(fields) < 2 or fields[0] != username or fields[1] not in {
        "L",
        "NP",
        "P",
    }:
        raise RuntimeError(f"Could not determine password status for {username}")
    return fields[1]


def _validated_user_home(account: pwd.struct_passwd) -> tuple[str, int]:
    home = account.pw_dir
    validate_filesystem_path(home, must_exist=True)
    if not os.path.isabs(home) or home == "/":
        raise RuntimeError(f"Refusing unsafe home directory for {account.pw_name}")
    home_stat = os.lstat(home)
    if not stat.S_ISDIR(home_stat.st_mode) or home_stat.st_uid != account.pw_uid:
        raise RuntimeError(f"Refusing unsafe home directory for {account.pw_name}")
    return home, stat.S_IMODE(home_stat.st_mode)


def _linger_enabled(username: str) -> bool:
    path = os.path.join(SYSTEMD_LINGER_DIR, username)
    if not os.path.lexists(path):
        return False
    if not stat.S_ISREG(os.lstat(path).st_mode):
        raise RuntimeError(f"Refusing unsafe systemd linger marker: {path}")
    return True


def _capture_user_controls(
    config: SetupConfig,
    account: pwd.struct_passwd,
) -> JSONDict:
    _home, home_mode = _validated_user_home(account)
    linger_enabled = (
        _linger_enabled(config.username)
        if can_manage_system_services(config.machine_type)
        else None
    )
    return {
        "home_mode": home_mode,
        "password_status": _password_status(config.username),
        "linger_enabled": linger_enabled,
    }


def _apply_user_controls(
    config: SetupConfig,
    account: pwd.struct_passwd,
) -> None:
    home, _home_mode = _validated_user_home(account)
    os.chmod(home, 0o700, follow_symlinks=False)
    if _password_status(config.username) != "L":
        run(["usermod", "--lock", config.username])
    if (
        can_manage_system_services(config.machine_type)
        and _linger_enabled(config.username)
    ):
        run(["loginctl", "disable-linger", config.username])


def _restore_user_controls(
    config: SetupConfig,
    account: pwd.struct_passwd,
    controls: JSONDict,
) -> None:
    home, _home_mode = _validated_user_home(account)
    os.chmod(home, int(controls["home_mode"]), follow_symlinks=False)
    current_password_status = _password_status(config.username)
    if controls["password_status"] == "P" and current_password_status == "L":
        run(["usermod", "--unlock", config.username])
    elif controls["password_status"] == "NP" and current_password_status == "L":
        run(["passwd", "--delete", config.username])
    elif (
        controls["password_status"] == "L"
        and current_password_status != "L"
    ):
        run(["usermod", "--lock", config.username])
    linger_enabled = controls["linger_enabled"]
    if (
        isinstance(linger_enabled, bool)
        and can_manage_system_services(config.machine_type)
        and _linger_enabled(config.username) != linger_enabled
    ):
        action = "enable-linger" if linger_enabled else "disable-linger"
        run(["loginctl", action, config.username])


def configure_agent_user_security(config: SetupConfig) -> None:
    """Reconcile reversible Linux-account restrictions for an agent user."""

    if not validate_username(config.username):
        raise ValueError(f"Invalid setup username: {config.username}")
    if config.username == "root":
        return
    if is_dry_run():
        posture = (
            "hardened user"
            if config.harden_user
            else "hardened agent"
            if config.harden_agent
            else "standard user"
        )
        print(f"  [DRY-RUN] Would reconcile the {posture} account policy")
        return

    try:
        account = pwd.getpwnam(config.username)
    except KeyError as exc:
        raise RuntimeError(
            f"Target user does not exist: {config.username}"
        ) from exc
    state = _load_user_security_state(account.pw_uid)
    removed_groups = set(state["removed_groups"])
    desired_denied_groups = (
        _USER_DENIED_GROUPS
        if config.harden_user
        else _AGENT_DENIED_GROUPS
        if config.harden_agent or config.privilege_broker_port is not None
        else frozenset()
    )
    current_groups = _account_groups(config.username, account.pw_gid)
    primary_group = grp.getgrgid(account.pw_gid).gr_name
    if primary_group in desired_denied_groups:
        raise RuntimeError(
            f"Cannot harden {config.username}: denied primary group {primary_group}"
        )

    newly_removed_groups = desired_denied_groups & current_groups
    removed_groups.update(newly_removed_groups)
    controls = state["user_controls"]
    if config.harden_user and controls is None:
        controls = _capture_user_controls(config, account)
    pending_state: JSONDict = {
        "version": _USER_SECURITY_STATE_VERSION,
        "uid": account.pw_uid,
        "removed_groups": sorted(removed_groups),
        "user_controls": controls,
    }
    if newly_removed_groups or (
        config.harden_user and state["user_controls"] is None
    ):
        _write_user_security_state(pending_state)

    for group_name in sorted(desired_denied_groups & current_groups):
        run(["gpasswd", "--delete", config.username, group_name])

    retained_removed_groups = set(removed_groups)
    for group_name in sorted(removed_groups - desired_denied_groups):
        if not _group_exists(group_name):
            print(f"  ! Cannot restore missing group: {group_name}")
            continue
        elif group_name not in current_groups:
            run(
                [
                    "usermod",
                    "--append",
                    "--groups",
                    group_name,
                    config.username,
                ]
            )
        retained_removed_groups.discard(group_name)

    if config.harden_user:
        _apply_user_controls(config, account)
    elif controls is not None:
        _restore_user_controls(config, account, controls)
        controls = None

    final_state: JSONDict = {
        "version": _USER_SECURITY_STATE_VERSION,
        "uid": account.pw_uid,
        "removed_groups": sorted(retained_removed_groups),
        "user_controls": controls,
    }
    _write_user_security_state(final_state)
    posture = (
        "hardened user"
        if config.harden_user
        else "hardened agent"
        if config.harden_agent
        else "standard user"
    )
    print(f"  ✓ {posture.capitalize()} account policy configured")
    if config.privilege_broker_port is not None:
        print("  ✓ Brokered account: privileged supplementary groups removed; restart existing user sessions to discard old groups")


def _ensure_codex_policy_directory() -> None:
    if os.path.lexists(CODEX_SYSTEM_CONFIG_DIR):
        directory_stat = os.lstat(CODEX_SYSTEM_CONFIG_DIR)
        if (
            not stat.S_ISDIR(directory_stat.st_mode)
            or directory_stat.st_uid != os.geteuid()
            or stat.S_IMODE(directory_stat.st_mode) & 0o022
        ):
            raise RuntimeError(
                f"Refusing unsafe Codex policy directory: {CODEX_SYSTEM_CONFIG_DIR}"
            )
    else:
        os.makedirs(CODEX_SYSTEM_CONFIG_DIR, mode=0o755)
    os.chown(CODEX_SYSTEM_CONFIG_DIR, 0, 0)
    os.chmod(CODEX_SYSTEM_CONFIG_DIR, 0o755)


def _has_managed_codex_policy_marker(path: str) -> bool:
    with open(path, "r", encoding="utf-8") as file_obj:
        first_line = file_obj.readline(len(_MANAGED_MARKER) + 2)
    return first_line == f"{_MANAGED_MARKER}\n"


def _validate_managed_codex_policy_target(path: str) -> None:
    """Reject unsafe or administrator-owned policy paths before any write."""

    if os.path.lexists(path):
        path_stat = os.lstat(path)
        if (
            not stat.S_ISREG(path_stat.st_mode)
            or path_stat.st_uid != os.geteuid()
            or stat.S_IMODE(path_stat.st_mode) & 0o022
        ):
            raise RuntimeError(f"Refusing unsafe Codex policy path: {path}")
        if not _has_managed_codex_policy_marker(path):
            raise RuntimeError(
                f"Refusing to replace unmanaged Codex policy: {path}"
            )


def _is_managed_codex_policy(path: str) -> bool:
    """Return whether an existing regular policy file belongs to infra-tools."""

    if not os.path.lexists(path):
        return False
    path_stat = os.lstat(path)
    if not stat.S_ISREG(path_stat.st_mode):
        return False
    if (
        path_stat.st_uid != os.geteuid()
        or stat.S_IMODE(path_stat.st_mode) & 0o022
    ):
        raise RuntimeError(f"Refusing unsafe Codex policy path: {path}")
    return _has_managed_codex_policy_marker(path)


def _write_managed_codex_policy(path: str, content: str) -> None:
    """Atomically replace only an absent or infra-tools-owned policy file."""

    tomllib.loads(content)
    _validate_managed_codex_policy_target(path)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as file_obj:
            existing = file_obj.read()
        if existing == content:
            os.chown(path, 0, 0)
            os.chmod(path, 0o644)
            return

    write_text_atomic(path, content, mode=0o644)
    os.chown(path, 0, 0)
    os.chmod(path, 0o644)


def configure_codex_security_policy(config: SetupConfig) -> None:
    """Set overridable Codex defaults or enforce the hardened boundary."""

    policy_name = "hardened" if config.harden_agent else "auto-reviewed workspace"
    if is_dry_run():
        print(f"  [DRY-RUN] Would configure the Codex {policy_name} policy")
        return

    _ensure_codex_policy_directory()
    config_path = os.path.join(
        CODEX_SYSTEM_CONFIG_DIR, CODEX_SYSTEM_CONFIG_NAME
    )
    requirements_path = os.path.join(
        CODEX_SYSTEM_CONFIG_DIR, CODEX_REQUIREMENTS_NAME
    )
    config_content = _codex_config_content(hardened=config.harden_agent)
    tomllib.loads(config_content)
    if config.harden_agent:
        requirements_content = _codex_requirements_content()
        tomllib.loads(requirements_content)
        _validate_managed_codex_policy_target(config_path)
        _validate_managed_codex_policy_target(requirements_path)
        _write_managed_codex_policy(requirements_path, requirements_content)
        _write_managed_codex_policy(config_path, config_content)
    else:
        if not os.path.lexists(config_path) or _is_managed_codex_policy(
            config_path
        ):
            _write_managed_codex_policy(config_path, config_content)
        else:
            print(f"  ! Preserving unmanaged Codex defaults: {config_path}")

        if _is_managed_codex_policy(requirements_path):
            remove_file_durable(requirements_path)
        elif os.path.lexists(requirements_path):
            print(
                f"  ! Preserving unmanaged Codex requirements: {requirements_path}"
            )
    print(f"  ✓ Codex {policy_name} policy configured")
