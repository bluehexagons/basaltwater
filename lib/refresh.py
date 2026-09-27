"""Upgrade the active source and replay this machine's successful setup."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shlex
import stat
import sys
import tempfile

from lib.arg_parser import create_setup_argument_parser
from lib.atomic_io import read_json_file, write_json_atomic
from lib.cachyos import is_cachyos
from lib.channel_manager import get_channel_info, managed_repository_path, upgrade_channel
from lib.config import SetupConfig
from lib.machine_state import SETUP_CONFIG_FILE, _validate_setup_config
from lib.remote_utils import read_os_release, run
from lib.validation import validate_filesystem_path


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
        if args.dry_run:
            info = get_channel_info(repository)
            if not info.get("channel") or info.get("installation_type") == "setup-snapshot":
                raise ValueError("refresh requires a managed source channel; upgrade setup snapshots from their controller")
            print(f"[DRY-RUN] Would upgrade Basaltwater on channel {info['channel']}; no fetch or checkout")
            print(f"Saved local setup: {config.system_type} for {config.username}")
            _print_dry_run_plan(get_steps_for_system_type(config))
            return 0
        info = upgrade_channel(repository)
        print(f"Basaltwater is at {str(info['commit'])[:12]} on {info['channel']}", flush=True)
        print(f"Repeating saved local {config.system_type} setup for {config.username}", flush=True)
        # A private argument file keeps configuration out of process listings.
        # Start the upgraded target runner directly: no credential prompts,
        # controller reprovisioning, source restaging, or old Python imports.
        with tempfile.TemporaryDirectory(prefix="basaltwater-refresh-") as temporary:
            argument_file = Path(temporary) / "setup-args.json"
            write_json_atomic(str(argument_file), arguments, mode=0o600)
            result = run([
                sys.executable, str(Path(repository) / "remote_setup.py"),
                "--args-file", str(argument_file),
            ], interactive=True, check=False, timeout=None, cwd=repository)
        return result.returncode
    except (OSError, RuntimeError, ValueError, TypeError, KeyError) as exc:
        print(f"Error: {exc}")
        return 1
