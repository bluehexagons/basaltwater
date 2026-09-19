#!/usr/bin/env python3
# PYTHON_ARGCOMPLETE_OK
"""
infra_tools - Unified entry point for infrastructure setup and management.

This script provides a unified interface to all infra_tools functionality,
combining setup and patch operations into a single command-line tool.

Usage:
    basaltw setup <system_type> <host> [options]
    basaltw patch <host> [options]
    basaltw --help

System Types:
    control_plane         Infrastructure control plane setup
    workstation_desktop  Desktop workstation setup
    pc_dev              PC development environment
    workstation_dev     Developer workstation setup
    server_dev          Development server setup
    server_web          Web server setup
    server_lite         Lightweight server setup
    server_proxmox      Proxmox server setup
"""

from __future__ import annotations

import argparse
import getpass
import ipaddress
import json
import os
import sys
import time
from typing import Optional, Tuple, cast

try:
    import argcomplete
except ImportError:
    argcomplete = None

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib.arg_parser import add_setup_arguments
from lib.agent_cli import add_agent_subparser, run_agent_command
from lib.gogs_cli import add_gogs_subparser, run_gogs_command
from lib.homebox_cli import add_homebox_subparser, run_homebox_command
from lib.homebox_config import validate_homebox_settings
from lib.cache import get_cache_path_for_host, load_setup_command, merge_setup_configs, save_setup_command
from lib.channel_manager import (
    ChannelError,
    get_channel_info,
    managed_repository_path,
    switch_channel,
    upgrade_channel,
)
from lib.cicd_cli import add_cicd_subparser, run_cicd_command
from lib.completions import run_completion_setup
from lib.config_cleanup import run_cleanup
from lib.config import WEB_PANEL_DEFAULT_PORT_SENTINEL, SetupConfig
from lib.credentials import (
    list_workspace_credentials,
    prepare_runtime_config,
    remove_workspace_credential,
    set_workspace_credential,
    store_cli_credentials,
)
from lib.firmware_cli import add_firmware_subparser, run_firmware_command
from lib.github_maintenance import add_maintenance_subparser, run_maintenance_command
from lib.display import (
    print_name_and_tags,
    print_service_access_summary,
    print_setup_summary,
)
from lib.interactive_shell import run_interactive_shell
from lib.installation_info import installation_version
from lib.notifications import validate_notification_args
from lib.orchestrator_bootstrap import LAUNCHER_NAME, run_orchestrator_bootstrap
from lib.plugin_registry import (
    format_system_type_help,
    get_system_type_names,
)
from lib.network_cli import add_network_subparser, run_network_command
from lib.local_cli import add_local_subparser, run_local_command
from lib.desktop_cli import add_desktop_subparser, run_desktop_command
from lib.proxmox_guest import (
    ProvisionError,
    _build_guest_hostname,
    ensure_guest_ipv4_route,
    refresh_managed_guest_host_keys,
)
from lib.proxmox_hosts import find_proxmox_host
from lib.proxmox_cli import add_proxmox_subparser, run_proxmox_command
from lib.vm_cli import add_vm_subparser, run_vm_command
from lib.sysadmin_cli import add_sysadmin_subparsers, run_sysadmin_command
from lib.python_setup import run_local_python_setup
from lib.recall import run_recall_command
from lib.reconstruct import run_reconstruct_command
from lib.remote_utils import confirm_unsupported_environment
from lib.setup_common import (
    REMOTE_SCRIPT_PATH,
    _apply_hosted_proxmox_defaults,
    adopt_verified_network_host,
    get_last_remote_access_details,
    register_proxmox_setup_host,
    remove_replaced_setup_cache,
    run_remote_setup,
)
from lib.interactive_setup import prompt_for_missing_passwords
from lib.system_utils import get_current_username
from lib.types import Deployments, JSONDict, JSONList, NestedStrList, StrList
from lib.validators import validate_host, validate_username
from lib.validation import (
    validate_apt_packages,
    validate_agent_repositories,
    validate_antistatic_settings,
    validate_deploy_specs,
    validate_deploy_targets,
    validate_gogs_settings,
    validate_hosted_flags,
    validate_network_setup_settings,
    validate_rdp_settings,
    validate_samba_settings,
    validate_samba_share_credentials,
    validate_samba_share_name,
    validate_samba_share_specs,
    validate_smb_mount_specs,
    validate_scrub_specs,
    validate_backup_specs,
    validate_web_interface_settings,
    validate_ssl_email,
    validate_sync_specs,
    validate_timezone_name,
    validate_workspace_dir,
)
from lib.workspace import get_setup_cache_dir, get_workspace_dir, set_workspace_dir


def _build_infra_tools_epilog() -> str:
    return f"""Available Commands:
    setup <type> <host> [args]   Run initial setup for a system type
    patch <host> [args]          Patch/update an existing system
    shares <host> [args]         Reconcile Samba shares without full setup
    list [pattern]              List saved configurations
    info [pattern]              Show saved configuration details
    cmd [pattern]               Show reconstructed setup commands
    rm <pattern>                Remove saved configurations
    cleanup [host] [options]   Remove obsolete local configuration state
    deploy <pattern>            Redeploy saved configurations
    recall <host> [username]    Fetch or reconstruct a remote setup command
    reconstruct                 Analyze this host and emit a setup summary
    completions                 Install shell completion for basaltw
    python-tools                Install local Python aliases, uv, and completion
    bootstrap                   Install packages, launcher, and completions (alias: self-setup)
    channel [CHANNEL]           Show or switch the installed source channel
    upgrade                     Upgrade the installed source on its selected channel
    agent doctor|update|auth   Check, update, or rotate agent credentials
    maintenance github ...      Audit/prune GitHub releases, artifacts, and caches
    network [subcommand]        Manage generic network inventory profiles
    local [subcommand]          Maintain this local Debian system
    firmware audit|update       Audit or deliberately update local firmware
    proxmox [subcommand]        Manage Proxmox hosts and containers (interactive shell with no args)
    vm [subcommand]             Manage guests through a provider-neutral command surface
    shell                       Interactive REPL for managing saved configurations
    credentials                 Manage workspace credentials
    cicd connect|status|test    Connect and inspect build/app deployment trust

Sysadmin Shortcuts:
    mount <host:path> <local>   Mount a remote directory via sshfs
    umount <local|host>         Unmount an sshfs mount
    health <host>               Show uptime, disk, memory, failed units, pending upgrades
    ssh <host> [-- cmd]         Open SSH session using saved config
    push <local> <host:path>    Rsync a local path to a remote host
    pull <host:path> [local]    Rsync a remote path to local
    key push <host>             Install local public key on a remote host
    df <host> [<host2> ...]     Multi-host disk usage table (>85% highlighted)
    fan <host> [..] -- <cmd>    Run a command on multiple hosts in parallel
    svc <host> <unit> [action]  Manage a systemd service (status/restart/start/stop/…)
    logs <host> <unit>          Show or follow journalctl output for a service
    upgrade <host> [<host2>…]   Run apt upgrade in parallel; report reboot-required
    reachable [hosts|pattern]   Check which saved hosts are reachable via SSH
    user rename <host> <new>    Rename the configured remote target user
    ssh-key enroll <host>       Enroll and verify a workspace SSH host key

System Types for setup:
{format_system_type_help()}

Examples:
  basaltw setup server_web 192.168.1.100 admin --ssl
  basaltw patch 192.168.1.100 --deploy api.example.com https://github.com/user/api.git
  basaltw shares 192.168.1.100 --share write media /srv/media alice,bob
  basaltw list prod
  basaltw deploy prod --yes
  basaltw recall example.com admin
  basaltw completions --shell zsh
  sudo basaltw self-setup --user admin [--qemu-guest-agent]
  basaltw list prod    # after self-setup, the launcher is on PATH
 """


def _current_command_name() -> str:
    return LAUNCHER_NAME


def _is_local_host(host: str) -> bool:
    """Return whether a command targets this orchestration host."""

    return host in {"localhost", "127.0.0.1", "::1"}


def _managed_repository() -> str:
    return managed_repository_path(__file__)


def run_channel_command(args: argparse.Namespace) -> int:
    """Show or switch the channel selected for the installed worktree."""

    try:
        repository = _managed_repository()
        if args.channel_name is None:
            info = get_channel_info(repository)
            if info.get("managed"):
                print(f"Channel: {info['channel']}")
            else:
                print("Channel: unmanaged checkout")
            print(f"Commit: {str(info['commit'])[:12]}")
            if info.get("branch"):
                print(f"Branch: {info['branch']}")
            if info.get("version"):
                print(f"Version: {info['version']}")
            if info.get("dirty") is True:
                print("Source state: dirty when deployed")
            return 0

        info = switch_channel(repository, args.channel_name)
        print(f"Switched to channel {info['channel']} at {str(info['commit'])[:12]}")
        return 0
    except (ChannelError, ValueError, OSError) as exc:
        print(f"Error: {exc}")
        return 1


def run_tool_upgrade_command(args: argparse.Namespace | None = None) -> int:
    """Upgrade the installed source to the latest commit on its channel."""

    del args
    try:
        info = upgrade_channel(_managed_repository())
        if info.get("updated"):
            print(f"Upgraded {info['channel']} to {str(info['commit'])[:12]}")
        else:
            print(f"basaltw is already up to date on {info['channel']} ({str(info['commit'])[:12]})")
        return 0
    except (ChannelError, ValueError, OSError) as exc:
        print(f"Error: {exc}")
        return 1


def create_infra_tools_parser() -> Tuple[argparse.ArgumentParser, argparse.ArgumentParser, argparse.ArgumentParser]:
    """Create the main argument parser for infra_tools."""
    parser = argparse.ArgumentParser(
        prog=LAUNCHER_NAME,
        description="Basaltwater — infrastructure management, from one machine to your whole network",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_build_infra_tools_epilog()
    )
    parser.add_argument(
        "--workspace",
        help="Workspace root for saved setups, credentials, known_hosts, and history"
    )
    parser.add_argument(
        "--version",
        action="version",
        version=(
            f"%(prog)s {installation_version(os.path.dirname(os.path.abspath(__file__)))}"
        ),
    )
    
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    
    # Setup subcommand
    setup_parser = subparsers.add_parser(
        "setup",
        help="Run initial setup for a system type",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Run 'basaltw setup --help' for full options"
    )
    add_setup_arguments(setup_parser, allow_steps=True, include_system_type=True)
    setup_parser.add_argument(
        "--verify-provider",
        action="store_true",
        help=(
            "Verify a cached provisioned guest against Proxmox and reconcile "
            "supported provider-side settings"
        ),
    )
    
    # Patch subcommand
    patch_parser = subparsers.add_parser(
        "patch",
        help="Patch/update an existing system",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Run 'basaltw patch --help' for full options"
    )
    patch_parser.add_argument(
        "host",
        help="IP address or hostname of the remote host"
    )
    patch_parser.add_argument(
        "username",
        nargs="?",
        default=None,
        help="Username (defaults to current user)"
    )
    add_setup_arguments(patch_parser, allow_steps=True, include_host=False)

    shares_parser = subparsers.add_parser(
        "shares",
        help="Quickly reconcile Samba shares on an existing system",
    )
    shares_parser.add_argument("host", help="Host with a saved setup configuration")
    shares_parser.add_argument("username", nargs="?", default=None)
    shares_parser.add_argument("-k", "--key", dest="ssh_key", help="SSH private key path")
    shares_parser.add_argument(
        "--share",
        dest="samba_shares",
        action="append",
        nargs=4,
        metavar=("ACCESS_TYPE", "SHARE_NAME", "PATH", "USERS"),
        help="Add or replace a share by name; repeat as needed",
    )
    shares_parser.add_argument(
        "--remove-share",
        action="append",
        default=[],
        metavar="SHARE_NAME",
        help="Remove a managed share by name; repeat as needed",
    )
    shares_parser.add_argument(
        "--credential",
        dest="share_credentials",
        action="append",
        nargs=2,
        metavar=("USERNAME", "PASSWORD"),
        help="Save or update a share user's workspace credential",
    )
    shares_parser.add_argument("--dry-run", action="store_true")
    shares_parser.add_argument(
        "--workspace",
        help="Workspace root for saved setups, credentials, known_hosts, and history",
    )

    list_parser = subparsers.add_parser(
        "list",
        aliases=["ls"],
        help="List saved configurations",
    )
    list_parser.add_argument("pattern", nargs="?", default=None, help="Optional host, name, or tag filter")
    list_parser.add_argument(
        "--workspace",
        help="Workspace root for saved setups, credentials, known_hosts, and history"
    )
    list_parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as a JSON array for scripting",
    )

    info_parser = subparsers.add_parser(
        "info",
        help="Show saved configuration details",
    )
    info_parser.add_argument("pattern", nargs="?", default=None, help="Optional host, name, or tag filter")
    info_parser.add_argument(
        "--workspace",
        help="Workspace root for saved setups, credentials, known_hosts, and history"
    )
    info_parser.add_argument(
        "--compact",
        action="store_true",
        help="Show one-line summary per configuration instead of full details",
    )

    cmd_parser = subparsers.add_parser(
        "cmd",
        aliases=["command"],
        help="Show reconstructed setup commands",
    )
    cmd_parser.add_argument("pattern", nargs="?", default=None, help="Optional host, name, or tag filter")
    cmd_parser.add_argument(
        "--workspace",
        help="Workspace root for saved setups, credentials, known_hosts, and history"
    )

    remove_parser = subparsers.add_parser(
        "rm",
        aliases=["remove"],
        help="Remove saved configurations",
    )
    remove_parser.add_argument("pattern", help="Host, name, or tag filter to remove")
    remove_parser.add_argument("-y", "--yes", action="store_true", help="Remove without prompting")
    remove_parser.add_argument(
        "--workspace",
        help="Workspace root for saved setups, credentials, known_hosts, and history"
    )

    cleanup_parser = subparsers.add_parser(
        "cleanup",
        help="Remove obsolete local setup and Proxmox configuration state",
    )
    cleanup_parser.add_argument(
        "host",
        nargs="?",
        help=(
            "Optional setup or Proxmox host to target; with no host, inspect all "
            "selected local state"
        ),
    )
    cleanup_parser.add_argument(
        "--setup-cache",
        action="store_true",
        help="Inspect obsolete setup-cache files",
    )
    cleanup_parser.add_argument(
        "--proxmox-registry",
        action="store_true",
        help="Inspect invalid Proxmox host records",
    )
    cleanup_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show findings without changing files",
    )
    cleanup_parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Apply cleanup without prompting",
    )
    cleanup_parser.add_argument(
        "--workspace",
        default=argparse.SUPPRESS,
        help="Workspace root for saved setups, credentials, known_hosts, and history",
    )

    deploy_parser = subparsers.add_parser(
        "deploy",
        help="Redeploy saved configurations",
    )
    add_deploy_command_arguments(deploy_parser, include_workspace=True)

    recall_parser = subparsers.add_parser(
        "recall",
        help="Fetch or reconstruct a remote setup command",
    )
    recall_parser.add_argument("host", help="IP address or hostname of the remote host")
    recall_parser.add_argument(
        "username",
        nargs="?",
        default=None,
        help="Username (defaults to current user)",
    )
    recall_parser.add_argument("-k", "--key", dest="ssh_key", help="SSH private key path")

    reconstruct_parser = subparsers.add_parser(
        "reconstruct",
        help="Analyze this host and emit a reconstructed setup summary",
    )
    reconstruct_parser.add_argument(
        "--compact",
        "-c",
        action="store_true",
        help="Output compact JSON (default: pretty-printed)",
    )

    completions_parser = subparsers.add_parser(
        "completions",
        help="Install shell completion for basaltw",
    )
    completions_parser.add_argument(
        "--shell",
        choices=["bash", "zsh", "fish", "tcsh", "auto"],
        default="auto",
        help="Shell type (default: auto-detect)",
    )
    completions_parser.add_argument(
        "--global",
        dest="global_install",
        action="store_true",
        help="Install completions system-wide (requires sudo/root)",
    )
    completions_parser.add_argument(
        "--user",
        action="store_true",
        help="Install completions for current user only (default)",
    )

    python_tools_parser = subparsers.add_parser(
        "python-tools",
        aliases=["admin-python"],
        help="Install local Python aliases, uv, and completion",
    )
    python_tools_parser.add_argument(
        "--shell",
        choices=["bash", "zsh", "fish", "tcsh"],
        default="bash",
        help="Shell to configure for completion (default: bash)",
    )
    python_tools_parser.add_argument(
        "--script-path",
        dest="script_path",
        default=None,
        help="Absolute path to infra_tools.py (used to install the user launcher)",
    )

    bootstrap_parser = subparsers.add_parser(
        "bootstrap",
        aliases=["self-setup"],
        help="Install local packages, launcher, and completions for basaltw",
    )
    bootstrap_parser.add_argument(
        "--shell",
        choices=["bash", "zsh", "fish", "tcsh"],
        default="bash",
        help="Shell to configure for completion (default: bash)",
    )
    bootstrap_parser.add_argument(
        "--user",
        dest="bootstrap_user",
        help="Local user to configure (defaults to SUDO_USER or current user)",
    )
    bootstrap_parser.add_argument(
        "--skip-system-packages",
        action="store_true",
        help="Skip local system-package installation and only configure basaltw for the target user",
    )
    bootstrap_parser.add_argument(
        "--qemu-guest-agent",
        action="store_true",
        help="Install, start, and enable Proxmox's qemu-guest-agent (requires root)",
    )

    channel_parser = subparsers.add_parser(
        "channel",
        help="Show or switch the installed source channel",
        description=(
            "Select the Git source used by the installed launcher. Channels are "
            "stable, dev, v<version>, branch-<branch>, or commit-<hash>."
        ),
    )
    channel_parser.add_argument(
        "channel_name",
        nargs="?",
        help="Channel to select; omit to show the current channel",
    )

    credentials_parser = subparsers.add_parser(
        "credentials",
        help="Manage workspace credentials",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    credentials_parser.add_argument(
        "--workspace",
        help="Workspace root for saved setups, credentials, known_hosts, and history"
    )
    credentials_subparsers = credentials_parser.add_subparsers(dest="credentials_command", help="Credential commands")

    credentials_set_parser = credentials_subparsers.add_parser("set", help="Save or replace a credential")
    credentials_set_parser.add_argument("username", help="Credential username")
    credentials_set_parser.add_argument(
        "password",
        nargs="?",
        help="Credential password (omit to enter it without exposing it in process arguments)",
    )

    credentials_subparsers.add_parser("list", help="List saved credential usernames")

    credentials_remove_parser = credentials_subparsers.add_parser("remove", help="Remove a saved credential")
    credentials_remove_parser.add_argument("username", help="Credential username to remove")

    add_network_subparser(subparsers)
    add_local_subparser(subparsers)
    add_desktop_subparser(subparsers)
    add_firmware_subparser(subparsers)
    add_proxmox_subparser(subparsers)
    add_vm_subparser(subparsers)
    add_maintenance_subparser(subparsers)
    add_sysadmin_subparsers(subparsers)
    add_agent_subparser(subparsers)
    add_gogs_subparser(subparsers)
    add_homebox_subparser(subparsers)
    add_cicd_subparser(subparsers)

    shell_parser = subparsers.add_parser(
        "shell",
        help="Start the interactive basaltw REPL",
    )
    shell_parser.add_argument(
        "--workspace",
        help="Workspace root for saved setups, credentials, known_hosts, and history",
    )

    return parser, setup_parser, patch_parser

def get_all_configs(pattern: Optional[str] = None) -> Deployments:
    cache_dir = get_setup_cache_dir()
    if not os.path.exists(cache_dir):
        return []

    configs: Deployments = []
    try:
        for filename in os.listdir(cache_dir):
            if not filename.endswith(".json"):
                continue

            filepath = os.path.join(cache_dir, filename)
            try:
                with open(filepath, "r", encoding="utf-8") as file_obj:
                    data = cast(JSONDict, json.load(file_obj))
                    configs.append(data)
            except Exception:
                continue
    except Exception as exc:
        print(f"Error reading configurations: {exc}")
        return []

    if pattern:
        needle = pattern.lower()
        filtered: Deployments = []
        for config in configs:
            if needle in str(config.get("host", "")).lower():
                filtered.append(config)
                continue
            if needle in str(config.get("name", "")).lower():
                filtered.append(config)
                continue
            tags = cast(StrList, config.get("tags", []))
            for tag in tags:
                if needle in tag.lower():
                    filtered.append(config)
                    break
        configs = filtered

    configs.sort(key=lambda item: item.get("host", ""))
    return configs


def reconstruct_command(config: SetupConfig) -> str:
    """Reconstruct the user-facing setup command from cached configuration."""
    return " ".join(config.to_setup_command())


def list_configurations(
    pattern: Optional[str] = None, *, json_output: bool = False
) -> int:
    from datetime import datetime

    configs = get_all_configs(pattern)
    if not configs:
        if json_output:
            print("[]")
            return 0
        if pattern:
            print(f"No configurations found matching '{pattern}'")
        else:
            print("No saved configurations found.")
        return 1

    if json_output:
        print(json.dumps(list(configs), indent=2, default=str))
        return 0

    host_width = 30
    name_width = 20
    type_width = 20
    user_width = 15
    date_width = 20
    status_width = 10
    total_width = host_width + name_width + type_width + user_width + date_width + status_width

    print(
        f"{'HOST':<{host_width}} {'NAME':<{name_width}} {'TYPE':<{type_width}} "
        f"{'USER':<{user_width}} {'LAST RUN':<{date_width}} {'STATUS':<{status_width}}"
    )
    print("-" * total_width)

    for config in configs:
        host = config.get("host", "Unknown")
        name = config.get("name", "")
        system_type = config.get("system_type", "Unknown")
        args = cast(JSONDict, config.get("args", {}))
        username = args.get("username", "Unknown")

        last_start_time = config.get("last_start_time")
        last_end_time = config.get("last_end_time")
        last_success = config.get("last_success")

        if last_start_time and last_end_time:
            start_dt = datetime.fromtimestamp(last_start_time)
            end_dt = datetime.fromtimestamp(last_end_time)
            duration = end_dt - start_dt
            last_run_str = f"{start_dt.strftime('%m/%d %H:%M')} ({duration.total_seconds():.0f}s)"
        elif last_start_time:
            start_dt = datetime.fromtimestamp(last_start_time)
            last_run_str = f"{start_dt.strftime('%m/%d %H:%M')} (running)"
        else:
            last_run_str = "Never"

        if last_success is True:
            status_str = "PASS"
        elif last_success is False:
            status_str = "FAIL"
        else:
            status_str = "UNKNOWN"

        print(
            f"{host:<{host_width}} {name:<{name_width}} {system_type:<{type_width}} "
            f"{username:<{user_width}} {last_run_str:<{date_width}} {status_str:<{status_width}}"
        )

    return 0


def show_info(pattern: Optional[str] = None, *, compact: bool = False) -> int:
    from datetime import datetime

    configs = get_all_configs(pattern)
    if not configs:
        if pattern:
            print(f"No configurations found matching '{pattern}'")
        else:
            print("No saved configurations found.")
        return 1

    for config in configs:
        host = config.get("host", "Unknown")
        name = config.get("name")
        tags = cast(StrList, config.get("tags", []))
        system_type = config.get("system_type", "Unknown")
        args = cast(JSONDict, config.get("args", {}))
        username = args.get("username", "Unknown")

        if compact:
            label = f"{host}"
            if name:
                label += f"/{name}"
            status = "PASS" if config.get("last_success") is True else (
                "FAIL" if config.get("last_success") is False else "UNKNOWN"
            )
            print(f"{label}  {system_type}  {username}  {status}")
            continue

        print("=" * 60)
        print(f"Host: {host}")
        if name:
            print(f"Name: {name}")
        if tags:
            print(f"Tags: {', '.join(tags)}")
        print(f"Type: {system_type}")
        print(f"User: {username}")
        print("-" * 60)

        deploy_specs = cast(JSONList, args.get("deploy_specs", []))
        if deploy_specs:
            print("Deployments:")
            for spec in deploy_specs:
                if isinstance(spec, list):
                    try:
                        print(f"  - {spec[1]} -> {spec[0]}")
                    except Exception:
                        print(f"  - {spec}")
                else:
                    print(f"  - {spec}")
        else:
            print("Deployments: None")

        features: StrList = []
        if args.get("enable_ssl"):
            features.append("SSL")
        if args.get("enable_cloudflare"):
            features.append("Cloudflare")
        if args.get("install_node"):
            features.append("Node")
        if args.get("install_go"):
            features.append("Go")
        if args.get("install_python"):
            features.append("Python")
        if args.get("install_data_analysis_tools"):
            features.append("Data analysis")
        if args.get("install_av_tools"):
            features.append("AV tools")
        if args.get("install_gl_tools"):
            features.append("OpenGL tools")
        if args.get("install_sunshine"):
            features.append("Sunshine")
        if args.get("install_moonlight"):
            features.append("Moonlight")
        if args.get("install_gaming"):
            features.append("CachyOS gaming bundle")
        if args.get("install_obs"):
            features.append("OBS Studio")
        if args.get("install_blender"):
            features.append("Blender")
        if args.get("install_kdenlive"):
            features.append("Kdenlive")
        if args.get("install_krita"):
            features.append("Krita")
        if args.get("install_inkscape"):
            features.append("Inkscape")
        if args.get("install_scribus"):
            features.append("Scribus")
        if args.get("install_audacity"):
            features.append("Audacity")
        if args.get("install_ardour"):
            features.append("Ardour")
        if args.get("install_lmms"):
            features.append("LMMS")
        if args.get("install_freecad"):
            features.append("FreeCAD")
        if args.get("install_kicad"):
            features.append("KiCad")
        if args.get("install_shotcut"):
            features.append("Shotcut")
        if args.get("install_gimp"):
            features.append("GIMP")
        if args.get("install_remmina"):
            features.append("Remmina")
        if args.get("install_sysadmin_tools"):
            features.append("Sysadmin tools")
        if args.get("install_gh"):
            features.append("GitHub CLI")
        if args.get("install_codex"):
            features.append("Codex CLI")
        if args.get("install_claude"):
            features.append("Claude Code")
        if args.get("install_opencode"):
            features.append("OpenCode")
        for interface in args.get("web_interfaces", []) or []:
            features.append(f"Web {interface}")
        for provider in args.get("device_pairing_providers", []) or []:
            features.append(f"Device pairing {provider}")
        if args.get("web_panel_port") is not None:
            features.append("Web panel")
        if args.get("install_office"):
            features.append("Office")
        if args.get("use_flatpak"):
            features.append("Flatpak")
        if args.get("enable_samba"):
            features.append("Samba")
        if args.get("gogs"):
            features.append("Gogs")
        if args.get("homebox"):
            features.append("HomeBox")
        if args.get("enable_syncthing"):
            features.append("Syncthing")

        if features:
            print(f"Features: {', '.join(features)}")

        samba_shares = cast(JSONList, args.get("samba_shares", []))
        if samba_shares:
            print("Samba Shares:")
            for share in samba_shares:
                if isinstance(share, list):
                    try:
                        share_list = cast(JSONList, share)
                        print(f"  - {share_list[1]}_{share_list[0]}: {share_list[2]}")
                    except Exception:
                        continue

        last_start_time = config.get("last_start_time")
        last_end_time = config.get("last_end_time")
        last_success = config.get("last_success")

        if last_start_time:
            start_dt = datetime.fromtimestamp(last_start_time)
            print(f"Last Run: {start_dt.strftime('%Y-%m-%d %H:%M:%S')}")
            if last_end_time:
                end_dt = datetime.fromtimestamp(last_end_time)
                duration = end_dt - start_dt
                print(f"Duration: {duration.total_seconds():.1f}s")
                print(f"Status: {'PASS' if last_success else 'FAIL'}")
            else:
                print("Status: In Progress")
        else:
            print("Last Run: Never")

        print()

    return 0


def show_command(pattern: Optional[str] = None) -> int:
    configs = get_all_configs(pattern)
    if not configs:
        if pattern:
            print(f"No configurations found matching '{pattern}'")
        else:
            print("No saved configurations found.")
        return 1

    for config_data in configs:
        host = config_data.get("host", "Unknown")
        system_type = config_data.get("system_type", "Unknown")
        args_dict = cast(JSONDict, config_data.get("args", {}))

        print("=" * 60)
        print(f"Host: {host}")
        print(f"System Type: {system_type}")
        print("-" * 60)

        try:
            config = SetupConfig.from_dict(str(host), str(system_type), args_dict)
            print(reconstruct_command(config))
        except Exception as exc:
            print(f"Error reconstructing command: {exc}")
        print()

    return 0


def remove_configurations(pattern: str, force: bool) -> int:
    configs = get_all_configs(pattern)
    if not configs:
        print(f"No configurations found matching '{pattern}'")
        return 1

    print(f"Found {len(configs)} configuration(s) to remove:")
    for config in configs:
        print(f"  - {config.get('host')}")

    if not force:
        response = input("\nAre you sure you want to remove these configurations? [y/N] ")
        if response.lower() != "y":
            print("Aborted.")
            return 0

    count = 0
    for config in configs:
        host = config.get("host")
        if not host:
            continue
        cache_path = get_cache_path_for_host(str(host))
        try:
            if os.path.exists(cache_path):
                os.remove(cache_path)
                print(f"Removed {host}")
                count += 1
        except Exception as exc:
            print(f"Error removing {host}: {exc}")

    print(f"\nRemoved {count} configuration(s).")
    return 0


def _execute_patch_config(config: SetupConfig) -> int:
    if not validate_username(config.username):
        print(f"Error: Invalid username: {config.username}")
        return 1

    if not os.path.exists(REMOTE_SCRIPT_PATH):
        print(f"Error: Remote setup script not found: {REMOTE_SCRIPT_PATH}")
        return 1

    print("=" * 60)
    print(f"Patching System: {config.system_type}")
    print("=" * 60)
    print(f"Host: {config.host}")
    print(f"User: {config.username}")
    print(f"Timezone: {config.timezone}")
    print("=" * 60)
    print()

    try:
        runtime_config = _prepare_runtime_config_for_cli(config)
    except ValueError as exc:
        print(f"Error: {exc}")
        return 1

    previous_host = config.host
    replaced_cache_host: Optional[str] = None
    start_time = time.time()
    returncode = 1
    try:
        if not config.dry_run:
            store_cli_credentials(config)
        returncode = run_remote_setup(runtime_config)
        if returncode == 0:
            replaced_cache_host = adopt_verified_network_host(
                config,
                runtime_config,
                previous_host,
            )
    finally:
        end_time = time.time()
        success = returncode == 0
        if not config.dry_run:
            save_setup_command(config, start_time, end_time, success, operation="patch")

    if replaced_cache_host:
        remove_replaced_setup_cache(replaced_cache_host, config.host)

    if returncode != 0:
        print(f"\n✗ Patch failed (exit code: {returncode})")
        return 1

    print()
    print("=" * 60)
    print("Patch Complete!")
    print("=" * 60)
    print(f"Host: {config.host}")
    print("System has been updated with new configuration")
    print_service_access_summary(
        config,
        remote_access_details=get_last_remote_access_details(),
    )

    if config.friendly_name or config.tags:
        print()
        print_name_and_tags(config)

    print("=" * 60)
    return 0


def _patch_preserve_keys(args: argparse.Namespace) -> set[str]:
    preserve_keys: set[str] = set()
    if getattr(args, "privilege_broker", None) is None and not getattr(args, "disable_privilege_broker", False):
        preserve_keys.update({"privilege_broker_port", "disable_privilege_broker"})
    if getattr(args, "nopasswd", None) is None:
        preserve_keys.add("nopasswd")
    explicit_swap_mode = getattr(args, "swap_mode", None)
    if explicit_swap_mode is None:
        preserve_keys.add("swap_mode")
    if explicit_swap_mode is None:
        for field in ("swap_files", "swap_devices", "swap_zram"):
            if getattr(args, field, None) is None:
                preserve_keys.add(field)
    for field in (
        "swappiness",
        "zswap",
        "zswap_max_pool_percent",
        "swap_resume",
    ):
        if explicit_swap_mode == "none":
            continue
        if field == "swap_resume" and explicit_swap_mode is not None:
            continue
        if (
            field == "zswap_max_pool_percent"
            and getattr(args, "zswap", None) is False
        ):
            continue
        if getattr(args, field, None) is None:
            preserve_keys.add(field)
    if getattr(args, "proxmox_balloon_target", None) is None:
        preserve_keys.add("proxmox_balloon_target")
    if getattr(args, "machine_type", None) is None:
        preserve_keys.add("machine_type")
    if getattr(args, "hosted_node", None) is None:
        preserve_keys.update(
            {
                "hosted_node",
                "hosted_user",
                "hosted_key",
                "hosted_bridge",
                "container_memory",
                "vm_balloon_min",
                "vm_balloon_shares",
                "allow_memory_overcommit",
                "container_storage",
                "storage_mounts",
                "storage_caches",
                "container_cores",
                "vm_cpu_type",
                "vm_disk_discard",
                "vm_disk_ssd",
                "vm_disk_backup",
                "vm_disk_settings",
                "container_base",
                "vm_image",
            }
        )
    if getattr(args, "device_pairing_port", None) is None:
        preserve_keys.add("device_pairing_port")
    if getattr(args, "default_web_ports", None) is None:
        preserve_keys.add("default_web_ports")
    if not getattr(args, "agent_tools", None) and not getattr(args, "no_agent_tools", None):
        preserve_keys.update({"agent_tools", "agent_tools_removed"})
    if (
        not getattr(args, "access_sources", None)
        and not getattr(args, "clear_access_sources", False)
    ):
        preserve_keys.update({"access_sources", "clear_access_sources"})
    if getattr(args, "lan_access", None) is None:
        preserve_keys.update({"lan_access", "clear_lan_access"})
    if getattr(args, "enable_mdns", None) is None:
        preserve_keys.update({"enable_mdns", "clear_mdns"})
    if (
        not getattr(args, "web_interfaces", None)
        and not getattr(args, "disable_web_interface", False)
    ):
        preserve_keys.update({"web_interfaces", "disable_web_interface"})
    if (
        not getattr(args, "web_interface_sources", None)
        and not getattr(args, "clear_web_interface_sources", False)
    ):
        preserve_keys.update({"web_interface_sources", "clear_web_interface_sources"})
    if (
        not getattr(args, "rdp_allowed_sources", None)
        and not getattr(args, "clear_rdp_sources", False)
    ):
        preserve_keys.update({"rdp_allowed_sources", "clear_rdp_sources"})
    if (
        not getattr(args, "browser_automation", None)
        and not getattr(args, "disable_browser_automation", False)
    ):
        preserve_keys.update({"browser_automation", "disable_browser_automation"})
    if (
        not getattr(args, "git_auth_source", None)
        and not getattr(args, "git_auth_file", None)
        and not getattr(args, "git_auth_token", None)
    ):
        preserve_keys.update({"git_auth_source", "disable_git_auth"})
    if (
        not getattr(args, "agent_auth_source", None)
        and not getattr(args, "agent_auth_files", None)
    ):
        preserve_keys.update({"agent_auth_source", "disable_agent_auth"})
    if not getattr(args, "device_pairing_providers", None) and not getattr(
        args, "disable_device_pairing", False
    ):
        preserve_keys.update({"device_pairing_providers", "disable_device_pairing"})
    if getattr(args, "web_panel_port", None) is None and not getattr(
        args, "disable_web_panel", False
    ):
        preserve_keys.update({"web_panel_port", "disable_web_panel"})
    return preserve_keys


_PROVISIONING_CHANGE_ARGS = (
    "machine_type",
    "hosted_bridge",
    "container_memory",
    "vm_balloon_min",
    "vm_balloon_shares",
    "allow_memory_overcommit",
    "container_storage",
    "storage_mounts",
    "storage_caches",
    "container_cores",
    "vm_cpu_type",
    "vm_disk_discard",
    "vm_disk_ssd",
    "vm_disk_backup",
    "vm_disk_settings",
    "container_base",
    "vm_image",
    "vm_image_storage",
)

_CACHED_PROVISIONING_FIELDS = (
    "machine_type",
    "hosted_node",
    "hosted_user",
    "hosted_key",
    "hosted_bridge",
    "container_memory",
    "vm_balloon_min",
    "vm_balloon_shares",
    "allow_memory_overcommit",
    "container_storage",
    "storage_mounts",
    "storage_caches",
    "container_cores",
    "vm_cpu_type",
    "vm_disk_discard",
    "vm_disk_ssd",
    "vm_disk_backup",
    "vm_disk_settings",
    "container_base",
    "vm_image",
    "vm_image_storage",
)

_RECONCILABLE_VM_PROVISIONING_CHANGES = {
    "container_memory",
    "vm_balloon_min",
    "vm_balloon_shares",
    "allow_memory_overcommit",
    "container_cores",
    "vm_cpu_type",
    "vm_disk_discard",
    "vm_disk_ssd",
    "vm_disk_backup",
    "vm_disk_settings",
}

_PROVIDER_BINDING_FIELDS = {
    "hosted_node",
    "hosted_user",
    "hosted_key",
    "hosted_bridge",
}

_PROVISIONING_FIELD_FLAGS = {
    "machine_type": "--machine",
    "hosted_bridge": "--bridge",
    "container_memory": "--memory",
    "vm_balloon_min": "--balloon-min",
    "vm_balloon_shares": "--balloon-shares",
    "allow_memory_overcommit": "--allow-memory-overcommit",
    "container_storage": "--storage",
    "storage_mounts": "--storage-mount",
    "storage_caches": "--storage-cache",
    "container_cores": "--cores",
    "vm_cpu_type": "--cpu-type",
    "vm_disk_discard": "--disk-discard",
    "vm_disk_ssd": "--disk-ssd",
    "vm_disk_backup": "--disk-backup",
    "vm_disk_settings": "--disk-ssd/--disk-discard/--disk-backup NAME",
    "container_base": "--base",
    "vm_image": "--image",
    "vm_image_storage": "--image-storage",
}


def _provisioning_changes_requested(
    config: SetupConfig,
    cached_config: SetupConfig,
    args: argparse.Namespace,
) -> bool:
    """Return whether explicit guest-shape arguments differ from local state."""
    return any(
        getattr(args, field, None) is not None
        and getattr(config, field) != getattr(cached_config, field)
        for field in _PROVISIONING_CHANGE_ARGS
    )


def _canonical_provisioning_node(node: object) -> str:
    """Return a stable registered-host address for provider comparisons."""

    value = str(node or "").strip()
    registered = find_proxmox_host(value) if value else None
    return (registered.address if registered else value).lower().rstrip(".")


def _provider_rebind_requested(
    config: SetupConfig,
    cached_config: Optional[SetupConfig],
    args: argparse.Namespace,
) -> bool:
    """Return whether setup explicitly moves a saved guest to another node."""

    if cached_config is None or getattr(args, "hosted_node", None) is None:
        return False
    return _canonical_provisioning_node(
        config.hosted_node
    ) != _canonical_provisioning_node(cached_config.hosted_node)


def _provider_binding_was_explicit(
    args: argparse.Namespace,
    field: str,
) -> bool:
    """Return whether a provider-binding field was supplied on the CLI."""

    if field in {"hosted_user", "hosted_key"}:
        return hasattr(args, field)
    return getattr(args, field, None) is not None


def _storage_declaration_sizes(
    specs: Optional[NestedStrList],
) -> Optional[dict[str, str]]:
    """Return logical disk sizes when a storage declaration is well formed."""

    if not specs:
        return None
    sizes: dict[str, str] = {}
    for spec in specs:
        if spec and spec[0] == "template":
            continue
        if len(spec) < 2 or spec[0] in sizes:
            return None
        sizes[spec[0]] = spec[-1]
    return sizes or None


def _storage_declaration_can_be_verified(
    config: SetupConfig,
    cached_config: SetupConfig,
) -> bool:
    """Return whether live provider state can verify a storage declaration."""

    desired = _storage_declaration_sizes(config.container_storage)
    cached = _storage_declaration_sizes(cached_config.container_storage)
    return (
        desired is not None
        and cached is not None
        and set(desired) == set(cached)
    )


def _named_specs(
    specs: Optional[NestedStrList],
) -> Optional[dict[str, StrList]]:
    """Return unique CLI declarations keyed by their logical first field."""

    result: dict[str, StrList] = {}
    for spec in specs or []:
        if not spec or spec[0] in result:
            return None
        result[spec[0]] = list(spec)
    return result


def _canonical_storage_mount(spec: StrList) -> tuple[str, str, str, str]:
    """Return a mount declaration with its optional values made explicit."""

    return (
        spec[0],
        spec[1],
        spec[2] if len(spec) >= 3 else "ext4",
        spec[3] if len(spec) >= 4 else "empty",
    )


def _merge_additive_vm_storage(
    config: SetupConfig,
    cached_config: Optional[SetupConfig],
    args: argparse.Namespace,
    *,
    provider_rebind: bool = False,
) -> Optional[set[str]]:
    """Merge additive mounted disks into a saved managed VM layout.

    Existing declarations may be omitted for a concise setup command or
    repeated unchanged. The returned names are the only provider identities
    that the caller may create; an empty set is a valid idempotent subset
    request, while ``None`` means the request is not an additive layout.
    All previously managed disks must still exist.
    """

    if (
        cached_config is None
        or provider_rebind
        or config.machine_type != "vm"
        or cached_config.machine_type != "vm"
        or getattr(args, "container_storage", None) is None
        or getattr(args, "storage_mounts", None) is None
    ):
        return None

    cached_storage = _named_specs(cached_config.container_storage)
    requested_storage = _named_specs(config.container_storage)
    cached_mounts = _named_specs(cached_config.storage_mounts)
    requested_mounts = _named_specs(config.storage_mounts)
    if (
        not cached_storage
        or requested_storage is None
        or cached_mounts is None
        or requested_mounts is None
        or any(len(spec) < 2 for spec in cached_mounts.values())
        or any(len(spec) < 2 for spec in requested_mounts.values())
        or "root" not in cached_storage
    ):
        return None

    for name in set(cached_storage) & set(requested_storage):
        if requested_storage[name] != cached_storage[name]:
            return None
    new_storage_names = set(requested_storage) - set(cached_storage)
    if any(
        name in {"root", "template"}
        or len(requested_storage[name]) != 3
        for name in new_storage_names
    ):
        return None

    for name in set(cached_mounts) & set(requested_mounts):
        if _canonical_storage_mount(requested_mounts[name]) != (
            _canonical_storage_mount(cached_mounts[name])
        ):
            return None
    new_mount_names = set(requested_mounts) - set(cached_mounts)
    if new_mount_names != new_storage_names or any(
        _canonical_storage_mount(requested_mounts[name])[1] == "/home"
        or _canonical_storage_mount(requested_mounts[name])[3] != "empty"
        for name in new_mount_names
    ):
        return None

    requested_mounted_disks = (
        set(requested_storage) & set(requested_mounts)
    ) - {"root", "template"}
    if not requested_mounted_disks:
        return None

    merged_disk_settings: Optional[NestedStrList] = None
    if getattr(args, "vm_disk_settings", None) is not None:
        cached_settings = _named_specs(cached_config.vm_disk_settings)
        requested_settings = _named_specs(config.vm_disk_settings)
        if cached_settings is None or requested_settings is None:
            return None
        merged_disk_settings = [
            *[
                list(spec)
                for name, spec in cached_settings.items()
                if name not in requested_settings
            ],
            *[list(spec) for spec in requested_settings.values()],
        ] or None

    config.container_storage = [
        *[list(spec) for spec in cached_config.container_storage or []],
        *[
            list(spec)
            for name, spec in requested_storage.items()
            if name in new_storage_names
        ],
    ]
    config.storage_mounts = [
        *[list(spec) for spec in cached_config.storage_mounts or []],
        *[
            list(spec)
            for name, spec in requested_mounts.items()
            if name in new_mount_names
        ],
    ]

    if getattr(args, "vm_disk_settings", None) is not None:
        config.vm_disk_settings = merged_disk_settings

    return new_storage_names


def _unsupported_cached_provisioning_changes(
    config: SetupConfig,
    cached_config: SetupConfig,
    args: argparse.Namespace,
    *,
    provider_rebind: bool = False,
    additive_storage_request: bool = False,
) -> list[str]:
    """Return explicit existing-guest changes setup cannot safely reconcile."""

    changed_fields = [
        field
        for field in _PROVISIONING_CHANGE_ARGS
        if getattr(args, field, None) is not None
        and getattr(config, field) != getattr(cached_config, field)
    ]
    if cached_config.machine_type == "vm":
        additive_storage_fields = (
            {"container_storage", "storage_mounts"}
            if additive_storage_request
            else set()
        )
        changed_fields = [
            field
            for field in changed_fields
            if field not in _RECONCILABLE_VM_PROVISIONING_CHANGES
            and field not in additive_storage_fields
            and not (
                field == "container_storage"
                and _storage_declaration_can_be_verified(config, cached_config)
            )
        ]
        if provider_rebind:
            changed_fields = [
                field
                for field in changed_fields
                if field != "hosted_bridge"
            ]
    return [_PROVISIONING_FIELD_FLAGS[field] for field in changed_fields]


def _provisioning_cache_target(host: str) -> str:
    """Return the address used to look up saved provisioning metadata."""
    if "/" not in host:
        return host
    try:
        return str(ipaddress.ip_interface(host).ip)
    except ValueError:
        return host


def _load_cached_provisioning_metadata(
    config: SetupConfig,
) -> Optional[SetupConfig]:
    """Return saved provisioning metadata for this guest, when available."""
    if not config.hosted_node:
        return None
    cached_config = load_setup_command(_provisioning_cache_target(config.host))
    if cached_config is None or not cached_config.hosted_node:
        return None
    return cached_config


def _reuse_cached_provisioning_metadata(
    config: SetupConfig,
    args: argparse.Namespace,
    cached_config: Optional[SetupConfig] = None,
    *,
    provider_rebind: bool = False,
) -> bool:
    """Hydrate an existing guest from local state and skip Proxmox discovery."""
    if not config.hosted_node:
        return False

    cache_target = _provisioning_cache_target(config.host)
    cached_config = cached_config or _load_cached_provisioning_metadata(config)
    if cached_config is None:
        return False
    provisioning_changes_requested = _provisioning_changes_requested(
        config,
        cached_config,
        args,
    )

    requested_system_hostname = getattr(args, "system_hostname", None)
    requested_friendly_name = getattr(args, "friendly_name", None)
    desired_system_hostname = (
        requested_system_hostname
        if requested_system_hostname is not None
        else cached_config.system_hostname
    )
    desired_friendly_name = (
        requested_friendly_name
        if requested_friendly_name is not None
        else cached_config.friendly_name
    )
    config.system_hostname = desired_system_hostname
    config.friendly_name = desired_friendly_name
    cached_vm_name = cached_config.system_hostname or _build_guest_hostname(
        cache_target,
        cached_config.friendly_name,
        default_prefix="vm",
    )
    desired_vm_name = desired_system_hostname or _build_guest_hostname(
        cache_target,
        desired_friendly_name,
        default_prefix="vm",
    )
    vm_identity_changed = (
        config.machine_type == "vm" and desired_vm_name != cached_vm_name
    )

    for field in _CACHED_PROVISIONING_FIELDS:
        if field in _PROVIDER_BINDING_FIELDS and (
            provider_rebind or _provider_binding_was_explicit(args, field)
        ):
            continue
        if (
            field in _PROVISIONING_CHANGE_ARGS
            and getattr(args, field, None) is not None
        ):
            continue
        setattr(config, field, getattr(cached_config, field))

    if config.ssh_key is None:
        config.ssh_key = cached_config.ssh_key

    # Network defaults are resolved by Proxmox during the first provisioning
    # pass. Preserve them on a repeated command, but never replace an
    # explicit value supplied on the command line. A legacy cache without
    # these values must go through Proxmox again so they can be refreshed.
    for field in (
        "static_ipv4",
        "static_ipv6",
        "network_gateway4",
        "network_gateway6",
        "network_dns",
        "network_interface",
    ):
        if getattr(config, field) is not None:
            continue
        cached_value = getattr(cached_config, field)
        if cached_value is None:
            continue
        setattr(
            config,
            field,
            list(cached_value) if field == "network_dns" else cached_value,
        )

    if config.static_ipv4 and (
        not config.network_gateway4 or not config.network_dns
    ):
        return False

    disk_policy_requested = any(
        getattr(args, field, None) is not None
        for field in (
            "vm_disk_discard",
            "vm_disk_ssd",
            "vm_disk_backup",
            "vm_disk_settings",
        )
    )
    if (
        provider_rebind
        or provisioning_changes_requested
        or vm_identity_changed
        or getattr(args, "verify_provider", False)
        or disk_policy_requested
    ):
        # Provider-side names and explicit verification requests must be
        # checked against Proxmox instead of trusted from local metadata.
        # Disk flags are also repair requests: an older cache can contain the
        # desired defaults even when the provider never received those hints.
        return False

    return True


def _is_cached_provisioned_guest_identity(
    config: SetupConfig,
    cached_config: Optional[SetupConfig],
) -> bool:
    """Return whether config retains a saved guest's non-provider identity."""

    if cached_config is None:
        return False
    return (
        _provisioning_cache_target(config.host)
        == _provisioning_cache_target(cached_config.host)
        and config.machine_type == cached_config.machine_type
    )


def _refresh_existing_managed_guest_host_keys(
    config: SetupConfig,
    cached_config: Optional[SetupConfig],
) -> None:
    """Refresh SSH trust only for an existing guest known in local metadata."""
    if not _is_cached_provisioned_guest_identity(config, cached_config):
        return
    refresh_managed_guest_host_keys(
        _provisioning_cache_target(config.host),
        cast(str, config.hosted_node),
        config.hosted_user,
        config.hosted_key,
        dry_run=config.dry_run,
    )


def _prepare_runtime_config_for_cli(config: SetupConfig) -> SetupConfig:
    if config.privilege_broker_auth and config.privilege_broker_port is None:
        raise ValueError("--privilege-broker-password requires an enabled privilege broker")
    _apply_hosted_proxmox_defaults(config, None)
    runtime_config = prepare_runtime_config(config)
    validate_timezone_name(runtime_config.timezone)
    validate_apt_packages(runtime_config.apt_packages)
    validate_agent_repositories(runtime_config.agent_repos)
    validate_notification_args(runtime_config.notify_specs)
    validate_ssl_email(runtime_config.ssl_email)
    validate_deploy_specs(runtime_config.deploy_specs)
    validate_deploy_targets(runtime_config.deploy_targets)
    validate_sync_specs(runtime_config.sync_specs)
    validate_backup_specs(runtime_config.backup_specs)
    validate_scrub_specs(runtime_config.scrub_specs)
    validate_web_interface_settings(runtime_config)
    validate_smb_mount_specs(runtime_config.smb_mounts)
    validate_samba_settings(runtime_config)
    validate_samba_share_specs(
        runtime_config.samba_shares,
        runtime_config.share_credentials,
    )
    validate_gogs_settings(runtime_config)
    validate_homebox_settings(runtime_config)
    validate_antistatic_settings(runtime_config)
    validate_hosted_flags(runtime_config)
    validate_network_setup_settings(runtime_config)
    validate_rdp_settings(runtime_config)
    validate_samba_share_credentials(runtime_config)
    return runtime_config


def add_deploy_command_arguments(
    parser: argparse.ArgumentParser,
    *,
    include_workspace: bool = False,
) -> None:
    parser.add_argument("pattern", help="Host, name, or tag filter to redeploy")
    parser.add_argument("-y", "--yes", action="store_true", help="Deploy without prompting")
    parser.add_argument(
        "--deploy-latest",
        dest="deploy_latest",
        action="store_true",
        help="Deploy the latest versions of packages and releases, bypassing the release age policy",
    )
    if include_workspace:
        parser.add_argument(
            "--workspace",
            help="Workspace root for saved setups, credentials, known_hosts, and history"
        )


class DeployArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        usage = self.format_usage().strip()
        if usage.startswith("usage:"):
            usage = "Usage:" + usage[len("usage:"):]
        raise ValueError(f"{usage}\n{message}")


def parse_deploy_command_args(args: list[str]) -> argparse.Namespace:
    parser = DeployArgumentParser(prog="deploy", add_help=False)
    add_deploy_command_arguments(parser)
    return parser.parse_args(args)


def run_deploy_command(args: argparse.Namespace) -> int:
    return deploy_configurations(
        args.pattern,
        args.yes,
        getattr(args, "deploy_latest", False),
    )


def deploy_configurations(pattern: str, force: bool, deploy_latest: bool = False) -> int:
    configs = get_all_configs(pattern)
    if not configs:
        print(f"No configurations found matching '{pattern}'")
        return 1

    print(f"Found {len(configs)} configuration(s) to deploy:")
    for config in configs:
        host = config.get("host")
        deploy_specs = cast(JSONList, cast(JSONDict, config.get("args", {})).get("deploy_specs", []))
        print(f"  - {host} ({len(deploy_specs)} deployments)")

    if not force:
        response = input("\nAre you sure you want to deploy to these hosts? [y/N] ")
        if response.lower() != "y":
            print("Aborted.")
            return 0

    failures = 0
    for config_data in configs:
        host = config_data.get("host")
        system_type = config_data.get("system_type")
        args_dict = config_data.get("args", {})

        print(f"\nDeploying to {host}...")
        try:
            if not isinstance(host, str) or not isinstance(system_type, str) or not isinstance(args_dict, dict):
                raise ValueError("Invalid cached configuration format")
            config = SetupConfig.from_dict(host, system_type, cast(JSONDict, args_dict))
            config.deploy_latest = deploy_latest
            if _execute_patch_config(config) != 0:
                failures += 1
        except Exception as exc:
            print(f"Error creating config for {host}: {exc}")
            failures += 1

    if failures > 0:
        print(f"\nCompleted with {failures} failure(s).")
        return 1

    print("\nAll deployments completed successfully.")
    return 0


def run_setup_command(args: argparse.Namespace) -> int:
    """Execute the setup command."""
    if args.system_type == "agent_cachyos":
        from lib.cachyos import run_cachyos_command

        return run_cachyos_command(args)
    explicit_ipv4 = getattr(args, "static_ipv4", None)
    if getattr(args, "hosted_node", None) and isinstance(explicit_ipv4, str) and explicit_ipv4:
        print(
            "Error: --ip is redundant with --provision-on; put the guest address "
            "and optional prefix in the positional HOST[/PREFIX] target"
        )
        return 1

    try:
        prompt_for_missing_passwords(args, args.system_type)
    except (EOFError, KeyboardInterrupt, ValueError) as exc:
        print(f"Error: {exc}")
        return 1

    try:
        config = SetupConfig.from_args(args, args.system_type)
    except ValueError as exc:
        print(f"Error: {exc}")
        return 1
    if getattr(args, "verify_provider", False) and not config.hosted_node:
        print("Error: --verify-provider requires --provision-on")
        return 1

    cached_provisioning = _load_cached_provisioning_metadata(config)
    provider_rebind = _provider_rebind_requested(
        config,
        cached_provisioning,
        args,
    )
    additive_storage_result = _merge_additive_vm_storage(
        config,
        cached_provisioning,
        args,
        provider_rebind=provider_rebind,
    )
    additive_storage_request = additive_storage_result is not None
    additive_storage_disks = additive_storage_result or set()
    if cached_provisioning is not None:
        unsupported_changes = _unsupported_cached_provisioning_changes(
            config,
            cached_provisioning,
            args,
            provider_rebind=provider_rebind,
            additive_storage_request=additive_storage_request,
        )
        if unsupported_changes:
            print(
                "Error: setup cannot reconcile these provisioning-only changes "
                f"on an existing guest: {', '.join(unsupported_changes)}. "
                "Use the explicit vm/proxmox modification commands or provision "
                "a replacement guest."
            )
            return 1
    verify_existing_storage = bool(
        cached_provisioning is not None
        and config.machine_type == "vm"
        and (
            provider_rebind
            or getattr(args, "verify_provider", False)
            or (
                getattr(args, "container_storage", None) is not None
                and config.container_storage
                != cached_provisioning.container_storage
            )
        )
    )
    reuse_cached_provisioning = _reuse_cached_provisioning_metadata(
        config,
        args,
        cached_provisioning,
        provider_rebind=provider_rebind,
    )

    if provider_rebind and config.machine_type != "vm":
        print(
            "Error: changing --provision-on for a saved guest is currently "
            "supported only for QEMU VMs"
        )
        return 1

    if not validate_username(config.username):
        print(f"Error: Invalid username: {config.username}")
        return 1

    if config.hosted_node and config.activate_network is True:
        print(
            "Error: --activate-network is for patching an already saved Proxmox "
            "guest; provisioned guests boot directly on their requested address"
        )
        return 1
    
    try:
        runtime_config = _prepare_runtime_config_for_cli(config)
    except ValueError as e:
        print(f"Error: {e}")
        return 1

    if not validate_host(config.host):
        print(f"Error: Invalid IP address or hostname: {config.host}")
        return 1
    
    description = f"{args.system_type.replace('_', ' ').title()} Setup"
    print_setup_summary(config, description)

    if config.hosted_node and reuse_cached_provisioning:
        try:
            _refresh_existing_managed_guest_host_keys(
                config,
                cached_provisioning,
            )
        except ProvisionError as exc:
            print(
                "\n✗ Failed to refresh the managed guest SSH host key: "
                f"{exc}"
            )
            return 1
        print(
            "  ✓ Guest already provisioned; refreshed SSH host trust "
            "through Proxmox"
        )
    elif config.hosted_node:
        if config.machine_type == "vm":
            from lib.proxmox_vm import (
                VMAlreadyExists,
                provision_vm,
                verify_vm_rebind_source_stopped,
            )

            print(f"\n{'='*60}")
            print(f"Provisioning VM on {config.hosted_node}...")
            print(f"{'='*60}")
            try:
                if provider_rebind:
                    assert cached_provisioning is not None
                    verify_vm_rebind_source_stopped(
                        cached_provisioning,
                        dry_run=config.dry_run,
                    )
                allow_existing_data_disks = (
                    _is_cached_provisioned_guest_identity(
                        config,
                        cached_provisioning,
                    )
                )
                if provider_rebind:
                    provision_vm(
                        config,
                        image=config.vm_image,
                        allow_existing_data_disks=allow_existing_data_disks,
                        require_existing_vm=True,
                        require_existing_name=True,
                        start_existing_vm=True,
                        verify_existing_bridge=True,
                        verify_existing_storage=True,
                    )
                elif verify_existing_storage:
                    storage_extension_options = (
                        {
                            "attach_missing_data_disks": (
                                additive_storage_disks
                            )
                        }
                        if additive_storage_disks
                        else {}
                    )
                    provision_vm(
                        config,
                        image=config.vm_image,
                        allow_existing_data_disks=allow_existing_data_disks,
                        require_existing_vm=True,
                        verify_existing_storage=True,
                        **storage_extension_options,
                    )
                else:
                    existing_vm_options = (
                        {"require_existing_vm": True}
                        if allow_existing_data_disks
                        else {}
                    )
                    provision_vm(
                        config,
                        image=config.vm_image,
                        allow_existing_data_disks=allow_existing_data_disks,
                        **existing_vm_options,
                    )
            except VMAlreadyExists:
                from lib.swap_config import swap_device_disk_names

                has_managed_data_disks = bool(
                    config.storage_mounts
                    or config.storage_caches
                    or swap_device_disk_names(config)
                )
                if has_managed_data_disks and not _is_cached_provisioned_guest_identity(
                    config,
                    cached_provisioning,
                ):
                    print(
                        "Error: named VM data disks, caches, and swap disks are "
                        "provisioning-only; "
                        "refusing to adopt disks on an existing unsaved VM"
                    )
                    return 1
                try:
                    _refresh_existing_managed_guest_host_keys(
                        config,
                        cached_provisioning,
                    )
                except ProvisionError as exc:
                    print(
                        "\n✗ Failed to refresh the managed guest SSH host key: "
                        f"{exc}"
                    )
                    return 1
                print("  ✓ VM already provisioned, skipping creation")
            except Exception as e:
                print(f"\n✗ Failed to provision VM: {e}")
                return 1
        else:
            from lib.proxmox_node import provision_container, ContainerAlreadyExists

            print(f"\n{'='*60}")
            print(f"Provisioning LXC container on {config.hosted_node}...")
            print(f"{'='*60}")
            try:
                provision_container(config)
            except ContainerAlreadyExists:
                try:
                    _refresh_existing_managed_guest_host_keys(
                        config,
                        cached_provisioning,
                    )
                except ProvisionError as exc:
                    print(
                        "\n✗ Failed to refresh the managed guest SSH host key: "
                        f"{exc}"
                    )
                    return 1
                print("  ✓ Container already provisioned, skipping creation")
            except Exception as e:
                print(f"\n✗ Failed to provision container: {e}")
                return 1

        try:
            runtime_config = _prepare_runtime_config_for_cli(config)
        except ValueError as e:
            print(f"Error: {e}")
            return 1

    if config.hosted_node and config.static_ipv4 and config.network_gateway4:
        try:
            ensure_guest_ipv4_route(
                config.static_ipv4,
                config.network_gateway4,
                "root",
                config.ssh_key,
                dry_run=config.dry_run,
            )
        except ProvisionError as exc:
            print(f"\n✗ Failed to prepare the provisioned guest network: {exc}")
            return 1

    if not config.dry_run:
        store_cli_credentials(config)
        if not provider_rebind and not additive_storage_disks:
            save_setup_command(config, operation="setup")

    if not os.path.exists(REMOTE_SCRIPT_PATH):
        print(f"Error: Remote setup script not found: {REMOTE_SCRIPT_PATH}")
        return 1
    
    previous_host = config.host
    replaced_cache_host: Optional[str] = None
    start_time = time.time()
    returncode = 1
    try:
        returncode = run_remote_setup(runtime_config)
        if returncode == 0:
            replaced_cache_host = adopt_verified_network_host(
                config,
                runtime_config,
                previous_host,
            )
    finally:
        end_time = time.time()
        success = (returncode == 0)
        if (
            not config.dry_run
            and (not provider_rebind or success)
            and (not additive_storage_disks or success)
        ):
            save_setup_command(config, start_time, end_time, success, operation="setup")

    if replaced_cache_host:
        remove_replaced_setup_cache(replaced_cache_host, config.host)
    
    if returncode != 0:
        print(f"\n✗ Setup failed (exit code: {returncode})")
        return 1

    try:
        register_proxmox_setup_host(config)
    except ValueError as exc:
        print(f"\n✗ Setup completed, but Proxmox host registration failed: {exc}")
        return 1
    
    print()
    print("=" * 60)
    print("Setup Complete!")
    print("=" * 60)
    print_service_access_summary(
        config,
        remote_access_details=get_last_remote_access_details(),
    )
    print("=" * 60)
    
    return 0


def run_patch_command(args: argparse.Namespace) -> int:
    """Execute the patch command."""
    if not validate_host(args.host):
        print(f"Error: Invalid IP address or hostname: {args.host}")
        return 1
    
    username = args.username if args.username else get_current_username()
    
    if not validate_username(username):
        print(f"Error: Invalid username: {username}")
        return 1
    
    cached_config = load_setup_command(args.host)
    if not cached_config:
        print(f"Error: No cached setup found for {args.host}")
        print(f"Please run the initial setup first using 'basaltw setup <system_type> {args.host}'")
        return 1

    if getattr(args, "enable_rdp", None) is None:
        args.enable_rdp = cached_config.enable_rdp
    if getattr(args, "git_access", None) is None:
        args.git_access = cached_config.git_access

    try:
        new_config = SetupConfig.from_args(args, cached_config.system_type)
    except ValueError as exc:
        print(f"Error: {exc}")
        return 1
    merged_config = merge_setup_configs(
        cached_config,
        new_config,
        preserve_keys=_patch_preserve_keys(args),
    )
    if (
        getattr(args, "web_panel_port", None)
        is WEB_PANEL_DEFAULT_PORT_SENTINEL
    ):
        merged_config.web_panel_port = 443 if merged_config.enable_ssl else 80
    return _execute_patch_config(merged_config)


def _apply_share_updates(config: SetupConfig, args: argparse.Namespace) -> None:
    """Apply share CLI mutations to a cached configuration in place."""

    remove_names = set(args.remove_share)
    for share_name in remove_names:
        validate_samba_share_name(share_name)

    updated_by_name = {
        share_spec[1]: list(share_spec)
        for share_spec in config.samba_shares or []
        if share_spec[1] not in remove_names
    }
    for share_spec in args.samba_shares or []:
        updated_by_name[share_spec[1]] = list(share_spec)

    config.samba_shares = list(updated_by_name.values()) or None
    config.enable_samba = True
    config.share_credentials = args.share_credentials
    config.dry_run = args.dry_run
    if args.username:
        config.username = args.username
    if args.ssh_key:
        config.ssh_key = args.ssh_key


def run_shares_command(args: argparse.Namespace) -> int:
    """Reconcile only Samba shares without running the full setup lifecycle."""

    cached_config = load_setup_command(args.host)
    if not cached_config:
        print(f"Error: No cached setup found for {args.host}")
        return 1

    try:
        _apply_share_updates(cached_config, args)
        if not validate_username(cached_config.username):
            raise ValueError(f"Invalid username: {cached_config.username}")
        share_config = SetupConfig(
            host=cached_config.host,
            username=cached_config.username,
            system_type=cached_config.system_type,
            machine_type=cached_config.machine_type,
            ssh_key=cached_config.ssh_key,
            dry_run=cached_config.dry_run,
            enable_samba=True,
            samba_shares=cached_config.samba_shares,
            share_credentials=cached_config.share_credentials,
            scrub_specs=cached_config.scrub_specs,
        )
        runtime_config = prepare_runtime_config(share_config)
        validate_samba_share_specs(
            runtime_config.samba_shares,
            runtime_config.share_credentials,
        )
        validate_scrub_specs(runtime_config.scrub_specs)
        validate_samba_share_credentials(runtime_config)
    except ValueError as exc:
        print(f"Error: {exc}")
        return 1

    runtime_config.custom_steps = "reconcile_samba_shares"
    start_time = time.time()
    if not cached_config.dry_run:
        store_cli_credentials(cached_config)
    returncode = run_remote_setup(runtime_config)
    end_time = time.time()

    if not cached_config.dry_run:
        save_setup_command(
            cached_config,
            start_time,
            end_time,
            returncode == 0,
            operation="shares",
        )
    if returncode != 0:
        print(f"\n✗ Samba share update failed (exit code: {returncode})")
        return 1

    print(f"\n✓ Samba shares updated on {cached_config.host}")
    return 0


def main() -> int:
    """Main entry point for basaltw."""
    parser, _setup_parser, _patch_parser = create_infra_tools_parser()

    if argcomplete:
        argcomplete.autocomplete(parser)

    args = parser.parse_args()
    if getattr(args, 'workspace', None):
        try:
            validate_workspace_dir(args.workspace)
        except ValueError as e:
            print(f"Error: {e}")
            return 1
        set_workspace_dir(args.workspace)
    
    if not args.command:
        parser.print_help()
        return 0
    
    if args.command in {"setup", "patch", "shares"} and _is_local_host(args.host):
        from lib.cachyos import is_cachyos

        if args.command == "setup" and args.system_type == "agent_cachyos":
            return run_setup_command(args)
        if is_cachyos():
            print("Error: CachyOS local setup supports only agent_cachyos; rerun that profile to add tools")
            return 1
        if not confirm_unsupported_environment(f"{args.command} on the local host"):
            return 1

    if args.command == "setup":
        return run_setup_command(args)
    elif args.command == "patch":
        return run_patch_command(args)
    elif args.command == "shares":
        return run_shares_command(args)
    elif args.command in {"list", "ls"}:
        return list_configurations(args.pattern, json_output=getattr(args, "json", False))
    elif args.command == "info":
        return show_info(args.pattern, compact=getattr(args, "compact", False))
    elif args.command in {"cmd", "command"}:
        return show_command(args.pattern)
    elif args.command in {"rm", "remove"}:
        return remove_configurations(args.pattern, args.yes)
    elif args.command == "cleanup":
        has_selector = args.setup_cache or args.proxmox_registry
        include_setup_cache = args.setup_cache or not has_selector
        include_proxmox_registry = args.proxmox_registry or (
            not has_selector and args.host is None
        )
        return run_cleanup(
            args.host,
            workspace=getattr(args, "workspace", None),
            include_setup_cache=include_setup_cache,
            include_proxmox_registry=include_proxmox_registry,
            dry_run=args.dry_run,
            assume_yes=args.yes,
        )
    elif args.command == "deploy":
        return run_deploy_command(args)
    elif args.command == "reconstruct":
        return run_reconstruct_command(args.compact)
    elif args.command == "recall":
        username = args.username if args.username else get_current_username()
        if not validate_host(args.host):
            print(f"Error: Invalid IP address or hostname: {args.host}")
            return 1
        if not validate_username(username):
            print(f"Error: Invalid username: {username}")
            return 1
        return run_recall_command(args.host, username, args.ssh_key)
    elif args.command == "completions":
        return run_completion_setup(
            shell=args.shell,
            global_install=args.global_install,
            command_name=_current_command_name(),
        )
    elif args.command in {"python-tools", "admin-python"}:
        return run_local_python_setup(
            args.shell,
            command_name=_current_command_name(),
            script_path=getattr(args, "script_path", None) or sys.argv[0],
        )
    elif args.command in {"bootstrap", "self-setup"}:
        from lib.cachyos import is_cachyos

        if not is_cachyos() and not args.skip_system_packages and not confirm_unsupported_environment("local bootstrap"):
            return 1
        return run_orchestrator_bootstrap(
            script_path=sys.argv[0],
            shell=args.shell,
            requested_user=args.bootstrap_user,
            skip_system_packages=args.skip_system_packages,
            install_qemu_guest_agent=args.qemu_guest_agent,
        )
    elif args.command == "channel":
        return run_channel_command(args)
    elif args.command == "upgrade" and not getattr(args, "hosts", None):
        if getattr(args, "check", False):
            print("Error: --check requires at least one remote host")
            return 1
        return run_tool_upgrade_command(args)
    elif args.command == "network":
        return run_network_command(args)
    elif args.command == "local":
        if args.local_command != "cachyos-doctor" and not confirm_unsupported_environment("local maintenance"):
            return 1
        return run_local_command(args)
    elif args.command == "desktop":
        return run_desktop_command(args)
    elif args.command == "firmware":
        return run_firmware_command(args)
    elif args.command == "proxmox":
        return run_proxmox_command(args)
    elif args.command == "vm":
        return run_vm_command(args)
    elif args.command == "maintenance":
        return run_maintenance_command(args)
    elif args.command == "agent":
        return run_agent_command(args)
    elif args.command == "gogs":
        return run_gogs_command(args)
    elif args.command == "homebox":
        return run_homebox_command(args)
    elif args.command == "cicd":
        return run_cicd_command(args)
    elif args.command in {"mount", "umount", "health", "ssh", "push", "pull", "key", "ssh-key", "df", "fan", "svc", "logs", "upgrade", "reachable", "user"}:
        return run_sysadmin_command(args)
    elif args.command == "shell":
        return run_interactive_shell(getattr(args, "workspace", None))
    elif args.command == "credentials":
        try:
            if args.credentials_command == "set":
                password = args.password
                if password is None:
                    password = getpass.getpass(f"Password for {args.username}: ")
                set_workspace_credential(args.username, password)
                print(f"Saved credential for {args.username} in {get_workspace_dir()}")
                return 0
            if args.credentials_command == "list":
                usernames = list_workspace_credentials()
                if not usernames:
                    print("No saved credentials.")
                    return 0
                for username in usernames:
                    print(username)
                return 0
            if args.credentials_command == "remove":
                removed = remove_workspace_credential(args.username)
                if removed:
                    print(f"Removed credential for {args.username}")
                else:
                    print(f"No saved credential for {args.username}")
                return 0
        except ValueError as e:
            print(f"Error: {e}")
            return 1
        print("Error: credentials command required (set, list, remove)")
        return 1
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
