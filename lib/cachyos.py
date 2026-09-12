"""Explicit support boundary for local, user-owned CachyOS coding desktops."""

from __future__ import annotations

import argparse
from dataclasses import fields
import os
import platform
import pwd
import shutil
import subprocess

from lib.config import SetupConfig
from lib.validation import validate_filesystem_path, validate_agent_repositories
from lib.validators import validate_host, validate_username


PROFILE = "agent_cachyos"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
_OPTIONS = {
    "host", "username", "system_type", "dry_run", "machine_type",
    "agent_tools", "no_agent_tools", "install_node", "install_python",
    "install_go", "install_git_lfs", "install_av_tools", "install_gl_tools",
    "install_godot", "agent_workspace", "agent_repos", "web_interfaces",
    "web_interface_host", "web_interface_port",
}
_CONFIG_OPTIONS = (_OPTIONS - {"no_agent_tools"}) | {
    "agent_tools_removed", "install_gh", "install_codex", "install_claude",
    "install_opencode",
}


def is_cachyos() -> bool:
    try:
        return platform.freedesktop_os_release().get("ID") == "cachyos"
    except OSError:
        return False


def _default_args(*, for_remote: bool = False) -> argparse.Namespace:
    from lib.arg_parser import add_setup_arguments

    parser = argparse.ArgumentParser(add_help=False)
    add_setup_arguments(parser, for_remote=for_remote, allow_steps=True,
                        include_system_type=not for_remote)
    return parser.parse_args(
        ["--system-type", PROFILE] if for_remote else [PROFILE, "localhost"]
    )


def cachyos_config_from_args(
    args: argparse.Namespace, *, for_remote: bool = False,
) -> SetupConfig:
    """Reject unrelated setup features before config normalization or side effects."""
    defaults = vars(_default_args(for_remote=for_remote))
    for name, value in vars(args).items():
        if name in _OPTIONS or name == "command":
            continue
        if value != defaults.get(name) and value is not None and value is not False:
            raise ValueError(f"agent_cachyos does not support setup option {name!r}")
    copied = _default_args()
    for name in _OPTIONS:
        if hasattr(args, name) and getattr(args, name) != defaults.get(name):
            setattr(copied, name, getattr(args, name))
    copied.host = getattr(args, "host", "localhost")
    copied.username = getattr(args, "username", None) or pwd.getpwuid(os.getuid()).pw_name
    config = SetupConfig.from_args(copied, PROFILE)
    validate_cachyos_config(config)
    return config


def validate_cachyos_config(config: SetupConfig) -> None:
    """Fail closed when generic callers select features outside this composition."""
    baseline = SetupConfig.from_args(_default_args(), PROFILE)
    for field in fields(config):
        if field.name not in _CONFIG_OPTIONS and (
            getattr(config, field.name) != getattr(baseline, field.name)
        ):
            raise ValueError(f"agent_cachyos does not support {field.name!r}")
    if config.system_type != PROFILE:
        raise ValueError("Expected agent_cachyos profile")
    if not validate_host(config.host) or config.host not in LOCAL_HOSTS:
        raise ValueError("agent_cachyos supports local setup only; use localhost")
    if config.machine_type not in {"auto", "hardware"}:
        raise ValueError("agent_cachyos supports existing bare-metal workstations only")
    if not validate_username(config.username) or config.username == "root":
        raise ValueError("Run agent_cachyos as your existing non-root desktop user")
    if config.web_interface_host not in {None, "127.0.0.1"}:
        raise ValueError("CachyOS T3 Code must bind to 127.0.0.1")
    if config.web_interfaces and config.web_interface_port < 1024:
        raise ValueError("CachyOS T3 Code requires an unprivileged port (1024-65535)")
    validate_agent_repositories(config.agent_repos)
    if config.agent_workspace:
        validate_filesystem_path(config.agent_workspace)
        if not os.path.isabs(config.agent_workspace):
            raise ValueError("--agent-workspace must be an absolute path")


def preflight_cachyos(config: SetupConfig) -> None:
    """Read-only checks; dry-run plans can also be generated on the CI host."""
    validate_cachyos_config(config)
    if config.dry_run:
        return
    if not is_cachyos() or platform.machine() != "x86_64":
        raise ValueError("agent_cachyos requires CachyOS on x86_64")
    try:
        account = pwd.getpwnam(config.username)
    except KeyError as exc:
        raise ValueError(f"Existing desktop account not found: {config.username}") from exc
    if os.geteuid() == 0 or os.geteuid() != account.pw_uid:
        raise ValueError("Run setup from a terminal as the existing desktop user, without sudo")
    if os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY"):
        raise ValueError("Run agent_cachyos locally in the workstation's desktop session")
    from lib.machine_state import detect_machine_type

    if detect_machine_type() != "hardware":
        raise ValueError("agent_cachyos requires an existing bare-metal workstation")
    if not shutil.which("pacman") or not shutil.which("plasmashell"):
        raise ValueError("CachyOS with KDE Plasma must already be installed")
    validate_filesystem_path(account.pw_dir, must_exist=True, check_writable=True)
    if os.path.realpath(os.path.expanduser("~")) != os.path.realpath(account.pw_dir):
        raise ValueError("HOME must belong to the invoking desktop user")
    if config.web_interfaces:
        from lib.remote_utils import run

        result = run(["systemctl", "--user", "show-environment"],
                     capture_output=True, check=False)
        if result.returncode:
            raise ValueError("T3 Code requires an active systemd user session; log into KDE first")


def run_cachyos_command(args: argparse.Namespace) -> int:
    try:
        config = cachyos_config_from_args(args)
        # Keep target mutations inside the target-side setup boundary. This
        # profile needs neither SSH staging nor controller credential copying.
        from remote_setup import run_cachyos_setup

        return run_cachyos_setup(config)
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"Error: {exc}")
        return 1


def bootstrap_cachyos(
    script_path: str, shell: str, requested_user: str | None, *,
    skip_system_packages: bool, install_qemu_guest_agent: bool,
) -> int:
    """Install the user launcher without Debian Python aliases or host policies."""
    from common.cachyos_steps import configure_cachyos_shell, install_missing_packages
    from lib.orchestrator_bootstrap import install_launcher, resolve_bootstrap_user

    try:
        username, home = resolve_bootstrap_user(requested_user)
        if os.geteuid() == 0 or pwd.getpwnam(username).pw_uid != os.geteuid():
            raise ValueError("Run the CachyOS installer as your desktop user, without sudo")
        if install_qemu_guest_agent:
            raise ValueError("CachyOS bootstrap does not support --qemu-guest-agent")
        if not skip_system_packages:
            install_missing_packages(["python", "git", "curl", "openssh", "rsync", "tar"])
        configure_cachyos_shell(home, shell)
        launcher = install_launcher(script_path, target_dir=os.path.join(home, ".local", "bin"))
        print(f"Installed CachyOS user launcher: {launcher}")
        return 0
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"Error: {exc}")
        return 1
