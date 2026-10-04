"""Upgrade the active source and replay this machine's successful setup."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shlex
import shutil
import stat
import sys
import tempfile

from lib.arg_parser import create_setup_argument_parser
from lib.atomic_io import read_json_file, write_json_atomic, write_text_atomic
from lib.cachyos import is_cachyos
from lib.channel_manager import get_channel_info, managed_repository_path, switch_channel, upgrade_channel
from lib.installation_info import INSTALLATION_METADATA_FILENAME
from lib.config import AGENT_TOOLS, SetupConfig
from lib.machine_state import SETUP_CONFIG_FILE, _validate_setup_config
from lib.maintenance_lock import maintenance_lock
from lib.remote_utils import read_os_release, run
from lib.validation import validate_channel, validate_filesystem_path


_SOURCE_URL = "https://github.com/bluehexagons/basaltwater.git"
_SNAPSHOT_SOURCE_NAMES = {
    "basaltwater.py", "remote_setup.py", "lib", "plugins", "game", "desktop",
    "web", "smb", "security", "sync", "common", "deploy", INSTALLATION_METADATA_FILENAME,
}


def _refresh_channel(info: dict[str, object]) -> str:
    """Use the snapshot's branch, defaulting detached snapshots to main."""
    if info.get("installation_type") == "setup-snapshot":
        branch = info.get("branch")
        channel = "dev" if not branch or branch == "main" else "branch-" + str(branch)
    else:
        channel = info.get("channel")
    if not isinstance(channel, str):
        raise ValueError("Installed source has no managed channel or setup provenance")
    validate_channel(channel)
    return channel


def _upgrade_snapshot(repository: str, channel: str) -> dict[str, object]:
    """Stage a managed checkout and retain runtime data with rollback on activation."""
    from lib.privilege_policy import protected_path

    protected_path(repository, directory=True)
    protected_path(os.path.join(repository, INSTALLATION_METADATA_FILENAME))
    parent = os.path.dirname(repository)
    validate_filesystem_path(parent, must_exist=True)
    temporary = tempfile.mkdtemp(prefix=".basaltwater-refresh-", dir=parent)
    keep_staged = False
    try:
        staged = os.path.join(temporary, "source")
        result = run([
            "env", "GIT_TERMINAL_PROMPT=0", "git", "clone", "--", _SOURCE_URL, staged,
        ], check=False, capture_output=True, timeout=300)
        if result.returncode:
            raise RuntimeError("Could not download Basaltwater source; installed snapshot is unchanged")
        info = switch_channel(staged, channel)
        write_text_atomic(os.path.join(staged, ".basaltwater", "managed-install"), "basaltwater-v1\n")
        with open(os.path.join(staged, ".git", "info", "exclude"), "a", encoding="utf-8") as exclusions:
            exclusions.write("\n/deployments/\n/worktrees/\n/agent_payload/\n/device_pairing_payload/\n/web_panel_payload/\n/.remote_setup_args.json\n")
        # The controller stages just these source entries. Preserve all other
        # top-level runtime data without copying potentially large deployments.
        data_names = sorted(set(os.listdir(repository)) - _SNAPSHOT_SOURCE_NAMES)
        for name in data_names:
            if os.path.lexists(os.path.join(staged, name)):
                raise ValueError(f"Downloaded source conflicts with retained runtime data: {name}")
        backup = tempfile.mkdtemp(prefix=".basaltwater-before-refresh-", dir=parent)
        os.rmdir(backup)
        moved = []
        renamed = False
        try:
            os.rename(repository, backup)
            renamed = True
            for name in data_names:
                os.rename(os.path.join(backup, name), os.path.join(staged, name))
                moved.append(name)
            os.chmod(staged, 0o755)
            os.rename(staged, repository)
        except BaseException as error:
            try:
                for name in reversed(moved):
                    os.rename(os.path.join(staged, name), os.path.join(backup, name))
                if renamed:
                    os.rename(backup, repository)
            except OSError as recovery_error:
                keep_staged = True
                raise RuntimeError(
                    f"Source recovery needs review: backup={backup}, staged={staged}: {recovery_error}"
                ) from error
            raise
        os.chmod(backup, 0o700)
        print(f"Previous setup source retained at {backup}", flush=True)
        return info
    finally:
        if not keep_staged:
            shutil.rmtree(temporary)


def _apply_refresh(
    repository: str, channel: str, info: dict[str, object],
    config: SetupConfig, arguments: list[str],
) -> int:
    """Hold the setup lock across source activation and target reconciliation."""
    with maintenance_lock() as acquired:
        if not acquired:
            raise RuntimeError("Setup or maintenance is already running; refresh has made no changes")
        info = (
            _upgrade_snapshot(repository, channel)
            if info.get("installation_type") == "setup-snapshot" else upgrade_channel(repository)
        )
        print(f"Basaltwater is at {str(info['commit'])[:12]} on {info['channel']}", flush=True)
        print(f"Repeating saved local {config.system_type} setup for {config.username}", flush=True)
        # Keep saved options private and start the upgraded runner in a fresh process.
        with tempfile.TemporaryDirectory(prefix="basaltwater-refresh-") as temporary:
            argument_file = Path(temporary) / "setup-args.json"
            write_json_atomic(str(argument_file), arguments, mode=0o600)
            result = run([
                sys.executable, str(Path(repository) / "remote_setup.py"),
                "--args-file", str(argument_file),
            ], interactive=True, check=False, timeout=None, cwd=repository)
        return result.returncode


def _debian_setup() -> SetupConfig:
    """Read successful target state without triggering legacy state rewrites."""
    path = Path(SETUP_CONFIG_FILE)
    validate_filesystem_path(str(path), must_exist=True)
    info = path.lstat()
    parent = path.parent.stat()  # The standard state directory may be a managed symlink.
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or info.st_mode & 0o077 or parent.st_uid != os.geteuid()
            or parent.st_mode & 0o022):
        raise ValueError("Saved Debian setup must be a private root-owned file in a protected state directory")
    data = read_json_file(str(path))
    if _validate_setup_config(data):
        raise ValueError("Invalid saved Debian setup; restore its verified state before refreshing")
    data = dict(data)
    system_type = data.pop("system_type")
    data.pop("host", None)
    if "agent_tools" in data:
        # Modern records save the complete selection, including no agents.
        # Explicit exclusions protect both from_dict and the fresh target
        # parser from adding providers when profile defaults change.
        selected = set(data.get("agent_tools") or []) | {
            tool for tool in AGENT_TOOLS if data.get("install_" + tool)
        }
        selected -= set(data.get("agent_tools_removed") or [])
        data["agent_tools_removed"] = list(dict.fromkeys([
            *(data.get("agent_tools_removed") or []),
            *(tool for tool in AGENT_TOOLS if tool not in selected),
        ]))
    # Use the established serialization boundary to discard transient
    # credential-copy, password, live-network, and swap-initialization intent.
    config = SetupConfig.from_dict("localhost", system_type, data)
    config = SetupConfig.from_dict("localhost", system_type, config.to_dict())
    if config.system_type == "agent_cachyos" or config.custom_steps or config.dry_run:
        raise ValueError("refresh requires a completed full Debian setup")
    if config.enable_rdp:
        config.rdp_existing_password = True
    return config


def run_refresh_command(args: argparse.Namespace) -> int:
    if is_cachyos():
        from lib.cachyos_refresh import run_refresh_command as refresh_cachyos

        return refresh_cachyos(args)
    try:
        if getattr(args, "setup_overrides", None):
            raise ValueError("Additional refresh setup flags currently require CachyOS; use explicit setup on Debian")
        if read_os_release().get("ID") != "debian":
            raise ValueError("refresh supports local Debian and CachyOS setups")
        if os.geteuid() != 0:
            raise ValueError("Debian refresh requires root; run `sudo basaltw refresh` (also for --dry-run)")
        if not Path(SETUP_CONFIG_FILE).exists():
            raise ValueError("No successful local Debian setup is saved; run setup on this machine first")
        config = _debian_setup()
        arguments = shlex.split(" ".join(config.to_remote_args()))
        # A target refresh reuses local provider credentials instead of asking
        # for a fresh interactive Codex login.
        arguments.extend(["--agent-auth-mode", "none"])
        # Reuse target validation before changing source. It validates storage
        # as an existing target, without re-provisioning a VM from controller flags.
        from remote_setup import config_from_remote_args, _print_dry_run_plan
        from lib.system_types import get_steps_for_system_type

        parser = create_setup_argument_parser("Saved Debian setup", for_remote=True, allow_steps=True)
        config = config_from_remote_args(parser.parse_args(arguments))
        repository = managed_repository_path()
        info = get_channel_info(repository)
        channel = _refresh_channel(info)
        if args.dry_run:
            print(f"[DRY-RUN] Would upgrade Basaltwater on channel {channel}; no fetch or checkout")
            print(f"Saved local setup: {config.system_type} for {config.username}")
            _print_dry_run_plan(get_steps_for_system_type(config))
            return 0
        return _apply_refresh(repository, channel, info, config, arguments)
    except (OSError, RuntimeError, ValueError, TypeError, KeyError) as exc:
        print(f"Error: {exc}")
        return 1
